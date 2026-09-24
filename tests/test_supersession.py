"""Supersession (v0.3): a stale note is readable on purpose, never retrievable
by accident.

Measured before this existed: with four stale notes beside their successors,
the stale copy tied on BM25 score in 7 of 12 queries and on coverage in 11 of
12, ranked first in 8 of 12, and was cited in 12 of 12 — and both abstention
gates passed it every time. No ranking rule separates "relevant" from "no
longer true"; exclusion at index build does, the same way entitlements do.
"""
from pathlib import Path

import pytest

from grounded_mcp.entitlements import Entitlements
from grounded_mcp.index import VaultIndex
from grounded_mcp.vault import _superseded_by, parse_note

VAULT = Path(__file__).parent.parent / "demo_vault"
ENTS = Entitlements.load(VAULT / "entitlements.yaml")

STALE = "internal/hr/expenses-policy-2025.md"
CURRENT = "internal/hr/expenses-policy.md"


@pytest.fixture(scope="module")
def staff():
    return VaultIndex(VAULT, ENTS.profile("staff"))


@pytest.fixture(scope="module")
def exec_():
    return VaultIndex(VAULT, ENTS.profile("exec"))


def test_frontmatter_parses_superseded_by():
    stale = parse_note(VAULT, VAULT / STALE)
    current = parse_note(VAULT, VAULT / CURRENT)
    assert stale.superseded and stale.superseded_by == CURRENT
    assert not current.superseded and current.superseded_by is None


def test_pointer_normalised_and_shapes_rejected():
    assert _superseded_by({"superseded_by": "./a/b.md"}) == "a/b.md"
    assert _superseded_by({"superseded_by": "a\\b.md"}) == "a/b.md"
    assert _superseded_by({"superseded_by": True}) is None
    assert _superseded_by({"superseded_by": ["a.md"]}) is None
    assert _superseded_by({"superseded_by": "  "}) is None
    assert _superseded_by({}) is None


def test_stale_note_never_surfaces_in_search_even_unthresholded(staff):
    for q in ("meal allowance while travelling", "hotel cost cap when travelling",
              "expense claim deadline days", "mileage rate"):
        r = staff.search(q, k=10, min_score=0.0, min_coverage=0.0)
        paths = [h.note_path for h in r.hits]
        assert STALE not in paths, (q, paths)
        assert CURRENT in paths, (q, paths)


def test_current_version_is_top_hit_thresholded(staff):
    r = staff.search("meal allowance while travelling", k=5, min_score=1.0, min_coverage=0.5)
    assert not r.abstained
    assert r.hits[0].note_path == CURRENT


def test_stale_note_readable_by_path_with_pointer(staff):
    note = staff.get_note(STALE)
    assert note is not None
    assert note.superseded and note.superseded_by == CURRENT
    assert staff.is_superseded(STALE)
    assert not staff.is_superseded(CURRENT)
    assert STALE not in staff.notes and STALE in staff.superseded


def test_entitlements_still_win_over_supersession(staff, exec_):
    stale_restricted = "restricted/finance/salary-bands-2025.md"
    assert staff.get_note(stale_restricted) is None          # denied: same as nonexistent
    assert not staff.is_superseded(stale_restricted)         # not even acknowledged
    note = exec_.get_note(stale_restricted)                  # exec may read history
    assert note is not None and note.superseded_by == "restricted/finance/salary-bands.md"
    r = exec_.search("senior engineer salary band", k=10, min_score=0.0, min_coverage=0.0)
    assert all(h.note_path != stale_restricted for h in r.hits)
    assert r.hits[0].note_path == "restricted/finance/salary-bands.md"


def test_stale_notes_are_not_backlink_sources(staff):
    # deploy-process-2025 links to [[incident-runbook]]; only the current note counts.
    sources = staff.backlinks("internal/engineering/incident-runbook.md")
    assert "internal/engineering/deploy-process.md" in sources
    assert "internal/engineering/deploy-process-2025.md" not in sources
