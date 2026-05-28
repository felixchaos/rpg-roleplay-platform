#!/usr/bin/env bash
# Headless smoke test of the frontend ↔ backend wiring.
# Probes the static server and the REST surface the JSX expects.
set -u

BASE_FE="http://127.0.0.1:5173"
BASE_BE="http://127.0.0.1:7860"
COOKIE="$(mktemp /tmp/rpg-cookies.XXXX)"
trap 'rm -f "$COOKIE"' EXIT

PASS=0; FAIL=0; SKIP=0

color() { case "$1" in ok) printf "\033[32m%s\033[0m" "$2";; bad) printf "\033[31m%s\033[0m" "$2";; warn) printf "\033[33m%s\033[0m" "$2";; *) printf "%s" "$2";; esac; }

run() {
  # run TAG METHOD URL [BODY] [EXPECT_CODE]
  local tag="$1" method="$2" url="$3" body="${4:-}" expect="${5:-200}"
  local code
  if [ -n "$body" ]; then
    code=$(curl -sS -o /dev/null -m 6 -w '%{http_code}' -X "$method" "$url" \
      -H 'Content-Type: application/json' -d "$body" -b "$COOKIE" -c "$COOKIE")
  else
    code=$(curl -sS -o /dev/null -m 6 -w '%{http_code}' -X "$method" "$url" -b "$COOKIE" -c "$COOKIE")
  fi
  if [ "$code" = "$expect" ]; then
    color ok "  PASS"; printf " %-7s %-32s -> %s\n" "$tag" "$method $url" "$code"
    PASS=$((PASS+1))
  else
    color bad "  FAIL"; printf " %-7s %-32s -> %s (expected %s)\n" "$tag" "$method $url" "$code" "$expect"
    FAIL=$((FAIL+1))
  fi
}

echo "--- Static frontend assets (port 5173)"
run static GET "$BASE_FE/Login.html"
run static GET "$BASE_FE/Platform.html"
run static GET "$BASE_FE/Game%20Console.html"
run static GET "$BASE_FE/src/api-client.js"
run static GET "$BASE_FE/src/data-loader.js"
run static GET "$BASE_FE/src/platform-app.jsx"
run static GET "$BASE_FE/src/game-app.jsx"
run static GET "$BASE_FE/src/mock-data.js"

echo "--- Backend health (anonymous)"
run health GET "$BASE_BE/api/auth/me"
run health GET "$BASE_BE/api/platform"
run health GET "$BASE_BE/api/platform/commands"

echo "--- Auth lifecycle"
# random username so we don't collide with prior runs
SUFFIX=$(date +%s)
USER="smoke_$SUFFIX"
run auth POST "$BASE_BE/api/auth/register" "{\"username\":\"$USER\",\"password\":\"smoke_pw_2026_long\",\"display_name\":\"Smoke\"}"
run auth GET  "$BASE_BE/api/auth/me"
run auth GET  "$BASE_BE/api/me/profile"

echo "--- Authed feature endpoints (existing api.py)"
run feat GET "$BASE_BE/api/scripts"
run feat GET "$BASE_BE/api/saves"
run feat GET "$BASE_BE/api/library"
run feat GET "$BASE_BE/api/settings"
run feat GET "$BASE_BE/api/me/character-cards"
run feat GET "$BASE_BE/api/me/credentials"
run feat GET "$BASE_BE/api/me/personas"
run feat GET "$BASE_BE/api/state"
run feat GET "$BASE_BE/api/models"
run feat GET "$BASE_BE/api/tools"
run feat GET "$BASE_BE/api/worldline/variables"
run feat GET "$BASE_BE/api/memories"

echo "--- Authed feature endpoints (frontend_routes.py)"
run new GET "$BASE_BE/api/search?q=test"
run new GET "$BASE_BE/api/auth/sessions"
run new GET "$BASE_BE/api/auth/login-history"
run new GET "$BASE_BE/api/plugins"
run new POST "$BASE_BE/api/profile/visibility" '{"email":"self","phone":"self"}'
run new POST "$BASE_BE/api/me/preference" '{"two_fa":true,"email_notif":true}'
run new POST "$BASE_BE/api/auth/password" '{"current":"smoke_pw_2026_long","next":"smoke_pw_2026_long_v2"}'
run new POST "$BASE_BE/api/auth/sms-code" '{"phone":"+8612345678901"}'
run new POST "$BASE_BE/api/auth/sms-verify" '{"phone":"+8612345678901","code":"123456"}'
run new POST "$BASE_BE/api/account/export" '{"scope":"all","format":"zip","email":"test@example.com"}'
run new POST "$BASE_BE/api/models/validate" '{"api_id":"openai"}'

echo "--- Branches: anon 401, owner-of-nothing -> empty list 200 (前端 BranchesPage 在没存档时不应再发 /api/branches/{id})"
# 现在新注册用户没存档，/api/saves 是空 → 前端不发 /api/branches/...
SAVES_BODY=$(curl -sS -m 6 -b "$COOKIE" "$BASE_BE/api/saves")
echo "    /api/saves payload (first 120 chars): ${SAVES_BODY:0:120}"
# 跨用户 / 不存在的 save_id 仍应 4xx（前端 mock id 11 即此场景，证明 gate 在后端是稳的）
run guard GET "$BASE_BE/api/branches/999999" "" 403

echo "--- Sessions cleanup"
run dn POST "$BASE_BE/api/auth/logout"

echo
echo "Results: $(color ok PASS=$PASS)  $(color bad FAIL=$FAIL)"
[ "$FAIL" = "0" ] && exit 0 || exit 1
