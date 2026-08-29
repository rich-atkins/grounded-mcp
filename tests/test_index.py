from pathlib import Path

import pytest

from grounded_mcp.entitlements import Entitlements
from grounded_mcp.index import VaultIndex

VAULT = Path(__file__).parent.parent / "demo_vault"
ENTS = Entitlements.load(VAULT / "entitlements.yaml")


@pytest.fixture(scope="module")
def staff_index():
    return VaultIndex(VAULT, ENTS.profile("staff"))


@pytest.fixture(scope="module")
def exec_index():
    return VaultIndex(VAULT, ENTS.profile("exec"))


def test_search_returns_cited_hits(staff_index):
    r = staff_index.search("widget pro pricing", k=3, min_score=0.5)
    assert not r.abstained
    assert r.hits[0].citation_id.startswith("public/products/widget-pro.md")
    assert r.hits[0].score > 0


def test_abstains_on_out_of_vault_query(staff_index):
    r = staff_index.search("zeppelin maintenance schedule volcano", k=5, min_score=0.5)
    assert r.abstained
    assert r.hits == []
    assert r.reason  # human-readable explanation present


def test_restricted_never_indexed_for_staff(staff_index):
    # Structural leakage check: content that exists ONLY in restricted/ must
    # produce zero hits for staff — not low-ranked hits, zero.
    r = staff_index.search("salary bands bonus pool", k=10, min_score=0.0)
    assert all(not h.note_path.startswith("restricted/") for h in r.hits)
    r2 = staff_index.search("Nimbus acquisition Cumulus valuation", k=10, min_score=0.0)
    assert all(not h.note_path.startswith("restricted/") for h in r2.hits)


def test_exec_can_see_restricted(exec_index):
    r = exec_index.search("salary bands", k=5, min_score=0.5)
    assert not r.abstained
    assert r.hits[0].note_path == "restricted/finance/salary-bands.md"


def test_redaction_guard_excludes_secret_note(staff_index):
    assert "internal/it/legacy-credentials.md" in staff_index.redacted_paths
    assert staff_index.get_note("internal/it/legacy-credentials.md") is None
    r = staff_index.search("telemetry uploader legacy credentials rotate", k=10, min_score=0.0)
    assert all(h.note_path != "internal/it/legacy-credentials.md" for h in r.hits)


def test_get_note_denied_equals_nonexistent(staff_index):
    assert staff_index.get_note("restricted/finance/salary-bands.md") is None
    assert staff_index.get_note("no/such/note.md") is None


def test_backlinks_within_entitlements(staff_index, exec_index):
    # board minutes link to project-nimbus; staff can see neither side
    assert staff_index.backlinks("restricted/finance/project-nimbus.md") == []
    assert "restricted/board/board-minutes-2026-06.md" in exec_index.backlinks(
        "restricted/finance/project-nimbus.md"
    )
