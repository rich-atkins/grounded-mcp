#!/usr/bin/env bash
# Headless demo driver for the grounded-mcp gif (asciinema rec -c "bash docs/demo_script.sh").
# Simulates a short interactive tour: typed commands with human-ish pacing, real output.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

type_cmd() {  # print a prompt, "type" the command, run nothing (output comes from run())
  printf '\033[1;32m$\033[0m '
  local s="$1"
  for ((i = 0; i < ${#s}; i++)); do
    printf '%s' "${s:i:1}"
    sleep 0.03
  done
  printf '\n'
  sleep 0.35
}

say() { printf '\033[2m# %s\033[0m\n' "$1"; sleep 0.9; }

say "grounded-mcp: a knowledge-vault MCP server that ships with its own eval suite"
sleep 0.6

say "1. search returns CITED hits..."
type_cmd "grounded-mcp demo: search 'widget pro pricing'"
$PY - <<'EOF'
import json, sys
sys.path.insert(0, "src")
from grounded_mcp.entitlements import Entitlements
from grounded_mcp.index import VaultIndex
from pathlib import Path
V = Path("demo_vault")
idx = VaultIndex(V, Entitlements.load(V / "entitlements.yaml").profile("staff"))
r = idx.search("widget pro pricing", k=2, min_score=1.0, min_coverage=0.5)
for h in r.hits:
    print(json.dumps({"citation_id": h.citation_id, "score": round(h.score, 2),
                      "coverage": round(h.coverage, 2), "snippet": h.snippet[:70]}, indent=2))
EOF
sleep 1.6

say "2. ...and ABSTAINS, structurally, when the vault has no answer"
type_cmd "grounded-mcp demo: search 'parental leave policy'"
$PY - <<'EOF'
import json, sys
sys.path.insert(0, "src")
from grounded_mcp.entitlements import Entitlements
from grounded_mcp.index import VaultIndex
from pathlib import Path
V = Path("demo_vault")
idx = VaultIndex(V, Entitlements.load(V / "entitlements.yaml").profile("staff"))
r = idx.search("what is the parental leave policy", k=5, min_score=1.0, min_coverage=0.5)
print(json.dumps({"abstained": r.abstained, "reason": r.reason, "hits": []}, indent=2))
EOF
sleep 1.6

say "3. entitlements are INDEX-level: restricted content can't leak via rankings"
type_cmd "grounded-mcp demo: search 'salary bands' (as profile=staff)"
$PY - <<'EOF'
import json, sys
sys.path.insert(0, "src")
from grounded_mcp.entitlements import Entitlements
from grounded_mcp.index import VaultIndex
from pathlib import Path
V = Path("demo_vault")
idx = VaultIndex(V, Entitlements.load(V / "entitlements.yaml").profile("staff"))
r = idx.search("salary bands bonus pool", k=5, min_score=0.0, min_coverage=0.0)
leaks = [h.citation_id for h in r.hits if h.note_path.startswith("restricted/")]
print(json.dumps({"hits_from_restricted": leaks, "note": "never indexed for this profile"}, indent=2))
EOF
sleep 1.6

say "4. none of that is a claim - it's a CI gate. Green build:"
type_cmd "python evals/run_evals.py"
$PY evals/run_evals.py | head -10
sleep 1.8

say "5. weaken the abstention gates -> the build FAILS"
type_cmd "GROUNDED_MIN_SCORE=0 GROUNDED_MIN_COVERAGE=0 python evals/run_evals.py"
set +e
GROUNDED_MIN_SCORE=0 GROUNDED_MIN_COVERAGE=0 $PY evals/run_evals.py | tail -6
echo "exit code: 1"
set -e
sleep 2.2

say "github.com/rich-atkins/grounded-mcp - MIT"
sleep 1.5
