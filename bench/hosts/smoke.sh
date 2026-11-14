#!/bin/sh
# host smoke run: which coding CLIs are installed and signed in, a small run on each, one summary file.
#
#   sh bench/hosts/smoke.sh [OUT_DIR]
#
# it spends real money or plan usage: one short sign-in prompt per host, then 4 questions x 4 arms x 1 repeat
# per host (16 sessions). settings, all optional:
#   SMOKE_HOSTS="claude copilot codex"   hosts to try
#   SMOKE_ARMS=plain,leks,team,subagent  arms to run
#   CLAUDE_MODEL=sonnet  CLAUDE_SMALL_MODEL=haiku
#   COPILOT_MODEL=auto   COPILOT_SMALL_MODEL=   (empty: the subagent arm is left out on copilot)
#   CODEX_MODEL=         CODEX_SMALL_MODEL=     (empty CODEX_MODEL: codex is checked but not run)
#   CODEX_RATES=IN,CACHED,OUT  USD per 1M tokens, so codex gets a cost figure
#   PYTHON=python3       interpreter with leks importable (3.10 or later)
# exit 0 only when every host in SMOKE_HOSTS ran and every session ended ok; 1 when a host is missing,
# not signed in, not configured or failed, after summary.md is written; 1 on bad input before any run
set -u

cd "$(dirname "$0")/../.." || exit 1
PY=${PYTHON:-python3}
OUT=${1:-bench/hosts/runs/smoke-$(date -u +%Y%m%d-%H%M%S)}
HOSTS=${SMOKE_HOSTS:-claude copilot codex}
ARMS=${SMOKE_ARMS:-plain,leks,team,subagent}
PING='Reply with the single word ok.'

if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "need Python 3.10 or later as \$PYTHON (now: $PY)" >&2
  exit 1
fi
if ! PYTHONPATH=. "$PY" -c 'import leks' 2>/dev/null; then
  echo "leks does not import with $PY from this clone" >&2
  exit 1
fi
if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
  echo "$OUT is not empty; name a new folder" >&2
  exit 1
fi
mkdir -p "$OUT"

# run a command with a time limit, output thrown away; exit 124 on a timeout
limited() {
  "$PY" - "$@" <<'EOF'
import subprocess, sys
try:
    r = subprocess.run(sys.argv[1:], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=180)
    sys.exit(r.returncode)
except subprocess.TimeoutExpired:
    sys.exit(124)
except OSError:
    sys.exit(127)
EOF
}

signed_in() {
  case "$1" in
    claude) limited claude -p "$PING" --model "${CLAUDE_SMALL_MODEL:-haiku}" --max-budget-usd 0.05 \
              --no-session-persistence --strict-mcp-config --output-format json ;;
    copilot) limited copilot -p "$PING" --model="${COPILOT_MODEL:-auto}" --no-ask-user --no-auto-update \
              --no-color --disable-builtin-mcps ;;
    codex) if [ -n "${CODEX_MODEL:-}" ]; then
             limited codex exec --ephemeral --skip-git-repo-check --sandbox read-only --model "$CODEX_MODEL" -- "$PING"
           else
             limited codex exec --ephemeral --skip-git-repo-check --sandbox read-only -- "$PING"
           fi ;;
  esac
}

arms_for() {
  # the subagent arm needs a small model name on copilot and codex
  small=$1
  if [ -z "$small" ]; then
    echo "$ARMS" | tr ',' '\n' | grep -v '^subagent$' | paste -sd, -
  else
    echo "$ARMS"
  fi
}

STATUS="$OUT/status.md"
{
  echo "| host | installed | version | signed in | ran | note |"
  echo "|---|---|---|---|---|---|"
} > "$STATUS"
RAN=""
BAD=0
for h in $HOSTS; do
  if ! command -v "$h" >/dev/null 2>&1; then
    echo "| $h | no | - | - | no | not on PATH |" >> "$STATUS"
    BAD=1
    continue
  fi
  ver=$("$h" --version 2>/dev/null | head -n 1)
  echo "checking that $h is signed in (one short prompt)"
  if ! signed_in "$h"; then
    echo "| $h | yes | ${ver:-?} | no | no | the sign-in prompt failed; sign in and run again |" >> "$STATUS"
    BAD=1
    continue
  fi
  note=""
  case "$h" in
    claude)
      set -- --model "${CLAUDE_MODEL:-sonnet}" --small-model "${CLAUDE_SMALL_MODEL:-haiku}" --arms "$ARMS" ;;
    copilot)
      a=$(arms_for "${COPILOT_SMALL_MODEL:-}")
      [ "$a" != "$ARMS" ] && note="no COPILOT_SMALL_MODEL, subagent arm left out"
      set -- --model "${COPILOT_MODEL:-auto}" --arms "$a"
      [ -n "${COPILOT_SMALL_MODEL:-}" ] && set -- "$@" --small-model "$COPILOT_SMALL_MODEL" ;;
    codex)
      if [ -z "${CODEX_MODEL:-}" ]; then
        echo "| $h | yes | ${ver:-?} | yes | no | set CODEX_MODEL to run it |" >> "$STATUS"
        BAD=1
        continue
      fi
      a=$(arms_for "${CODEX_SMALL_MODEL:-}")
      [ "$a" != "$ARMS" ] && note="no CODEX_SMALL_MODEL, subagent arm left out"
      set -- --model "$CODEX_MODEL" --arms "$a"
      [ -n "${CODEX_SMALL_MODEL:-}" ] && set -- "$@" --small-model "$CODEX_SMALL_MODEL"
      [ -n "${CODEX_RATES:-}" ] && set -- "$@" --rates "$CODEX_RATES" ;;
  esac
  echo "running the smoke set on $h"
  "$PY" bench/hosts/run.py "$h" --smoke --python "$PY" --out "$OUT/$h" "$@"
  rc=$?
  # run.py exits 1 when a session failed; its results still get summarised
  if [ -s "$OUT/$h/results.jsonl" ] && \
     "$PY" bench/hosts/summarise.py "$OUT/$h" --md "$OUT/$h.md" --json "$OUT/$h.json" > /dev/null; then
    RAN="$RAN $h"
    if [ "$rc" -eq 0 ]; then
      echo "| $h | yes | ${ver:-?} | yes | yes | ${note:--} |" >> "$STATUS"
    else
      echo "| $h | yes | ${ver:-?} | yes | failed | some sessions did not end ok, see the output above |" >> "$STATUS"
      BAD=1
    fi
  else
    echo "| $h | yes | ${ver:-?} | yes | failed | see the output above |" >> "$STATUS"
    BAD=1
  fi
done

SUMMARY="$OUT/summary.md"
{
  echo "# Host smoke run, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  cat "$STATUS"
  for h in $RAN; do
    echo
    sed 's/^## /### /; s/^# /## /' "$OUT/$h.md"
  done
} > "$SUMMARY"
echo "summary: $SUMMARY"
cat "$STATUS"
exit "$BAD"
