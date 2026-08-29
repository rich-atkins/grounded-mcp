from pathlib import Path

from grounded_mcp.vault import load_vault, parse_note, resolve_citation, slugify

VAULT = Path(__file__).parent.parent / "demo_vault"


def test_slugify():
    assert slugify("Deploy Process") == "deploy-process"
    assert slugify("What's  new?") == "whats-new"


def test_parse_note_frontmatter_and_sections():
    note = parse_note(VAULT, VAULT / "public" / "products" / "widget-pro.md")
    assert note.title == "Widget Pro"
    assert "product" in note.tags
    headings = [s.heading for s in note.sections]
    assert "Pricing" in headings and "Support" in headings
    pricing = next(s for s in note.sections if s.heading == "Pricing")
    assert pricing.citation_id == "public/products/widget-pro.md#pricing"
    assert "£249" in pricing.text
    assert pricing.start_line >= 1 and pricing.end_line >= pricing.start_line


def test_wikilinks_extracted():
    note = parse_note(VAULT, VAULT / "public" / "welcome.md")
    assert "widget-pro" in note.links and "onboarding" in note.links


def test_load_vault_finds_all_zones():
    paths = {n.path for n in load_vault(VAULT)}
    assert any(p.startswith("public/") for p in paths)
    assert any(p.startswith("internal/") for p in paths)
    assert any(p.startswith("restricted/") for p in paths)


def test_resolve_citation():
    assert resolve_citation("a/b.md#pricing") == ("a/b.md", "pricing")
    assert resolve_citation("a/b.md") == ("a/b.md", None)
