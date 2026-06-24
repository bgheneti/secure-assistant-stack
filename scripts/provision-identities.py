#!/usr/bin/env python3
"""
provision-identities.py — create OneCLI identities for every identity-bearing
tier in tiers.yaml and write their aoc_ access tokens into .env.

Replaces the manual "open the dashboard → create an agent → copy the token"
flow (the One-time SaaS authorization in the README). Safe to re-run: existing
identities are left alone and their tokens are just re-fetched.

    python3 scripts/provision-identities.py            # create + write .env
    python3 scripts/provision-identities.py --dry-run  # show what would change

Reach OneCLI at $ONECLI_API_URL (default http://127.0.0.1:10254). The script
edits the .env next to tiers.yaml in place, preserving its mode and any other
vars. Requires python3 + pyyaml (auto-installed if missing, like generate-tiers.py).
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

try:
    import yaml
except ImportError:
    sys.stderr.write("Installing pyyaml...\n")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml"])
    import yaml


STACK = os.environ.get("STACK_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _p(path):
    return path if os.path.isabs(path) else os.path.join(STACK, path)


def _identity_env_var(identity):
    """Env var holding the OneCLI access token for an identity name.
    Mirrors generate-tiers.py so the names always agree."""
    return f"ONECLI_TOKEN_{identity.upper()}"


# ---------------------------------------------------------------------------
# tiers.yaml
# ---------------------------------------------------------------------------
def identities_from_tiers(path):
    """Return ordered [(identity_name, env_var)] for every tier with an identity."""
    with open(path) as f:
        cfg = yaml.safe_load(f)
    out = []
    seen = set()
    for t in cfg.get("tiers", []):
        ident = t.get("identity")
        if ident and ident not in seen:
            seen.add(ident)
            out.append((ident, _identity_env_var(ident)))
    return out


# ---------------------------------------------------------------------------
# OneCLI API (stdlib only — no requests dependency)
# ---------------------------------------------------------------------------
class OneCLIClient:
    def __init__(self, base_url, timeout=10):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _req(self, method, path, payload=None):
        url = f"{self.base}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode()
                return r.status, (json.loads(body) if body.strip() else None)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            return e.code, body

    def wait_until_up(self, retries=30, delay=2):
        for i in range(1, retries + 1):
            try:
                status, body = self._req("GET", "/api/health")
                if status == 200:
                    return True
            except Exception:
                pass
            sys.stderr.write(f"  waiting for OneCLI ({i}/{retries})...\n")
            time.sleep(delay)
        return False

    def list_agents(self):
        status, body = self._req("GET", "/api/agents")
        if status != 200:
            raise RuntimeError(f"GET /api/agents -> {status}: {body}")
        return body if isinstance(body, list) else []

    def create_agent(self, identifier):
        status, body = self._req(
            "POST", "/api/agents", {"name": identifier, "identifier": identifier}
        )
        if status in (200, 201):
            return "created", None
        if status == 409:
            return "exists", None
        raise RuntimeError(f"POST /api/agents({identifier}) -> {status}: {body}")


# ---------------------------------------------------------------------------
# .env editing (in place, preserves mode + unrelated lines)
# ---------------------------------------------------------------------------
def update_env(env_path, updates, dry_run=False):
    """updates: {VAR_NAME: value}. Replaces the value on a matching 'VAR=' line,
    or appends the line if absent. Returns list of (var, old, new, action)."""
    with open(env_path) as f:
        lines = f.readlines()

    have = set()
    changes = []
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z][A-Z0-9_]*)=.*$", line.rstrip("\n"))
        if not m or m.group(1) not in updates:
            continue
        var = m.group(1)
        have.add(var)
        old_val = line.split("=", 1)[1].rstrip("\n")
        new_val = updates[var]
        if old_val == new_val:
            changes.append((var, old_val, new_val, "unchanged"))
        else:
            changes.append((var, old_val, new_val, "updated"))
            lines[i] = f"{var}={new_val}\n"

    for var, new_val in updates.items():
        if var not in have:
            changes.append((var, "(absent)", new_val, "added"))
            lines.append(f"{var}={new_val}\n")

    if not dry_run:
        mode = os.stat(env_path).st_mode
        with open(env_path, "w") as f:
            f.writelines(lines)
        os.chmod(env_path, mode)
    return changes


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tiers", default=_p("tiers.yaml"), help="path to tiers.yaml")
    ap.add_argument("--env", default=_p(".env"), help="path to .env to update")
    ap.add_argument("--api-url", default=os.environ.get("ONECLI_API_URL", "http://127.0.0.1:10254"),
                    help="OneCLI dashboard API base URL")
    ap.add_argument("--dry-run", action="store_true", help="show planned changes; do not write")
    args = ap.parse_args()

    tiers_path = args.tiers
    env_path = args.env
    if not os.path.isfile(tiers_path):
        sys.exit(f"ERROR: {tiers_path} not found")
    if not os.path.isfile(env_path):
        sys.exit(f"ERROR: {env_path} not found (cp .env.example .env first)")

    identities = identities_from_tiers(tiers_path)
    if not identities:
        print("No identity-bearing tiers in tiers.yaml — nothing to provision.")
        return

    print(f"Identity-bearing tiers need these OneCLI identities:")
    for name, var in identities:
        print(f"  {name:12} -> {var}")

    client = OneCLIClient(args.api_url)
    print(f"\nConnecting to OneCLI at {args.api_url} ...")
    if not client.wait_until_up():
        sys.exit("ERROR: OneCLI not reachable. Start the supporting stack first "
                 "(bring-up.sh step 3), then re-run this script.")

    # --- create missing identities ---
    print()
    existing = {a["identifier"]: a for a in client.list_agents()}
    for name, _ in identities:
        if name in existing:
            print(f"  [skip ] identity '{name}' already exists")
            continue
        result, _ = client.create_agent(name)
        print(f"  [{'created' if result == 'created' else 'exists'}] identity '{name}'")

    # --- collect tokens ---
    existing = {a["identifier"]: a for a in client.list_agents()}
    updates = {}
    missing = []
    for name, var in identities:
        agent = existing.get(name)
        if agent and agent.get("accessToken"):
            updates[var] = agent["accessToken"]
        else:
            missing.append(name)

    if missing:
        sys.exit(f"ERROR: no access token returned for: {', '.join(missing)}")

    # --- write .env ---
    print(f"\n{'[dry-run] ' if args.dry_run else ''}Updating {env_path}:")
    changes = update_env(env_path, updates, dry_run=args.dry_run)
    for var, old, new, action in changes:
        print(f"  [{action:9}] {var}")
    print(f"\nDone. {len(updates)} identity token(s) "
          f"{'shown (dry-run)' if args.dry_run else 'written to .env'}.")
    if not args.dry_run:
        print("Re-run bring-up.sh to inject the real tokens into the tier configs.")


if __name__ == "__main__":
    main()
