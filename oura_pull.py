"""TwoPandas: pull Oura Ring data via the Oura API V2 (personal use).

Usage:
  python oura_pull.py auth            # owner signs in on this computer
  python oura_pull.py auth --paste    # owner approves on his device, you paste the redirect URL
  python oura_pull.py pull --days 30  # write CSVs to data/

Standard library only; Python 3.9+.
"""

import argparse
import csv
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

AUTH_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
API_BASE = "https://api.ouraring.com/v2/usercollection"
SCOPES = "personal daily heartrate workout session spo2"

HERE = Path(__file__).resolve().parent
TOKENS_FILE = HERE / "tokens.json"
DATA_DIR = HERE / "data"

# Date-based endpoints (start_date / end_date params)
ENDPOINTS = ["daily_sleep", "daily_readiness", "daily_activity", "daily_spo2", "sleep"]


# ---------- config ----------

def load_env():
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    cfg = {
        "client_id": os.environ.get("OURA_CLIENT_ID", ""),
        "client_secret": os.environ.get("OURA_CLIENT_SECRET", ""),
        "redirect_uri": os.environ.get("OURA_REDIRECT_URI", "http://localhost:8989/callback"),
    }
    if not cfg["client_id"] or not cfg["client_secret"] or "paste-" in cfg["client_id"]:
        sys.exit("Missing credentials: copy .env.example to .env and fill in the Client ID and Secret.")
    return cfg


# ---------- tokens ----------

def post_form(url, fields):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"Token request failed ({e.code}): {e.read().decode(errors='replace')}")


def save_tokens(tok):
    tok["expires_at"] = int(time.time()) + int(tok.get("expires_in", 0)) - 60
    TOKENS_FILE.write_text(json.dumps(tok, indent=2))
    try:
        os.chmod(TOKENS_FILE, 0o600)
    except OSError:
        pass


def exchange_code(cfg, code):
    tok = post_form(TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg["redirect_uri"],
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
    })
    save_tokens(tok)
    print("Authorized. Tokens saved to tokens.json.")


def access_token(cfg):
    if not TOKENS_FILE.exists():
        sys.exit("Not authorized yet: run `python oura_pull.py auth` first.")
    tok = json.loads(TOKENS_FILE.read_text())
    if time.time() >= tok.get("expires_at", 0):
        new = post_form(TOKEN_URL, {
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        })
        new.setdefault("refresh_token", tok["refresh_token"])
        save_tokens(new)
        tok = new
    return tok["access_token"]


# ---------- auth flow ----------

def build_auth_url(cfg, state):
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": SCOPES,
        "state": state,
    })


def parse_redirect(url, state):
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if "error" in params:
        sys.exit(f"Authorization denied: {params['error'][0]}")
    if params.get("state", [""])[0] != state:
        sys.exit("State mismatch: start over with `auth`.")
    if "code" not in params:
        sys.exit("No authorization code found in that URL.")
    return params["code"][0]


def auth_local(cfg):
    state = secrets.token_urlsafe(16)
    redirect = urllib.parse.urlparse(cfg["redirect_uri"])
    result = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith(redirect.path):
                result["url"] = f"http://{redirect.netloc}{self.path}"
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Authorization received. You can close this tab.")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer((redirect.hostname, redirect.port or 80), Handler)
    url = build_auth_url(cfg, state)
    print("Opening the Oura sign-in page. If it doesn't open, visit:\n" + url)
    webbrowser.open(url)
    while "url" not in result:
        server.handle_request()
    server.server_close()
    exchange_code(cfg, parse_redirect(result["url"], state))


def auth_paste(cfg):
    state = secrets.token_urlsafe(16)
    print("Send this link to the account owner. He signs in and approves access:\n")
    print(build_auth_url(cfg, state))
    print("\nHis browser will then land on a page that won't load. He copies that full")
    print("address (starts with the redirect URI) and sends it back within a few minutes.\n")
    pasted = input("Paste the full redirect address here: ").strip()
    exchange_code(cfg, parse_redirect(pasted, state))


# ---------- data ----------

def api_get(token, endpoint, params):
    rows, next_token = [], None
    while True:
        q = dict(params)
        if next_token:
            q["next_token"] = next_token
        req = urllib.request.Request(
            f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(q)}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as e:
            print(f"  {endpoint}: HTTP {e.code} ({e.read().decode(errors='replace')[:200]})")
            return rows
        rows.extend(payload.get("data", []))
        next_token = payload.get("next_token")
        if not next_token:
            return rows


def flatten(row):
    flat = {}
    for key, value in row.items():
        if isinstance(value, dict):
            for sub, subval in value.items():
                if not isinstance(subval, (dict, list)):
                    flat[f"{key}.{sub}"] = subval
        elif not isinstance(value, list):
            flat[key] = value
    return flat


def write_csv(name, rows):
    if not rows:
        print(f"  {name}: no data")
        return
    flat = [flatten(r) for r in rows]
    fields = sorted({k for r in flat for k in r})
    for pref in ("score", "day", "id"):
        if pref in fields:
            fields.insert(0, fields.pop(fields.index(pref)))
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / f"{name}.csv"
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flat)
    print(f"  {name}: {len(rows)} rows -> {path.relative_to(HERE)}")


def pull(cfg, days):
    token = access_token(cfg)
    end = date.today()
    start = end - timedelta(days=days)
    params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    print(f"Pulling {start} to {end}")
    for endpoint in ENDPOINTS:
        write_csv(endpoint, api_get(token, endpoint, params))


# ---------- main ----------

def main():
    parser = argparse.ArgumentParser(description="TwoPandas Oura data puller")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auth", help="authorize access (one time)")
    a.add_argument("--paste", action="store_true", help="owner approves on his own device")
    p = sub.add_parser("pull", help="download data to CSV")
    p.add_argument("--days", type=int, default=30)
    args = parser.parse_args()

    cfg = load_env()
    if args.cmd == "auth":
        auth_paste(cfg) if args.paste else auth_local(cfg)
    else:
        pull(cfg, args.days)


if __name__ == "__main__":
    main()
