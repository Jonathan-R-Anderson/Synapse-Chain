# Python Ethereum-Like Client Workspace

This repository contains a Python implementation of an Ethereum-like blockchain stack split into focused packages instead of one monolith.

## Repository Layout

- `consensus/`: beacon-style hybrid consensus simulator with fork choice, attestations, committees, PBFT-style networking, and DHT research components
- `execution/`: execution-layer workspace with reusable crates for primitives, crypto, encoding, state, transactions, zk, EVM, block execution, sync, replay, and JSON-RPC
- `debug/`: namespace package hook for the execution tracing tools
- `replay/`: namespace package hook for block replay tooling
- `execution_tests/`: namespace package hook for execution fixture runners
- `docker-compose.yml`: quick-start container definitions
- `.env`: default environment values for Docker Compose
- `graphical_client.py`: Python desktop wallet and explorer for the execution JSON-RPC API
- `deploy_contract.py`: Python CLI for deploying compiled smart contracts through the execution JSON-RPC API

## Components

### Consensus

The consensus client lives under `consensus/src/consensus` and models:

- validator scoring based on stake plus compute, storage, networking, and activity
- proposer and committee selection
- attestation processing, checkpoints, justification, and finality
- asyncio networking experiments including gossip, DHT lookup, and PBFT-style committee phases

See [`consensus/README.md`](./consensus/README.md).

### Execution

The execution workspace lives under `execution/src/crates` and is split into packages:

- `primitives`: `U256`, `Address`, and `Hash`
- `crypto`: Keccak-256, secp256k1 signing and address derivation
- `encoding`: RLP
- `state`: account/state storage and trie commitments
- `zk`: proof abstractions and verifier registry hooks
- `transactions`: legacy, EIP-1559, and ZK-aware transaction models
- `evm`: interpreter, gas, calls, creates, logs, storage, and precompiles
- `execution`: block processing, receipts, RPC, sync, replay, and debugging tools

See [`execution/src/crates/README.md`](./execution/src/crates/README.md).

### Sync Subsystem

The execution crate now includes a role-aware sync framework under `execution/src/crates/execution/src/execution/sync` with:

- full sync
- snap sync
- light sync
- checkpoint persistence
- reorg-aware canonical chain tracking
- state reconstruction
- snapshot generation and restoration
- proof-verification hooks for future zk-based sync

It supports composing runtime roles such as full, archive, light, validator, builder, state-provider, RPC, indexer, zk prover/verifier, watchtower, bootnode, DHT, and snapshot generator.

## Running Locally

### Prerequisites

- Docker Engine with `docker compose`
- Python 3 for invoking `start.py`
- [`.env`](./.env) populated with the runtime values you want to use

The root launcher is Docker-backed. It either starts long-running Compose services or runs the consensus simulator as a one-shot container.

### Quick Start

Bring up the full execution-side stack:

```bash
python3 start.py start all
python3 start.py ps
python3 start.py logs all
```

Run the consensus simulation as a separate one-shot workload:

```bash
python3 start.py start consensus
```

If you only want the JSON-RPC endpoint:

```bash
python3 start.py start rpc
```

The RPC server listens on `http://127.0.0.1:${EXECUTION_RPC_PORT}` using values from [`.env`](./.env).

### `start.py` Command Modes

`start.py` has four command modes:

| Command | What it does |
| --- | --- |
| `python3 start.py start <target>` | Starts one target, or multiple long-running execution services when `<target>` is `all`. |
| `python3 start.py stop <target>` | Stops one target, or all long-running execution services when `<target>` is `all`. |
| `python3 start.py logs <target>` | Shows logs for one target, or tails logs for all long-running execution services when `<target>` is `all`. |
| `python3 start.py ps` | Shows current Compose service status. |

Useful flags:

- `--dry-run`: print the Docker command without executing it
- `--build` / `--no-build`: enable or skip image rebuilds before launch
- `--detach` / `--no-detach`: run attached or detached for `start`
- `--remove` / `--no-remove`: remove stopped containers instead of just stopping them
- `--follow` / `--no-follow`: follow logs live or print and exit
- `--tail <n>`: control how many log lines are shown

### `start.py` Start Targets

The `start` command accepts these targets:

