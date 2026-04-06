#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import time
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, Request, build_opener


_NONCE_RE = re.compile(r'<input type="hidden" name="nonce" value="([^"]+)"', re.IGNORECASE)
_CHECKBOX_RE = re.compile(r'<input type="checkbox"([^>]*)>', re.IGNORECASE)
_NAME_RE = re.compile(r'name="([^"]+)"', re.IGNORECASE)
_SAM_ROW_RE = re.compile(
    r'<tr><td align="right"><label for="(?P<client_id>\d+)">SAMBridge</label></td>(?P<row>.*?)</tr>',
    re.IGNORECASE | re.DOTALL,
)


class _OpenerLike(Protocol):
    def open(self, request: Request | str, timeout: float | None = None): ...


@dataclass(frozen=True, slots=True)
class ClientConfigState:
    nonce: str
    enabled_names: tuple[str, ...]
    sam_client_id: str
    sam_checkbox_name: str
    sam_enabled: bool
    sam_running: bool


def parse_config_clients_html(html: str) -> ClientConfigState:
    nonce_match = _NONCE_RE.search(html)
    if nonce_match is None:
        raise ValueError("missing client configuration nonce")

    enabled_names: list[str] = []
    for match in _CHECKBOX_RE.finditer(html):
        attrs = match.group(1)
        name_match = _NAME_RE.search(attrs)
        if name_match is None:
            continue
        if "checked" not in attrs.lower() or "disabled" in attrs.lower():
            continue
        enabled_names.append(name_match.group(1))

    sam_match = _SAM_ROW_RE.search(html)
    if sam_match is None:
        raise ValueError("missing SAMBridge row in client configuration page")

    sam_client_id = sam_match.group("client_id")
    sam_row = sam_match.group("row")
    sam_name_match = _NAME_RE.search(sam_row)
    if sam_name_match is None:
        raise ValueError("missing SAMBridge checkbox name")
    sam_checkbox_name = sam_name_match.group(1)
    return ClientConfigState(
        nonce=nonce_match.group(1),
        enabled_names=tuple(enabled_names),
        sam_client_id=sam_client_id,
        sam_checkbox_name=sam_checkbox_name,
        sam_enabled="checked" in sam_row.lower(),
        sam_running=f'value="Stop {sam_client_id}"' in sam_row,
    )


def _decode_response(opener: _OpenerLike, request: Request | str, *, timeout: float) -> str:
    with opener.open(request, timeout=timeout) as response:
        body = response.read()
    return body.decode("utf-8", errors="replace")


def _fetch_config_page(opener: _OpenerLike, base_url: str, *, timeout: float) -> ClientConfigState:
    html = _decode_response(opener, urljoin(base_url.rstrip("/") + "/", "configclients"), timeout=timeout)
    return parse_config_clients_html(html)


def _post_form(opener: _OpenerLike, base_url: str, payload: dict[str, str], *, timeout: float) -> str:
    request = Request(
        urljoin(base_url.rstrip("/") + "/", "configclients"),
        data=urlencode(payload).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return _decode_response(opener, request, timeout=timeout)


def _build_save_payload(state: ClientConfigState) -> dict[str, str]:
    payload = {"nonce": state.nonce, "action": "Save Client Configuration"}
    for name in state.enabled_names:
        payload[name] = "on"
    payload[state.sam_checkbox_name] = "on"
    return payload


def _wait_for_config_state(
    opener: _OpenerLike,
    base_url: str,
    *,
    wait_seconds: float,
    poll_interval: float,
    request_timeout: float,
) -> ClientConfigState:
    deadline = time.monotonic() + max(0.0, wait_seconds)
    last_error: Exception | None = None
    while True:
        try:
            return _fetch_config_page(opener, base_url, timeout=request_timeout)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            detail = "" if last_error is None else f": {last_error}"
            raise RuntimeError(f"timed out waiting for I2P client configuration page{detail}") from last_error
        time.sleep(poll_interval)


def ensure_sam_bridge(
    base_url: str,
    *,
    opener: _OpenerLike | None = None,
    wait_seconds: float = 120.0,
    poll_interval: float = 1.0,
    request_timeout: float = 10.0,
) -> bool:
    opener = opener or build_opener(HTTPCookieProcessor(CookieJar()))
    state = _wait_for_config_state(
        opener,
        base_url,
        wait_seconds=wait_seconds,
        poll_interval=poll_interval,
        request_timeout=request_timeout,
    )
    changed = False
    if not state.sam_enabled:
        _post_form(opener, base_url, _build_save_payload(state), timeout=request_timeout)
        changed = True
        state = _wait_for_config_state(
            opener,
            base_url,
            wait_seconds=request_timeout,
            poll_interval=poll_interval,
            request_timeout=request_timeout,
        )

    if not state.sam_running:
        _post_form(
            opener,
            base_url,
            {"nonce": state.nonce, "action": f"Start {state.sam_client_id}"},
            timeout=request_timeout,
        )
        changed = True

    verify_deadline = time.monotonic() + max(request_timeout, 10.0)
    while True:
        state = _fetch_config_page(opener, base_url, timeout=request_timeout)
        if state.sam_enabled and state.sam_running:
            return changed
        if time.monotonic() >= verify_deadline:
            raise RuntimeError("SAMBridge did not become active after configuration")
        time.sleep(poll_interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enable and start the I2P SAM bridge through the router console.")
    parser.add_argument("--base-url", default="http://127.0.0.1:7657")
    parser.add_argument("--wait-seconds", type=float, default=120.0)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--request-timeout", type=float, default=10.0)
    args = parser.parse_args(argv)

    try:
        changed = ensure_sam_bridge(
            args.base_url,
            wait_seconds=args.wait_seconds,
            poll_interval=args.poll_interval,
            request_timeout=args.request_timeout,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to enable I2P SAMBridge: {exc}", flush=True)
        return 1
    if changed:
        print("I2P SAMBridge enabled and running.", flush=True)
    else:
        print("I2P SAMBridge was already enabled and running.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
