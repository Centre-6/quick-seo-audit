#!/usr/bin/env python3
"""Shared DataForSEO credential resolution and request helper.

One module so the rules about where credentials come from, and how they are
handled, are stated once rather than drifting across four scripts.

The credential is never printed, never placed in a URL, and never passed as a
command-line argument. All three leak: a URL into server logs and referrer
headers, an argument into the process list where any other user on the machine
can read it, and a print into whatever captures your terminal.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

BASE = "https://api.dataforseo.com"
US, EN = 2840, "en"


def auth_header() -> str:
    """Credentials, in order of preference.

    1. DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD
    2. DATAFORSEO_BASE64, a pre-built base64 of login:password
    3. DATAFORSEO_SECRETS_DIR, a directory holding dataforseo-api-base64.key,
       or dataforseo-api-login.key plus dataforseo-api-password.key
    """
    login = os.environ.get("DATAFORSEO_LOGIN")
    pw = os.environ.get("DATAFORSEO_PASSWORD")
    if login and pw:
        return "Basic " + base64.b64encode(f"{login}:{pw}".encode()).decode()
    if os.environ.get("DATAFORSEO_BASE64"):
        return "Basic " + os.environ["DATAFORSEO_BASE64"].strip()

    raw = os.environ.get("DATAFORSEO_SECRETS_DIR", "")
    if raw:
        secrets = Path(raw).expanduser()
        b64 = secrets / "dataforseo-api-base64.key"
        if b64.exists():
            return "Basic " + b64.read_text().strip()
        lf, pf = (secrets / "dataforseo-api-login.key",
                  secrets / "dataforseo-api-password.key")
        if lf.exists() and pf.exists():
            return ("Basic " + base64.b64encode(
                f"{lf.read_text().strip()}:{pf.read_text().strip()}".encode()).decode())

    raise SystemExit(
        "No DataForSEO credentials found. Set DATAFORSEO_LOGIN and "
        "DATAFORSEO_PASSWORD, or DATAFORSEO_BASE64, or point "
        "DATAFORSEO_SECRETS_DIR at a directory holding the key files.")


def post(path: str, payload, timeout: int = 120):
    """POST to the API. Returns (first_result, error_string). Never raises on an
    API-level error, because the caller usually wants to report the error text
    rather than crash in the middle of an audit."""
    import requests
    r = requests.post(BASE + path,
                      headers={"Authorization": auth_header(),
                               "Content-Type": "application/json"},
                      json=payload, timeout=timeout)
    body = r.json()
    tasks = body.get("tasks") or []
    if not tasks:
        return None, f"no tasks in response: {body.get('status_message')}"
    t = tasks[0]
    if (t.get("status_code") or 0) >= 40000:
        return None, f"{t.get('status_code')} {t.get('status_message')}"
    res = t.get("result") or []
    return (res[0] if res else None), None
