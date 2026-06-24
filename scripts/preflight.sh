#!/usr/bin/env bash
# preflight.sh — static + runtime checks for the assistant stack.
# Sets COMPOSE_FILE if docker-compose.tiers.yml exists.
set -uo pipefail
d="$(cd "$(dirname "$0")" && pwd)"
while [ "$d" != "/" ] && [ ! -f "$d/docker-compose.yml" ]; do d="$(dirname "$d")"; done
[ -f "$d/docker-compose.yml" ] && cd "$d" || { echo "[FAIL] can't locate stack root"; exit 1; }
[ -f docker-compose.tiers.yml ] && export COMPOSE_FILE="docker-compose.yml:docker-compose.tiers.yml"

PASS=0; FAIL=0; WARN=0
ok()   { echo "  [ OK ] $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN+1)); }
have() { command -v "$1" >/dev/null 2>&1; }
read_tiers() { python3 -c "import yaml,itertools; cfg=yaml.safe_load(open('tiers.yaml')); print(' '.join(t['name'] for t in cfg.get('tiers',[])))" 2>/dev/null; }
read_tier_val() { python3 -c "import yaml; cfg=yaml.safe_load(open('tiers.yaml')); print([t['$2'] for t in cfg['tiers'] if t['name']=='$1'][0])" 2>/dev/null; }

echo "== STATIC =="
[ -f docker-compose.yml ] && ok "docker-compose.yml present" || bad "missing"
[ -f docker-compose.tiers.yml ] && ok "docker-compose.tiers.yml present" || warn "missing (run: scripts/generate-tiers.py)"
[ -f tiers.yaml ] && ok "tiers.yaml present" || warn "missing (default tiers not created)"
if have docker && docker compose config >/dev/null 2>&1; then ok "compose config parses"
else bad "compose config invalid — check vars resolve"; fi

[ -f .env ] && ok ".env present" || bad "missing (cp .env.example .env)"
grep -q 'REPLACE\|change-me' .env && bad ".env has placeholders" || ok ".env placeholders filled"
k=$(grep -E '^ONECLI_SECRET_ENCRYPTION_KEY=' .env | cut -d= -f2-)
[ "${#k}" -ge 40 ] && ok "ONECLI_SECRET_ENCRYPTION_KEY looks right" || bad "ONECLI_SECRET_ENCRYPTION_KEY too short"
for f in squid/squid.conf squid/allowlist.txt litellm/config.yaml Dockerfile.sandbox; do
  [ -f "$f" ] && ok "found $f" || bad "missing $f"
done

# Check MCP dirs from tiers.yaml
if [ -f tiers.yaml ]; then
  for d in $(python3 -c "
import yaml,os; c=yaml.safe_load(open('tiers.yaml'))
for t in c.get('tiers',[]):
  if t.get('mcp'): print(t.get('mcp_dir','mcp/'+t['name']))
" 2>/dev/null); do
    [ -d "$d" ] && ok "mcp dir $d/" || warn "mcp dir $d/ missing (tier won't have tools)"
  done
fi

imgvals() { grep -E '^[[:space:]]*image:' docker-compose.yml docker-compose.tiers.yml 2>/dev/null | sed 's/#.*//'; }
imgvals | grep -qE ':v[0-9]+\.[0-9]+\.x' && bad "'.x' image tag" || ok "no .x tags"
imgvals | grep -qi 'PLACEHOLDER' && bad "PLACEHOLDER image ref" || ok "no PLACEHOLDER refs"
grep -qi 'PLACEHOLDER' litellm/config.yaml 2>/dev/null && warn "litellm/config.yaml has PLACEHOLDER model id"
grep -qiE 'docker\.io|ghcr\.io' squid/allowlist.txt 2>/dev/null && ok "allowlist has registry (dind pulls)" || warn "allowlist has no registry"

! have docker && exit $([ $FAIL -eq 0 ] && echo 0 || echo 1)
[ "${1:-}" = "static" ] && exit $([ $FAIL -eq 0 ] && echo 0 || echo 1)

echo "== RUNTIME =="
NET=assistant-net
curlnet() { docker run --rm --network "$NET" curlimages/curl:latest -sS --max-time 8 "$@"; }
TIERS=$(read_tiers)

# --- discover containers dynamically ---
echo "  (discovering containers from docker ps + tiers.yaml)"
for c in squid onecli-db onecli litellm privatemode-proxy; do
  docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null | grep -q running && ok "$c running" || bad "$c not running"
done
TOTAL=0
for c in $(docker ps --format '{{.Names}}' | grep -E '^(zeroclaw-|mcp-|dind-)' || true); do
  docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null | grep -q running && ok "$c running" || bad "$c not running"
  TOTAL=$((TOTAL+1))
done
[ "$TOTAL" -gt 0 ] || warn "no tier containers (zeroclaw-/mcp-/dind-) found"

# --- onecli health ---
docker inspect onecli --format '{{.State.Health.Status}}' 2>/dev/null | grep -q healthy && ok "onecli healthy" \
  || { curlnet http://onecli:10254 >/dev/null 2>&1 && ok "onecli serving" || bad "onecli not serving"; }
curlnet http://litellm:4000/health >/dev/null 2>&1 && ok "litellm healthy" || warn "litellm /health no answer"

# --- CA cert ---
docker exec onecli test -f /data/ca/ca.pem 2>/dev/null && ok "OneCLI CA cert exported" || bad "CA cert missing"

# --- MCP gateways ---
for gw in $(docker ps --format '{{.Names}}' | grep '^mcp-' || true); do
  env=$(docker inspect "$gw" --format '{{range .Config.Env}}{{.}}{{"\n"}}{{end}}' 2>/dev/null)
  echo "$env" | grep -q MCP_GATEWAY_AUTH_TOKEN && ok "$gw MCP_GATEWAY_AUTH_TOKEN" || bad "$gw missing MCP_GATEWAY_AUTH_TOKEN"
  echo "$env" | grep -q '@onecli:10255' && ok "$gw proxy auth" || bad "$gw missing proxy auth"
  cmd=$(docker inspect "$gw" --format '{{range .Config.Cmd}}{{.}} {{end}}' 2>/dev/null)
  case "$cmd" in *--block-network*) ok "$gw --block-network (tool net containment)" ;; *) warn "$gw missing --block-network (tool containers may reach the network)" ;; esac
done

# --- floor (no internet from assistant-net) ---
curlnet http://example.com >/dev/null 2>&1 && bad "FLOOR BREACH: example.com reachable" || ok "floor holds"

# --- DinD containment + sandbox ---
for dind in $(docker ps --format '{{.Names}}' | grep '^dind-' || true); do
  docker exec "$dind" docker run --rm curlimages/curl:latest -sS --max-time 8 http://example.com >/dev/null 2>&1 \
    && bad "$dind tool containers have internet (containment broken)" || ok "$dind tool containers contained"
  docker exec "$dind" docker images 2>/dev/null | grep -q assistant-sandbox \
    && ok "assistant-sandbox image in $dind" || warn "assistant-sandbox not built in $dind"
done

# --- CA readable by identity tiers ---
for zc in $(docker ps --format '{{.Names}}' | grep '^zeroclaw-' || true); do
  docker exec "$zc" sh -c 'test -f ${SSL_CERT_FILE:-/certs/onecli/ca.pem}' 2>/dev/null \
    && ok "$zc can read CA cert" || warn "$zc missing CA cert (expected if no identity)"
done

# --- dashboard ports ---
for p in 10254 $(python3 -c "
import yaml; cfg=yaml.safe_load(open('tiers.yaml'))
for t in cfg.get('tiers',[]): print(t.get('port',3000))
" 2>/dev/null); do
  (have nc && nc -z 127.0.0.1 "$p" 2>/dev/null) || curl -sS --max-time 4 "http://127.0.0.1:$p" >/dev/null 2>&1 \
    && ok "dashboard $p open" || warn "dashboard $p not reachable on loopback"
done

# --- NO ANTHROPIC ---
grep -riq 'anthropic' litellm/config.yaml 2>/dev/null && bad "litellm config has anthropic" || ok "litellm: no anthropic"
grep -qi 'anthropic' squid/allowlist.txt 2>/dev/null && bad "allowlist permits anthropic" || ok "allowlist: no anthropic"
for zc in $(docker ps --format '{{.Names}}' | grep '^zeroclaw-' || true); do
  docker exec "$zc" sh -c 'cat /data/config.toml 2>/dev/null' 2>/dev/null | grep -qi 'anthropic' \
    && bad "$zc config has anthropic" || ok "$zc: no anthropic"
done

echo
echo "RESULT: PASS=$PASS FAIL=$FAIL WARN=$WARN"
exit $([ $FAIL -eq 0 ] && echo 0 || echo 1)
