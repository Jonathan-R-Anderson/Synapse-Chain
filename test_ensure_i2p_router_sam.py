from __future__ import annotations

import unittest
from dataclasses import dataclass
from urllib.parse import parse_qs

import ensure_i2p_router_sam as ensure_sam


CONFIG_PAGE_SAM_DISABLED = """
<form action="" method="POST">
<input type="hidden" name="nonce" value="nonce-disabled" >
<table id="clientconfig">
<tr><td align="right"><label for="4">consoleBrowser</label></td><td align="center"><input type="checkbox" class="optbox" id="4" name="4.enabled"></td><td align="center"><button type="submit" name="action" value="Start 4" >Start<span class=hide> 4</span></button></td></tr>
<tr><td align="right"><label for="1">SAMBridge</label></td><td align="center"><input type="checkbox" class="optbox" id="1" name="1.enabled"></td><td align="center"><button type="submit" name="action" value="Start 1" >Start<span class=hide> 1</span></button></td></tr>
<tr><td align="right"><label for="2">Tunnels</label></td><td align="center"><input type="checkbox" class="optbox" id="2" name="2.enabled" checked="checked" ></td><td align="center"><button type="submit" name="action" value="Stop 2" >Stop<span class=hide> 2</span></button></td></tr>
<tr><td align="right"><label for="0">webConsole</label></td><td align="center"><input type="checkbox" class="optbox" id="0" name="0.enabled" checked="checked" disabled="disabled" ></td><td align="center"></td></tr>
</table>
</form>
"""

CONFIG_PAGE_SAM_ENABLED = """
<form action="" method="POST">
<input type="hidden" name="nonce" value="nonce-enabled" >
<table id="clientconfig">
<tr><td align="right"><label for="1">SAMBridge</label></td><td align="center"><input type="checkbox" class="optbox" id="1" name="1.enabled" checked="checked" ></td><td align="center"><button type="submit" name="action" value="Stop 1" >Stop<span class=hide> 1</span></button></td></tr>
<tr><td align="right"><label for="2">Tunnels</label></td><td align="center"><input type="checkbox" class="optbox" id="2" name="2.enabled" checked="checked" ></td><td align="center"><button type="submit" name="action" value="Stop 2" >Stop<span class=hide> 2</span></button></td></tr>
</table>
</form>
"""

CONFIG_PAGE_SAM_PERSISTED = """
<form action="" method="POST">
<input type="hidden" name="nonce" value="nonce-persisted" >
<table id="clientconfig">
<tr><td align="right"><label for="1">SAMBridge</label></td><td align="center"><input type="checkbox" class="optbox" id="1" name="1.enabled" checked="checked" ></td><td align="center"><button type="submit" name="action" value="Start 1" >Start<span class=hide> 1</span></button></td></tr>
<tr><td align="right"><label for="2">Tunnels</label></td><td align="center"><input type="checkbox" class="optbox" id="2" name="2.enabled" checked="checked" ></td><td align="center"><button type="submit" name="action" value="Stop 2" >Stop<span class=hide> 2</span></button></td></tr>
</table>
</form>
"""


@dataclass
class _FakeResponse:
    body: str

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.body.encode("utf-8")


class _FakeOpener:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.posts: list[dict[str, list[str]]] = []

    def open(self, request, timeout=None):  # noqa: ANN001
        data = getattr(request, "data", None)
        if data is not None:
            self.posts.append(parse_qs(data.decode("ascii"), keep_blank_values=True))
        if not self._responses:
            raise AssertionError("unexpected request with no remaining fake responses")
        return _FakeResponse(self._responses.pop(0))


class EnsureI2PRouterSamTests(unittest.TestCase):
    def test_parse_config_clients_html_extracts_sam_state(self) -> None:
        state = ensure_sam.parse_config_clients_html(CONFIG_PAGE_SAM_DISABLED)

        self.assertEqual(state.nonce, "nonce-disabled")
        self.assertEqual(state.enabled_names, ("2.enabled",))
        self.assertEqual(state.sam_client_id, "1")
        self.assertEqual(state.sam_checkbox_name, "1.enabled")
        self.assertFalse(state.sam_enabled)
        self.assertFalse(state.sam_running)

    def test_ensure_sam_bridge_saves_and_starts_sam_when_missing(self) -> None:
        opener = _FakeOpener(
            [
                CONFIG_PAGE_SAM_DISABLED,
                CONFIG_PAGE_SAM_PERSISTED,
                CONFIG_PAGE_SAM_PERSISTED,
                CONFIG_PAGE_SAM_ENABLED,
                CONFIG_PAGE_SAM_ENABLED,
            ]
        )

        changed = ensure_sam.ensure_sam_bridge(
            "http://i2p-router:7657",
            opener=opener,
            wait_seconds=0,
            poll_interval=0,
            request_timeout=1,
        )

        self.assertTrue(changed)
        self.assertEqual(len(opener.posts), 2)
        self.assertEqual(opener.posts[0]["action"], ["Save Client Configuration"])
        self.assertEqual(opener.posts[0]["1.enabled"], ["on"])
        self.assertEqual(opener.posts[0]["2.enabled"], ["on"])
        self.assertEqual(opener.posts[1]["action"], ["Start 1"])
        self.assertEqual(opener.posts[1]["nonce"], ["nonce-persisted"])

    def test_ensure_sam_bridge_is_noop_when_sam_is_already_running(self) -> None:
        opener = _FakeOpener([CONFIG_PAGE_SAM_ENABLED, CONFIG_PAGE_SAM_ENABLED])

        changed = ensure_sam.ensure_sam_bridge(
            "http://i2p-router:7657",
            opener=opener,
            wait_seconds=0,
            poll_interval=0,
            request_timeout=1,
        )

        self.assertFalse(changed)
        self.assertEqual(opener.posts, [])


if __name__ == "__main__":
    unittest.main()
