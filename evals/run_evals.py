#!/usr/bin/env python3
"""grounded-mcp eval harness — the proof behind the three guardrails.

Four eval families over the committed demo vault:
  retrieval   hit@1 / recall@5 / MRR against a golden query set (gated vs baseline)
  abstention  out-of-vault questions must return an explicit abstention (gated)
  leakage     denied-zone content must NEVER surface — unthresholded, hard zero
  redaction   the seeded-secrets note must never be served, and banned strings
              must appear in no output, ever — hard zero

Exit code is non-zero on any regression vs the committed baseline, any leak,
or any redaction failure — wire it straight into CI.

Usage:
  python evals/run_evals.py                 # run + compare vs baseline
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


def _index(profile_name: str) -> VaultIndex:
    ents = Entitlements.load(VAULT / "entitlements.yaml")
    return VaultIndex(VAULT, ents.profile(profile_name))


def eval_retrieval(block: dict) -> dict:
    idx = _index(block["profile"])
    hits1 = hits5 = 0
    rr_sum = 0.0
    failures = []
    for item in block["queries"]:
        q, expect = item["q"], set(item["expect"])
        r = idx.search(q, k=K, min_score=MIN_SCORE, min_coverage=MIN_COVERAGE)
        paths = [h.note_path for h in r.hits]
        rank = next((i + 1 for i, p in enumerate(paths) if p in expect), None)
        if rank == 1:
            hits1 += 1
        if rank is not None:
            hits5 += 1
            rr_sum += 1.0 / rank
        else:
            failures.append(f"  MISS  {q!r} -> {paths[:3] or 'ABSTAINED'}")
    n = len(block["queries"])
    return {
        "n": n,
        "hit_at_1": round(hits1 / n, 4),
        "recall_at_5": round(hits5 / n, 4),
        "mrr": round(rr_sum / n, 4),
        "failures": failures,
    }


def eval_abstention(block: dict) -> dict:
    idx = _index(block["profile"])
    correct = 0
    failures = []
    for q in block["queries"]:
        r = idx.search(q, k=K, min_score=MIN_SCORE, min_coverage=MIN_COVERAGE)
        if r.abstained:
            correct += 1
        else:
            top = r.hits[0]
            failures.append(
                f"  FALSE-ANSWER  {q!r} -> {top.citation_id} (score {top.score:.2f})"
            )
    n = len(block["queries"])
    return {"n": n, "abstention_rate": round(correct / n, 4), "failures": failures}


def eval_leakage(block: dict) -> dict:
    leaks = []
    for profile_name in block["profiles"]:
        idx = _index(profile_name)
        allowed = idx.profile.permits
        # Harshest setting: unthresholded search — even a low-ranked snippet
        # from a denied path is a leak.
        for q in block["queries"]:
            r = idx.search(q, k=10, min_score=0.0, min_coverage=0.0)
            for h in r.hits:
                if not allowed(h.note_path):
                    leaks.append(f"  LEAK [{profile_name}] {q!r} -> {h.citation_id}")
        for path in block["direct_reads"]:
            if not allowed(path) and idx.get_note(path) is not None:
                leaks.append(f"  LEAK [{profile_name}] direct read -> {path}")
    return {"leaks": leaks, "leak_count": len(leaks)}


def eval_redaction(block: dict) -> dict:
    violations = []
    banned = block["banned_strings"]
    for profile_name in block["profiles"]:
        idx = _index(profile_name)
        for q in block["queries"]:
            r = idx.search(q, k=10, min_score=0.0, min_coverage=0.0)
            for h in r.hits:
                blob = h.snippet + h.citation_id
                for s in banned:
                    if s in blob:
                        violations.append(f"  SECRET [{profile_name}] {q!r} -> {h.citation_id}")
        for path in block["direct_reads"]:
            if idx.get_note(path) is not None:
                violations.append(f"  SERVED [{profile_name}] secret note -> {path}")
    return {"violations": violations, "violation_count": len(violations)}


def main() -> int:
    golden = yaml.safe_load(GOLDEN.read_text())
    results = {
        "min_score": MIN_SCORE,
        "retrieval": eval_retrieval(golden["retrieval"]),
        "retrieval_entitled": eval_retrieval(golden["retrieval_entitled"]),
        "abstention": eval_abstention(golden["abstention"]),
        "leakage": eval_leakage(golden["leakage"]),
        "redaction": eval_redaction(golden["redaction"]),
    }

    print(f"grounded-mcp eval scorecard  (min_score={MIN_SCORE}, min_coverage={MIN_COVERAGE})")
    print("=" * 56)
    r = results["retrieval"]
    print(f"retrieval   (staff, n={r['n']}):  hit@1 {r['hit_at_1']:.2f}   "
          f"recall@5 {r['recall_at_5']:.2f}   MRR {r['mrr']:.2f}")
    re_ = results["retrieval_entitled"]
    print(f"retrieval   (exec,  n={re_['n']}):  hit@1 {re_['hit_at_1']:.2f}   "
          f"recall@5 {re_['recall_at_5']:.2f}   MRR {re_['mrr']:.2f}")
    a = results["abstention"]
    print(f"abstention  (n={a['n']}):         rate  {a['abstention_rate']:.2f}   (target 1.00)")
    print(f"leakage     : {results['leakage']['leak_count']} (must be 0)")
    print(f"redaction   : {results['redaction']['violation_count']} (must be 0)")
    for fam in ("retrieval", "retrieval_entitled", "abstention"):
        for line in results[fam]["failures"]:
            print(line)
    for line in results["leakage"]["leaks"] + results["redaction"]["violations"]:
        print(line)

    failed = []
    if results["leakage"]["leak_count"]:
        failed.append("leakage (hard zero violated)")
    if results["redaction"]["violation_count"]:
        failed.append("redaction (hard zero violated)")

    if "--write-baseline" in sys.argv:
        BASELINE.write_text(json.dumps(results, indent=2) + "\n")
        print(f"\nbaseline written -> {BASELINE.name}")
        return 1 if failed else 0

    if BASELINE.exists():
        base = json.loads(BASELINE.read_text())
        for fam in ("retrieval", "retrieval_entitled"):
            for metric in ("hit_at_1", "recall_at_5", "mrr"):
                if results[fam][metric] < base[fam][metric] - TOLERANCE:
                    failed.append(
                        f"{fam}.{metric} regressed: "
                        f"{base[fam][metric]:.2f} -> {results[fam][metric]:.2f}"
                    )
        if results["abstention"]["abstention_rate"] < base["abstention"]["abstention_rate"] - TOLERANCE:
            failed.append(
                f"abstention_rate regressed: "
                f"{base['abstention']['abstention_rate']:.2f} -> "
                f"{results['abstention']['abstention_rate']:.2f}"
            )
    else:
        print("\nWARNING: no baseline committed — run with --write-baseline to set one")

    print("=" * 56)
    if failed:
        print("EVAL GATE: FAIL")
        for f in failed:
            print(f"  - {f}")
        return 1
    print("EVAL GATE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