| Target | Docker service | What it runs | What it accomplishes |
| --- | --- | --- | --- |
| `i2p` | `i2p-router` | `geti2p/i2p` | Starts the I2P router and SAM bridge used by privacy-mode execution peers. The router console is exposed on `127.0.0.1:${I2P_CONSOLE_PORT}` by default. |
| `rpc` | `execution-rpc` | `python -m rpc.server` | Starts the execution JSON-RPC API for local wallet/tool compatibility. This is the service that exposes port `8545` by default. |
| `full` | `execution-full` | `container_node.py --mode full --keep-alive` | Runs a full-sync execution demo node with pruning enabled. It validates and stores recent chain state under the mounted execution data directory. |
| `light` | `execution-light` | `container_node.py --mode light --keep-alive` | Runs a light-sync execution demo node. It tracks headers and proof-backed state fragments instead of maintaining full historical state. |
| `archive` | `execution-archive` | `container_node.py --mode archive --keep-alive` | Runs an archive execution demo node with pruning disabled so older history is retained. |
| `bootnode` | `execution-bootnode` | `container_node.py --mode bootnode --keep-alive` | Runs a discovery-focused bootnode demo. It maintains peer-discovery state and does not perform chain sync. |
| `state-provider` | `execution-state-provider` | `container_node.py --mode state-provider --keep-alive` | Runs a full node that also serves state and blocks and can generate snapshots for other sync workflows. |
| `validator` | `execution-validator` | `container_node.py --mode validator --keep-alive` | Runs a full-validation execution demo node configured for validator-style responsibilities. |
| `consensus` | `consensus-sim` | `python -m consensus` | Runs the beacon-style consensus simulator for the configured validator count and epoch count, then exits. This target uses `docker compose --profile consensus run --rm --build consensus-sim` rather than a long-running `up` service. |
| `all` | multiple services | `docker compose up --build -d ...` | Starts every long-running execution-side service together: `i2p`, `rpc`, `full`, `light`, `archive`, `bootnode`, `state-provider`, and `validator`. It does not include `consensus`. |

`all` intentionally excludes `consensus` because the consensus simulator is modeled as a one-shot job, not a persistent service.

### Whole-Stack Bring-Up Diagram

The current Compose setup is best thought of as one launcher driving several independent demo workloads. The execution demo containers share the same mounted execution data root, while the consensus simulator is separate and optional.

```text
                                  +----------------------+
                                  |      .env file       |
                                  | ports, chain id,     |
                                  | data dir, epochs     |
                                  +----------+-----------+
                                             |
                                             v
+----------------------+           +---------+----------+           +----------------------+
|   Docker Engine      |<----------|      start.py      |---------->| ./data/execution     |
|   + docker compose   |           | launcher wrapper   |           | mounted into         |
+----------+-----------+           +---------+----------+           | execution containers |
           ^                                 |                      +----------------------+
           |                                 |
           |                +----------------+----------------+
           |                |                                 |
           |                v                                 v
           |   python3 start.py start all         python3 start.py start consensus
           |                |                                 |
           |                v                                 v
           |   +---------------------------+       +---------------------------+
           |   | Long-running execution    |       | One-shot consensus job    |
           |   | services                  |       | consensus-sim             |
           |   +---------------------------+       +---------------------------+
           |   | i2p-router                |       | runs for                  |
           |   | execution-rpc             |       | CONSENSUS_EPOCHS, then    |
           |   | execution-full            |       | exits                     |
           |   | execution-light           |
           |   | execution-archive         |       +---------------------------+
           |   | execution-bootnode        |
           |   | execution-state-provider  |
           |   | execution-validator       |
           |   +-------------+-------------+
           |                 |
           |                 v
           |       http://127.0.0.1:8545
           |       from execution-rpc
           |
           +-- inspected with:
               python3 start.py ps
               python3 start.py logs all
```

The execution node containers currently run role-aware demo workloads from `execution/src/crates/execution/examples/container_node.py`. In plain mode they still fall back to in-process fixture peers. In privacy mode they can expose an I2P SAM-backed overlay for execution sync peer traffic. The separate consensus simulator remains an in-memory network and is not routed over I2P yet.

### Long-Running Server Launch

There are now two distinct launch styles in this repository:

