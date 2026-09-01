"""The MCP server surface: four read-only tools over entitlement-scoped indexes.

Design invariants:
  * every content-bearing return carries stable citation ids
  * search abstains explicitly instead of serving weak matches
  * a denied note and a nonexistent note produce the SAME response — the
    server must not act as an existence oracle for content you can't see
  * read-only by design: no write tools in v1 (that's a trust feature)

v0.2 adds HTTP mode with per-client entitlements: over streamable HTTP each
request's profile is resolved from its VERIFIED bearer token (an unknown token
is a 401, never a default profile). Over stdio, client and server remain the
same user and GROUNDED_PROFILE applies — the v0.1 boundary, still honestly held.
"""

from __future__ import annotations

import threading

from mcp.server import MCPServer

from .config import Config
from .index import VaultIndex
from .vault import resolve_citation, slugify

_NOT_AVAILABLE = {
    "found": False,
    "reason": "note not available (it does not exist, or is outside your entitlements)",
}


class _Indexes:
    """Per-profile VaultIndex cache. Enforcement stays index-level: each profile
    gets its own index built only from what it may see."""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._cache: dict[str, VaultIndex] = {}
        self._lock = threading.Lock()

    def for_profile(self, name: str) -> VaultIndex:
        with self._lock:
            if name not in self._cache:
                profile = self._cfg.entitlements.profile(name)
                self._cache[name] = VaultIndex(
                    self._cfg.vault_root, profile,
                    serve_redacted=self._cfg.serve_redacted)
            return self._cache[name]


def _request_profile(cfg: Config, token_map) -> str:
    """The profile for THIS tool call.

    HTTP mode: from the verified bearer token, resolved through the token map.
    A verified token whose client is missing from the map is a server
    misconfiguration and raises (fail closed) rather than guessing.
    stdio mode: the configured profile (no token exists to consult).
    """
    if token_map is None:
        return cfg.profile.name
    from mcp.server.auth.middleware.auth_context import get_access_token
    token = get_access_token()
    if token is None:
        # Auth middleware should have rejected already; do not serve anyway.
        raise PermissionError("no verified token on an authenticated transport")
    profile = token_map.profile_for_client(token.client_id)
    if profile is None:
        raise PermissionError(
            f"verified client {token.client_id!r} has no profile in the token "
            f"map: refusing to guess an entitlement")
    return profile


def build_server(config: Config | None = None) -> MCPServer:
    cfg = config or Config.from_env()

    token_map = None
    server_kwargs: dict = {}
    if cfg.transport == "http":
        if cfg.tokens_path is None:
            raise ValueError(
                "http mode requires GROUNDED_TOKENS: serving a network endpoint "
                "without per-client auth would silently widen v0.1's stdio "
                "boundary instead of closing it")
        from pydantic import AnyHttpUrl

        from mcp.server.auth.settings import AuthSettings

        from .authz import TokenMap, VaultTokenVerifier
        token_map = TokenMap.load(cfg.tokens_path)
        server_kwargs = {
            "token_verifier": VaultTokenVerifier(token_map),
            "auth": AuthSettings(
                issuer_url=AnyHttpUrl(cfg.issuer_url),
                resource_server_url=AnyHttpUrl(cfg.server_url),
                required_scopes=["read"],
            ),
        }

    indexes = _Indexes(cfg)

    mcp = MCPServer(
        "grounded-mcp",
        instructions=(
            "Search and read a markdown knowledge vault. Every result carries a "
            "citation id (path#heading) — quote it when using the content. When "
            "search returns abstained=true, the vault has no adequate answer: say "
            "so rather than guessing. Some content may be outside your entitlements; "
            "absence of a note is not evidence it doesn't exist."
        ),
        **server_kwargs,
    )

    def _index() -> VaultIndex:
        return indexes.for_profile(_request_profile(cfg, token_map))

    @mcp.tool()
    def search(query: str, k: int = 5) -> dict:
        """Search the vault (BM25). Returns cited hits, or an explicit abstention
        when nothing clears the relevance threshold."""
        result = _index().search(query, k=k, min_score=cfg.min_score,
                                 min_coverage=cfg.min_coverage)
        if result.abstained:
            return {
                "abstained": True,
                "reason": result.reason,
                "threshold": result.threshold,
                "hits": [],
            }
        return {
            "abstained": False,
            "hits": [
                {
                    "citation_id": h.citation_id,
                    "title": h.title,
                    "heading": h.heading or None,
                    "score": round(h.score, 3),
                    "coverage": round(h.coverage, 2),
                    "snippet": h.snippet,
                    "lines": [h.start_line, h.end_line],
                }
                for h in result.hits
            ],
        }

    @mcp.tool()
    def read_note(citation: str, section_only: bool = False) -> dict:
        """Read a note (or one section) by citation id or vault-relative path.
        Content blocks carry their own citation anchors for quoting."""
        note_path, slug = resolve_citation(citation)
        note = _index().get_note(note_path)
        if note is None:
            return dict(_NOT_AVAILABLE)
        sections = note.sections
        if slug is not None and section_only:
            sections = [s for s in sections if slugify(s.heading) == slug]
            if not sections:
                return dict(_NOT_AVAILABLE)
        return {
            "found": True,
            "path": note.path,
            "title": note.title,
            "tags": note.tags,
            "frontmatter": {k: v for k, v in note.frontmatter.items() if k != "tags"},
            "sections": [
                {
                    "citation_id": s.citation_id,
                    "heading": s.heading or None,
                    "lines": [s.start_line, s.end_line],
                    "text": s.text,
                }
                for s in sections
            ],
        }

    @mcp.tool()
    def backlinks(path: str) -> dict:
        """Notes (within your entitlements) whose wikilinks point at this note."""
        idx = _index()
        note = idx.get_note(path)
        if note is None:
            return dict(_NOT_AVAILABLE)
        return {
            "found": True,
            "path": note.path,
            "links_out": note.links,
            "backlinks": idx.backlinks(path),
        }

    @mcp.tool()
    def browse(prefix: str = "", tag: str = "") -> dict:
        """List notes visible under your entitlements, optionally filtered by
        path prefix and/or tag."""
        idx = _index()
        notes = [
            n for n in idx.notes.values()
            if n.path.startswith(prefix) and (not tag or tag.lstrip("#") in n.tags)
        ]
        return {
            "count": len(notes),
            "notes": [
                {"path": n.path, "title": n.title, "tags": n.tags}
                for n in sorted(notes, key=lambda n: n.path)
            ],
        }

    return mcp


def main() -> None:
    cfg = Config.from_env()
    server = build_server(cfg)
    if cfg.transport == "http":
        server.run(transport="streamable-http", host=cfg.host, port=cfg.port)
    else:
        server.run()


if __name__ == "__main__":
    main()
