"""Per-client authorization for HTTP mode (v0.2).

Over stdio, client and server are the same user and entitlement profiles are a
deployment-pattern demonstration (v0.1's honestly stated boundary). Over
streamable HTTP that boundary closes: each client presents a bearer token, the
token maps to an entitlements profile, and every tool call resolves its index
through the VERIFIED identity — never through anything the client merely claims.

Token file (GROUNDED_TOKENS, yaml) — tokens are stored as sha256 hashes so the
file can live in config management without being a secret itself:

    tokens:
      - sha256: "9f86d081884c7d65..."   # sha256 of the raw bearer token
        client_id: analytics-team
        profile: staff
      - sha256: "60303ae22b998861..."
        client_id: contractor-portal
        profile: contractor

Design rule carried from v0.1: an UNKNOWN token is a hard 401, never a fallback
to some default profile — silent fallback would be an entitlement bypass.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from mcp.server.auth.provider import AccessToken, TokenVerifier


@dataclass
class TokenRecord:
    client_id: str
    profile: str


class TokenMap:
    def __init__(self, records: dict[str, TokenRecord]):
        self._by_hash = records

    @classmethod
    def load(cls, path: str | Path) -> "TokenMap":
        data = yaml.safe_load(Path(path).read_text()) or {}
        records: dict[str, TokenRecord] = {}
        for row in data.get("tokens") or []:
            digest = str(row["sha256"]).lower()
            records[digest] = TokenRecord(client_id=str(row["client_id"]),
                                          profile=str(row["profile"]))
        if not records:
            raise ValueError(f"no tokens defined in {path}: an HTTP server with "
                             f"an empty token map would deny everyone, or worse, "
                             f"tempt a default. Refusing to start.")
        return cls(records)

    def lookup(self, raw_token: str) -> TokenRecord | None:
        digest = hashlib.sha256(raw_token.encode()).hexdigest()
        return self._by_hash.get(digest)

    def profile_for_client(self, client_id: str) -> str | None:
        for rec in self._by_hash.values():
            if rec.client_id == client_id:
                return rec.profile
        return None


class VaultTokenVerifier(TokenVerifier):
    """Bearer-token verifier backed by the hashed token map."""

    def __init__(self, token_map: TokenMap):
        self._map = token_map

    async def verify_token(self, token: str) -> AccessToken | None:
        rec = self._map.lookup(token)
        if rec is None:
            return None  # unknown token -> 401; never a default profile
        return AccessToken(token=token, client_id=rec.client_id,
                           scopes=["read"], expires_at=None)


def hash_token(raw: str) -> str:
    """Helper for operators: the value to put in the tokens file."""
    return hashlib.sha256(raw.encode()).hexdigest()


if __name__ == "__main__":  # operator helper: python -m grounded_mcp.authz <token>
    import sys
    print(hash_token(sys.argv[1]))
