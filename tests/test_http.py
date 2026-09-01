"""Integration: per-client entitlements over REAL streamable HTTP.

This is v0.2's whole point: the v0.1 README said stdio profiles demonstrate a
pattern rather than enforce against a hostile peer. Here two different bearer
tokens hit the same live HTTP server and get different vaults — and a bad token
gets nothing at all. Enforcement is index-level per verified identity, and the
leakage probe runs over the wire, not in-process (the v0.1.1 lesson: an eval
suite's coverage is bounded by the path it exercises).
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from grounded_mcp.authz import hash_token

VAULT = Path(__file__).parent.parent / "demo_vault"
SERVER = Path(sys.executable).parent / "grounded-mcp"

STAFF_TOKEN = "test-staff-token-0123456789abcdef"
CONTRACTOR_TOKEN = "test-contractor-token-0123456789"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("http")
    tokens = tmp / "tokens.yaml"
    tokens.write_text(
        "tokens:\n"
        f"  - sha256: \"{hash_token(STAFF_TOKEN)}\"\n"
        "    client_id: staff-app\n    profile: staff\n"
        f"  - sha256: \"{hash_token(CONTRACTOR_TOKEN)}\"\n"
        "    client_id: contractor-app\n    profile: contractor\n")
    port = _free_port()
    env = dict(os.environ)
    env.update(GROUNDED_VAULT=str(VAULT), GROUNDED_TRANSPORT="http",
               GROUNDED_TOKENS=str(tokens), GROUNDED_PORT=str(port),
               GROUNDED_HOST="127.0.0.1")
    proc = subprocess.Popen([str(SERVER)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}/mcp"
    for _ in range(60):  # wait for the port
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            if proc.poll() is not None:
                out = proc.stdout.read().decode(errors="replace")
                raise RuntimeError(f"server died on startup:\n{out}")
            time.sleep(0.25)
    else:
        proc.kill()
        raise RuntimeError("server never opened its port")
    yield url
    proc.terminate()
    proc.wait(timeout=10)


async def _call(url: str, token: str, tool: str, arguments: dict) -> dict:
    import httpx2
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with streamable_http_client(url, http_client=client) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
            text = "\n".join(getattr(c, "text", "") for c in result.content)
            return json.loads(text)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_staff_token_sees_internal(http_server):
    out = _run(_call(http_server, STAFF_TOKEN, "browse", {"prefix": "internal/"}))
    assert out["count"] > 0


def test_contractor_token_sees_no_internal(http_server):
    out = _run(_call(http_server, CONTRACTOR_TOKEN, "browse", {"prefix": "internal/"}))
    assert out["count"] == 0
    # And a direct read of an internal note is indistinguishable from nonexistent.
    note = _run(_call(http_server, CONTRACTOR_TOKEN, "read_note",
                      {"citation": "internal/hr/onboarding.md", "section_only": False}))
    assert note["found"] is False


def test_leakage_probe_over_the_wire(http_server):
    # Restricted-only content must produce zero hits for staff, over real HTTP.
    out = _run(_call(http_server, STAFF_TOKEN, "search",
                     {"query": "salary bands bonus pool", "k": 10}))
    for h in out["hits"]:
        assert not h["citation_id"].startswith("restricted/")


def test_bad_token_is_rejected(http_server):
    with pytest.raises(Exception):  # 401 surfaces as a transport/session error
        _run(_call(http_server, "not-a-real-token", "browse", {}))