- embedded devnet: the Tk client can start a prefunded local dev chain for wallet and contract testing
- long-running server stack: `start.py` plus Docker Compose starts the persistent execution services defined in `docker-compose.yml`

The long-running stack is the non-dev launch path for this repository. It is persistent, uses the mounted execution data directory, and consumes bootstrap and chain settings from [`.env`](./.env). It is still this repository's own chain and demo runtime, not Ethereum mainnet or an official public testnet.

To launch the long-running stack with explicit bootstrap settings:

1. Edit [`.env`](./.env).
2. Choose a chain ID for your network and leave `EXECUTION_DATA_DIR` pointed at durable storage.
3. Set `EXECUTION_BOOTNODES` to a comma-separated list of bootstrap peers.
4. Optionally set `EXECUTION_STATIC_PEERS` to a comma-separated list of peers you always want loaded into the node config.

Single-host Compose example:

```dotenv
EXECUTION_CHAIN_ID=1337
EXECUTION_BOOTNODES=execution-bootnode
EXECUTION_STATIC_PEERS=
```

Remote-host example:

```dotenv
EXECUTION_CHAIN_ID=424242
EXECUTION_BOOTNODES=bootnode-1.example.org:30303,bootnode-2.example.org:30303
EXECUTION_STATIC_PEERS=full-1.example.org:30303,state-provider-1.example.org:30303
```

Then bring the stack up:

```bash
python3 start.py start bootnode
python3 start.py start all
python3 start.py ps
python3 start.py logs bootnode
python3 start.py logs all
```

To run the execution stack through I2P:

1. Leave the checked-in default `EXECUTION_PRIVACY_NETWORK=i2p` in [`.env`](./.env), or set it explicitly if you changed it.
2. Leave `python3 start.py start all` as the launcher entrypoint. `all` now includes `i2p-router`.
3. Keep `EXECUTION_I2P_BOOTSTRAP_FILE=/var/lib/crypto/execution/i2p/bootstrap-peers.txt` for the single-host Compose stack.
4. The `execution-bootnode` container publishes its I2P destination into that shared file automatically.
5. The other execution containers wait for that file and use the published destination through the SAM bridge on `i2p-router:7656`.

Minimal privacy-mode `.env` example:

```dotenv
EXECUTION_PRIVACY_NETWORK=i2p
EXECUTION_BOOTNODES=
EXECUTION_STATIC_PEERS=
EXECUTION_I2P_SAM_HOST=i2p-router
EXECUTION_I2P_SAM_PORT=7656
EXECUTION_I2P_BOOTSTRAP_FILE=/var/lib/crypto/execution/i2p/bootstrap-peers.txt
```

What those settings do today:

- `EXECUTION_CHAIN_ID` is now loaded by the execution demo containers as well as the RPC server, so the long-running roles and the JSON-RPC endpoint stay on the same configured chain ID
- `EXECUTION_BOOTNODES` and `EXECUTION_STATIC_PEERS` are now loaded into the execution demo node config and surfaced in startup logs and bootnode discovery state
- the default single-host bootstrap value is `execution-bootnode`, which is the Compose service name reachable from the other execution containers
- `EXECUTION_PRIVACY_NETWORK=i2p` switches execution peer discovery and sync traffic onto an I2P SAM stream overlay backed by the `i2p-router` container
- `EXECUTION_I2P_BOOTSTRAP_FILE` provides a shared destination file so the bootnode can publish its I2P endpoint for the rest of the execution stack

Current limit:

- the execution stack now has an I2P overlay for execution sync peer traffic, but the JSON-RPC API still binds normally on `EXECUTION_RPC_HOST:EXECUTION_RPC_PORT` unless you front it separately
- the consensus simulator still uses an in-memory network and is not on I2P yet
- this remains this repository's own chain and demo runtime, not Ethereum mainnet or a public testnet

### Direct Docker Commands

If you prefer Docker Compose directly, the launcher maps to these common commands:

```bash
docker compose up --build i2p-router execution-rpc
docker compose up --build -d i2p-router execution-rpc execution-full execution-light execution-archive execution-bootnode execution-state-provider execution-validator
docker compose --profile consensus run --rm --build consensus-sim
docker compose logs --tail=100 -f i2p-router execution-rpc execution-full execution-light execution-archive execution-bootnode execution-state-provider execution-validator
```

