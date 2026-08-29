from pathlib import Path

import pytest

from grounded_mcp.entitlements import Entitlements, Profile, find_secrets

VAULT = Path(__file__).parent.parent / "demo_vault"


def test_deny_wins_over_allow():
    p = Profile(name="t", allow=["**"], deny=["restricted/**"])
    assert p.permits("public/welcome.md")
    assert not p.permits("restricted/finance/salary-bands.md")


def test_default_deny_when_no_allow_matches():
    p = Profile(name="t", allow=["public/**"], deny=[])
    assert not p.permits("internal/hr/onboarding.md")


def test_load_demo_profiles():
    ents = Entitlements.load(VAULT / "entitlements.yaml")
    staff = ents.profile("staff")
    assert staff.permits("internal/hr/onboarding.md")
    assert not staff.permits("restricted/board/board-minutes-2026-06.md")
    contractor = ents.profile("contractor")
    assert contractor.permits("public/careers.md")
    assert not contractor.permits("internal/hr/onboarding.md")


def test_unknown_profile_is_a_hard_error_not_a_bypass():
    ents = Entitlements.load(VAULT / "entitlements.yaml")
    with pytest.raises(KeyError):
        ents.profile("nonexistent")


def test_missing_file_yields_permissive_default():
    ents = Entitlements.load(None)
    assert ents.profile("anything").permits("any/path.md")


def test_find_secrets():
    assert "aws-access-key" in find_secrets("key AKIAIOSFODNN7EXAMPLE here")
    assert "generic-assigned-secret" in find_secrets('api_key = "demo1234demo1234demo1234"')
    assert find_secrets("a perfectly normal note about pricing at £249") == []
