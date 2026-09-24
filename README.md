# grounded-mcp

**An MCP server for markdown knowledge vaults that ships with its own eval suite.**

![grounded-mcp demo: cited search, structural abstention, index-level entitlements, and the eval gate going red when weakened](docs/demo.gif)

Plenty of servers expose your notes over the [Model Context Protocol](https://modelcontextprotocol.io).
Before you point an agent at a knowledge store, though, you need answers to four questions most of
them skip:

1. **Can I trust what it returns?** Every content-bearing response carries a stable
   citation id (`path#heading` + line spans) — nothing is returned that can't be quoted with provenance.
2. **Does it know when it doesn't know?** Search **abstains** — an explicit, structured
   "no adequate answer" — instead of dressing weak matches up as results.
3. **Who is allowed to see what?** Declarative **entitlements**, enforced at *index* level:
   content a profile can't see is never indexed for it, so it can't leak through scores,
   snippets, or rankings. A **redaction guard** refuses to serve notes that look like they
   contain credentials, regardless of entitlements.
4. **Is it still true?** A note marked `superseded_by: <path>` is **never searchable** and is
   read back flagged with its successor. "No longer true" is a different failure from "no
   answer" and "no access", and the suite counts it separately (v0.3).

And none of that is a claim — it's a **CI-gated scorecard** run against the committed demo vault,
reported **by slice** (each slice is one kind of failure; the case count sits next to pass/fail
so nothing hides inside an aggregate):

```
slice          n  pass  fail   detail
answerable    24    24     0   hit@1 1.00  recall@5 1.00  MRR 1.00  miss 0  false-abstain 0
unsupported   10    10     0   abstention 1.00  false-answer 0
superseded    16    16     0   stale-cited 0 (must be 0)  current-hit 12/12  history-readable 4/4
denied        24    24     0   leaks 0 (must be 0)
secret         6     6     0   violations 0 (must be 0)
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
| `read_note(citation, section_only)` | A note or single section by citation id; every block carries its own citation anchor. A superseded note comes back with `superseded: true`, `superseded_by` and a warning. |
| `backlinks(path)` | Wikilink graph: what links here, within your entitlements. |
| `browse(prefix, tag, include_superseded)` | List visible *current* notes by folder/tag; superseded ones only on request, each with its successor path. |

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
| `GROUNDED_TRANSPORT` | `stdio` | `stdio` \| `http` (v0.2) |
| `GROUNDED_TOKENS` | — | REQUIRED in http mode: yaml token map (sha256 → client/profile) |
| `GROUNDED_HOST` / `GROUNDED_PORT` | `127.0.0.1` / `8000` | http bind address |

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

## Why supersession is structural, not a ranking rule (a measured finding, v0.3)

Real vaults keep old versions: last year's expenses policy next to this year's, an archived
datasheet, a superseded deploy runbook. Mark the old one in its frontmatter:

```yaml
---
title: Expenses Policy
superseded_by: internal/hr/expenses-policy.md
---
```

and it is excluded from the search index at build time — the same mechanism as entitlements,
so it can never be scored, ranked or cited — while `read_note` still returns it by path,
flagged, so history stays reachable on purpose and never by accident.

Why exclusion rather than a recency boost: measured before this existed, with four stale notes
placed beside their successors, the stale copy **tied its successor on BM25 score in 7 of 12
queries, tied on coverage in 11 of 12, ranked first in 8 of 12 and was cited in 12 of 12**.
Where scores tied the stale note won every time, because `-2025.md` sorts before `.md`.
Both abstention gates passed it in 12 of 12: a stale note *is* relevant, it is just no longer
true, and no relevance signal can see that. The v0.2 scorecard reported the damage as a
ranking regression in retrieval and named the wrong cause. v0.3 gives "no longer true" its
own slice, with a hard zero.

## HTTP mode (v0.2): real per-client entitlements

```bash
python -m grounded_mcp.authz my-raw-token        # -> sha256 for the tokens file
GROUNDED_TRANSPORT=http GROUNDED_TOKENS=tokens.yaml GROUNDED_VAULT=~/notes grounded-mcp
```

Each client's bearer token maps to an entitlements profile (see `example-tokens.yaml`);
the server keeps one index per profile, so enforcement stays index-level per VERIFIED
identity. Unknown token = 401, never a default. Empty token file = server refuses to
start. Tested over the wire: two tokens against one live server get different vaults,
and the leakage probe runs as a real client.

## Honest boundaries

- **stdio = one user.** Over stdio, client and server run as the same user, so profiles
  demonstrate the *deployment pattern* rather than enforce against a hostile peer — for
  real multi-client enforcement, use HTTP mode (above). The enforcement machinery is
  identical either way — index-level, not response-filtering.
- **A denied note and a nonexistent note return the same response.** The server refuses to be
  an existence oracle for content outside your entitlements.
- **The redaction patterns are high-precision, not exhaustive.** They catch key-shaped strings
  (AWS/GitHub/Slack tokens, private-key blocks, `api_key = "..."` assignments), not every secret.
- **Supersession is declared, not detected.** The server trusts `superseded_by` in frontmatter;
  it does not guess that two similar notes are versions of each other. A stale note nobody
  marked is still served — the eval slice exists so you can measure how many you have.
- **BM25 is the deliberate v0.1 baseline** — deterministic, dependency-free, measurable. Hybrid
  semantic retrieval lands in v0.4 *with its eval delta published, per slice, against this baseline*.

## The demo vault

A fictional company handbook ("ACME Ltd") with three zones — `public/`, `internal/`,
`restricted/` — plus one deliberately seeded fake-credentials note (the classic AWS
documentation example key) that the redaction guard must refuse to serve, and four
`*-2025.md` notes marked `superseded_by` their current versions so the superseded slice has
something real-shaped to fail on. All content is
synthetic; the vault exists so the eval suite has something real-shaped to prove things against.

## Development

```bash
pip install -e ".[dev]"
pytest -q                      # unit tests
python evals/run_evals.py      # the eval gate (CI runs both)
python evals/run_evals.py --write-baseline   # accept current scores after a deliberate change
```

## Roadmap

- **v0.2** ✅ shipped — streamable HTTP transport with per-client entitlements.
- **v0.3** ✅ shipped — supersession (`superseded_by`) enforced at index build, plus the slice-aware scorecard (answerable / unsupported / superseded / denied / secret, n next to pass/fail, false abstention split from misses).
- **v0.4** — hybrid semantic retrieval, landing only with its eval delta vs the BM25 baseline published *per slice*.
- Pluggable store backends (the vault interface is small); community adapters welcome.

MIT © Richard Atkins
