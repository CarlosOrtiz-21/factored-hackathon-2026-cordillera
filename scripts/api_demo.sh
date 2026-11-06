#!/usr/bin/env bash
# End-to-end API demo with curl: approve, reject, and expire a card block.
#
# 1) Start the server (short approval TTL so the expiry flow is quick):
#      APPROVAL_TTL_MINUTES=1 uv run uvicorn --factory cardagent.api.main:create_app --port 8000
# 2) In another terminal:
#      bash scripts/api_demo.sh            # all three flows
#      bash scripts/api_demo.sh approve    # just one: approve | reject | expire
#
# Uses the demo codes from settings (customer 246810, auditor 135790) and a demo customer
# picked from data/bank.db. Approved blocks are real in the demo database; reset it with
#   uv run python -m pipelines.etl.run --skip-extract --rebuild-bank
set -euo pipefail
API="${API:-http://localhost:8000}"
FLOWS="${1:-approve reject expire}"

json() { uv run python -c "import json,sys; print(json.load(sys.stdin)$1)"; }
# JSON bodies built in Python with ASCII escapes: on Windows, curl receives command-line
# arguments in the console code page and would corrupt accented characters ("perdí").
body() { uv run python -c "import json,sys; print(json.dumps(dict(a.split('=',1) for a in sys.argv[1:])))" "$@"; }

# Each flow uses a different demo customer whose ONLY credit card is active (synthetic data),
# so an earlier flow's block doesn't affect the next one and there's no "which card?" question.
pick() { uv run python -c "
import sqlite3
rows = sqlite3.connect('data/bank.db').execute(
  \"SELECT customer_id, max(product_number) FROM cards WHERE card_kind='credit' \"
  \"GROUP BY customer_id HAVING count(*) = 1 AND max(product_status) = 'Active' \"
  \"ORDER BY customer_id\").fetchall()
print(*rows[$1])"; }

AUDITOR_TOKEN=$(curl -s -X POST "$API/api/auth/auditor-login" -H 'Content-Type: application/json' \
  -d '{"auditor_id":"auditor-demo","code":"135790"}' | json "['token']")

run_flow() {
  local flow="$1" index="$2"
  echo; echo "================ flow: $flow ================"
  read -r CUSTOMER CARD < <(pick "$index")
  echo "demo customer: $CUSTOMER"
  local login token thread
  login=$(curl -s -X POST "$API/api/auth/login" -H 'Content-Type: application/json' \
    -d "{\"customer_id\":\"$CUSTOMER\",\"code\":\"246810\"}")
  token=$(echo "$login" | json "['token']")
  thread=$(echo "$login" | json "['thread_id']")
  echo "Lía (welcome): $(echo "$login" | json "['welcome']")"

  echo; echo "--- customer: 'perdí mi tarjeta $CARD, bloquéala' (SSE) ---"
  body "thread_id=$thread" "message=perdí mi tarjeta $CARD, bloquéala por favor" \
    | curl -sN -X POST "$API/api/chat/stream" -H "Authorization: Bearer $token" \
        -H 'Content-Type: application/json' --data-binary @-

  echo "--- auditor: pending approvals ---"
  curl -s "$API/api/approvals/pending" -H "Authorization: Bearer $AUDITOR_TOKEN"; echo

  echo "--- customer tries to approve their own request (expect 403) ---"
  curl -s -o /dev/null -w "HTTP %{http_code}\n" -X POST "$API/api/approve" \
    -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
    -d "{\"thread_id\":\"$thread\",\"decision\":\"approved\"}"

  case "$flow" in
    approve|reject)
      local decision; decision=$([ "$flow" = approve ] && echo approved || echo rejected)
      echo "--- auditor decides: $decision ---"
      curl -s -X POST "$API/api/approve" -H "Authorization: Bearer $AUDITOR_TOKEN" \
        -H 'Content-Type: application/json' \
        -d "{\"thread_id\":\"$thread\",\"decision\":\"$decision\",\"comment\":\"demo\"}"; echo
      ;;
    expire)
      echo "--- nobody decides; waiting for the approval to expire (TTL + sweep, ~90 s) ---"
      sleep 95
      curl -s "$API/api/approvals/pending" -H "Authorization: Bearer $AUDITOR_TOKEN" >/dev/null
      ;;
  esac

  echo "--- customer receives the outcome (SSE) ---"
  curl -sN "$API/api/chat/$thread/events" -H "Authorization: Bearer $token"

  echo "--- auditor: audit trail (tokens only, no raw PII) ---"
  curl -s "$API/api/threads/$thread/state" -H "Authorization: Bearer $AUDITOR_TOKEN" \
    | json "['audit_trail'][-4:]"
}

i=0
for flow in $FLOWS; do run_flow "$flow" "$i"; i=$((i + 1)); done