### Environment Variables

The default Docker values live in [`.env`](./.env):

- `EXECUTION_RPC_HOST`
- `EXECUTION_RPC_PORT`
- `EXECUTION_CHAIN_ID`
- `EXECUTION_MINING_MODE`
- `EXECUTION_PRIVACY_NETWORK`
- `EXECUTION_DATA_DIR`
- `EXECUTION_STATE_ROOT`
- `EXECUTION_NODE_STATUS_INTERVAL`
- `EXECUTION_BOOTNODES`
- `EXECUTION_STATIC_PEERS`
- `EXECUTION_I2P_SAM_HOST`
- `EXECUTION_I2P_SAM_PORT`
- `EXECUTION_I2P_SIGNATURE_TYPE`
- `EXECUTION_I2P_INBOUND_QUANTITY`
- `EXECUTION_I2P_OUTBOUND_QUANTITY`
- `EXECUTION_I2P_TIMEOUT_SECONDS`
- `EXECUTION_I2P_BOOTSTRAP_FILE`
- `EXECUTION_I2P_BOOTSTRAP_WAIT_SECONDS`
- `EXECUTION_I2P_PUBLISH_DESTINATION`
- `I2P_DATA_DIR`
- `I2P_CONSOLE_PORT`
- `CONSENSUS_VALIDATORS`
- `CONSENSUS_EPOCHS`

`EXECUTION_DATA_DIR` is mounted into the execution containers so there is a stable host path available for persisted sync and RPC artifacts. `EXECUTION_STATE_ROOT` is the in-container base path used by the execution demo modes when they create per-role state directories such as `demo-full` or `demo-validator`. `EXECUTION_BOOTNODES` and `EXECUTION_STATIC_PEERS` are comma-separated peer references consumed by the long-running execution demo roles. `EXECUTION_CHAIN_ID` now applies to both `execution-rpc` and the example execution containers. `EXECUTION_PRIVACY_NETWORK=i2p` enables the I2P SAM overlay for execution sync peers, and `EXECUTION_I2P_BOOTSTRAP_FILE` is the shared destination file used by the bootnode publisher and the other execution services.

### Tests

Execution crate tests:

```bash
python3 execution/src/crates/run_tests.py
```

Consensus tests:

```bash
python3 consensus/run_tests.py
```

### Running the RPC Server Without Docker

```bash
PYTHONPATH=execution/src/crates/primitives/src:execution/src/crates/crypto/src:execution/src/crates/encoding/src:execution/src/crates/state/src:execution/src/crates/zk/src:execution/src/crates/transactions/src:execution/src/crates/evm/src:execution/src/crates/execution/src \
python3 -m rpc.server --host 127.0.0.1 --port 8545 --chain-id 1337 --mode instant
```

### Graphical Desktop Client

The repository now includes a Python desktop client in [graphical_client.py](./graphical_client.py).

Launch it against an existing RPC server:

```bash
python3 graphical_client.py
```

Or launch it with the built-in prefunded devnet:

```bash
python3 graphical_client.py --start-devnet
```

The embedded devnet runs on `http://127.0.0.1:8546` by default and exposes three prefunded wallets so you can send native-coin transfers immediately from the GUI.

What the desktop client includes:

- connection dashboard for chain id, block number, gas pricing, and latest block state
- embedded devnet launcher with prefunded accounts for local testing
- wallet import by private key with address derivation
- native transfer builder and signed transaction submission
- Solidity contract compile/deploy workflow using `solc` plus the execution deployment toolkit
- consensus simulation and reward-block mining lab using the execution `dev_mine` extension
- network map tab that inspects the configured stack and, when Docker access is available, current container IP addresses
- block, transaction, receipt, and trace explorer
- `eth_call`, `eth_estimateGas`, and `debug_traceCall` tools
- raw JSON-RPC console for arbitrary method calls

Current client scope:

- it supports native-coin transfers on this chain
- it can deploy compiled Solidity contracts, but Solidity compilation still depends on an external `solc` install
- it can mint developer reward blocks from the GUI, but those rewards come from a dev RPC extension and are not canonical Ethereum consensus behavior
- it does not implement an exchange, order book, AMM, or token trading backend because those contracts and APIs do not exist in the current node

### Smart Contract Deployment

