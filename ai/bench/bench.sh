#!/usr/bin/env bash
# Benchmark script for llama-server
# Usage: ./bench.sh <machine-name> [server-url]
# Example: ./bench.sh rigel http://localhost:8080

set -euo pipefail

MACHINE="${1:?Usage: ./bench.sh <machine-name> [server-url]}"
SERVER="${2:-http://localhost:8080}"
RESULTS_DIR="$(dirname "$0")/results"
PROMPTS_DIR="$(dirname "$0")/prompts"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
OUTFILE="${RESULTS_DIR}/${MACHINE}-${TIMESTAMP}.json"

mkdir -p "$RESULTS_DIR"

echo "=== Benchmarking $MACHINE against $SERVER ==="
echo "Results will be saved to $OUTFILE"

# Check server is up
if ! curl -sf "$SERVER/health" > /dev/null 2>&1; then
  echo "ERROR: Server not responding at $SERVER/health"
  exit 1
fi

# Get model info
MODEL_INFO=$(curl -sf "$SERVER/v1/models" || echo '{"data":[{"id":"unknown"}]}')
MODEL_ID=$(echo "$MODEL_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null || echo "unknown")
echo "Model: $MODEL_ID"

results="[]"

for prompt_file in "$PROMPTS_DIR"/*.txt; do
  prompt_name=$(basename "$prompt_file" .txt)
  prompt_content=$(cat "$prompt_file")
  echo ""
  echo "--- Running prompt: $prompt_name ---"

  # Build the request
  request=$(python3 -c "
import json
print(json.dumps({
    'model': '$MODEL_ID',
    'messages': [{'role': 'user', 'content': $(python3 -c "import json; print(json.dumps(open('$prompt_file').read()))")}],
    'max_tokens': 512,
    'temperature': 0.7,
    'stream': False
}))
")

  # Time the request
  start_time=$(python3 -c "import time; print(time.time())")

  response=$(curl -sf "$SERVER/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "$request" 2>&1) || {
    echo "  FAILED: $response"
    continue
  }

  end_time=$(python3 -c "import time; print(time.time())")

  # Parse response
  eval_result=$(python3 -c "
import json, sys

resp = json.loads('''$response''') if len('''$response''') < 10000 else json.loads(sys.stdin.read())
usage = resp.get('usage', {})
content = resp.get('choices', [{}])[0].get('message', {}).get('content', '')
prompt_tokens = usage.get('prompt_tokens', 0)
completion_tokens = usage.get('completion_tokens', 0)
total_time = $end_time - $start_time

print(json.dumps({
    'prompt': '$prompt_name',
    'prompt_tokens': prompt_tokens,
    'completion_tokens': completion_tokens,
    'total_time_s': round(total_time, 2),
    'tok_per_s': round(completion_tokens / total_time, 1) if total_time > 0 else 0,
    'response_preview': content[:200]
}))
" 2>/dev/null || echo '{"error": "parse failed"}')

  echo "  $eval_result"

  results=$(python3 -c "
import json
r = json.loads('$results') if '$results' != '[]' else []
r.append(json.loads('''$eval_result'''))
print(json.dumps(r))
")
done

# Write final results
python3 -c "
import json
output = {
    'machine': '$MACHINE',
    'model': '$MODEL_ID',
    'timestamp': '$TIMESTAMP',
    'server': '$SERVER',
    'results': json.loads('''$results''')
}
with open('$OUTFILE', 'w') as f:
    json.dump(output, f, indent=2)
print()
print('=== Results saved to $OUTFILE ===')
"
