"""Vault model: parse a folder of Obsidian-flavoured markdown into notes and sections.

A *note* is one ``.md`` file. A *section* is a heading-delimited span within it —
the unit of retrieval and citation. Citation ids are stable strings of the form
``relative/path.md#heading-slug`` (or just the path for the preamble before the
first heading), plus 1-based line spans for precise quoting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")


def slugify(text: str) -> str:
    """Obsidian/GitHub-style heading slug: lowercase, spaces to hyphens."""
    slug = re.sub(r"[^\w\s-]", "", text.strip().lower())
    return re.sub(r"[\s_]+", "-", slug).strip("-")


@dataclass
class Section:
    """A heading-delimited span of a note; the retrieval + citation unit."""

    note_path: str          # vault-relative posix path
    heading: str            # "" for the preamble before the first heading
    level: int              # 0 for preamble, else 1-6
    start_line: int         # 1-based, inclusive (the heading line itself)
    end_line: int           # 1-based, inclusive
    text: str               # body text of the span (includes the heading line)

    @property
    def citation_id(self) -> str:
        if not self.heading:
            return self.note_path
        return f"{self.note_path}#{slugify(self.heading)}"


@dataclass
class Note:
    path: str               # vault-relative posix path
    title: str
    frontmatter: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)   # wikilink targets (note names)
    sections: list[Section] = field(default_factory=list)
    mtime: float = 0.0

    @property
    def body(self) -> str:
        return "\n".join(s.text for s in self.sections)


def _split_frontmatter(raw: str) -> tuple[dict, str, int]:
    """Return (frontmatter dict, remaining text, line offset of remaining text)."""
    if raw.startswith("---\n"):
        end = raw.find("\n---", 4)
        if end != -1:
            fm_text = raw[4:end]
            rest_start = raw.find("\n", end + 4)
            rest = raw[rest_start + 1 :] if rest_start != -1 else ""
            try:
                fm = yaml.safe_load(fm_text) or {}
                if not isinstance(fm, dict):
                    fm = {}
            except yaml.YAMLError:
                fm = {}
            offset = raw[: rest_start + 1].count("\n") if rest_start != -1 else raw.count("\n")
            return fm, rest, offset
    return {}, raw, 0


def parse_note(vault_root: Path, file_path: Path) -> Note:
    raw = file_path.read_text(encoding="utf-8", errors="replace")
    rel = file_path.relative_to(vault_root).as_posix()
    fm, body, offset = _split_frontmatter(raw)

    lines = body.splitlines()
    sections: list[Section] = []
    cur_heading, cur_level, cur_start, cur_lines = "", 0, 1, []

    def flush(end_line: int) -> None:
        text = "\n".join(cur_lines).strip("\n")
        if text.strip() or cur_heading:
            sections.append(
                Section(
                    note_path=rel,
                    heading=cur_heading,
                    level=cur_level,
                    start_line=cur_start + offset,
                    end_line=end_line + offset,
                    text=text,
                )
            )

    for i, line in enumerate(lines, start=1):
        m = HEADING_RE.match(line)
        if m:
            flush(i - 1)
            cur_heading, cur_level, cur_start, cur_lines = m.group(2).strip(), len(m.group(1)), i, [line]
        else:
            cur_lines.append(line)
    flush(len(lines))

    tags = set()
    for t in fm.get("tags") or []:
        if isinstance(t, str):
            tags.add(t.lstrip("#"))
    for m in TAG_RE.finditer(body):
        tags.add(m.group(1))

    title = str(fm.get("title") or "").strip() or file_path.stem

    return Note(
        path=rel,
        title=title,
        frontmatter=fm,
        tags=sorted(tags),
        links=sorted({m.group(1).strip() for m in WIKILINK_RE.finditer(body)}),
        sections=sections,
        mtime=file_path.stat().st_mtime,
    )


def load_vault(vault_root: Path) -> list[Note]:
    """Parse every markdown note under ``vault_root`` (sorted, deterministic)."""
    notes = []
    for p in sorted(vault_root.rglob("*.md")):
        if any(part.startswith(".") for part in p.relative_to(vault_root).parts):
            continue  # skip hidden dirs (.obsidian, .trash, ...)
        notes.append(parse_note(vault_root, p))
    return notes


def resolve_citation(citation_id: str) -> tuple[str, str | None]:
    """Split a citation id into (note_path, heading_slug|None)."""
    if "#" in citation_id:
        path, slug = citation_id.split("#", 1)
        return path, slug or None
    return citation_id, None