The repository now includes a first-class contract deployment tool in [deploy_contract.py](./deploy_contract.py). It signs a contract-creation transaction locally, submits it through the execution JSON-RPC API, waits for the receipt by default, and reports the deployed address and receipt status as JSON.

The execution engine already supports EVM `CREATE`. The new deployment feature adds a supported workflow around it:

- load compiled bytecode from `.bin`, `.hex`, or JSON artifacts
- optionally load ABI metadata from a separate `.abi` file
- ABI-encode constructor arguments
- sign a legacy or EIP-1559 deployment transaction locally
- submit it with `eth_sendRawTransaction`
- wait for `eth_getTransactionReceipt`
- confirm deployed code with `eth_getCode`

#### 1. Start an RPC endpoint

Any execution RPC node works. Two practical options in this repository are:

```bash
python3 start.py start rpc
```

Or the prefunded embedded devnet from the desktop client:

```bash
python3 graphical_client.py --start-devnet
```

If you use the embedded devnet, point the deployer at `http://127.0.0.1:8546` and use private keys `1`, `2`, or `3`.

#### 2. Compile Solidity externally

This repository does not bundle `solc`, Foundry, or Hardhat. Compile Solidity with an external tool first.

Example with `solc`:

```bash
mkdir -p build
solc --bin --abi Counter.sol -o build
```

That produces:

- `build/Counter.bin`: deployment bytecode
- `build/Counter.abi`: ABI metadata

The deploy tool also accepts JSON artifacts that already include `abi` and deployable bytecode.

#### 3. Deploy the contract

Deploy a `solc --bin --abi` artifact pair:

```bash
python3 deploy_contract.py \
  --rpc-url http://127.0.0.1:8546 \
  --artifact build/Counter.bin \
  --abi-path build/Counter.abi \
  --private-key 1
```

Deploy a contract with constructor arguments:

```bash
python3 deploy_contract.py \
  --rpc-url http://127.0.0.1:8546 \
  --artifact build/Greeter.bin \
  --abi-path build/Greeter.abi \
  --private-key 1 \
  --constructor-args '["hello from this chain"]'
```

Deploy from a JSON artifact containing multiple contracts:

```bash
python3 deploy_contract.py \
  --rpc-url http://127.0.0.1:8546 \
  --artifact build/Combined.json \
  --contract-name Counter \
  --private-key 1
```

The command prints JSON describing the deployment, for example:

```json
{
  "artifact": "/abs/path/build/Counter.bin",
  "contractName": "Counter",
  "sender": "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf",
  "nonce": 0,
  "transactionHash": "0x...",
  "predictedContractAddress": "0xf2e246bb76df876cef8b38ae84130f4f55de395b",
  "contractAddress": "0xf2e246bb76df876cef8b38ae84130f4f55de395b",
  "txType": "eip1559",
  "receiptStatus": "0x1",
  "blockNumber": "0x1",
  "gasUsed": "0x..."
}
```

Important flags:

- `--tx-type legacy` to send a legacy deployment transaction instead of EIP-1559
- `--gas-limit`, `--value`, `--chain-id` to override transaction fields
- `--gas-price` for legacy transactions
- `--max-priority-fee-per-gas` and `--max-fee-per-gas` for EIP-1559
- `--no-wait` to return immediately after the raw transaction is accepted
- `--include-receipt`, `--include-transaction`, `--include-raw-transaction` for more detailed JSON output

Show all options with:

```bash
python3 deploy_contract.py --help
```

#### Current ABI Scope

Constructor argument encoding in the deploy tool currently supports these Solidity ABI scalar types:

- `address`
- `bool`
- `uint` and `uint<N>`
- `int` and `int<N>`
- `bytes`
- `bytes<N>`
- `string`

Arrays and tuples are not implemented yet in the Python ABI encoder used by the deploy CLI.

### API Interaction Guide

The execution API is an HTTP JSON-RPC 2.0 server intended for local Ethereum-style tooling.

#### Transport

- Endpoint: `http://127.0.0.1:${EXECUTION_RPC_PORT}`
- Protocol: JSON-RPC 2.0 over HTTP `POST`
- Content type: `application/json`
- Batch requests: supported
- Notifications: supported by omitting `id`; the server responds with HTTP `204 No Content`
- CORS: enabled with `Access-Control-Allow-Origin: *`
- WebSockets: not implemented
- Auth: not implemented

