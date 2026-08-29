"""The MCP server surface: four read-only tools over the entitlement-scoped index.

Design invariants:
  * every content-bearing return carries stable citation ids
  * search abstains explicitly instead of serving weak matches
  * a denied note and a nonexistent note produce the SAME response — the
    server must not act as an existence oracle for content you can't see
  * read-only by design: no write tools in v1 (that's a trust feature)
"""

from __future__ import annotations

from mcp.server import MCPServer

from .config import Config
from .index import VaultIndex
from .vault import resolve_citation, slugify

_NOT_AVAILABLE = {
    "found": False,
    "reason": "note not available (it does not exist, or is outside your entitlements)",
}


def build_server(config: Config | None = None) -> MCPServer:
    cfg = config or Config.from_env()
    index = VaultIndex(cfg.vault_root, cfg.profile, serve_redacted=cfg.serve_redacted)

    mcp = MCPServer(
        "grounded-mcp",
        instructions=(
            "Search and read a markdown knowledge vault. Every result carries a "
            "citation id (path#heading) — quote it when using the content. When "
            "search returns abstained=true, the vault has no adequate answer: say "
            "so rather than guessing. Some content may be outside your entitlements; "
            "absence of a note is not evidence it doesn't exist."
        ),
    )

    @mcp.tool()
    def search(query: str, k: int = 5) -> dict:
        """Search the vault (BM25). Returns cited hits, or an explicit abstention
        when nothing clears the relevance threshold."""
        result = index.search(query, k=k, min_score=cfg.min_score,
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
        note = index.get_note(note_path)
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
        note = index.get_note(path)
        if note is None:
            return dict(_NOT_AVAILABLE)
        return {
            "found": True,
            "path": note.path,
            "links_out": note.links,
            "backlinks": index.backlinks(path),
        }

    @mcp.tool()
    def browse(prefix: str = "", tag: str = "") -> dict:
        """List notes visible under your entitlements, optionally filtered by
        path prefix and/or tag."""
        notes = [
            n for n in index.notes.values()
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
    build_server().run()


if __name__ == "__main__":
    main()
