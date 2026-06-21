#!/usr/bin/env bash
# =============================================================================
# preflight.sh — run from the stack dir. Two phases:
#   (1) STATIC  : checks you can run BEFORE `docker compose up` (files, .env, config)
#   (2) RUNTIME : checks AFTER bring-up (container health, floor, dashboards, no-Anthropic)
# Usage:  bash scripts/preflight.sh          (runs both; runtime checks skip if stack down)
#         bash scripts/preflight.sh static    (static only)
# Exit non-zero if any HARD check fails. WARN items are advisory.
# =============================================================================
set -uo pipefail
# find repo root = nearest dir containing docker-compose.yml (works from root or scripts/)
d="$(cd "$(dirname "$0")" && pwd)"
while [ "$d" != "/" ] && [ ! -f "$d/docker-compose.yml" ]; do d="$(dirname "$d")"; done
[ -f "$d/docker-compose.yml" ] && cd "$d" || { echo "[FAIL] can't locate docker-compose.yml"; exit 1; }

PASS=0; FAIL=0; WARN=0
ok()   { echo "  [ OK ] $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN] $*"; WARN=$((WARN+1)); }
have() { command -v "$1" >/dev/null 2>&1; }

echo "== STATIC =="

# --- compose file present + parses ------------------------------------------
if [ -f docker-compose.yml ]; then ok "docker-compose.yml present"; else bad "docker-compose.yml missing"; fi
if have docker; then
  if docker compose config >/dev/null 2>&1; then ok "compose config parses (vars resolve)"
  else bad "compose config invalid — run: docker compose config"; fi
else warn "docker not on PATH — skipping compose validation"; fi

# --- .env present + no leftover placeholders --------------------------------
if [ -f .env ]; then
  ok ".env present"
  if grep -q 'REPLACE\|change-me' .env; then bad ".env still has REPLACE/change-me placeholders"; else ok ".env placeholders filled"; fi
  # key length sanity for the vault encryption key
  k=$(grep -E '^ONECLI_SECRET_ENCRYPTION_KEY=' .env | cut -d= -f2-)
  if [ "${#k}" -ge 40 ]; then ok "ONECLI_SECRET_ENCRYPTION_KEY looks like base64-32"; else bad "ONECLI_SECRET_ENCRYPTION_KEY too short (use: openssl rand -base64 32)"; fi
else bad ".env missing — cp .env.example .env"; fi

# --- bind-mount sources the compose expects (missing FILE -> docker makes an empty DIR) ---
for f in squid/squid.conf squid/allowlist.txt litellm/config.yaml Dockerfile.sandbox; do
  if [ -f "$f" ]; then ok "found $f"; else bad "missing $f (compose bind-mount / build will break)"; fi
done
for d in mcp/personal mcp/tasks; do
  if [ -d "$d" ]; then ok "found $d/"; else warn "missing $d/ (updates/tasks tiers won't work until populated)"; fi
done

# --- image tags that will fail to pull (placeholders) -----------------------
imgvals() { grep -E '^[[:space:]]*image:' docker-compose.yml | sed 's/#.*//'; }
if imgvals | grep -qE ':v[0-9]+\.[0-9]+\.x'; then bad "a '.x' image tag (e.g. v0.5.x) — not a real tag, pull will fail"; else ok "no '.x' placeholder image tags"; fi
if imgvals | grep -qi 'PLACEHOLDER'; then bad "a PLACEHOLDER image ref — replace before up"; else ok "no PLACEHOLDER image refs"; fi

# --- model id placeholder still in litellm config ---------------------------
if [ -f litellm/config.yaml ] && grep -qi 'PLACEHOLDER' litellm/config.yaml; then
  warn "litellm/config.yaml still has PLACEHOLDER model id — set the real PrivateMode model"
fi

# --- squid allowlist must cover the registry the dind pulls from ------------
if [ -f squid/allowlist.txt ]; then
  if grep -qiE 'docker\.io|ghcr\.io' squid/allowlist.txt; then ok "allowlist includes a container registry (dind pulls)"
  else warn "allowlist has no docker.io/ghcr.io — dind sandbox-image pull will be DENIED"; fi
fi

if ! have docker; then echo; echo "static: PASS=$PASS FAIL=$FAIL WARN=$WARN"; exit $([ $FAIL -eq 0 ] && echo 0 || echo 1); fi
[ "${1:-}" = "static" ] && { echo; echo "static: PASS=$PASS FAIL=$FAIL WARN=$WARN"; exit $([ $FAIL -eq 0 ] && echo 0 || echo 1); }

