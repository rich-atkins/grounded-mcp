"""Environment-driven configuration.

| Env var                  | Default                     | Meaning |
|--------------------------|-----------------------------|---------|
| GROUNDED_VAULT           | ./demo_vault                | vault root folder |
| GROUNDED_PROFILE         | default                     | entitlements profile to serve as |
| GROUNDED_ENTITLEMENTS    | <vault>/entitlements.yaml   | rules file (missing = allow all) |
| GROUNDED_MIN_SCORE       | 1.0                         | abstention score threshold (higher = stricter) |
| GROUNDED_MIN_COVERAGE    | 0.5                         | abstention term-coverage threshold (0..1) |
| GROUNDED_SERVE_REDACTED  | false                       | serve notes containing secret patterns |

The private-instance boundary lives here: pointing GROUNDED_VAULT at a real
vault is pure local configuration — nothing about that vault enters this repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .entitlements import Entitlements, Profile


@dataclass
class Config:
    vault_root: Path
    profile: Profile
    min_score: float
    min_coverage: float
    serve_redacted: bool

    @classmethod
    def from_env(cls) -> "Config":
        vault_root = Path(os.environ.get("GROUNDED_VAULT", "demo_vault")).expanduser().resolve()
        if not vault_root.is_dir():
            raise FileNotFoundError(f"vault root not found: {vault_root}")
        ent_path = Path(
            os.environ.get("GROUNDED_ENTITLEMENTS", vault_root / "entitlements.yaml")
        ).expanduser()
        entitlements = Entitlements.load(ent_path if ent_path.exists() else None)
        return cls(
            vault_root=vault_root,
            profile=entitlements.profile(os.environ.get("GROUNDED_PROFILE", "default")),
            min_score=float(os.environ.get("GROUNDED_MIN_SCORE", "1.0")),
            min_coverage=float(os.environ.get("GROUNDED_MIN_COVERAGE", "0.5")),
            serve_redacted=os.environ.get("GROUNDED_SERVE_REDACTED", "").lower()
            in ("1", "true", "yes"),
        )
