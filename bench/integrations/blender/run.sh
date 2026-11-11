#!/bin/sh
# one tool-server test session with blender: leks on words only next to the blender lab mcp server,
# then a separate background blender checks the saved file against D-07
# usage: sh bench/integrations/blender/run.sh <run id>
# needs: claude (the claude code cli), uv, a python3 that can import leks, blender, the blender mcp server
#   BLENDER_MCP_DIR  folder of the blender mcp server (required)
#   BLENDER          blender binary (default: the macOS app)
#   OUT              where runs go (default: bench/integrations/runs)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
ID=${1:?usage: run.sh <run id>}
: "${BLENDER_MCP_DIR:?set BLENDER_MCP_DIR to the folder of the Blender MCP server}"
BLENDER=${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}
OUT=${OUT:-$REPO/bench/integrations/runs}
W=$OUT/blender_$ID
rm -rf "$W"; mkdir -p "$W/proj"

# a fresh project from the template with the five test notes
T=$REPO/examples/project-template
cp -R "$T/AGENTS.md" "$T/CLAUDE.md" "$T/.mcp.json" "$T/.claude" "$W/proj/"
cp -R "$HERE/notes" "$W/proj/notes"

# leks config (words only, no models) and the blender server config
python3 - "$W" "$BLENDER_MCP_DIR" "$BLENDER" <<'PY'
import json, os, sys
w, mcp_dir, blender = sys.argv[1:4]
cfg = {"version": 3, "home": os.path.join(w, "home"),
       "roots": [{"name": "notes", "path": os.path.join(w, "proj", "notes"), "kind": "notes"}],
       "profiles": {"architect": {"roots": ["notes"]}}, "default_profile": "architect",
       "models": {"backend": "torch", "device": "auto", "weights_dir": None, "embedder": None, "reranker": None}}
json.dump(cfg, open(os.path.join(w, "leks-config.json"), "w"), indent=2)
srv = {"mcpServers": {"blender": {"command": "uv", "args": ["run", "--directory", mcp_dir, "blender-mcp"],
                                  "env": {"BLENDER_PATH": blender}}}}
json.dump(srv, open(os.path.join(w, "blender-mcp.json"), "w"), indent=2)
PY
export LEKS_CONFIG="$W/leks-config.json"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"

"$BLENDER" --background --factory-startup --python "$HERE/make_start.py" -- "$W/start.blend" > "$W/make_start.log" 2>&1
cd "$W/proj"
python3 -m leks build > "$W/build.txt" 2>&1

P="Build the display pedestal for the product scene in Blender, exactly as our current spec says. Start from the empty file $W/start.blend and save the result as $W/pedestal.blend. Take the spec from the project notes. When you are done, tell me which note you followed and why."
echo "blender run $ID session $(date '+%H:%M:%S')"
claude -p "$P" --model sonnet --effort medium --setting-sources project --strict-mcp-config \
  --mcp-config .mcp.json --mcp-config "$W/blender-mcp.json" \
  --permission-mode dontAsk --allowedTools "mcp__leks,mcp__blender" \
  --disallowedTools "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,Agent,NotebookEdit" \
  --output-format stream-json --verbose --include-hook-events --max-budget-usd 3 \
  > "$W/session.jsonl" 2> "$W/session.err"
echo "blender run $ID session exit $? $(date '+%H:%M:%S')"

# the check: blender opens the saved file in the background, the agent never saw this script
"$BLENDER" --background "$W/pedestal.blend" --python "$HERE/verify_pedestal.py" 2>&1 | grep '^VERIFY' > "$W/verify.txt"
cat "$W/verify.txt"