Minimal request envelope:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "eth_blockNumber",
  "params": []
}
```

Minimal error envelope:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32602,
    "message": "invalid params"
  }
}
```

#### Common Encoding Rules

- Quantities are `0x`-prefixed hex strings such as `0x0`, `0x1`, `0x5208`
- Binary data is `0x`-prefixed hex such as transaction payloads, bytecode, calldata, and hashes
- Addresses are 20-byte hex strings such as `0x1111111111111111111111111111111111111111`
- Hashes are 32-byte hex strings
- Booleans must be JSON `true` / `false`

Block selectors accepted by the API:

- `"latest"`
- `"earliest"`
- `"pending"`
- a hex quantity such as `"0x0"` or `"0xa"`

The tags `"safe"` and `"finalized"` are currently rejected.

#### Ways To Interact With The API

You can interact with the API from any HTTP client that can send JSON. The most practical options are:

1. `curl` for direct manual inspection
2. Python scripts using `urllib.request` or `requests`
3. JavaScript using `fetch`
4. Ethereum-compatible tooling that lets you point at a custom JSON-RPC URL and call explicit methods

`curl` helper function:

```bash
RPC_URL="http://127.0.0.1:8545"

rpc() {
  local method="$1"
  local params="${2:-[]}"
  curl -s "$RPC_URL" \
    -H 'content-type: application/json' \
    --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"${method}\",\"params\":${params}}"
  echo
}
```

Examples:

```bash
rpc web3_clientVersion
rpc net_version
rpc eth_chainId
rpc eth_blockNumber
rpc eth_getBlockByNumber '["latest", true]'
rpc eth_getBalance '["0x1111111111111111111111111111111111111111","latest"]'
```

Python example:

```python
import json
from urllib.request import Request, urlopen

RPC_URL = "http://127.0.0.1:8545"

def rpc(method: str, params=None, request_id: int = 1):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": [] if params is None else params,
    }).encode("utf-8")
    request = Request(
        RPC_URL,
        data=payload,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urlopen(request) as response:
        body = response.read().decode("utf-8")
    return json.loads(body)

print(rpc("eth_blockNumber"))
print(rpc("eth_getBlockByNumber", ["latest", True]))
```

JavaScript example:

```js
const RPC_URL = "http://127.0.0.1:8545";

async function rpc(method, params = [], id = 1) {
  const response = await fetch(RPC_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      jsonrpc: "2.0",
      id,
      method,
      params,
    }),
  });
  return await response.json();
}

console.log(await rpc("eth_blockNumber"));
console.log(await rpc("eth_getBlockByNumber", ["latest", true]));
```

#### Batch Requests

Batch requests are standard JSON arrays of request objects:

```bash
curl -s "$RPC_URL" \
  -H 'content-type: application/json' \
  --data '[
    {"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]},
    {"jsonrpc":"2.0","id":2,"method":"eth_blockNumber","params":[]},
    {"jsonrpc":"2.0","id":3,"method":"web3_clientVersion","params":[]}
  ]'
```

#### Notifications

If you omit `id`, the server treats the request as a notification and returns no JSON body:

```bash
curl -i "$RPC_URL" \
  -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[]}'
```

#### Supported RPC Methods

##### Metadata And Network

| Method | Params | Returns | Notes |
| --- | --- | --- | --- |
| `web3_clientVersion` | `[]` | client version string | Example: `"python-execution/phase8"` |
| `net_version` | `[]` | network id string | Defaults to the configured chain id as a string |
| `eth_chainId` | `[]` | hex quantity | Returns the configured chain id |

Examples:

```bash
rpc web3_clientVersion
rpc net_version
rpc eth_chainId
```

##### Chain And Block Queries

| Method | Params | Returns | Notes |
| --- | --- | --- | --- |
| `eth_blockNumber` | `[]` | hex quantity | Current head block number |
| `eth_getBlockByNumber` | `[blockSelector, fullTransactions]` | block object or `null` | `fullTransactions` must be a boolean |
| `eth_feeHistory` | `[blockCount, newestBlock, rewardPercentiles?]` | fee history object | `reward` entries are currently stubbed as `0x0` values |

Examples:

