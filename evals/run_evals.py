#!/usr/bin/env python3
"""grounded-mcp eval harness — the proof behind the guardrails, reported by slice.

Every golden case belongs to exactly one SLICE, named for the kind of failure
it can expose. Each slice reports its case count next to pass/fail, so a small
suite stays interpretable and no failure mode can hide inside an aggregate:

  answerable   the answer exists and the caller may see it: hit@1 / recall@5 /
               MRR, with misses split into MISS (wrong note) and FALSE-ABSTAIN
               (abstained when an answer existed)          — gated vs baseline
  unsupported  no answer exists in the vault: must abstain
               (a FALSE-ANSWER is a confident wrong answer) — gated vs baseline
  superseded   the answer exists, is visible, and is NO LONGER TRUE: a stale
               version sits beside its successor. The stale path must never be
               cited (hard zero), the current one must still be found, and a
               direct read of the stale path must come back WITH its successor
               pointer (history is reachable on purpose, never by accident)
  denied       the answer exists but is outside the caller's entitlements:
               unthresholded search and direct reads must return nothing
                                                            — hard zero
  secret       the seeded-credentials note must never be served, and banned
               strings must appear in no output              — hard zero

Why slices (v0.3): before the superseded slice existed, four stale notes in the
vault surfaced as a RANKING regression in the answerable slice — the gate went
red and named the wrong cause. "No answer", "no access" and "no longer true"
are different failures and are counted as such.

Exit code is non-zero on any hard-zero violation, or when any slice's fail
count or metric regresses against the committed baseline — wire it into CI.

Usage:
  python evals/run_evals.py                   # run + compare vs baseline
  python evals/run_evals.py --write-baseline  # accept current scores as baseline
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from grounded_mcp.entitlements import Entitlements  # noqa: E402
from grounded_mcp.index import VaultIndex  # noqa: E402

VAULT = ROOT / "demo_vault"
GOLDEN = Path(__file__).parent / "golden.yaml"
BASELINE = Path(__file__).parent / "baseline.json"

MIN_SCORE = float(os.environ.get("GROUNDED_MIN_SCORE", "1.0"))
MIN_COVERAGE = float(os.environ.get("GROUNDED_MIN_COVERAGE", "0.5"))
K = 5
TOLERANCE = 1e-6  # metrics are deterministic; any drop is a real regression

SLICES = ("answerable", "unsupported", "superseded", "denied", "secret")
HARD_ZERO = {  # slice -> counter that must be 0 regardless of baseline
    "superseded": ("stale_cited", "pointer_missing"),
    "denied": ("leaks",),
    "secret": ("violations",),
}

_ENTS = Entitlements.load(VAULT / "entitlements.yaml")
_INDEXES: dict[str, VaultIndex] = {}


def _index(profile_name: str) -> VaultIndex:
    if profile_name not in _INDEXES:
        _INDEXES[profile_name] = VaultIndex(VAULT, _ENTS.profile(profile_name))
    return _INDEXES[profile_name]


def _thresholded(idx: VaultIndex, q: str):
    return idx.search(q, k=K, min_score=MIN_SCORE, min_coverage=MIN_COVERAGE)


def _unthresholded(idx: VaultIndex, q: str):
    # Harshest setting: even a low-ranked snippet counts.
    return idx.search(q, k=10, min_score=0.0, min_coverage=0.0)


# -- slices ----------------------------------------------------------------

def eval_answerable(blocks: list[dict]) -> dict:
    """Retrieval over one or more profile blocks; per-profile metrics kept."""
    out = {"n": 0, "pass": 0, "fail": 0, "miss": 0, "false_abstain": 0,
           "profiles": {}, "failures": []}
    hits1 = hits5 = 0
    rr_sum = 0.0
    for block in blocks:
        prof = block["profile"]
        idx = _index(prof)
        p_hits1 = p_hits5 = 0
        p_rr = 0.0
        for item in block["queries"]:
            q, expect = item["q"], set(item["expect"])
            r = _thresholded(idx, q)
            paths = [h.note_path for h in r.hits]
            rank = next((i + 1 for i, p in enumerate(paths) if p in expect), None)
            out["n"] += 1
            if rank == 1:
                p_hits1 += 1
            if rank is not None:
                p_hits5 += 1
                p_rr += 1.0 / rank
                out["pass"] += 1
            else:
                out["fail"] += 1
                if r.abstained:
                    out["false_abstain"] += 1
                    out["failures"].append(
                        f"  FALSE-ABSTAIN [{prof}] {q!r} -> abstained "
                        f"(best rejected {r.best_rejected})")
                else:
                    out["miss"] += 1
                    out["failures"].append(f"  MISS [{prof}] {q!r} -> {paths[:3]}")
        n = len(block["queries"])
        out["profiles"][prof] = {
            "n": n,
            "hit_at_1": round(p_hits1 / n, 4),
            "recall_at_5": round(p_hits5 / n, 4),
            "mrr": round(p_rr / n, 4),
        }
        hits1 += p_hits1
        hits5 += p_hits5
        rr_sum += p_rr
    n = out["n"]
    out["hit_at_1"] = round(hits1 / n, 4)
    out["recall_at_5"] = round(hits5 / n, 4)
    out["mrr"] = round(rr_sum / n, 4)
    return out


def eval_unsupported(block: dict) -> dict:
    idx = _index(block["profile"])
    out = {"n": 0, "pass": 0, "fail": 0, "false_answer": 0, "failures": []}
    for q in block["queries"]:
        r = _thresholded(idx, q)
        out["n"] += 1
        if r.abstained:
            out["pass"] += 1
        else:
            out["fail"] += 1
            out["false_answer"] += 1
            top = r.hits[0]
            out["failures"].append(
                f"  FALSE-ANSWER {q!r} -> {top.citation_id} (score {top.score:.2f})")
    out["abstention_rate"] = round(out["pass"] / out["n"], 4)
    return out


def eval_superseded(block: dict) -> dict:
    out = {"n": 0, "pass": 0, "fail": 0, "stale_cited": 0, "current_hit": 0,
           "current_missed": 0, "pointer_missing": 0, "reads": 0, "failures": []}
    for case in block["cases"]:
        prof = case.get("profile", "staff")
        idx = _index(prof)
        q, expect, stale = case["q"], set(case["expect"]), set(case["stale"])
        out["n"] += 1
        ok = True
        # Hard zero: the stale path must not surface even unthresholded.
        cited = [h.citation_id for h in _unthresholded(idx, q).hits if h.note_path in stale]
        if cited:
            ok = False
            out["stale_cited"] += 1
            out["failures"].append(f"  STALE-CITED [{prof}] {q!r} -> {cited[0]}")
        # And the current version must still be found (thresholded).
        r = _thresholded(idx, q)
        paths = [h.note_path for h in r.hits]
        if any(p in expect for p in paths):
            out["current_hit"] += 1
        else:
            ok = False
            out["current_missed"] += 1
            out["failures"].append(
                f"  CURRENT-{'ABSTAINED' if r.abstained else 'MISSED'} [{prof}] {q!r} -> "
                f"{paths[:3] or 'ABSTAINED'}")
        out["pass" if ok else "fail"] += 1
    for read in block.get("direct_reads", []):
        prof = read.get("profile", "staff")
        idx = _index(prof)
        out["n"] += 1
        out["reads"] += 1
        note = idx.get_note(read["path"])
        if note is not None and note.superseded_by == read["superseded_by"]:
            out["pass"] += 1
        else:
            out["fail"] += 1
            out["pointer_missing"] += 1
            out["failures"].append(
                f"  NO-POINTER [{prof}] read {read['path']} -> "
                f"{'not readable' if note is None else note.superseded_by!r}")
    return out


def eval_denied(block: dict) -> dict:
    out = {"n": 0, "pass": 0, "fail": 0, "leaks": 0, "profiles": block["profiles"],
           "failures": []}
    for prof in block["profiles"]:
        idx = _index(prof)
        allowed = idx.profile.permits
        for q in block["queries"]:
            out["n"] += 1
            leaked = [h.citation_id for h in _unthresholded(idx, q).hits
                      if not allowed(h.note_path)]
            if leaked:
                out["fail"] += 1
                out["leaks"] += 1
                out["failures"].append(f"  LEAK [{prof}] {q!r} -> {leaked[0]}")
            else:
                out["pass"] += 1
        for path in block["direct_reads"]:
            out["n"] += 1
            if not allowed(path) and idx.get_note(path) is not None:
                out["fail"] += 1
                out["leaks"] += 1
                out["failures"].append(f"  LEAK [{prof}] direct read -> {path}")
            else:
                out["pass"] += 1
    return out


def eval_secret(block: dict) -> dict:
    out = {"n": 0, "pass": 0, "fail": 0, "violations": 0, "profiles": block["profiles"],
           "failures": []}
    banned = block["banned_strings"]
    for prof in block["profiles"]:
        idx = _index(prof)
        for q in block["queries"]:
            out["n"] += 1
            bad = None
            for h in _unthresholded(idx, q).hits:
                blob = h.snippet + h.citation_id
                if any(s in blob for s in banned):
                    bad = h.citation_id
                    break
            if bad:
                out["fail"] += 1
                out["violations"] += 1
                out["failures"].append(f"  SECRET [{prof}] {q!r} -> {bad}")
            else:
                out["pass"] += 1
        for path in block["direct_reads"]:
            out["n"] += 1
            if idx.get_note(path) is not None:
                out["fail"] += 1
                out["violations"] += 1
                out["failures"].append(f"  SERVED [{prof}] secret note -> {path}")
            else:
                out["pass"] += 1
    return out


# -- scorecard ---------------------------------------------------------------

def _detail(name: str, s: dict) -> str:
    if name == "answerable":
        profs = "  ".join(f"{p} n={m['n']}" for p, m in s["profiles"].items())
        return (f"hit@1 {s['hit_at_1']:.2f}  recall@5 {s['recall_at_5']:.2f}  "
                f"MRR {s['mrr']:.2f}  miss {s['miss']}  false-abstain {s['false_abstain']}"
                f"   [{profs}]")
    if name == "unsupported":
        return f"abstention {s['abstention_rate']:.2f}  false-answer {s['false_answer']}"
    if name == "superseded":
        q = s["n"] - s["reads"]
        return (f"stale-cited {s['stale_cited']} (must be 0)  current-hit "
                f"{s['current_hit']}/{q}  history-readable "
                f"{s['reads'] - s['pointer_missing']}/{s['reads']}")
    if name == "denied":
        return f"leaks {s['leaks']} (must be 0)   [{', '.join(s['profiles'])}]"
    if name == "secret":
        return f"violations {s['violations']} (must be 0)   [{', '.join(s['profiles'])}]"
    return ""


def print_scorecard(results: dict) -> None:
    print(f"grounded-mcp eval scorecard  (min_score={MIN_SCORE}, min_coverage={MIN_COVERAGE})")
    print("=" * 72)
    print(f"{'slice':<12}{'n':>4}{'pass':>6}{'fail':>6}   detail")
    for name in SLICES:
        s = results["slices"][name]
        print(f"{name:<12}{s['n']:>4}{s['pass']:>6}{s['fail']:>6}   {_detail(name, s)}")
    for name in SLICES:
        for line in results["slices"][name]["failures"]:
            print(line)


def gate(results: dict, base: dict | None) -> list[str]:
    failed = []
    for name, counters in HARD_ZERO.items():
        for c in counters:
            if results["slices"][name][c]:
                failed.append(f"{name}.{c} = {results['slices'][name][c]} (hard zero violated)")
    if base is None:
        return failed
    for name in SLICES:
        cur, old = results["slices"][name], base["slices"].get(name)
        if old is None:
            failed.append(f"{name}: no baseline for this slice — run --write-baseline")
            continue
        if cur["fail"] > old["fail"]:
            failed.append(f"{name}.fail regressed: {old['fail']} -> {cur['fail']} (n={cur['n']})")
        for metric in ("hit_at_1", "recall_at_5", "mrr", "abstention_rate"):
            if metric in old and cur[metric] < old[metric] - TOLERANCE:
                failed.append(f"{name}.{metric} regressed: {old[metric]:.2f} -> {cur[metric]:.2f}")
    return failed


def main() -> int:
    golden = yaml.safe_load(GOLDEN.read_text())
    results = {
        "schema": 2,
        "min_score": MIN_SCORE,
        "min_coverage": MIN_COVERAGE,
        "slices": {
            "answerable": eval_answerable(golden["answerable"]),
            "unsupported": eval_unsupported(golden["unsupported"]),
            "superseded": eval_superseded(golden["superseded"]),
            "denied": eval_denied(golden["denied"]),
            "secret": eval_secret(golden["secret"]),
        },
    }
    print_scorecard(results)

    if "--write-baseline" in sys.argv:
        failed = gate(results, None)
        BASELINE.write_text(json.dumps(results, indent=2) + "\n")
        print(f"\nbaseline written -> {BASELINE.name}")
    else:
        base = None
        if BASELINE.exists():
            base = json.loads(BASELINE.read_text())
            if base.get("schema") != 2:
                print("\nWARNING: baseline is pre-v0.3 (no slices) — run --write-baseline")
                base = None
        else:
            print("\nWARNING: no baseline committed — run with --write-baseline to set one")
        failed = gate(results, base)

    print("=" * 72)
    if failed:
        print("EVAL GATE: FAIL")
        for f in failed:
            print(f"  - {f}")
        return 1
    print("EVAL GATE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
