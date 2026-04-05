from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field

from .types import Block, CommitteeSelection, Message, MessageType, Vote


@dataclass(slots=True)
class PBFTSession:
    block: Block
    selection: CommitteeSelection
    prevotes: dict[int, str] = field(default_factory=dict)
    precommits: dict[int, str] = field(default_factory=dict)
    commits: dict[int, str] = field(default_factory=dict)
    prevote_sent: bool = False
    precommit_sent: bool = False
    commit_sent: bool = False
    finalized_block: Block | None = None
    future: asyncio.Future["BFTConsensusResult"] | None = None


@dataclass(frozen=True, slots=True)
class BFTConsensusResult:
    committee_id: str
    block_hash: str
    committed: bool
    signatures: tuple[str, ...]
    member_ids: tuple[int, ...]


class PBFTService:
    def __init__(self, node: "Node") -> None:
        self.node = node
        self.sessions: dict[tuple[str, str], PBFTSession] = {}
        self.pending_votes: dict[tuple[str, str], list[Vote]] = {}

    @staticmethod
    def quorum_size(member_count: int) -> int:
        return (2 * member_count) // 3 + 1

    def _session_key(self, committee_id: str, block_hash: str) -> tuple[str, str]:
        return (committee_id, block_hash)

    def _sign_vote(self, phase: MessageType, block_hash: str, committee_id: str) -> str:
        payload = f"{self.node.node_id}:{phase.value}:{block_hash}:{committee_id}".encode("utf-8")
        return hashlib.sha256(self.node.secret + payload).hexdigest()

    def _get_or_create_session(self, block: Block, selection: CommitteeSelection) -> PBFTSession:
        key = self._session_key(selection.committee_id, block.hash)
        session = self.sessions.get(key)
        if session is None:
            session = PBFTSession(block=block, selection=selection)
            self.sessions[key] = session
        return session

    async def _replay_pending_votes(self, key: tuple[str, str]) -> None:
        buffered = self.pending_votes.pop(key, [])
        for vote in buffered:
            message = Message(type=vote.phase, payload=vote, sender=vote.voter_id)
            if vote.phase == MessageType.BFT_PREVOTE:
                await self._on_prevote(message)
            elif vote.phase == MessageType.BFT_PRECOMMIT:
                await self._on_precommit(message)
            elif vote.phase == MessageType.BFT_COMMIT:
                await self._on_commit(message)

    def _validate_block(self, block: Block, selection: CommitteeSelection) -> bool:
        if block.committee_id != selection.committee_id:
            return False
        if tuple(block.committee_members) != tuple(selection.member_ids):
            return False
        if len({transaction.hash for transaction in block.transactions}) != len(block.transactions):
            return False
        if block.parent_hash != self.node.head_hash:
            return False
        if block.height != self.node.head_height + 1:
            return False
        return True

    async def finalize_block(self, block: Block, *, key: str) -> BFTConsensusResult:
        selection = await self.node.committee_selector.select_committee(key)
        if self.node.node_id != selection.leader_id:
            leader = self.node.network.get_node(selection.leader_id)
            if leader is None:
                raise ValueError("leader is unavailable")
            return await leader.bft.finalize_block(block, key=key)
        prepared_block = Block(
            parent_hash=block.parent_hash,
            transactions=block.transactions,
            signatures=(),
            committee_id=selection.committee_id,
            proposer_id=selection.leader_id,
            height=block.height,
            committee_members=selection.member_ids,
        )
        session = self._get_or_create_session(prepared_block, selection)
        if session.future is None:
            session.future = asyncio.get_running_loop().create_future()
        proposal = {
            "block": prepared_block,
            "selection": selection,
        }
        if self.node.node_id == selection.leader_id:
            for member_id in selection.member_ids:
                if member_id == self.node.node_id:
                    continue
                await self.node.send_message(member_id, Message(type=MessageType.BFT_PROPOSE, payload=proposal, sender=self.node.node_id))
        await self._on_propose(Message(type=MessageType.BFT_PROPOSE, payload=proposal, sender=selection.leader_id))
        return await asyncio.wait_for(session.future, timeout=self.node.config.bft.commit_timeout)

    async def handle_message(self, message: Message) -> None:
        if message.type == MessageType.BFT_PROPOSE:
            await self._on_propose(message)
        elif message.type == MessageType.BFT_PREVOTE:
            await self._on_prevote(message)
        elif message.type == MessageType.BFT_PRECOMMIT:
            await self._on_precommit(message)
        elif message.type == MessageType.BFT_COMMIT:
            await self._on_commit(message)

    async def _broadcast_vote(self, vote: Vote, members: tuple[int, ...]) -> None:
        message = Message(type=vote.phase, payload=vote, sender=self.node.node_id)
        await self.handle_message(message)
        for member_id in members:
            if member_id == self.node.node_id:
                continue
            await self.node.send_message(member_id, message.forwarded(sender=self.node.node_id))

    async def _on_propose(self, message: Message) -> None:
        payload = message.payload
        block = payload["block"]
        selection = payload["selection"]
        assert isinstance(block, Block)
        assert isinstance(selection, CommitteeSelection)
        if self.node.node_id not in selection.member_ids:
            return
        session = self._get_or_create_session(block, selection)
        if self.node.malicious:
            return
        if not self._validate_block(block, selection):
            return
        await self._replay_pending_votes(self._session_key(selection.committee_id, block.hash))
        if session.prevote_sent:
            return
        session.prevote_sent = True
        vote = Vote(
            phase=MessageType.BFT_PREVOTE,
            block_hash=block.hash,
            committee_id=selection.committee_id,
            voter_id=self.node.node_id,
            signature=self._sign_vote(MessageType.BFT_PREVOTE, block.hash, selection.committee_id),
        )
        await self._broadcast_vote(vote, selection.member_ids)

    async def _on_prevote(self, message: Message) -> None:
        vote = message.payload
        assert isinstance(vote, Vote)
        session = self.sessions.get(self._session_key(vote.committee_id, vote.block_hash))
        if session is None:
            self.pending_votes.setdefault(self._session_key(vote.committee_id, vote.block_hash), []).append(vote)
            return
        if vote.voter_id not in session.selection.member_ids:
            return
        session.prevotes[vote.voter_id] = vote.signature
        if self.node.malicious or session.precommit_sent:
            return
        if len(session.prevotes) >= self.quorum_size(len(session.selection.member_ids)):
            session.precommit_sent = True
            precommit = Vote(
                phase=MessageType.BFT_PRECOMMIT,
                block_hash=vote.block_hash,
                committee_id=vote.committee_id,
                voter_id=self.node.node_id,
                signature=self._sign_vote(MessageType.BFT_PRECOMMIT, vote.block_hash, vote.committee_id),
            )
            await self._broadcast_vote(precommit, session.selection.member_ids)

    async def _on_precommit(self, message: Message) -> None:
        vote = message.payload
        assert isinstance(vote, Vote)
        session = self.sessions.get(self._session_key(vote.committee_id, vote.block_hash))
        if session is None:
            self.pending_votes.setdefault(self._session_key(vote.committee_id, vote.block_hash), []).append(vote)
            return
        if vote.voter_id not in session.selection.member_ids:
            return
        session.precommits[vote.voter_id] = vote.signature
        if self.node.malicious or session.commit_sent:
            return
        if len(session.precommits) >= self.quorum_size(len(session.selection.member_ids)):
            session.commit_sent = True
            commit = Vote(
                phase=MessageType.BFT_COMMIT,
                block_hash=vote.block_hash,
                committee_id=vote.committee_id,
                voter_id=self.node.node_id,
                signature=self._sign_vote(MessageType.BFT_COMMIT, vote.block_hash, vote.committee_id),
            )
            await self._broadcast_vote(commit, session.selection.member_ids)

    async def _on_commit(self, message: Message) -> None:
        vote = message.payload
        assert isinstance(vote, Vote)
        session = self.sessions.get(self._session_key(vote.committee_id, vote.block_hash))
        if session is None:
            self.pending_votes.setdefault(self._session_key(vote.committee_id, vote.block_hash), []).append(vote)
            return
        if vote.voter_id not in session.selection.member_ids:
            return
        session.commits[vote.voter_id] = vote.signature
        if len(session.commits) < self.quorum_size(len(session.selection.member_ids)):
            return
        if session.finalized_block is None:
            finalized = session.block.with_signatures(tuple(sorted(session.commits.values())))
            session.finalized_block = finalized
            self.node.apply_finalized_block(finalized)
            await self.node.dht.store(finalized.hash, finalized)
            for transaction in finalized.transactions:
                await self.node.dht.store(transaction.hash, transaction)
            await self.node.gossip.gossip(MessageType.BLOCK_GOSSIP, finalized)
        result = BFTConsensusResult(
            committee_id=session.selection.committee_id,
            block_hash=session.block.hash,
            committed=True,
            signatures=tuple(sorted(session.commits.values())),
            member_ids=session.selection.member_ids,
        )
        if session.future is not None and not session.future.done():
            session.future.set_result(result)