```bash
rpc eth_blockNumber
rpc eth_getBlockByNumber '["latest", false]'
rpc eth_getBlockByNumber '["latest", true]'
rpc eth_getBlockByNumber '["pending", true]'
rpc eth_feeHistory '["0x4","latest",[25,50,75]]'
```

##### State Queries

| Method | Params | Returns | Notes |
| --- | --- | --- | --- |
| `eth_getBalance` | `[address, blockSelector?]` | hex quantity | Supports `latest`, `earliest`, `pending`, or explicit block number |
| `eth_getTransactionCount` | `[address, blockSelector?]` | hex quantity | Returns account nonce |
| `eth_getCode` | `[address, blockSelector?]` | hex data | Returns deployed bytecode |
| `eth_getStorageAt` | `[address, position, blockSelector?]` | 32-byte hex word | `position` must be a hex quantity |
| `eth_gasPrice` | `[]` | hex quantity | Suggested gas price |
| `eth_maxPriorityFeePerGas` | `[]` | hex quantity | Suggested tip |
| `eth_accounts` | `[]` | address array | Empty by default unless `local_accounts` is configured |

Examples:

```bash
rpc eth_getBalance '["0x1111111111111111111111111111111111111111","latest"]'
rpc eth_getTransactionCount '["0x1111111111111111111111111111111111111111","pending"]'
rpc eth_getCode '["0x1111111111111111111111111111111111111111","latest"]'
rpc eth_getStorageAt '["0x1111111111111111111111111111111111111111","0x0","latest"]'
rpc eth_gasPrice
rpc eth_maxPriorityFeePerGas
rpc eth_accounts
```

##### Simulation And Tracing

| Method | Params | Returns | Notes |
| --- | --- | --- | --- |
| `eth_call` | `[callObject, blockSelector?, stateOverrides?]` | hex data | Third argument is accepted by the handler signature but state override semantics are not implemented |
| `eth_estimateGas` | `[callObject, blockSelector?, stateOverrides?]` | hex quantity | Binary-search estimate up to the configured gas cap |
| `debug_traceCall` | `[callObject, blockSelector, traceOptions?]` | trace object | Supports toggling memory/stack/storage capture |
| `debug_traceTransaction` | `[txHash, traceOptions?]` | trace object | Traces a mined transaction against reconstructed pre-state |

Call object fields accepted by `eth_call`, `eth_estimateGas`, and `debug_traceCall`:

- `from`
- `to`
- `gas`
- `gasPrice`
- `maxFeePerGas`
- `maxPriorityFeePerGas`
- `value`
- `data`
- `input`
- `accessList`

If both `data` and `input` are provided, they must be identical.

Trace options accepted by `debug_traceCall` and `debug_traceTransaction`:

- `disableMemory`
- `disableStack`
- `disableStorage`

Examples:

```bash
rpc eth_call '[{"from":"0x0000000000000000000000000000000000000000","to":"0x1111111111111111111111111111111111111111","data":"0x"},"latest"]'
rpc eth_estimateGas '[{"from":"0x0000000000000000000000000000000000000000","to":"0x1111111111111111111111111111111111111111","data":"0x"},"latest"]'
rpc debug_traceCall '[{"from":"0x0000000000000000000000000000000000000000","to":"0x1111111111111111111111111111111111111111","data":"0x"},"latest",{"disableMemory":true}]'
rpc debug_traceTransaction '["0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",{}]'
```

##### Transaction Submission

| Method | Params | Returns | Notes |
| --- | --- | --- | --- |
| `eth_sendRawTransaction` | `[signedTxHex]` | transaction hash | The transaction must already be signed locally |
| `eth_getTransactionByHash` | `[txHash]` | transaction object or `null` | Pending transactions have `null` block fields |
| `eth_getTransactionReceipt` | `[txHash]` | receipt object or `null` | `null` until a pending transaction is included in a block |

Examples:

```bash
rpc eth_getTransactionByHash '["0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]'
rpc eth_getTransactionReceipt '["0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]'
rpc eth_sendRawTransaction '["0xSIGNED_TRANSACTION_BYTES"]'
```

Important write-path notes:

- The server does not expose `eth_sendTransaction`
- The server does not manage unlocked keys
- `eth_accounts` is empty by default
- A raw transaction must be signed before submission
- The sender must already have enough balance for gas and value

