#!/usr/bin/env bash
# Ask the running API a farmer question, the way the app does.
#
#   scripts/pilot_chat.sh "onion price in Rahuri from 1 to 9 September"
#   scripts/pilot_chat.sh --lang en "what is the mandi rate for potato in Pune"
#
# Unlike scripts/pilot_mandi_check.py, which calls get_mandi_prices directly,
# this goes through the model: it is the model that decides to load the mandi
# skill, geocode the place name and call the tool. Nothing here names a tool.
#
# Needs the stack up (docker compose -f docker-compose.yml -f
# docker-compose.pilot.yml up -d) and private_key.pem next to this repo -- the
# same key app/auth/jwt_auth.py verifies against. Both are local-only.
set -euo pipefail

cd "$(dirname "$0")/.."

LANG_CODE=hi
SESSION="pilot-$$"
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --lang)    LANG_CODE="$2"; shift 2 ;;
    --session) SESSION="$2";   shift 2 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

QUERY="${1:-onion price in Rahuri from 1 to 9 September}"

# Minted inside the container so the key never leaves it and the host needs no
# Python deps. Ten minutes is long enough for one question and short enough
# that a copied token is not worth keeping.
TOKEN=$(docker exec sva_app python -c '
import jwt, time
from cryptography.hazmat.primitives import serialization
with open("/app/private_key.pem", "rb") as fh:
    key = serialization.load_pem_private_key(fh.read(), password=None)
print(jwt.encode({
    "sub": "pilot-tester",
    "mobile": "9999999999",
    "name": "Pilot Tester",
    "channel": "BharatVistaar",
    "is_guest_user": False,
    "exp": int(time.time()) + 600,
}, key, algorithm="RS256"))
')

echo "> $QUERY"
echo

curl -sN -G "http://localhost:8000/api/chat/" \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "query=$QUERY" \
  --data-urlencode "session_id=$SESSION" \
  --data-urlencode "source_lang=$LANG_CODE" \
  --data-urlencode "target_lang=$LANG_CODE"
echo
