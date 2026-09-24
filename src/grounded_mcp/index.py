"""SQLite FTS5 index with index-level entitlement enforcement.

The index a principal searches is built ONLY from the notes their profile
permits (and that pass the redaction guard). Denied content is never inserted,
so it cannot influence scores, appear in snippets, or leak via result counts —
enforcement by construction, not by response filtering.

BM25 is the deliberate v0.1 baseline: deterministic, dependency-free (stdlib
sqlite3), and honestly measurable. Hybrid semantic retrieval is a v0.2 change
that must publish its eval delta over this baseline.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from .entitlements import Profile, find_secrets
from .vault import Note, load_vault

# Column weights for bm25(): heading and title matches count more than body.
_W_HEADING, _W_TITLE, _W_TAGS, _W_TEXT = 4.0, 3.0, 2.0, 1.0

_TERM_RE = re.compile(r"[\w'-]+")

# Minimal stopword list for coverage: question scaffolding, not content.
# Measured on the demo vault: false answers ride on exactly these words
# ("what is the ... policy" matching a note titled Expenses Policy).
_STOPWORDS = frozenset(
    "a an and are at by come did do does for how i in is it long many much my of on "
    "or that the this to was we what when where which who with".split()
)


def _content_terms(query: str) -> list[str]:
    """Query terms that carry content — stopwords stripped, lowercased."""
    return [t for t in _TERM_RE.findall(query.lower()) if t not in _STOPWORDS]


@dataclass
class Hit:
    citation_id: str
    note_path: str
    heading: str
    title: str
    score: float          # higher is better (negated bm25 rank)
    coverage: float       # fraction of content terms present in the note
    snippet: str
    start_line: int
    end_line: int


@dataclass
class SearchResult:
    hits: list[Hit]
    abstained: bool
    threshold: float
    min_coverage: float
    best_rejected: tuple[float, float] | None = None  # (score, coverage)

    @property
    def reason(self) -> str | None:
        if not self.abstained:
            return None
        if self.best_rejected is None:
            return "no sections matched the query at all"
        s, c = self.best_rejected
        return (
            f"best match scored {s:.2f} with term coverage {c:.2f}; "
            f"the bar is score >= {self.threshold:.2f} AND coverage >= {self.min_coverage:.2f}"
        )


def _fts_query(query: str) -> str:
    """User text -> safe FTS5 MATCH expression: OR of quoted content terms.

    Quoting each term neutralises FTS5 operator syntax in user input; OR keeps
    recall graded so BM25 + the abstention gate decide, not query syntax.
    Stopwords are dropped — they only let scaffolding words drive matches.
    """
    terms = _content_terms(query)
    return " OR ".join(f'"{t}"' for t in terms) if terms else '""'


class VaultIndex:
    """A per-profile FTS5 index over the permitted, redaction-clean sections."""

    def __init__(self, vault_root: Path, profile: Profile, serve_redacted: bool = False):
        self.vault_root = vault_root
        self.profile = profile
        self.serve_redacted = serve_redacted
        # check_same_thread=False + a lock: the MCP SDK executes sync tool
        # functions on worker threads, so the connection built at server start
        # must be usable from them. The lock serialises access (found by the
        # grounded-harness bridge integration test — the in-process eval suite
        # never crossed a thread, so v0.1 shipped with every DB-touching tool
        # broken over real MCP transport).
        self._db = sqlite3.connect(":memory:", check_same_thread=False)
        self._db_lock = threading.Lock()
        self._mtimes: dict[str, float] = {}
        self.notes: dict[str, Note] = {}
        self.superseded: dict[str, Note] = {}   # stale notes: readable, never retrievable
        self.redacted_paths: set[str] = set()
        self._build()

    # -- build ---------------------------------------------------------------

    def _build(self) -> None:
        with self._db_lock:
            self._build_locked()

    def _build_locked(self) -> None:
        db = self._db
        db.execute("DROP TABLE IF EXISTS sections")
        db.execute(
            """
            CREATE VIRTUAL TABLE sections USING fts5(
                citation_id UNINDEXED, note_path UNINDEXED,
                heading, title, tags, text,
                start_line UNINDEXED, end_line UNINDEXED
            )
            """
        )
        self.notes, self.superseded, self.redacted_paths = {}, {}, set()
        all_notes = load_vault(self.vault_root)
        self._mtimes = {n.path: n.mtime for n in all_notes}

        for note in all_notes:
            if not self.profile.permits(note.path):
                continue  # never indexed: index-level enforcement
            if not self.serve_redacted and find_secrets(note.body):
                self.redacted_paths.add(note.path)
                continue  # redaction guard: secret-bearing notes are not served
            if note.superseded:
                # Supersession is enforced the same way as entitlements: a
                # stale note is never inserted, so it cannot be scored, ranked
                # or cited. Measured before this existed (v0.3 probe): a stale
                # copy of a policy tied its successor on BM25 score in 7 of 12
                # queries, tied on coverage in 11 of 12, and won the tie every
                # time because "-2025.md" sorts before ".md". Both abstention
                # gates passed it in 12 of 12 — it IS relevant, it is just no
                # longer true. No ranking rule fixes that; exclusion does.
                self.superseded[note.path] = note
                continue
            self.notes[note.path] = note
            tags = " ".join(note.tags)
            for s in note.sections:
                db.execute(
                    "INSERT INTO sections VALUES (?,?,?,?,?,?,?,?)",
                    (s.citation_id, note.path, s.heading, note.title, tags,
                     s.text, s.start_line, s.end_line),
                )
        db.commit()

    def refresh(self) -> bool:
        """Rebuild when any note changed on disk (mtime/added/removed).

        Full rebuild on change is the honest 'incremental' at personal-vault
        scale (sub-second); returns True when a rebuild happened.
        """
        current = {
            p.relative_to(self.vault_root).as_posix(): p.stat().st_mtime
            for p in self.vault_root.rglob("*.md")
            if not any(part.startswith(".") for part in p.relative_to(self.vault_root).parts)
        }
        if current != self._mtimes:
            self._build()
            return True
        return False

    # -- query ---------------------------------------------------------------

    @staticmethod
    def _stem(word: str) -> str:
        """Light suffix strip — deliberately naive, measured by the eval suite."""
        for suf in ("ing", "ed", "es", "s"):
            if word.endswith(suf) and len(word) - len(suf) >= 3:
                return word[: -len(suf)]
        return word

    def _coverage(self, terms: list[str], note_path: str) -> float:
        """Fraction of content terms present in the note (title+tags+body).

        Terms and note tokens are compared after a light suffix strip, with
        bidirectional prefix matching ("code" ~ "coding", "take" ~ "takes").
        A v0.1 heuristic, not IR science — the eval suite is what makes it
        safe to hold, and it caught both failure directions while tuning.
        """
        if not terms:
            return 0.0
        note = self.notes.get(note_path)
        if note is None:
            return 0.0
        blob = f"{note.title} {' '.join(note.tags)} {note.body}".lower()
        tokens: set[str] = set()
        for raw in _TERM_RE.findall(blob):
            tokens.add(self._stem(raw))
            if "-" in raw:  # hyphen compounds also count as their parts
                tokens.update(self._stem(p) for p in raw.split("-") if p)

        def present(term: str) -> bool:
            t = self._stem(term)
            if t in tokens:
                return True
            return any(
                tok.startswith(t) or t.startswith(tok)
                for tok in tokens
                if min(len(tok), len(t)) >= 3
            )

        return sum(1 for t in terms if present(t)) / len(terms)

    def search(self, query: str, k: int = 5, min_score: float = 0.0,
               min_coverage: float = 0.5) -> SearchResult:
        """BM25 search gated by the abstention bar: score AND term coverage.

        Measured on the demo vault: score alone cannot separate true answers
        (as low as 2.4) from confident-looking misses (up to 4.3) — a single
        common word landing in a weighted title/tag field scores well. Coverage
        of the query's content terms is the discriminator; both gates together
        give abstention its teeth.
        """
        self.refresh()
        with self._db_lock:
            rows = self._db.execute(
                f"""
                SELECT citation_id, note_path, heading, title,
                       bm25(sections, 0, 0, {_W_HEADING}, {_W_TITLE}, {_W_TAGS}, {_W_TEXT}) AS rank,
                       snippet(sections, 5, '>>', '<<', ' … ', 12),
                       start_line, end_line
                FROM sections WHERE sections MATCH ?
                ORDER BY rank LIMIT ?
                """,
                (_fts_query(query), max(k, 1)),
            ).fetchall()

        terms = _content_terms(query)
        hits = [
            Hit(citation_id=r[0], note_path=r[1], heading=r[2], title=r[3],
                score=-r[4], coverage=self._coverage(terms, r[1]),
                snippet=r[5], start_line=r[6], end_line=r[7])
            for r in rows
        ]
        kept = [h for h in hits if h.score >= min_score and h.coverage >= min_coverage]
        if not kept:
            best = max(((h.score, h.coverage) for h in hits),
                       key=lambda sc: sc[0], default=None)
            return SearchResult(hits=[], abstained=True, threshold=min_score,
                                min_coverage=min_coverage, best_rejected=best)
        return SearchResult(hits=kept, abstained=False, threshold=min_score,
                            min_coverage=min_coverage)

    # -- direct access (same enforcement path) --------------------------------

    def get_note(self, note_path: str) -> Note | None:
        """A note IFF the profile permits it and it passed redaction.

        Superseded notes ARE returned here (with ``note.superseded_by`` set):
        history stays reachable on purpose, by path, never by search.
        """
        self.refresh()
        return self.notes.get(note_path) or self.superseded.get(note_path)

    def is_superseded(self, note_path: str) -> bool:
        return note_path in self.superseded

    def backlinks(self, note_path: str) -> list[str]:
        """Paths of permitted notes whose wikilinks target ``note_path``."""
        self.refresh()
        stem = Path(note_path).stem.lower()
        return sorted(
            n.path for n in self.notes.values()
            if any(link.lower() == stem for link in n.links) and n.path != note_path
        )
