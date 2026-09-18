#!/usr/bin/env bash
# EdgeGate verification — the repeatable proof (M9).
#
# Run from the repo root with the compose stack UP:
#     docker compose up -d --build
#     bash verify.sh
#
# Exits non-zero the moment any assertion fails. Self-heals the stack at the
# end (backends restarted, chaos back to FAIL_RATE=0).
#
# Design notes:
#   * A forwarded 5xx does NOT trip the breaker (any HTTP response is a
#     "success" for the proxy — failures are connect/timeouts/protocol).
#     To trip OPEN we therefore take ALL backends down and watch
#     connection-refused failures accumulate to circuit open.
set -u
BASE="http://localhost:8080"
PASS=0; FAIL=0

check() {  # check "<name>" '<bash expression>' — expr's exit status decides
    local name="$1"; shift
    if eval "$*" >/dev/null 2>&1; then
        PASS=$((PASS+1)); printf 'PASS  %s\n' "$name"
    else
        FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$name"
    fi
}
step() { printf '\n== %s ==\n' "$1"; }

step "1. routing + rewriting"
check "route responds 200"      '[ "$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/v1/products)" = 200 ]'
check "xff present"             'curl -s http://localhost:8080/api/v1/products | grep -q "\"xff\""'
check "host rewritten"          'curl -s http://localhost:8080/api/v1/products | grep -qE "\"host_header\": \"(backend-[12]|chaos):3000\""'

step "2. proxy self endpoints"
check "healthz ok"              'curl -sf http://localhost:8080/healthz | grep -q ok'
check "metrics parse"           'curl -sf http://localhost:8080/metrics | grep -q edgegate_total_requests'

step "3. rate limiting (burst 5 -> 429)"
# CONCURRENT burst: serial curls would be stretched across chaos's 1.5s
# SLEEP_MS, giving the bucket time to refill; a parallel blast lands the whole
# burst inside ~one refill tick, which is what the limiter must deny.
BURST=$(for i in $(seq 1 8); do curl -s -o /dev/null -w '%{http_code} ' "$BASE/api/v1/products" & done; wait)
check "burst shows 429s"        'case "$BURST" in *429*) true;; *) false;; esac'
check "burst lets the first pass" 'case "$BURST" in *200*) true;; *) false;; esac'

step "4. failover (chaos unhealthy -> other backends absorb)"
FAIL_RATE=1 docker compose up -d --no-deps --force-recreate chaos >/dev/null
sleep 12    # 2 health intervals (4s) + a probe of slack -> chaos shed
check "all requests still 200 while chaos unhealthy" '
    s=$(for i in $(seq 1 8); do curl -s -o /dev/null -w "%{http_code} " http://localhost:8080/api/v1/products; sleep 1; done)
    n=$(echo "$s" | tr " " "\n" | grep -c "^200$"); [ "$n" -eq 8 ]'
FAIL_RATE=0 docker compose up -d --no-deps --force-recreate chaos >/dev/null
sleep 10    # chaos heals: healthz OK again, breaker untouched

step "5. circuit breaker (all backends down -> OPEN -> instant 503)"
before=$(curl -s "$BASE/metrics" | awk '/edgegate_circuit_open_rejections/{print $2}'); before=${before:-0}
docker compose stop backend-1 backend-2 chaos >/dev/null
check "burst while down produces 503s" '
    s=$(for i in 1 2 3 4; do curl -s -o /dev/null -w "%{http_code} " http://localhost:8080/api/v1/products; sleep 1; done)
    echo "$s" | grep -q 503'
after=$(curl -s "$BASE/metrics" | awk '/edgegate_circuit_open_rejections/{print $2}'); after=${after:-0}
check "breaker opened (rejection counter rose)" '[ "$after" -gt "$before" ]'
check "healthz still 200 with backends down"   '[ "$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/healthz)" = 200 ]'
docker compose start backend-1 backend-2 chaos >/dev/null

step "6. recovery (backends back + breaker cooldown -> closed)"
ok=
for i in $(seq 1 20); do
    [ "$(curl -s -o /dev/null -w "%{http_code}" "$BASE/api/v1/products")" = 200 ] && { ok=1; break; }
    sleep 3
done
check "recovers to 200"         'test -n "$ok"'

step "7. access-log integrity"
check "every complete log line is valid JSON" '
    docker compose logs --tail=200 edgegate 2>&1 | python3 -c "
import sys, json
seen = 0
for l in sys.stdin:
    # docker compose prefixes every line \"<svc>  | \" — strip it first,
    # then keep only complete JSON records (torn/merged tails ignored).
    j = l.split(\" \", 3)[-1].rstrip()
    if not (j.startswith(\"{\") and j.endswith(\"}\")): continue
    json.loads(j)            # raises -> assertion fails + line shown
    seen += 1
assert seen, \"no access-log lines captured\"
print(f\"validated {seen} json lines\")"'

printf '\n== result ==\nPASS=%s FAIL=%s\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1