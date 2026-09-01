"""Environment-driven configuration.

| Env var                  | Default                     | Meaning |
|--------------------------|-----------------------------|---------|
| GROUNDED_VAULT           | ./demo_vault                | vault root folder |
| GROUNDED_PROFILE         | default                     | entitlements profile (stdio mode) |
| GROUNDED_ENTITLEMENTS    | <vault>/entitlements.yaml   | rules file (missing = allow all) |
| GROUNDED_MIN_SCORE       | 1.0                         | abstention score threshold (higher = stricter) |
| GROUNDED_MIN_COVERAGE    | 0.5                         | abstention term-coverage threshold (0..1) |
| GROUNDED_SERVE_REDACTED  | false                       | serve notes containing secret patterns |
| GROUNDED_TRANSPORT       | stdio                       | stdio \| http (streamable HTTP, v0.2) |
| GROUNDED_TOKENS          | (none)                      | REQUIRED in http mode: yaml token map (sha256 -> client/profile) |
| GROUNDED_HOST            | 127.0.0.1                   | http mode bind host |
| GROUNDED_PORT            | 8000                        | http mode bind port |
| GROUNDED_ISSUER_URL      | http://127.0.0.1:<port>     | OAuth metadata issuer (AuthSettings) |
| GROUNDED_SERVER_URL      | http://127.0.0.1:<port>/mcp | OAuth resource_server_url (AuthSettings) |

The private-instance boundary lives here: pointing GROUNDED_VAULT at a real
vault is pure local configuration — nothing about that vault enters this repo.

Mode difference that matters: in stdio mode GROUNDED_PROFILE picks the single
profile served (client and server are the same user). In http mode the profile
comes from each request's VERIFIED bearer token via the token map, and
GROUNDED_PROFILE is ignored for tool calls — identity is not a client claim.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .entitlements import DEFAULT_PROFILE, Entitlements, Profile


@dataclass
class Config:
    vault_root: Path
    entitlements: Entitlements
    profile: Profile          # the stdio-mode profile
    min_score: float
    min_coverage: float
    serve_redacted: bool
    transport: str = "stdio"
    tokens_path: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    issuer_url: str = ""
    server_url: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        vault_root = Path(os.environ.get("GROUNDED_VAULT", "demo_vault")).expanduser().resolve()
        if not vault_root.is_dir():
            raise FileNotFoundError(f"vault root not found: {vault_root}")
        ent_path = Path(
            os.environ.get("GROUNDED_ENTITLEMENTS", vault_root / "entitlements.yaml")
        ).expanduser()
        entitlements = Entitlements.load(ent_path if ent_path.exists() else None)
        transport = os.environ.get("GROUNDED_TRANSPORT", "stdio").lower()
        if transport not in ("stdio", "http"):
            raise ValueError(f"GROUNDED_TRANSPORT must be stdio or http, got {transport!r}")
        port = int(os.environ.get("GROUNDED_PORT", "8000"))
        tokens = os.environ.get("GROUNDED_TOKENS")
        # The stdio-mode profile is only resolved in stdio mode: in http mode
        # profiles come from verified tokens per request, and eagerly resolving
        # GROUNDED_PROFILE would hard-error on vaults that define named
        # profiles without a literal "default".
        if transport == "stdio":
            profile = entitlements.profile(os.environ.get("GROUNDED_PROFILE", "default"))
        else:
            profile = DEFAULT_PROFILE  # placeholder; never consulted for tool calls
        return cls(
            vault_root=vault_root,
            entitlements=entitlements,
            profile=profile,
            min_score=float(os.environ.get("GROUNDED_MIN_SCORE", "1.0")),
            min_coverage=float(os.environ.get("GROUNDED_MIN_COVERAGE", "0.5")),
            serve_redacted=os.environ.get("GROUNDED_SERVE_REDACTED", "").lower()
            in ("1", "true", "yes"),
            transport=transport,
            tokens_path=Path(tokens).expanduser() if tokens else None,
            host=os.environ.get("GROUNDED_HOST", "127.0.0.1"),
            port=port,
            issuer_url=os.environ.get("GROUNDED_ISSUER_URL", f"http://127.0.0.1:{port}"),
            server_url=os.environ.get("GROUNDED_SERVER_URL", f"http://127.0.0.1:{port}/mcp"),
        )