echo "== RUNTIME =="
NET=assistant-net
curlnet() { docker run --rm --network "$NET" curlimages/curl:latest -sS --max-time 8 "$@"; }

# --- containers up? ---------------------------------------------------------
for c in squid onecli-db onecli litellm privatemode-proxy mcp-personal mcp-tasks \
         dind-unrestricted zeroclaw-updates zeroclaw-tasks zeroclaw-unrestricted; do
  st=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "absent")
  case "$st" in
    running) ok "$c running" ;;
    absent)  bad "$c absent (never created — image pull? see: docker compose ps -a $c)" ;;
    *)       bad "$c is '$st' (see: docker logs $c)" ;;
  esac
done

# --- data plane reachable by service name -----------------------------------
curlnet http://onecli:10254 >/dev/null 2>&1 && ok "onecli serving (in-net)"   || bad "onecli not serving in-net (docker logs onecli)"
curlnet http://litellm:4000/health >/dev/null 2>&1 && ok "litellm health (in-net)" || warn "litellm /health no answer (VERIFY endpoint path)"

# --- THE FLOOR: a non-allowlisted host MUST be denied -----------------------
if curlnet http://example.com >/dev/null 2>&1; then bad "FLOOR BREACH: example.com reachable from assistant-net (should be denied)"
else ok "floor holds: example.com denied from assistant-net"; fi

# --- dind containment: tool containers must have no internet ----------------
if docker exec dind-unrestricted docker run --rm curlimages/curl:latest -sS --max-time 8 http://example.com >/dev/null 2>&1; then
  bad "dind tool containers can reach the internet (containment broken)"
else ok "dind tool containers have no internet (contained)"; fi
docker exec dind-unrestricted docker images 2>/dev/null | grep -q assistant-sandbox \
  && ok "assistant-sandbox image present in dind" || warn "assistant-sandbox not built in dind yet (step 4b)"
# verify the sandbox actually has the requested toolset (--version, no network needed)
if docker exec dind-unrestricted docker image inspect assistant-sandbox:1 >/dev/null 2>&1; then
  if docker exec dind-unrestricted docker run --rm --network none assistant-sandbox:1 \
       sh -c 'command -v uv && command -v gh && command -v rg && command -v jq && command -v rtk && command -v node && command -v pnpm && python3 --version' >/dev/null 2>&1; then
    ok "sandbox toolset present (uv, gh, rg, jq, rtk, node, pnpm, python3.13)"
  else warn "sandbox missing one of uv/gh/rg/jq/rtk/node/pnpm/pnpm/node — check Dockerfile.sandbox build logs (rtk PATH is the usual culprit)"; fi
fi

# --- dashboards published on host loopback ----------------------------------
for p in 10254 3000 3001 3002; do
  if have nc && nc -z 127.0.0.1 "$p" 2>/dev/null; then ok "dashboard port $p open on 127.0.0.1"
  elif curl -sS --max-time 4 "http://127.0.0.1:$p" >/dev/null 2>&1; then ok "dashboard $p answers on 127.0.0.1"
  else warn "dashboard $p not reachable on host loopback (admin-net attached? on Mac Desktop see PROJECT-STATE)"; fi
done

# --- ZERO ANTHROPIC EGRESS (rewritten for ZeroClaw — no Claude Code, no sk-ant) ---
echo "== NO-ANTHROPIC =="
if grep -riq 'anthropic' litellm/config.yaml 2>/dev/null; then bad "litellm config references anthropic — model path must be PrivateMode only"; else ok "litellm config has no anthropic route"; fi
if [ -f squid/allowlist.txt ] && grep -qi 'anthropic' squid/allowlist.txt; then bad "squid allowlist permits anthropic — remove it"; else ok "squid allowlist excludes anthropic"; fi
# zeroclaw config lives in the data volume; check no anthropic provider configured
for t in updates tasks unrestricted; do
  if docker exec "zeroclaw-$t" sh -c 'cat /data/config.toml 2>/dev/null' | grep -qi 'anthropic'; then
    bad "zeroclaw-$t config.toml references anthropic provider"
  else ok "zeroclaw-$t: no anthropic provider"; fi
done

echo
echo "RESULT: PASS=$PASS FAIL=$FAIL WARN=$WARN"
exit $([ $FAIL -eq 0 ] && echo 0 || echo 1)
