# Secure Assistant Stack

![status](https://img.shields.io/badge/status-pilot-orange) ![platform](https://img.shields.io/badge/platform-macOS%20%C2%B7%20Multipass-blue)

**A security-hardened way to run ZeroClaw — agents that can't exfiltrate your credentials or call home, by construction, not by policy.**
> Composes proven open-source components under one default-deny roof. Adds a tiered identity 
> model and one-command bring-up on top. Does **not** reinvent sandboxing or secret injection.

> Built on [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw) — a self-hosted personal LLM assistant runtime.

## What you get

ZeroClaw running in an isolated VM on your own machine, reachable over WhatsApp, Telegram, 
Signal, or Matrix. It can manage your email, tasks, and calendar — but it never holds your 
API keys directly, can't open arbitrary URLs, and can't phone home to a model provider 
without going through an allowlisted egress proxy first.

Three tiers ship by default: **updates** (Gmail + channels (ie. whatsapp), your personal identity), 
**tasks** (Marvin + channels (ie. whatsapp), a separate identity), and **unrestricted** (shell and file 
tools, sandboxed in Docker-in-Docker with no SaaS credentials). Each tier is its own 
container with its own blast radius.

## Quick Start

```bash
brew install multipass
./launch-multipass.sh --local && multipass shell assistant
```

## Why

Simon Willison's **lethal trifecta for Agents** consists of: access to private data, exposure to untrusted content,  and the ability to communicate externally.

Most agent setups hand it all three. This stack tries to remove two of the three **by construction** — the agent has **no direct internet** (every byte goes through an allowlist) and **no raw credentials** (the vault injects per-request, scoped tokens).

**Scope.** A single-user, self-hosted pilot: dev on macOS + Multipass today, aimed at a permanent Linux/ProxMox/NAS VM tomorrow. It is **not** hardened for multi-tenant or hostile use yet. Full threat model in [docs/SECURITY.md](docs/SECURITY.md).

**Status:** pilot — validated on macOS + Multipass. Other platforms (Multipass-on-Linux, libvirt/VirtualBox, Docker-on-host) are untested, and Tailscale-based dashboard access is planned. See [Usage options](#usage-options-not-yet-tested).

## Features

- **Default-deny egress** — no host internet; every outbound packet is evaluated against `squid/allowlist.txt`.
- **Zero raw credentials** — OneCLI injects scoped, per-request tokens at the proxy layer; the agent only ever sees placeholders.
- **TEE-confidential inference** — model calls route through a hardware enclave (PrivateMode), not a plain API.
- **Tiered identities** — N agents, each with its own identity, Model Context Protocol (MCP) tools, command allowlist, and blast radius. Three ship by default: `updates`, `tasks`, `unrestricted`.
- **Contained tool sandbox** — the unrestricted tier runs shell/file tools inside a sandboxed Docker-in-Docker (DinD) daemon with no external network.
- **One-command bring-up** — `launch-multipass.sh --local` provisions the VM; `bring-up.sh` starts and wires the whole stack.
- **Verifiable by design** — `scripts/preflight.sh` asserts the floor holds, certs are mounted, MCP gateways are authenticated, and the egress floor holds (no direct model-provider or internet leak).

## Contents

#### [Why](#why) · [Usage](#usage) · [Architecture](#architecture) · [Configuration](#configuration)
#### [VM details](#vm-details) · [Usage options](#usage-options-not-yet-tested) · [Roadmap](#roadmap) · [Contributing](#contributing) · [License](#license)
### Other Docs
#### [Security model](docs/SECURITY.md) · [Comparison with similar projects](docs/COMPARISON.md)

---

## Usage

### Requirements

- [Multipass](https://multipass.run) – `brew install multipass`. Linux VM options below.
- [PrivateMode](https://privatemode.ai) API key – LiteLLM can fall back to any less-secure OpenAI-compatible endpoint without it
- SaaS credentials (Gmail, Marvin, etc.) – Needed only for identity-bearing tiers (updates, tasks)

The whole stack comes up with one VM + one script. Steps 1–3 run on your Mac; steps 4–6 run inside the VM as the `assistant` service account.

### 1. Clone the Repo

```bash
git clone https://github.com/bgheneti/secure-assistant-stack.git
cd secure-assistant-stack
```

### 2. Configure
```bash
cp .env.example .env      # fill in the placeholders (encryption key, API keys…)
$EDITOR tiers.yaml        # optional: add/remove tiers, identities, ports, tools
```
> the 3 default tiers work out of the box
### 3. Setup the VM
```bash
./launch-multipass.sh --local
```

### 4. Start the stack
```bash
multipass exec assistant -- sudo -iu assistant bash /opt/bring-up.sh
```
### 5. Verify

```bash
#goal: FAIL=0
multipass exec assistant -- sudo -iu assistant bash /opt/assistant-stack/scripts/preflight.sh
# agent + MCP health    
multipass exec assistant -- sudo -iu assistant \
  bash -c "cd /opt/assistant-stack && docker compose exec zeroclaw-updates zeroclaw doctor"
```

### 6. Use your ZeroClaw Stack as an admin 🌟 
```bash
multipass info assistant # to get vm-ip
ssh -L 10254:127.0.0.1:10254 -L 3000:127.0.0.1:3000 ubuntu@<vm-ip> # add -L 300X:127.0.0.1:300X to use other tiers
```

#### A. Chat with your zeroclaw agents via the dashboard
Navigate to http://localhost:3000 in your browser to use ZeroClaw tier 1 (updates) agents

#### B. Add API credentials to your Vault (Gmail, Marvin, etc)
Navigate to http://localhost:10254
> Settings → Connections: connect Gmail -> 'personal'; paste Marvin API key -> 'tasks' 

#### C. Pair a Channel with your phone, after adding it in the zeroclaw dashboard
>`multipass exec assistant -- sudo -iu assistant docker exec zeroclaw-updates zeroclaw channel start`

## Components

| Component | Role |
|-----------|------|
| [ZeroClaw](https://github.com/zeroclaw-labs/zeroclaw) | personal agent runtime (think: self-hosted Claude/ChatGPT you control) — one isolated container per tier |
| [OneCLI](https://github.com/onecli/onecli) | credential vault + man-in-the-middle (MITM) proxy that swaps placeholders for real secrets on the wire |
| [LiteLLM](https://github.com/BerriAI/litellm) | model router |
| [PrivateMode](https://privatemode.ai) | TEE-confidential inference enclave |
| [Squid](https://github.com/ubuntu/squid) | default-deny, allowlisted egress (the only way out) |
| [Multipass](https://multipass.run) | one-command Ubuntu VM for Mac dev |

## Architecture

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {
  'fontFamily': 'monospace',
  'fontSize': '15px',
  'lineColor': '#444c56',
  'clusterBkg': '#ffffff',
  'clusterBorder': '#8c959f',
  'edgeLabelBackground': '#ffffff'
}}}%%
flowchart TB
  classDef agent    fill:#cfe3f7,stroke:#0b5cad,stroke-width:1.75px,color:#08305c
  classDef vault    fill:#e7dbf7,stroke:#6a3fbf,stroke-width:1.75px,color:#311a5e
  classDef db       fill:#cdeae0,stroke:#0f7a55,stroke-width:1.75px,color:#06382a
  classDef dash     fill:#ffe8c2,stroke:#b5650a,stroke-width:1.75px,color:#5c3304
  classDef squid    fill:#d6d8ff,stroke:#3730a3,stroke-width:2px,color:#1e1a66,font-weight:bold
  classDef internet fill:#ffd9cc,stroke:#c2410c,stroke-width:2px,color:#6b2207,font-weight:bold
  classDef user     fill:#f0eee9,stroke:#57606a,stroke-width:1.75px,color:#1f2328
  classDef channel  fill:#d6eef2,stroke:#0e7490,stroke-width:1.75px,color:#083344
 
  CHAN(["📱 paired channel<br/>WhatsApp · Telegram · Signal · Matrix"]):::channel
  USER(["👤 you (Mac)"]):::user
  INTERNET(["INTERNET"]):::internet
 
  subgraph VM["Docker host VM"]
    direction TB
 
    subgraph anet["assistant-net — NO direct internet"]
      direction TB
      zc(["zeroclaw-*<br/>agent containers"]):::agent
      mcp(["mcp-*<br/>MCP gateways"]):::agent
      onecli(["onecli<br/>vault + MITM proxy"]):::vault
      onedb[("onecli-db<br/>Postgres")]:::db
      llm(["litellm<br/>model router"]):::agent
      pm(["privatemode-proxy<br/>TEE enclave"]):::vault
    end
 
    subgraph enet["egress-net"]
      squid(["squid — default-deny allowlist<br/>THE ONLY WAY OUT"]):::squid
    end
 
    subgraph adm["admin-net — dev only"]
      dash(["dashboards<br/>127.0.0.1:10254 · 3000–3002"]):::dash
    end
  end
 
  zc -->|"model call"| llm --> pm
  pm -->|"attested inference"| squid
  zc -->|"tools"| mcp
  zc --> onecli
  mcp -->|"proxy-auth + secret injection :10255"| onecli
  onecli --> onedb
  onecli -->|"SaaS call (creds injected)"| squid
  squid -->|"allowlisted only"| INTERNET
  zc -->|"channel"| squid
  INTERNET <--> CHAN
  USER ~~~ zc
  USER -.->|"SSH tunnel"| dash
  CHAN ~~~ zc
  zc -.-> dash
  onecli -.-> dash
  mcp -.-> dash
  dash ~~~ squid
```
 

> The **solid** arrows are the data plane — notice they all funnel through Squid, which is the only thing bridging `assistant-net` to the internet. The **dashed** arrows are dev-only dashboard reach via `admin-net`; on a real Linux host that net is unnecessary (dashboards bind loopback).

Default 3 tiers: **updates** (WhatsApp, Gmail, identity `personal`), **tasks** (Signal, Marvin, identity `tasks`), **unrestricted** (shell/file tools, no SaaS creds, sandboxed in DinD).

## Configuration

**`tiers.yaml`** — define N tiers, each with identity, MCP gateway, port, command allowlist, sandbox flag. Edit this, re-run `generate-tiers.py`.

**`.env`** — OneCLI tokens (one per identity — **auto-written** by `bring-up.sh`), MCP gateway tokens, LiteLLM master key, PrivateMode key, DB password. Fill in before launching the VM.

**`squid/allowlist.txt`** — default-deny egress. Add domains your tools need.

**`mcp/<tier>/`** — per-tier MCP gateway config (registry.yaml, secrets, catalog).

## VM details

### Management  · *Mac*

| Action | Command |
|--------|---------|
| Create with local state (recommended) | `./launch-multipass.sh --local` |
| Create (clone from GitHub) | `./launch-multipass.sh` |
| Get a shell | `multipass shell assistant` |
| Tunnel dashboards to the Mac | `ssh -L 10254:127.0.0.1:10254 ubuntu@<vm-ip>` *(loopback, not admin-net IP; add `-L 3000:127.0.0.1:3000` … per tier)* |
| Update the stack | pull on the Mac → `./launch-multipass.sh --local` again (or edit in-VM) → `bash /opt/bring-up.sh` |
| Watch bring-up logs | `multipass exec assistant -- journalctl -u cloud-final -f` |
| Teardown | `multipass delete assistant && multipass purge` |

### Debugging  · *VM, as `assistant`*

> Everything in this table runs **inside the VM as `assistant`** (`sudo -iu assistant; cd $STACK_DIR`). To run the same from the Mac without a shell, prefix with `multipass exec assistant -- sudo -iu assistant -- `.

| Check this | Command |
|---|---|
| Container statuses | `docker compose ps` |
| Tier logs | `docker compose logs --tail=50 zeroclaw-<tier>` |
| Agent health | `docker compose exec zeroclaw-<tier> zeroclaw doctor` |
| Bind channel | `docker compose exec zeroclaw-<tier> zeroclaw channel start` |
| Full validation | `bash scripts/preflight.sh` |
| Restart a tier | `docker compose restart zeroclaw-<tier>` |

### Known quirks

- **MCP token mismatch:** if `zeroclaw doctor` shows no MCP connection, the gateway token doesn't match `.env`. Re-run `bash /opt/bring-up.sh` (it regenerates configs and re-injects secrets).
- **Sandbox build needs egress allowlist entries:** the DinD sandbox image builds *inside* the contained DinD daemon, so `squid/allowlist.txt` must include the build-time registries/CDNs (Docker Hub, Chainguard/R2, `packages.wolfi.dev`, npm, GitHub). For prod, build in CI and publish to ghcr.io, then allowlist only `ghcr.io`.
- **macOS networking:** `internal: true` Docker networks block host→container routing on macOS, so an `admin-net` bridge carries dashboard traffic. Not needed on real Linux (dashboards bind loopback; reach over SSH/on-box).
- *(Implementation details — the OneCLI CA export race, the `127.0.0.1` healthcheck, the MCP gateway `--block-network` flag — are documented as comments in `bring-up.sh`, `docker-compose.yml`, and `generate-tiers.py`.)*

## Roadmap

The pilot is architecturally complete but operationally young — roughly in priority order:

**Validate the promise (near-term)**
- Bind the messaging channels end-to-end: WhatsApp web-mode pairing for the `updates` tier, and **Signal via `signal-cli`** for `tasks` (a `signal-cli` service still needs adding to the stack — the tier is wired for it, the daemon isn't yet).
- Complete the first **brokered tool call** through OneCLI (e.g. Gmail via `mcp-personal`) and confirm the agent never sees the real token.
- Per-tier isolation tests: `tasks` cannot reach Google; `unrestricted` has no SaaS creds.
- Finish `scripts/verify-no-anthropic.sh` — assert no Anthropic provider in any config, provider `base_url` is LiteLLM, and Squid shows zero Anthropic egress.

**Access — Tailscale**
- Replace the SSH-tunnel + `admin-net` workaround with **Tailscale** SSH + MagicDNS: a Tailscale sidecar on `admin-net` (it dials out, needs no inbound port) and *off* the data-plane `assistant-net`. Solves Mac→dashboard routing cleanly and gives identity-based access to the dashboards.

**Confidentiality — pluggable TEE providers**
- Add **[Tinfoil](https://tinfoil.sh/)** (and other attested-inference providers) as a LiteLLM fallback behind the same allowlist, so you're not locked to PrivateMode. (`squid/allowlist.txt` already reserves the spot under "DEFERRED".)

**Production hardening**
- Move to a permanent **Linux VM** via the same `user-data.yaml` as a NoCloud seed (dev/prod parity).
- Pin every image by digest; add LiteLLM Postgres for **virtual keys + budgets**; tighten DinD (TLS on 2376, or a dedicated network) instead of unauthenticated 2375 on `assistant-net`; carry secrets as files rather than env vars.

**Supply chain (closes "Not mitigated" gaps)**
- SBOM / package scanning for tool images, and content inspection on allowlisted egress.

**Maybe**
- Per-tier DinD sidecars for the identity tiers (full sandbox parity for `updates`/`tasks`).
- A "compounding lessons" memory flow: a scheduled reflection job that distils sessions into a `LESSONS.md` the agent reloads.

## Usage options (not yet tested)

The stack has only been validated on macOS + Multipass. Promising untested alternatives:

- **Multipass on Linux** — `snap install multipass` then the same `launch-multipass.sh` flow.
- **libvirt / VirtualBox** — seed `user-data.yaml` via cloud-init on any Ubuntu 22.04+ VM.
- **Docker directly on host** — clone the repo, `export COMPOSE_FILE=docker-compose.yml:docker-compose.tiers.yml`, `docker compose up -d`. Missing: `bring-up.sh` automation, cloud-init, the multipass file transfer.
- **Tailscale** (planned) — replace SSH tunnels with Tailscale SSH + MagicDNS for dashboard access. Eliminates the `ssh -L` step. See [Roadmap](#roadmap).

## Contributing

Contributions are welcome. This is currently a small pilot — open an issue first to discuss the change you'd like to make. Please do not file public issues for security vulnerabilities; see [docs/SECURITY.md](docs/SECURITY.md) and email the maintainer privately instead. A `CONTRIBUTING.md` and `CODE_OF_CONDUCT.md` will be added as the project grows.
