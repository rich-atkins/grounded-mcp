"""Token map + verifier: unknown is 401, empty is refused, hashes not secrets."""
from __future__ import annotations

import asyncio

import pytest

from grounded_mcp.authz import TokenMap, VaultTokenVerifier, hash_token


def _tokens_file(tmp_path, rows):
    p = tmp_path / "tokens.yaml"
    body = "tokens:\n" + "".join(
        f"  - sha256: \"{h}\"\n    client_id: {c}\n    profile: {pr}\n"
        for h, c, pr in rows)
    p.write_text(body)
    return p


def test_lookup_by_hash_and_profile_mapping(tmp_path):
    path = _tokens_file(tmp_path, [
        (hash_token("s3cret-staff"), "analytics-team", "staff"),
        (hash_token("s3cret-contractor"), "contractor-portal", "contractor"),
    ])
    tm = TokenMap.load(path)
    assert tm.lookup("s3cret-staff").profile == "staff"
    assert tm.lookup("wrong-token") is None
    assert tm.profile_for_client("contractor-portal") == "contractor"
    assert tm.profile_for_client("nobody") is None


def test_empty_token_file_refuses_to_load(tmp_path):
    p = tmp_path / "tokens.yaml"
    p.write_text("tokens: []\n")
    with pytest.raises(ValueError, match="Refusing to start"):
        TokenMap.load(p)


def test_verifier_unknown_token_is_none_never_default(tmp_path):
    path = _tokens_file(tmp_path, [(hash_token("good"), "app", "staff")])
    v = VaultTokenVerifier(TokenMap.load(path))
    ok = asyncio.run(v.verify_token("good"))
    assert ok is not None and ok.client_id == "app"
    assert asyncio.run(v.verify_token("bad")) is None
