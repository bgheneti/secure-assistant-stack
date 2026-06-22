#!/bin/sh
# zeroclaw/entrypoint.sh
# Idempotently seeds config.toml from the template, then starts ZeroClaw.
# Runs as the container's default user (65534/nobody — distroless nonroot).

set -e

CONFIG_DIR="/data/.zeroclaw"
CONFIG_FILE="$CONFIG_DIR/config.toml"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "[entrypoint] No config.toml found — seeding from template"
  mkdir -p "$CONFIG_DIR"

  # Expand ZEROCLAW_PROVIDER_API_KEY into the template.
  # sed is not available in distroless; use the alpine helper pattern OR
  # rely on ZeroClaw's own env-var resolution for api_key = "${...}".
  # ZeroClaw resolves ${VAR} placeholders in config.toml at startup,
  # so we can drop the file as-is and let the runtime handle substitution.
  cp /etc/zeroclaw/config.template.toml "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
  echo "[entrypoint] config.toml written"
else
  echo "[entrypoint] config.toml already exists — skipping seed"
fi

# Hand off to ZeroClaw (pass through any CMD args from compose).
exec zeroclaw "$@"