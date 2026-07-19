#!/usr/bin/env python3
"""Health-check monitor for the society-site.

Pings the site URL periodically. If the site is unreachable after MAX_RETRIES
consecutive failures, emails the site admin. The alert is suppressed for
COOLDOWN_MINUTES after a previous alert to avoid spamming.

Usage:  uv run python scripts/healthcheck.py
"""
from __future__ import annotations

import os
import sys
import time
import json
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --- Configurable ---
MAX_RETRIES = int(os.environ.get("HEALTHCHECK_RETRIES", "3"))
RETRY_DELAY = int(os.environ.get("HEALTHCHECK_RETRY_DELAY", "30"))   # seconds
TIMEOUT = int(os.environ.get("HEALTHCHECK_TIMEOUT", "15"))           # seconds per request
COOLDOWN_MINUTES = int(os.environ.get("HEALTHCHECK_COOLDOWN", "60"))  # don't re-alert within this window
STATE_FILE = PROJECT_ROOT / "var" / "healthcheck-state.json"


def _load_env() -> None:
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _site_url() -> str:
    url = os.environ.get("SITE_URL", "").strip()
    if url:
        return url

    domain = os.environ.get("CLOUDFLARE_DOMAIN", "").strip()
    if domain:
        sub = os.environ.get("CLOUDFLARE_SUBDOMAIN", "").strip()
        host = f"{sub}.{domain}" if sub else domain
        return f"https://{host}"

    port = os.environ.get("PORT", "5005").strip()
    return f"http://127.0.0.1:{port}"


def _admin_email() -> str:
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    if email:
        return email
    # Fallback: read from MAIL_FROM (often the same address)
    return os.environ.get("MAIL_FROM", "").strip()


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"failures": 0, "last_alert": None}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"failures": 0, "last_alert": None}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def _send_alert(url: str, status: str) -> None:
    domain = os.environ.get("CLOUDFLARE_DOMAIN", url)
    body = (
        f"The society-site at {url} appears to be down.\n\n"
        f"Status: {status}\n"
        f"Time:   {datetime.now(timezone.utc).isoformat()}\n\n"
        f"Check gunicorn logs and systemctl status society-site for details.\n"
    )
    # Import the app's mail helper (reads SMTP env vars at send time, no
    # Flask context required).
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from app.services.mail import send_mail
    except ImportError:
        print("[healthcheck] ALERT would fire, but couldn't import mail helper.")
        print(body)
        return

    to = _admin_email()
    if not to:
        print("[healthcheck] No ADMIN_EMAIL or MAIL_FROM configured — can't send alert.")
        print(body)
        return

    ok = send_mail(to, f"[ALERT] Site down — {domain}", body)
    if ok:
        print(f"[healthcheck] Alert sent to {to}")
    else:
        print(f"[healthcheck] Failed to send alert to {to}")


def main() -> None:
    _load_env()
    url = _site_url()
    state = _load_state()

    # Check if we're in cooldown
    last_alert = state.get("last_alert")
    if last_alert:
        last_time = datetime.fromisoformat(last_alert)
        if datetime.now(timezone.utc) - last_time < timedelta(minutes=COOLDOWN_MINUTES):
            # Still in cooldown — just do the check but don't alert again
            pass

    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if 200 <= resp.status < 400:
                # Site is up — reset failures
                if state["failures"] > 0:
                    state["failures"] = 0
                    _save_state(state)
                print(f"[healthcheck] OK ({resp.status})")
                return
            else:
                raise urllib.error.HTTPError(url, resp.status, "bad status", resp.headers, None)
    except Exception as e:
        status = str(e)
        state["failures"] = state.get("failures", 0) + 1
        _save_state(state)
        failures = state["failures"]
        print(f"[healthcheck] FAIL ({failures}/{MAX_RETRIES}): {status}")

        if failures < MAX_RETRIES:
            # Retry after delay
            for attempt in range(MAX_RETRIES - failures):
                time.sleep(RETRY_DELAY)
                try:
                    with urllib.request.urlopen(
                        urllib.request.Request(url, method="GET"), timeout=TIMEOUT
                    ) as resp:
                        if 200 <= resp.status < 400:
                            state["failures"] = 0
                            _save_state(state)
                            print(f"[healthcheck] Recovered after {attempt+1} retry(ies)")
                            return
                except Exception as e2:
                    state["failures"] += 1
                    _save_state(state)
                    print(f"[healthcheck] Retry {attempt+1} failed: {e2}")

        # All retries exhausted — send alert if not in cooldown
        in_cooldown = False
        if last_alert:
            last_time = datetime.fromisoformat(last_alert)
            in_cooldown = (datetime.now(timezone.utc) - last_time) < timedelta(minutes=COOLDOWN_MINUTES)

        if not in_cooldown:
            _send_alert(url, str(status))
            state["last_alert"] = datetime.now(timezone.utc).isoformat()
            _save_state(state)
        else:
            print("[healthcheck] Alert suppressed (cooldown).")


if __name__ == "__main__":
    main()
