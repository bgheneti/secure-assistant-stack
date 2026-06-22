# ZeroClaw MCP config

## The split this tree encodes

Tools are routed by **auth style**, not by tier:

- **Static-token tools** (a fixed header or bearer that never rotates) run **behind the MCP gateway**. The real credential lives in OneCLI; `secrets.env` holds a placeholder; OneCLI swaps in the real value on egress. The server container never holds a raw credential.
- **Non-static auth** — Google OAuth (Gmail/Calendar), AWS SigV4, anything mTLS / DPoP / connection-bound — is **brokered by OneCLI directly** (agent-direct or in-agent) and is **not** defined in any catalog here. Stateful OAuth fights an MCP server's own token lifecycle and resists clean on-the-wire injection, so it stays out of the gateway.

That is why there is **no Gmail or Calendar entry** in `personal/`. Google is OneCLI's job.

## Layout

```
mcp/
  personal/   catalog defines Marvin (available, NOT enabled); registry empty
  tasks/      catalog defines + enables Marvin
```

Each tier has the four files the gateway expects: `catalog.yaml`, `registry.yaml`, `config.yaml`, `secrets.env` (mounted at `/mcp`, passed via `--catalog/--registry/--config/--secrets`).

`personal/registry.yaml` is an intentionally **empty registry** — the personal tier's real workload (Google) is OneCLI-direct, so the gateway exposes nothing there. See the note in that file: under this design you may not need the `mcp-personal` service running at all.

## Amazing Marvin server

Confirmed from `bgheneti/Amazing-Marvin-MCP`:

- Package `amazing-marvin-mcp`; the repo's `Dockerfile` sets `ENTRYPOINT ["amazing-marvin-mcp"]` (a stdio MCP server the gateway can spawn).
- Reads env var **`AMAZING_MARVIN_API_KEY`**.
- Uses `requests` to call `https://serv.amazingmarvin.com/api`, sending the key as the **`X-API-Token`** header. (28 tools incl. write ops like `create_task`, `mark_task_done`, time-tracking; no delete.)

### Build the image referenced by the catalog

```
docker build -t zeroclaw/amazing-marvin-mcp:local https://github.com/bgheneti/Amazing-Marvin-MCP.git
```

(Or build from a local clone.) The catalogs reference `zeroclaw/amazing-marvin-mcp:local`.

### OneCLI injection rule for Marvin

The server emits `X-API-Token: PLACEHOLDER_ONECLI_INJECTS` to `serv.amazingmarvin.com`. Configure OneCLI to match `(tier, host=serv.amazingmarvin.com)` and rewrite the `X-API-Token` header value to the real key. Squid must allowlist `serv.amazingmarvin.com`.

> Simpler alternative if you don't want OneCLI in Marvin's path: put the real key directly in `secrets.env`. The gateway keeps it out of the model's context, but the server container then holds the raw credential — a weaker posture than the OneCLI-injection default used here.

## Before any of this works (pre-flight)

1. **Transport flag.** In `docker-compose.yml`, both gateways use `--transport=streaming`. The gateway supports `stdio` and `sse` only; with `--port` it must be `sse`. Change `--transport=streaming` → `--transport=sse` (verify with `docker mcp gateway run --help`).
2. **Spawned-container egress + CA trust.** For OneCLI to inject, the Marvin container must egress through Squid and trust the OneCLI CA. Marvin is Python/`requests`, so it needs `HTTPS_PROXY=http://squid:3128` and `REQUESTS_CA_BUNDLE=/certs/onecli/ca.pem` (or `SSL_CERT_FILE`), plus the `onecli-ca` volume mounted into the spawned container. Confirm your gateway forwards per-server `env`/volumes to spawned containers (`docker compose run --rm mcp-tasks ... --dry-run --verbose` prints the `docker run` line). Without this, Marvin's HTTPS either bypasses OneCLI (placeholder sent → 401) or fails the TLS handshake.
3. **Permissions.** The empty `mcp/personal` and `mcp/tasks` dirs Docker auto-created earlier are root-owned; this tree replaces them. `chmod 600 */secrets.env`.

## Enabling Marvin in the personal tier

It's already defined in `personal/catalog.yaml`. Two edits: set `personal/registry.yaml` to enable `marvin: { ref: "" }`, and uncomment the `marvin_api_key` placeholder in `personal/secrets.env`.

## Format note

`registry.yaml` is a `registry:` map of enabled server → `{ ref: "" }`. `config.yaml` is a minimal `{}`. `secrets.env` keys must match the catalog `secrets[].name`. These schemas and the `sse` transport are worth a final check against your installed gateway version before going live.
