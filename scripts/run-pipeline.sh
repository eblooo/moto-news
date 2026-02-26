#!/usr/bin/env bash
# Moto-news pipeline with detailed logs.
# Usage: AGGR_URL=http://moto-news-svc:80 ./scripts/run-pipeline.sh
# Set AGGR_URL for your aggregator (default: http://moto-news-svc:80).

set -e
AGGR_URL="${AGGR_URL:-http://moto-news-svc:80}"
CONNECT_TIMEOUT="${CONNECT_TIMEOUT:-15}"
TRANSLATE_TIMEOUT="${TRANSLATE_TIMEOUT:-3600}"

echo "=== Moto-news pipeline $(date -u '+%a %b %d %H:%M:%S UTC %Y') ==="
echo "Aggregator: $AGGR_URL"
echo ""

# Print JSON response and each line of .data.log for detail
print_response() {
  local raw="$1"
  if command -v jq >/dev/null 2>&1; then
    echo "$raw" | jq -C . 2>/dev/null || echo "$raw"
    echo "$raw" | jq -r '.data.log[]? // empty' 2>/dev/null | while IFS= read -r line; do
      echo "  $line"
    done
  else
    echo "$raw"
  fi
  echo ""
}

# Health check
echo "--- Health check ---"
health=$(curl -sS -f --connect-timeout "$CONNECT_TIMEOUT" "$AGGR_URL/health" 2>&1) || {
  echo "ERROR: aggregator not reachable ($AGGR_URL)"
  exit 1
}
print_response "$health"

# Fetch
echo "--- Fetch ---"
fetch_resp=$(curl -sS -f -X POST "$AGGR_URL/api/fetch" 2>&1)
print_response "$fetch_resp"

# Translate
echo "--- Translate ---"
translate_resp=$(curl -sS -f -X POST "$AGGR_URL/api/translate?limit=5" --max-time "$TRANSLATE_TIMEOUT" 2>&1)
print_response "$translate_resp"
if command -v jq >/dev/null 2>&1; then
  last_err=$(echo "$translate_resp" | jq -r '.data.last_error // empty')
  if [ -n "$last_err" ]; then
    echo "  last_error: $last_err"
    echo ""
  fi
fi

# Publish
echo "--- Publish ---"
publish_resp=$(curl -sS -f -X POST "$AGGR_URL/api/publish?limit=50" 2>&1)
print_response "$publish_resp"

# Stats
echo "--- Stats ---"
stats_resp=$(curl -sS -f "$AGGR_URL/api/stats" 2>&1)
print_response "$stats_resp"

echo "=== Done ==="