##### Developer Extensions

| Method | Params | Returns | Notes |
| --- | --- | --- | --- |
| `dev_getConfig` | `[]` | object | Returns dev-side compatibility settings such as coinbase and mining mode |
| `dev_setCoinbase` | `[address]` | address | Updates the beneficiary used for later dev mining |
| `dev_mine` | `[options?]` | block summary array | Mines one or more blocks, can mine empty blocks, and can mint a configured developer reward to the beneficiary |

Example:

```bash
rpc dev_getConfig
rpc dev_setCoinbase '["0x1111111111111111111111111111111111111111"]'
rpc dev_mine '[{"count":"0x1","reward":"0x3e8","beneficiary":"0x1111111111111111111111111111111111111111","allowEmpty":true,"algorithm":"manual"}]'
```

#### Mining / Block Production Behavior

The RPC server supports two execution modes:

- `instant`: every accepted `eth_sendRawTransaction` is immediately sealed into a block
- `mempool`: submitted transactions remain pending until the node internally appends a block

The server does not currently expose a JSON-RPC method such as `evm_mine` for manual block production.

#### Response Shapes

Block objects returned by `eth_getBlockByNumber` include:

- `number`
- `hash`
- `parentHash`
- `nonce`
- `sha3Uncles`
- `logsBloom`
- `transactionsRoot`
- `stateRoot`
- `receiptsRoot`
- `miner`
- `difficulty`
- `totalDifficulty`
- `extraData`
- `size`
- `gasLimit`
- `gasUsed`
- `timestamp`
- `transactions`
- `uncles`
- `mixHash`
- `baseFeePerGas` when applicable

Transaction objects returned by `eth_getTransactionByHash` or embedded in full block responses include:

- `hash`
- `nonce`
- `blockHash`
- `blockNumber`
- `transactionIndex`
- `from`
- `to`
- `value`
- `gas`
- `gasPrice`
- `input`
- `type`
- `chainId`
- `v`
- `r`
- `s`
- `maxFeePerGas` for EIP-1559 / ZK transactions
- `maxPriorityFeePerGas` for EIP-1559 / ZK transactions
- `accessList` when present

Receipt objects returned by `eth_getTransactionReceipt` include:

- `transactionHash`
- `transactionIndex`
- `blockHash`
- `blockNumber`
- `from`
- `to`
- `contractAddress`
- `cumulativeGasUsed`
- `gasUsed`
- `logs`
- `logsBloom`
- `status`
- `effectiveGasPrice`
- `type`

Trace objects returned by `debug_traceCall` and `debug_traceTransaction` include:

- `gas`
- `failed`
- `returnValue`
- `structLogs`
- `error` when the call reverted or otherwise failed

Each `structLogs` entry includes:

- `pc`
- `op`
- `gas`
- `gasCost`
- `depth`
- `stack` unless disabled
- `memory` unless disabled
- `storage` unless disabled
- `error` when the step failed

#### Typical Error Codes

| Code | Meaning |
| --- | --- |
| `-32700` | parse error |
| `-32600` | invalid request |
| `-32601` | method not found |
| `-32602` | invalid params |
| `-32603` | internal error |
| `-32000` | server-side execution or validation error |
| `3` | execution reverted |

Typical write-path error messages include:

- `already known`
- `nonce too low`
- `nonce too high`
- `invalid sender`
- `insufficient funds for gas * price + value`
- `intrinsic gas too low`
- `unsupported transaction type`
- `invalid transaction`
- `out of gas`

#### Current Interaction Limits

- No WebSocket transport
- No filter or subscription methods
- No `eth_sendTransaction`
- No unlocked account management
- No RPC method for manual mining
- No `safe` or `finalized` block tags
- No log-filtering API such as `eth_getLogs`
- The stock server starts from a fresh in-memory genesis state unless you customize it
- Write flows are therefore possible only if you supply a valid signed transaction from an account that already has balance in the node state

## Where To Start

- execution RPC and block processing: [`execution/src/crates/execution/README.md`](./execution/src/crates/execution/README.md)
- consensus design: [`consensus/README.md`](./consensus/README.md)
- crate inventory: [`execution/src/crates/README.md`](./execution/src/crates/README.md)
