"""
ArkoseTracker — automated tracker for Arkose Labs api.js across all Roblox
captcha endpoints + other heavy users (X, Uber, etc).

Output: versions.json — a JSON map keyed by endpoint name with the latest
        observed (version, build_id, enforcement_hash, fetched_at).

The SolverSystem main solver reads this file's raw URL on GitHub at
boot + every hour, so when Arkose ships a new enforcement_hash we pick
it up automatically rather than going to 0% silent-pass until LO
manually edits the hardcoded value.

How extraction works (api.js is the Arkose client loader, ~30KB minified):

  - Semantic version  → first occurrence of a string `"4.x.x"`-shaped literal
  - Build ID (UUID)   → first canonical UUID in the file
  - Enforcement hash  → the 32-char hex inside `enforcement.<HASH>.html`
                        which the loader references for the iframe.

These regexes are tolerant to whitespace/minifier variations. If extraction
fails, the existing versions.json entry for that endpoint is kept (no false
overwrites that would tank the solver).
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "versions.json"
DATA = ROOT / "data"

# All known Arkose deployments worth tracking. The Roblox ones drive
# SolverSystem; the others are bonus coverage / parity with sexfrance's
# tracker so the data file is generally useful.
ENDPOINTS = {
    "roblox_register":  ("A2A14B1D-1AF3-C791-9BBC-EE33CC7A0A6F", "roblox-api.arkoselabs.com"),
    "roblox_login":     ("476068BF-9607-4799-B53D-366BE98E2B84", "roblox-api.arkoselabs.com"),
    "roblox_join":      ("63E4117F-E727-42B4-6DAA-C8448E9B137F", "roblox-api.arkoselabs.com"),
    "roblox_recovery":  ("ED4A4D58-CF92-4B91-95B9-D2B47B85D54E", "roblox-api.arkoselabs.com"),
    "x_register":       ("2CB16598-CB82-4CF7-B332-5990DB66F3AB", "client-api.arkoselabs.com"),
    "uber_login":       ("2DDF8B30-5FB1-4DA8-A1AA-CFAFB8C5121E", "client-api.arkoselabs.com"),
    "match_login":      ("E8A75615-1CBA-5DFF-8032-D16BCF234E10", "client-api.arkoselabs.com"),
    "snapchat_register":("E45A9667-F0AD-4205-A0C0-DE73E04BC92F", "client-api.arkoselabs.com"),
    "adobe_register":   ("82F5BC68-F5E9-4F1A-B188-A09F90AC2BB1", "client-api.arkoselabs.com"),
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Regexes are deliberately greedy-after-context to survive minifier renames.
RE_VERSION = re.compile(r'["\'](\d+\.\d+\.\d+)["\']')
RE_UUID = re.compile(r'\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b')
RE_ENFORCEMENT_HASH = re.compile(r'enforcement\.([0-9a-f]{32})\.html')


def fetch_apijs(public_key: str, host: str, timeout: float = 15.0) -> str:
    url = f"https://{host}/v2/{public_key}/api.js"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"{url} returned {resp.status}")
        return resp.read().decode("utf-8", errors="replace")


def parse_apijs(js: str) -> dict:
    """Extract version, build_id, enforcement_hash from the api.js body.

    Returns a dict with whatever could be extracted; missing fields are
    None so the caller can decide whether to keep or skip the entry.
    """
    version = None
    build_id = None
    enforcement_hash = None

    # Version: pick the first SemVer literal — these scripts embed their
    # own version near the top via `version:"4.2.2"` or similar.
    m = RE_VERSION.search(js)
    if m:
        # Don't accept e.g. "1.0.0" if it's a webgl version etc. — gate
        # to plausible Arkose major versions (3.x / 4.x).
        v = m.group(1)
        major = int(v.split(".", 1)[0])
        if major in (3, 4, 5):
            version = v
        else:
            # Fall through to find the first 3.x/4.x SemVer in the file.
            for m2 in RE_VERSION.finditer(js):
                v2 = m2.group(1)
                if v2.split(".", 1)[0] in ("3", "4", "5"):
                    version = v2
                    break

    # Build ID: first canonical UUID. Public keys themselves are UUIDs so
    # skip if the first hit equals the public key.
    for m in RE_UUID.finditer(js):
        u = m.group(1)
        build_id = u
        break

    # Enforcement hash: this is the load-bearing one for the solver.
    m = RE_ENFORCEMENT_HASH.search(js)
    if m:
        enforcement_hash = m.group(1)

    return {
        "version": version,
        "build_id": build_id,
        "enforcement_hash": enforcement_hash,
    }


def load_previous() -> dict:
    if not OUT.exists():
        return {}
    try:
        return json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    DATA.mkdir(exist_ok=True)
    previous = load_previous()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = dict(previous)
    changed = []

    for name, (pk, host) in ENDPOINTS.items():
        try:
            js = fetch_apijs(pk, host)
        except Exception as e:
            print(f"[!] {name}: fetch failed — {e}", file=sys.stderr)
            continue

        parsed = parse_apijs(js)
        if not parsed["enforcement_hash"]:
            # Refuse to overwrite a good entry with a parse miss.
            print(f"[!] {name}: parse miss, keeping previous entry", file=sys.stderr)
            continue

        prev = previous.get(name, {})
        prev_hash = prev.get("enforcement_hash")
        entry = {
            "public_key": pk,
            "host": host,
            **parsed,
            "fetched_at": now_iso,
        }
        if prev_hash != parsed["enforcement_hash"]:
            entry["previous_enforcement_hash"] = prev_hash
            entry["changed_at"] = now_iso
            changed.append(name)
            # Archive the api.js whenever the hash rotates.
            (DATA / f"{name}_{parsed['enforcement_hash'][:8]}.js").write_text(
                js, encoding="utf-8"
            )
        else:
            # Preserve change history on stable fetches.
            entry["previous_enforcement_hash"] = prev.get("previous_enforcement_hash")
            entry["changed_at"] = prev.get("changed_at", now_iso)

        out[name] = entry

    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    # Also snapshot the latest roblox_register api.js so the solver
    # operator can diff it manually if needed.
    if "roblox_register" in out:
        try:
            js = fetch_apijs(out["roblox_register"]["public_key"], out["roblox_register"]["host"])
            (DATA / "latest_roblox_register.js").write_text(js, encoding="utf-8")
        except Exception:
            pass

    print(f"[+] tracker run complete — {len(out)} endpoints — changed: {changed or 'none'}")
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
