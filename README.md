# Self-Hosted Personal AI Assistant — ZeroClaw stack

A private AI assistant on your own hardware. **TEE-confidential inference**, the **agent never
holds raw credentials**, **default-deny egress**, and **per-tier least privilege** — wired as
plain Docker Compose, portable from a Mac dev VM to an Unraid prod VM via one cloud-init seed.

This README is the front door. `PROJECT-STATE.md` is the deeper build log (decisions, hard-won
lessons, config templates, open VERIFY items). Read this to run it; read that to understand why.

---

## 1. What it is

Three agent **tiers**, each a separate ZeroClaw container with a hard isolation boundary:

| Tier | Channel | Tools | Credential path | Tool runtime |
|---|---|---|---|---|
| **updates** | WhatsApp (web mode) | Gmail / GCal | `mcp-personal` gateway → OneCLI identity `personal` | native |
| **tasks** | Signal | Amazing Marvin | `mcp-tasks` gateway → OneCLI identity `tasks` | native |
| **unrestricted** | none (CLI/dashboard) | local shell/file/browser/http/git | none (no SaaS creds) | Docker-sandboxed (DinD) |

Supporting services shared by all tiers: **LiteLLM → PrivateMode** (the TEE model path),
**OneCLI** (credential broker / vault), **Squid** (default-deny egress allowlist), and two
**Docker MCP Gateways** (one per sensitive tier).

```
 egress-net (bridge) → INTERNET ── only Squid is attached → the sole way out
        ▲
        │ HTTPS_PROXY (allowlist; no TLS bump)
╔═══════╪═══ assistant-net (internal: true — NO direct internet) ═══════════════╗
║  squid  litellm ─► privatemode-proxy ─(remote enclave, TEE attested)          ║
║  onecli ◄─ mcp-personal ─spawns► [google MCP]   onecli-db                     ║
║          ◄─ mcp-tasks ───spawns► [marvin MCP]                                 ║
║  zeroclaw-updates ─model► litellm   ─tools► mcp-personal                      ║
║  zeroclaw-tasks   ─model► litellm   ─tools► mcp-tasks                         ║
║  zeroclaw-unrestricted ─model► litellm   ─tools► dind-unrestricted ─spawns►   ║
║                                              [assistant-sandbox tool container]║
╚════════════════════════════════════════════════════════════════════════════╝
   admin-net (DEV ONLY, non-internal): dashboards only — onecli/mcp-*/zeroclaw-*
```

Two invariants the design protects:
- **The model path is never intercepted.** ZeroClaw → LiteLLM → PrivateMode runs by service
  name on the internal net (no proxy hop), so enclave attestation/encryption survive.
- **The floor is default-deny.** Everything sits on `assistant-net` (`internal: true`); only
  Squid bridges out, and only to allowlisted hosts.

---

## 2. Files

| Path | What |
|---|---|
| `docker-compose.yml` | the whole stack: supporting services + 3 ZeroClaw tiers + DinD + 3 networks |
| `.env.example` | copy to `.env`; all secrets the compose reads |
| `Dockerfile.sandbox` | builds `assistant-sandbox:1` (uv+py3.13, node LTS+corepack, gh, ripgrep, jq, git, rtk) — the unrestricted tier's tool image |
| `scripts/preflight.sh` | static + runtime validator (+ zero-Anthropic check) |
| `user-data.yaml` | cloud-init: provisions the VM (Multipass dev / Unraid prod) |
| `squid/{squid.conf,allowlist.txt}` | default-deny egress; human-readable allowlist |
| `litellm/config.yaml` | PrivateMode upstream; logging off; **set the real model id** |
| `mcp/personal/*`, `mcp/tasks/*` | per-tier gateway catalog/registry/config/secrets |
| `onecli/VAULT-SETUP.md` | create the two identities + scoped creds + CA export |
| per-tier `config.toml` | lives in each `zeroclaw-<tier>-data` volume (templates in PROJECT-STATE) |

---

## 3. Prerequisites

- Docker Engine + Compose v2 (`docker compose`, not `docker-compose`).
- A **PrivateMode** API key and your real model id.
- For channels: a phone with WhatsApp (web-mode QR linking) and/or `signal-cli` for Signal.
- ~8 GB RAM / 4 vCPU / 40 GB if running in a VM (the DinD + tool images need headroom).

---

## 4. Choose how to run it

There are two dev paths on a Mac. **The VM is recommended** — it removes a whole class of
Docker Desktop quirks and matches prod.

### Path A — Multipass VM (recommended)

