#!/usr/bin/env python3
"""
generate-tiers.py — read tiers.yaml, produce:
  1. docker-compose.tiers.yml  (MCP gateways + ZeroClaw containers + DinD)
  2. zeroclaw/<name>/config.toml  (per-tier ZeroClaw config)

Usage:
    scripts/generate-tiers.py              # use tiers.yaml in stack root
    scripts/generate-tiers.py path/to/tiers.yaml  # custom path

Idempotent — safe to re-run. Regenerates all output files.
"""

import os, sys, textwrap

# --- pyyaml ------------------------------------------------------------------
try:
    import yaml
except ImportError:
    sys.stderr.write("Installing pyyaml...\n")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml"])
    import yaml


STACK = os.environ.get("STACK_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _p(path):
    return path if os.path.isabs(path) else os.path.join(STACK, path)


def _identity_env_var(identity):
    """Env var holding the OneCLI access token for an identity name."""
    return f"ONECLI_TOKEN_{identity.upper()}"


def _identity_proxy_url(identity):
    """HTTPS_PROXY URL with embedded proxy-auth credentials."""
    ev = _identity_env_var(identity)
    return f"http://{identity}:${{{ev}}}@onecli:10255"


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------
def load_config(path=None):
    path = path or _p("tiers.yaml")
    with open(path) as f:
        cfg = yaml.safe_load(f)
    defaults = cfg.get("defaults", {})
    tiers = cfg.get("tiers", [])
    if not tiers:
        sys.stderr.write(f"ERROR: no tiers defined in {path}\n")
        sys.exit(1)

    # Fill defaults
    for t in tiers:
        for k in ("runtime", "workspace_unrestricted", "build_sandbox"):
            t.setdefault(k, False)
        t.setdefault("block_high_risk", True)
        t.setdefault("mcp", False)
        t.setdefault("mcp_token_var", f"MCP_{t['name'].upper()}_TOKEN")
        t.setdefault("mcp_dir", f"mcp/{t['name']}")
        t.setdefault("no_proxy", f"litellm,localhost,127.0.0.1")
        t.setdefault("commands", [])
        t.setdefault("auto_approve", [])
        t.setdefault("image", defaults.get("image"))
        if t.get("mcp") and t.get("mcp_port") is None:
            t["mcp_port"] = 8811 + [ti["name"] for ti in tiers].index(t["name"])
        t.setdefault("port", 3000 + [ti["name"] for ti in tiers].index(t["name"]))

    return tiers


# ---------------------------------------------------------------------------
# docker-compose.tiers.yml
# ---------------------------------------------------------------------------
def gen_compose(tiers):
    svc = {}
    vol = {}
    dind_names = []

    for t in tiers:
        name = t["name"]
        identity = t.get("identity")
        has_mcp = t["mcp"]
        is_docker = t["runtime"] == "docker"

        # --- MCP gateway ----------------------------------------------------
        if has_mcp:
            gw = f"mcp-{name}"
            env = {
                "MCP_GATEWAY_AUTH_TOKEN": f"${{{t['mcp_token_var']}}}",
                "HTTPS_PROXY": _identity_proxy_url(identity),
                "SSL_CERT_FILE": "/certs/onecli/ca.pem",
                "NO_PROXY": "localhost,127.0.0.1",
            }
            if identity:
                env[_identity_env_var(identity)] = f"${{{_identity_env_var(identity)}}}"

            svc[gw] = {
                "image": "docker/mcp-gateway:latest",
                "container_name": gw,
                "restart": "unless-stopped",
                "depends_on": ["onecli"],
                "networks": ["assistant-net", "admin-net"],
                "environment": env,
                "volumes": [
                    "/var/run/docker.sock:/var/run/docker.sock",
                    f"./{t['mcp_dir']}:/root/.docker/mcp:ro",
                    "onecli-ca:/certs/onecli:ro",
                ],
                "command": [
                    "--registry=/root/.docker/mcp/registry.yaml",
                    "--config=/root/.docker/mcp/config.yaml",
                    "--secrets=/root/.docker/mcp/secrets.env",
                    "--transport=streaming",
                    f"--port={t['mcp_port']}",
                    "--block-network",
                ],
                "ports": [f"127.0.0.1:{t['mcp_port']}:{t['mcp_port']}"],
            }

        # --- DinD sandbox ---------------------------------------------------
        dind = None
        if is_docker:
            dind = f"dind-{name}"
            dind_names.append(name)
            ws_vol = f"{name}-workspace"
            vol[ws_vol] = None
            vol[f"dind-{name}-data"] = None

            svc[dind] = {
                "image": "docker:27-dind",
                "container_name": dind,
                "restart": "unless-stopped",
                "privileged": True,
                "depends_on": ["squid"],
                "networks": ["assistant-net"],
                "command": [
                    "--host=unix:///var/run/docker.sock",
                    "--host=tcp://0.0.0.0:2375",
                    "--tls=false",
                ],
                "environment": {
                    "HTTP_PROXY": "http://squid:3128",
                    "HTTPS_PROXY": "http://squid:3128",
                    "NO_PROXY": "localhost,127.0.0.1",
                    "DOCKER_TLS_CERTDIR": "",
                },
                "volumes": [
                    f"dind-{name}-data:/var/lib/docker",
                    f"{name}-workspace:/workspace",
                ],
            }

        # --- ZeroClaw tier --------------------------------------------------
        zc = f"zeroclaw-{name}"
        deps = ["litellm", "squid"]
        if has_mcp:
            deps.append(f"mcp-{name}")
        if dind:
            deps.append(dind)

        env = {
            "ZEROCLAW_MODEL": "assistant",
            "ZEROCLAW_providers__models__litellm__assistant__api_key": "${LITELLM_MASTER_KEY}",
            "ZEROCLAW_providers__models__litellm__thinker__api_key": "${LITELLM_MASTER_KEY}",
            "NO_PROXY": t["no_proxy"],
        }

        if identity:
            env["HTTPS_PROXY"] = _identity_proxy_url(identity)
            env["HTTP_PROXY"] = _identity_proxy_url(identity)
            env[_identity_env_var(identity)] = f"${{{_identity_env_var(identity)}}}"
            env["SSL_CERT_FILE"] = "/certs/onecli/ca.pem"
        else:
            # No identity — egress via Squid, not OneCLI
            env["HTTP_PROXY"] = "http://squid:3128"
            env["HTTPS_PROXY"] = "http://squid:3128"

        if dind:
            env["DOCKER_HOST"] = f"tcp://{dind}:2375"

        zc_vols = [f"zeroclaw-{name}-data:/zeroclaw-data"]
        if identity:
            zc_vols.append("onecli-ca:/certs/onecli:ro")
        if t.get("workspace_unrestricted") and dind:
            zc_vols.append(f"{name}-workspace:/workspace")

        svc[zc] = {
            "image": t["image"],
            "container_name": zc,
            "restart": "unless-stopped",
            "depends_on": deps,
            "networks": ["assistant-net", "admin-net"],
            "environment": env,
            "volumes": zc_vols,
            "ports": [f"127.0.0.1:{t['port']}:42617"],
        }
        vol[f"zeroclaw-{name}-data"] = None

    # Assemble
    out = {
        "volumes": vol,
        "services": svc,
    }
    return out, dind_names


# ---------------------------------------------------------------------------
# config.toml per tier
# ---------------------------------------------------------------------------
def gen_config(t):
    commands = t["commands"]
    auto_approve = t["auto_approve"]
    is_docker = t["runtime"] == "docker"
    identity = t.get("identity")
    has_mcp = t["mcp"]

    risk_level = "supervised"
    forbid = [
        "/etc", "/root", "/home", "/usr", "/bin", "/sbin", "/lib",
        "/opt", "/boot", "/dev", "/proc", "/sys", "/var", "/tmp",
        "~/.ssh", "~/.gnupg", "~/.aws", "~/.config",
    ]

    lines = [
        "[gateway]",
        'port = 42617',
        'host = "[::]"',
        "allow_public_bind = true",
        "allow_remote_admin = true",
        "",
        "[risk_profiles.default]",
        f"allowed_commands = {_pp_yaml_list(commands)}",
        f"auto_approve = {_pp_yaml_list(auto_approve)}",
        f"block_high_risk_commands = {str(t.get('block_high_risk', True)).lower()}",
        f"forbidden_paths = {_pp_yaml_list(forbid)}",
        f'level = "{risk_level}"',
        "require_approval_for_medium_risk = true",
        f"workspace_only = {str(not t.get('workspace_unrestricted', False)).lower()}",
        "",
        "[agents.default]",
        "delegate_same_risk_profile = true",
        "enabled = true",
        'model_provider = "litellm.assistant"',
        'risk_profile = "default"',
        f'runtime_profile = "{"docker" if is_docker else "native"}"',
        "",
        "[agents.default.identity]",
        'format = "openclaw"',
        "",
        "[agents.default.memory]",
        'backend = "sqlite"',
        "",
        "[agents.default.workspace]",
        f"unrestricted_filesystem = {str(t.get('workspace_unrestricted', False)).lower()}",
        "",
        "[providers.models.litellm]",
        "",
        "[providers.models.litellm.assistant]",
        'uri = "http://litellm:4000"',
        'model = "assistant"',
        "think = true",
        'api_key = "LITELLM_MASTER_KEY"',
        "",
        "[providers.models.litellm.thinker]",
        'uri = "http://litellm:4000"',
        'model = "thinker"',
        'api_key = "LITELLM_MASTER_KEY"',
    ]

    if has_mcp:
        gw = f"mcp-{t['name']}"
        tok_var = t["mcp_token_var"]
        lines += [
            "",
            "[mcp]",
            "",
            "[[mcp.servers]]",
                'name = "gateway"',
                'transport = "sse"',
                f'url = "http://{gw}:{t["mcp_port"]}/mcp"',
                f'headers = {{ "Authorization" = "Bearer {tok_var}" }}',
            ]

    return "\n".join(lines) + "\n"


def _pp_yaml_list(items):
    """Format a Python list as a TOML-friendly inline array."""
    if not items:
        return "[]"
    parts = ", ".join(f'"{i}"' for i in items)
    return f"[{parts}]"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else _p("tiers.yaml")
    if not os.path.isfile(config_path):
        print(f"No {config_path} — nothing to generate.")
        return

    print(f"Reading {config_path}...")
    tiers = load_config(config_path)

    # 1. docker-compose.tiers.yml
    compose, dind_names = gen_compose(tiers)
    out_path = _p("docker-compose.tiers.yml")
    with open(out_path, "w") as f:
        f.write("# Autogenerated by scripts/generate-tiers.py — do not edit.\n")
        f.write("# Regenerate with: scripts/generate-tiers.py\n")
        f.write("# Source of truth: tiers.yaml\n\n")
        yaml.dump(compose, f, default_flow_style=False, sort_keys=False)
    print(f"  Wrote {out_path}")

    # 2. Per-tier config.toml
    for t in tiers:
        conf = gen_config(t)
        tdir = _p(f"zeroclaw/{t['name']}")
        os.makedirs(tdir, exist_ok=True)
        cpath = os.path.join(tdir, "config.toml")
        with open(cpath, "w") as f:
            f.write("# Autogenerated by scripts/generate-tiers.py — do not edit.\n")
            f.write("# Source of truth: tiers.yaml\n\n")
            f.write(conf)
        print(f"  Wrote {cpath}")

    # 3. Summary
    tier_names = [t["name"] for t in tiers]
    has_mcp_names = [t["name"] for t in tiers if t["mcp"]]
    print()
    print(f"Generated {len(tiers)} tier(s): {', '.join(tier_names)}")
    if has_mcp_names:
        print(f"MCP gateways enabled for: {', '.join(has_mcp_names)}")
    if dind_names:
        print(f"DinD sandboxes for: {', '.join(dind_names)}")
    print()
    print("Bring up with:")
    print(f"  docker compose -f {_p('docker-compose.yml')} -f {_p('docker-compose.tiers.yml')} up -d --wait")
    print()


if __name__ == "__main__":
    main()
