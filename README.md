# grounded-mcp

**An MCP server for markdown knowledge vaults that ships with its own eval suite.**

![grounded-mcp demo: cited search, structural abstention, index-level entitlements, and the eval gate going red when weakened](docs/demo.gif)

Plenty of servers expose your notes over the [Model Context Protocol](https://modelcontextprotocol.io).
Before you point an agent at a knowledge store, though, you need answers to three questions most of
them skip:

1. **Can I trust what it returns?** Every content-bearing response carries a stable
   citation id (`path#heading` + line spans) — nothing is returned that can't be quoted with provenance.
2. **Does it know when it doesn't know?** Search **abstains** — an explicit, structured
   "no adequate answer" — instead of dressing weak matches up as results.
3. **Who is allowed to see what?** Declarative **entitlements**, enforced at *index* level:
   content a profile can't see is never indexed for it, so it can't leak through scores,
   snippets, or rankings. A **redaction guard** refuses to serve notes that look like they
   contain credentials, regardless of entitlements.

And none of that is a claim — it's a **CI-gated scorecard** run against the committed demo vault:

```
retrieval   (staff, n=22):  hit@1 1.00   recall@5 1.00   MRR 1.00
abstention  (n=10):         rate  1.00   (target 1.00)
leakage     : 0 (must be 0)
redaction   : 0 (must be 0)
EVAL GATE: PASS
```

Weaken the abstention gates and the build fails — try it:

```bash
GROUNDED_MIN_SCORE=0 GROUNDED_MIN_COVERAGE=0 python evals/run_evals.py   # EVAL GATE: FAIL, exit 1
```

## Quickstart

```bash
pip install -e .
grounded-mcp          # serves the bundled ACME demo vault over stdio
```

Point it at your own vault (Obsidian works as-is — frontmatter, `[[wikilinks]]`, tags):

```bash
GROUNDED_VAULT=~/notes GROUNDED_PROFILE=default grounded-mcp
```

### Claude Code / Claude Desktop

```json
{
  "mcpServers": {
    "grounded": {
      "command": "grounded-mcp",
      "env": { "GROUNDED_VAULT": "/path/to/your/vault" }
    }
  }
}
```

## Tools

| Tool | What it does |
|---|---|
| `search(query, k)` | BM25 over titles/headings/tags/body. Cited hits — or an explicit abstention with the scores that failed the bar. |
| `read_note(citation, section_only)` | A note or single section by citation id; every block carries its own citation anchor. |
| `backlinks(path)` | Wikilink graph: what links here, within your entitlements. |
| `browse(prefix, tag)` | List visible notes by folder/tag. |

Read-only **by design** — a server that can quote your vault but never rewrite it is a trust
feature, not a missing feature.

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `GROUNDED_VAULT` | `./demo_vault` | vault root |
| `GROUNDED_PROFILE` | `default` | entitlements profile to serve as |
| `GROUNDED_ENTITLEMENTS` | `<vault>/entitlements.yaml` | rules file (absent = allow all) |
| `GROUNDED_MIN_SCORE` | `1.0` | abstention score threshold |
| `GROUNDED_MIN_COVERAGE` | `0.5` | abstention term-coverage threshold |
| `GROUNDED_SERVE_REDACTED` | `false` | serve notes containing secret patterns |

Entitlements (`deny` wins, then `allow`, then default-deny):

```yaml
profiles:
  staff:
    allow: ["public/**", "internal/**"]
    deny:  ["restricted/**"]
  contractor:
    allow: ["public/**"]
```

## Why abstention needs two gates (a measured finding)

BM25 score alone cannot separate true answers from confident-looking misses on a small vault:
in our golden set, genuine answers score as low as **2.4** while wrong-but-plausible matches
reach **4.3** — a single common word ("policy") landing in a weighted title field looks like a
result. The discriminator is **term coverage**: what fraction of the query's content words the
note actually contains. Both gates together take abstention from **0.30 → 1.00** with zero
retrieval loss. The eval suite is what made that tuning honest — full details in
`evals/run_evals.py` and the scorecard baseline.

## Honest boundaries

- **stdio = one user.** Over stdio, client and server run as the same user, so profiles here
  demonstrate the *deployment pattern* rather than enforce against a hostile peer. Real
  per-client entitlements arrive with the HTTP transport (v0.2). The enforcement machinery is
  identical either way — index-level, not response-filtering.
- **A denied note and a nonexistent note return the same response.** The server refuses to be
  an existence oracle for content outside your entitlements.
- **The redaction patterns are high-precision, not exhaustive.** They catch key-shaped strings
  (AWS/GitHub/Slack tokens, private-key blocks, `api_key = "..."` assignments), not every secret.
- **BM25 is the deliberate v0.1 baseline** — deterministic, dependency-free, measurable. Hybrid
  semantic retrieval lands in v0.2 *with its eval delta published against this baseline*.

## The demo vault

A fictional company handbook ("ACME Ltd") with three zones — `public/`, `internal/`,
`restricted/` — plus one deliberately seeded fake-credentials note (the classic AWS
documentation example key) that the redaction guard must refuse to serve. All content is
synthetic; the vault exists so the eval suite has something real-shaped to prove things against.

## Development

```bash
pip install -e ".[dev]"
pytest -q                      # unit tests
python evals/run_evals.py      # the eval gate (CI runs both)
python evals/run_evals.py --write-baseline   # accept current scores after a deliberate change
```

## Roadmap

- **v0.2** — streamable HTTP transport (2026-07-28 stateless spec) with per-client entitlement
  profiles; hybrid semantic retrieval with the eval delta vs BM25 published.
- Pluggable store backends (the vault interface is small); community adapters welcome.

MIT © Richard Atkins
