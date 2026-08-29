"""Entitlements and the redaction guard.

Entitlements are declarative allow/deny glob rules per *principal profile*,
loaded from an ``entitlements.yaml`` next to the vault (or a configured path):

    profiles:
      staff:
        allow: ["public/**", "internal/**"]
        deny:  ["restricted/**"]
      contractor:
        allow: ["public/**"]

Semantics — deny wins, then allow, then default-deny:
  * a path matching any ``deny`` glob is NOT visible
  * else a path matching any ``allow`` glob is visible
  * else it is NOT visible (default-deny keeps a forgotten folder private)
A missing entitlements file yields the permissive ``default`` profile
(allow everything) — a single-user local vault needs no ceremony.

Enforcement is INDEX-LEVEL: the search index for a profile is built only from
the notes that profile may see (see index.py). Denied content is never scored,
so it cannot leak through rankings, snippets, or result counts.

The redaction guard refuses to serve notes that appear to contain secrets
(API keys, tokens, private-key blocks) regardless of entitlements — a vault is
exactly where people paste credentials "just for now".
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Deliberately conservative patterns: high-precision secret shapes only.
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("generic-assigned-secret", re.compile(
        r"(?i)\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret|password)\b"
        r"\s*[:=]\s*[\"']?[A-Za-z0-9_\-/+]{16,}[\"']?"
    )),
]


def find_secrets(text: str) -> list[str]:
    """Names of secret patterns present in ``text`` (empty list = clean)."""
    return [name for name, pat in SECRET_PATTERNS if pat.search(text)]


@dataclass
class Profile:
    name: str
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)

    def permits(self, note_path: str) -> bool:
        if any(fnmatch.fnmatch(note_path, g) for g in self.deny):
            return False
        return any(fnmatch.fnmatch(note_path, g) for g in self.allow)


DEFAULT_PROFILE = Profile(name="default", allow=["**"], deny=[])


class Entitlements:
    def __init__(self, profiles: dict[str, Profile] | None = None):
        self.profiles = profiles or {}

    @classmethod
    def load(cls, path: Path | None) -> "Entitlements":
        if path is None or not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        profiles = {}
        for name, rules in (data.get("profiles") or {}).items():
            rules = rules or {}
            profiles[name] = Profile(
                name=name,
                allow=[str(g) for g in rules.get("allow") or []],
                deny=[str(g) for g in rules.get("deny") or []],
            )
        return cls(profiles)

    def profile(self, name: str) -> Profile:
        """Named profile, or default-permissive when no profiles are defined.

        Asking for an UNKNOWN name when profiles ARE defined is a hard error —
        silently falling back to permissive would be an entitlement bypass.
        """
        if not self.profiles:
            return DEFAULT_PROFILE
        if name in self.profiles:
            return self.profiles[name]
        raise KeyError(
            f"unknown entitlements profile {name!r}; defined: {sorted(self.profiles)}"
        )
