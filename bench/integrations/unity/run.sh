#!/bin/sh
# one tool-server test session with unity: leks on words only next to mcp for unity, which talks to an editor
# running in batch mode, then a second batch run of unity checks the saved scene against D-07
# usage: sh bench/integrations/unity/run.sh <run id>   one run
#        sh bench/integrations/unity/run.sh clear      remove the editor preferences the runs set
# needs: claude (the claude code cli), uv, a python3 that can import leks, a unity 6.3 editor,
#        a checkout of CoplayDev/unity-mcp at tag v10.3.0 (run `uv sync --frozen` in its Server folder once)
#   UNITY          unity editor binary (required)
#   UNITY_MCP_DIR  the unity-mcp checkout (required)
#   OUT            where runs go (default: bench/integrations/runs)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
ID=${1:?usage: run.sh <run id> | clear}
: "${UNITY:?set UNITY to the Unity editor binary}"
: "${UNITY_MCP_DIR:?set UNITY_MCP_DIR to the unity-mcp checkout}"
OUT=${OUT:-$REPO/bench/integrations/runs}
BASE=$OUT/unity_base
mkdir -p "$OUT"

# runs LeksSetup.Run (stdio, no telemetry, no client config rewrites) or LeksSetup.Clear in the base project
prefs() {
  mkdir -p "$BASE/Assets/LeksSetup/Editor"
  cp "$HERE/Setup/LeksSetup.cs" "$BASE/Assets/LeksSetup/Editor/"
  "$UNITY" -batchmode -nographics -projectPath "$BASE" -executeMethod "LeksSetup.$1" -logFile "$OUT/unity_prefs_$1.log"
  rm -rf "$BASE/Assets/LeksSetup" "$BASE/Assets/LeksSetup.meta" "$BASE/Library" "$BASE/Temp" "$BASE/Logs"
  grep LEKSSETUP "$OUT/unity_prefs_$1.log"
}

# an empty project with the package, made once and copied for every run
if [ ! -d "$BASE/ProjectSettings" ]; then
  echo "making the base project $(date '+%H:%M:%S')"
  "$UNITY" -batchmode -nographics -createProject "$BASE" -quit -logFile "$OUT/unity_create.log"
  python3 - "$BASE/Packages/manifest.json" "$UNITY_MCP_DIR/MCPForUnity" <<'PY'
import json, sys
path, pkg = sys.argv[1], sys.argv[2]
m = json.load(open(path))
m["dependencies"]["com.coplaydev.unity-mcp"] = "file:" + pkg
json.dump(m, open(path, "w"), indent=2)
PY
  prefs Run
fi
if [ "$ID" = clear ]; then prefs Clear; exit 0; fi

W=$OUT/unity_$ID; P=$W/UnityProj
rm -rf "$W"; mkdir -p "$W/proj"
cp -R "$BASE" "$P"

# a fresh project from the template with the five test notes
T=$REPO/examples/project-template
cp -R "$T/AGENTS.md" "$T/CLAUDE.md" "$T/.mcp.json" "$T/.claude" "$W/proj/"
cp -R "$HERE/notes" "$W/proj/notes"

# leks config (words only, no models) and the unity server config
python3 - "$W" "$UNITY_MCP_DIR" <<'PY'
import json, os, sys
w, mcp_dir = sys.argv[1:3]
cfg = {"version": 3, "home": os.path.join(w, "home"),
       "roots": [{"name": "notes", "path": os.path.join(w, "proj", "notes"), "kind": "notes"}],
       "profiles": {"architect": {"roots": ["notes"]}}, "default_profile": "architect",
       "models": {"backend": "torch", "device": "auto", "weights_dir": None, "embedder": None, "reranker": None}}
json.dump(cfg, open(os.path.join(w, "leks-config.json"), "w"), indent=2)
srv = {"mcpServers": {"unityMCP": {"command": "uv",
       "args": ["run", "--frozen", "--directory", os.path.join(mcp_dir, "Server"), "mcp-for-unity", "--transport", "stdio"],
       "env": {"UNITY_MCP_DISABLE_TELEMETRY": "1", "UNITY_MCP_DISABLE_FOCUS_NUDGE": "1"}}}}
json.dump(srv, open(os.path.join(w, "unity-mcp.json"), "w"), indent=2)
PY
export LEKS_CONFIG="$W/leks-config.json"
export PYTHONPATH="$REPO${PYTHONPATH:+:$PYTHONPATH}"
cd "$W/proj"
python3 -m leks build > "$W/build.txt" 2>&1

# the editor without a window; UNITY_MCP_ALLOW_BATCH lets the package start its bridge in batch mode
UNITY_MCP_ALLOW_BATCH=1 "$UNITY" -batchmode -nographics -projectPath "$P" -logFile "$W/editor.log" &
UPID=$!
n=0
until grep -q 'StdioBridgeHost started on port' "$W/editor.log" 2>/dev/null; do
  n=$((n+1)); if [ $n -gt 240 ]; then echo "bridge did not start"; kill $UPID; exit 1; fi; sleep 1
done
echo "unity run $ID bridge up after ${n}s"

PROMPT="Build the display pedestal for the product scene in Unity, exactly as our current spec says. The Unity project is already open in the editor. Save the scene as Assets/Scenes/Pedestal.unity. Take the spec from the project notes. When you are done, tell me which note you followed and why."
echo "unity run $ID session $(date '+%H:%M:%S')"
claude -p "$PROMPT" --model sonnet --effort medium --setting-sources project --strict-mcp-config \
  --mcp-config .mcp.json --mcp-config "$W/unity-mcp.json" \
  --permission-mode dontAsk --allowedTools "mcp__leks,mcp__unityMCP" \
  --disallowedTools "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task,Agent,NotebookEdit" \
  --output-format stream-json --verbose --include-hook-events --max-budget-usd 3 \
  > "$W/session.jsonl" 2> "$W/session.err"
echo "unity run $ID session exit $? $(date '+%H:%M:%S')"
kill $UPID 2>/dev/null
n=0; while kill -0 $UPID 2>/dev/null; do n=$((n+1)); [ $n -gt 60 ] && kill -9 $UPID; sleep 1; done

# the check: copied in only now, so the agent never saw it
mkdir -p "$P/Assets/LeksVerify/Editor"
cp "$HERE/Editor/VerifyPedestal.cs" "$HERE/Editor/LeksVerify.Editor.asmdef" "$P/Assets/LeksVerify/Editor/"
VERIFY_OUT="$W/verify.txt" "$UNITY" -batchmode -nographics -projectPath "$P" -executeMethod VerifyPedestal.Run -logFile "$W/verify_editor.log"
cat "$W/verify.txt" 2>/dev/null || { echo "no check output"; grep -E 'error CS' "$W/verify_editor.log" | head; }
rm -rf "$P/Library"
