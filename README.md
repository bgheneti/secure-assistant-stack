# Secure Assistant Stack

Self-hosted agent tiers with TEE-confidential inference and no unfiltered egress — the agent reads your integrations through a vault that provides fine tuned access controls never hands over the keys. Mac + Multipass (bare-metal Linux support planned).

## Requirements

| Resource | Required | Notes |
|----------|----------|-------|
| [Multipass](https://multipass.run) | Yes (Mac primary path) | `brew install multipass`. Linux VM options below. |
| Docker + Compose plugin | No | Auto-installed inside the VM by bring-up.sh. |
| [PrivateMode](https://privatemode.ai) API key | No | LiteLLM can fall back to any OpenAI-compatible endpoint without it. |
| SaaS credentials (Gmail, Marvin, etc.) | No | Needed only for identity-bearing tiers (updates, tasks). |

## Usage options (not yet tested)

The stack has only been validated on macOS + Multipass. Promising untested alternatives:

- **Multipass on Linux** — `snap install multipass` then same `launch-multipass.sh` flow.
- **libvirt / VirtualBox** — seed `user-data.yaml` via cloud-init on any Ubuntu 22.04+ VM.
- **Docker directly on host** — clone repo, `export COMPOSE_FILE=docker-compose.yml:docker-compose.tiers.yml`, `docker compose up -d`. Missing: bring-up.sh automation, cloud-init, multipass file transfer.
- **Tailscale** (planned) — replace SSH tunnels with Tailscale SSH + MagicDNS for dashboard access. Eliminates the `ssh -L` step.

## Primary workflow (configure → build → provision → use)

```bash
# 1. Configure tiers (optional — 3 default tiers work out of box)
cp .env.example .env        # fill in placeholders
vim tiers.yaml              # add/remove tiers, change identities, ports, tools

# 2. Generate compose fragment + tier configs
python3 scripts/generate-tiers.py

# 3. Build + start VM
./launch-multipass.sh --local   # transfers repo + .env; waits for bring-up
```

This creates an Ubuntu VM (`assistant`), copies the repo, and runs `bring-up.sh` via cloud-init. After ~5 min, the entire stack is running.

```bash
# 4. Tunnel dashboard & create OneCLI identities (one-time interactive)
multipass exec assistant -- ip addr show admin-net  # find admin IP, e.g. 10.0.2.x
multipass list                                      # find VM's external IP
ssh -L 10254:10.0.2.x:10254 ubuntu@<vm-external-ip>
# Now open http://localhost:10254 in your browser → OneCLI dashboard
# Other tier dashboards use ports from tiers.yaml (defaults: 3000, 3001, 3002).
# Tunnel those too: ssh -L 3000:10.0.2.x:3000 ... (repeat per tier)
```
Settings → Agents: create **personal** + **tasks** (one per identity in tiers.yaml).
Copy each `aoc_` token into `.env` on the VM (`ONECLI_TOKEN_PERSONAL=...`, `ONECLI_TOKEN_TASKS=...`).
Settings → Connections: authorize Gmail (personal), paste Amazing Marvin API key (tasks).

```bash
# 5. Restart with real tokens
multipass exec assistant -- sh -c 'cd /opt/assistant-stack && \
  docker compose up -d && \
  python3 scripts/generate-tiers.py && \
  bash -x bring-up.sh'

# 6. Verify & bind channels
multipass exec assistant -- bash /opt/assistant-stack/scripts/preflight.sh
multipass exec assistant -- docker compose exec zeroclaw-updates zeroclaw channel start
```

That's it. The agent talks to your SaaS accounts through OneCLI's MITM proxy — it never holds a raw credential. Model inference runs through a TEE enclave.

## Architecture

```
[Docker host VM]
  assistant-net (internal, no internet) ─── egress only via Squid ─── INTERNET
  ├── squid          default-deny allowlisted egress
  ├── onecli         vault + MITM proxy (injects creds on the wire)
  ├── onecli-db      Postgres backing the vault
  ├── litellm        model router → PrivateMode TEE
  ├── privatemode-proxy  TEE enclave proxy
  ├── mcp-*          Docker MCP gateways (one per identity tier)
  └── zeroclaw-*     agent containers (one per tier)
```

Default 3 tiers: **updates** (WhatsApp, Gmail, identity `personal`), **tasks** (Signal, Marvin, identity `tasks`), **unrestricted** (shell/file tools, no SaaS creds, sandboxed in DinD).

## Security model

**Goal: the agent has no unfiltered egress and never holds a raw credential — every outbound request routes through a vault that injects scoped short-lived tokens on the wire.**

The standard risk framework lists six categories: (1) prompt injection, (2) dangerous packages, (3) sensitive file access, (4) proprietary data exfiltration, (5) unauthorized privileged actions, and (6) viruses. Simon Willison condenses these into the **lethal trifecta**: (a) access to private data, (b) exposure to untrusted content, and (c) ability to communicate externally — any two together are dangerous.

This project addresses (c) aggressively and uses that to bound the others:

| Mitigated | How |
|-----------|-----|
| Data exfiltration (risk 4, trifecta a+c) | No direct internet. All egress through Squid (default-deny allowlist) + OneCLI MITM proxy. Agent can't phone home. |
| Credential theft via injection (risk 1, trifecta b→a) | Agent has no stored secrets. OneCLI injects per-request, scoped tokens at the proxy layer. An injected prompt can ask for credentials but nothing will hand them over. |
| Privileged action without consent (risk 5) | OneCLI acts as consent proxy — each SaaS action requires a scoped token generated by the vault, not by the agent. |
| Viruses / persistence (risk 6) | Containers are ephemeral. No host socket mount. Sandbox tier runs inside a contained DinD with no network. |

| Not mitigated | Why |
|---------------|-----|
| Prompt injection → tool misuse (risk 1 continued) | An injected prompt can still trick the agent into using its legitimate tools in unintended ways (e.g. "send that email"). Vault model doesn't prevent tool-level misuse. |
| Dangerous packages / supply chain (risk 2) | No SBOM validation. The `commands` allowlist on identity tiers helps but assumes the allowlist is correct. |
| Sensitive file access (risk 3) | Workspace volumes are shared by design. No per-file ACL. Mitigated only by isolation: identity tiers have no shell, and the sandbox tier has no credentials. |
| Covert channel via legitimate egress | If an allowlisted domain is malicious or compromised, data can flow out through it. No egress content inspection. |

The unrestricted tier is a deliberate gap: it has shell/file/browser tools and DinD sandbox with Squid egress. It has no SaaS credentials, so the trifecta never completes — but if you add credentials to it, treat it as a separate risk surface.

## Configuration

**`tiers.yaml`** — define N tiers, each with identity, MCP gateway, port, command allowlist, sandbox flag. Edit this, re-run `generate-tiers.py`.

**`.env`** — OneCLI tokens (one per identity), MCP gateway tokens, LiteLLM master key, PrivateMode key, DB password. Fill in before launching VM; update after provisioning OneCLI identities.

**`squid/allowlist.txt`** — default-deny egress. Add domains your tools need.

**`mcp/<tier>/`** — per-tier MCP gateway config (registry.yaml, secrets, catalog).

## VM details

### Management

| Action | Command (from Mac host) |
|--------|-------------------------|
| Create VM | `./launch-multipass.sh` (clones from GitHub) |
| Create with local state (recommended) | `./launch-multipass.sh --local` (transfers repo + uncommitted changes + `.env`) |
| SSH in | `multipass shell assistant` |
| Tunnel dashboards | `ssh -L 10254:10.0.2.x:10254 ubuntu@<vm-ip>` (ports per tiers.yaml) |
| Watch bring-up logs | `multipass exec assistant -- journalctl -u cloud-final -f` |
| Teardown | `multipass delete assistant && multipass purge` |

### Debugging

| Check this | From Mac host | Or from inside VM |
|---|---|---|
| Container statuses | `multipass exec assistant -- docker compose ps` | `docker compose ps` |
| Tier logs | `multipass exec assistant -- docker compose logs --tail=50 zeroclaw-<tier>` | `docker compose logs --tail=50 zeroclaw-<tier>` |
| Agent health | `multipass exec assistant -- docker compose exec zeroclaw-<tier> zeroclaw doctor` | `docker compose exec zeroclaw-<tier> zeroclaw doctor` |
| Bind channel | `multipass exec assistant -- docker compose exec zeroclaw-<tier> zeroclaw channel start` | `docker compose exec zeroclaw-<tier> zeroclaw channel start` |
| Full validation | `multipass exec assistant -- bash /opt/assistant-stack/scripts/preflight.sh` | `bash scripts/preflight.sh` |
| Restart a tier | `multipass exec assistant -- docker compose restart zeroclaw-<tier>` | `docker compose restart zeroclaw-<tier>` |

### Known quirks

- **CA cert race:** OneCLI generates its CA cert on first start. bring-up.sh waits for it in a retry loop, copies it to a shared volume, and sets `SSL_CERT_FILE` on every tier.
- **MCP token mismatch:** If `zeroclaw doctor` shows no MCP connection, the `MCP_GATEWAY_AUTH_TOKEN` in the tier's config.toml doesn't match `.env`. Re-run `scripts/generate-tiers.py` after updating `.env`.
- **Sandbox build:** The DinD sandbox Dockerfile builds INSIDE the contained daemon — the host never accesses the inner Docker socket.
- **macOS networking:** `internal: true` Docker networks block host→container routing on macOS. The `admin-net` bridge works around this. Not needed on real Linux.