```bash
multipass launch 24.04 --name assistant --cpus 4 --memory 8G --disk 40G --cloud-init user-data.yaml
multipass shell assistant
nano /opt/assistant-stack/.env          # fill secrets + set the model id in litellm/config.yaml
bash /opt/bring-up.sh                    # supporting stack → tiers → build sandbox image
```
Why it's better: the `internal: true` dashboards work on-box (no LinuxKit hop, so no
`localhost` refusal), the spawn-containment checks behave like prod, and volumes are native
ext4 (the bind-mount corruption in LESSON #2 doesn't apply). Reach dashboards from the Mac:
```bash
ssh -L 10254:127.0.0.1:10254 -L 3002:127.0.0.1:3002 assistant@<vm-ipv4>   # vm ip: multipass info assistant
# browse http://localhost:10254 (OneCLI) and :3002 (unrestricted) on the Mac
```
Do **not** `multipass mount` your Mac repo and run from it — that's the SSHFS/9p trap; clone
onto the VM (cloud-init does this) and edit there.

### Path B — Docker Desktop directly on the Mac

Works, with one caveat baked into the compose: `assistant-net` is `internal: true`, and
Desktop's host→container forwarder can't route into an internal subnet — so dashboards get
"connection refused". The compose includes a **DEV-ONLY `admin-net`** (a non-internal bridge)
that the dashboard services dual-home onto so the published `127.0.0.1` ports route. Nothing
extra to do — it's already wired. Just know `admin-net` is a dev accommodation, not part of
the prod floor (see §8).

---

## 5. Bring-up (manual, either path)

```bash
cp .env.example .env          # then fill secrets; set the model id in litellm/config.yaml
openssl rand -base64 32       # -> ONECLI_SECRET_ENCRYPTION_KEY (BACK IT UP)

# 1) supporting stack, wait for health
docker compose up -d --wait squid onecli-db onecli litellm privatemode-proxy mcp-personal mcp-tasks

# 2) OneCLI vault — dashboard at http://localhost:10254
#    create 'personal' + 'tasks' identities; Google->personal, Marvin->tasks; export CA.
#    (unrestricted needs NO identity — it brokers no SaaS creds.)

# 3) agent tiers + contained DinD
docker compose up -d dind-unrestricted zeroclaw-updates zeroclaw-tasks zeroclaw-unrestricted

# 4) build the tool sandbox INSIDE the dind (host can't reach dind:2375)
docker exec -i dind-unrestricted docker build -t assistant-sandbox:1 - < Dockerfile.sandbox

# 5) configure each tier (templates in PROJECT-STATE), then validate
docker exec -it zeroclaw-updates zeroclaw doctor      # run FIRST; confirms schema/health
docker exec -it zeroclaw-updates zeroclaw onboard     # write/repair config.toml

# 6) bind channels (interactive)
docker exec -it zeroclaw-updates zeroclaw channel start   # WhatsApp: scan QR (Linked Devices)
docker exec -it zeroclaw-tasks   zeroclaw channel start   # Signal via signal-cli

# 7) verify
bash scripts/preflight.sh
```

---

## 6. Using the assistant

**Dashboards** (web UI — chat, memory, cron, config): OneCLI `:10254`, mcp-personal `:8811`,
mcp-tasks `:8812`, and the tiers at `:3000` (updates) / `:3001` (tasks) / `:3002` (unrestricted).
ZeroClaw gateways require **pairing auth** — grab the one-time code and pair on first load:
```bash
docker logs zeroclaw-updates | grep -i pair
```

**The agents**, three ways:
1. **Channels** (the point): message `updates` on WhatsApp, `tasks` on Signal.
2. **CLI** (how you drive `unrestricted` and do setup): `docker exec -it zeroclaw-<tier> zeroclaw chat`.
3. **Dashboard chat** once paired.

---

## 7. Verification checklist

`scripts/preflight.sh` automates most of this. By hand, the load-bearing ones:
- **Model bypass** — a question to a tier shows in `docker logs litellm`, NOT in `docker logs onecli`.
- **Floor** — `docker run --rm --network assistant-net curlimages/curl -sS http://example.com` must FAIL.
- **5-A spawn containment** — MCP gateways spawn server containers onto `assistant-net` (no internet);
  confirm with `--dry-run --verbose`. DinD fallback if they land on the default bridge.
- **5-A′ dind containment** — a tool container spawned by `dind-unrestricted` cannot reach the internet.
- **Tier scoping** — `tasks` is DENIED Google by OneCLI; `unrestricted` has no SaaS creds at all.
- **Zero Anthropic** — no Anthropic provider in any `config.toml`, none in the Squid allowlist,
  model path is PrivateMode only (preflight's NO-ANTHROPIC phase).

---

## 8. Hardening & the prod path

This pilot favors getting end-to-end working; these are the known gaps to close before prod
(tracked in PROJECT-STATE "What's PENDING"):
- **DinD `2375` is unauthenticated** on `assistant-net` — switch to TLS (`2376`) or a dedicated
  net; consider Sysbox/rootless to drop `privileged: true`.
- **Pin every image by digest** (compose uses `:latest`/tags as placeholders).
- **LiteLLM virtual keys/budgets** need Postgres (deferred; master key meanwhile).
- **Secrets as files** under `/mnt/user/appdata`, not inline.
- **`admin-net` is dev-only** — on the pivot, delete the dashboard `ports:` and put a
  **Tailscale** sidecar on `admin-net` (it dials out, needs no inbound port; gate dashboards by
  tailnet identity). Keep the Tailscale node OFF `assistant-net`.
- **Unraid prod**: deploy the same compose as a Portainer stack, volumes under
  `/mnt/user/appdata/assistant/…`, dashboards on `127.0.0.1` reached via SSH/Tailscale. On a
  real VM the `internal: true` dashboard refusal doesn't occur, so `admin-net` isn't needed.

> The most isolated option is also the most operationally heavy. If your MCP set stays tiny and
> trusted, in-agent MCPs with a per-tier OneCLI identity deliver most of this at lower ops cost —
> the lighter path, documented for when you want it.
