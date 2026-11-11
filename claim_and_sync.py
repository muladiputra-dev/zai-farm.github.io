#!/usr/bin/env python3
"""
ZCode Claim & Sync Helper
Standalone Package (Sanitized for Multi-tenant & Community use)

Periodically sweeps 9router GLM connections, claims the ZCode Start Plan
(8M daily tokens), and activates/deactivates connections based on quota availability.
"""

import urllib.request
import json
import http.cookiejar
import time
import os
import sys
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"


def load_config():
    cfg = {
        "xrouter_domain": "your-9router.example.com",
        "xrouter_pass": "YourPassword123!"
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw_cfg = json.load(f)
                xr = raw_cfg.get("xrouter", {})
                if xr.get("domain"):
                    cfg["xrouter_domain"] = xr["domain"]
                if xr.get("password"):
                    cfg["xrouter_pass"] = xr["password"]
        except Exception:
            pass
    cfg["xrouter_domain"] = os.environ.get("XROUTER_DOMAIN", cfg["xrouter_domain"])
    cfg["xrouter_pass"] = os.environ.get("XROUTER_PASS", cfg["xrouter_pass"])
    return cfg


CONFIG = load_config()
XROUTER_DOMAIN = CONFIG["xrouter_domain"]
XROUTER_PASS = CONFIG["xrouter_pass"]


def get_opener():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    login_url = f"https://{XROUTER_DOMAIN}/api/auth/login"
    req = urllib.request.Request(
        login_url,
        data=json.dumps({"password": XROUTER_PASS}).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Origin": f"https://{XROUTER_DOMAIN}",
            "Referer": f"https://{XROUTER_DOMAIN}/login"
        }
    )
    opener.open(req)
    return opener


def check_and_claim_zcode(jwt: str, user_id: str):
    headers = {
        "Authorization": f"Bearer {jwt}",
        "X-Device-Mid": str(user_id),
        "X-Platform": "win32-x64",
        "X-ZCode-App-Version": "3.17.1",
        "User-Agent": "ZCode/3.17.1 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"
    }
    endpoints = [
        "https://zcode.z.ai/api/v1/marketing/touch?seq=1",
        "https://zcode.z.ai/api/v1/incentive/referral/summary",
        "https://zcode.z.ai/api/v1/zcode-plan/billing/preview?app_version=3.17.1",
        "https://zcode.z.ai/api/v1/zcode-plan/billing/balance?app_version=3.17.1"
    ]
    plans = []
    for ep in endpoints:
        req = urllib.request.Request(ep, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                res = json.loads(r.read())
                if "balance" in ep:
                    for p in res.get("data", {}).get("plans", []):
                        pid = p.get("plan_id")
                        if pid:
                            plans.append(pid)
        except Exception:
            pass
    return plans


def update_connection_active(opener, conn_id: str, is_active: bool):
    url = f"https://{XROUTER_DOMAIN}/api/providers/{conn_id}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"isActive": is_active}).encode(),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Origin": f"https://{XROUTER_DOMAIN}",
            "Referer": f"https://{XROUTER_DOMAIN}/providers"
        },
        method="PUT"
    )
    resp = opener.open(req)
    return json.loads(resp.read().decode())


def run_sync():
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Starting ZCode Claim & Sync sweep...")
    opener = get_opener()

    # 1. Get GLM connections
    req = urllib.request.Request(
        f"https://{XROUTER_DOMAIN}/api/providers?provider=glm",
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    resp = opener.open(req)
    data = json.loads(resp.read().decode())
    connections = data.get("connections", [])
    print(f"Found {len(connections)} GLM connections in 9router.")

    claimed_count = 0
    disabled_count = 0
    healthy_count = 0
    pending_count = 0

    for c in connections:
        conn_id = c.get("id")
        email = c.get("email") or c.get("name") or conn_id
        is_active = c.get("isActive", True)
        psd = c.get("providerSpecificData", {})
        jwt = psd.get("zcodeJwtToken")
        user_id = psd.get("userId") or str(uuid.uuid4())

        if not jwt:
            continue

        plans = check_and_claim_zcode(jwt, user_id)
        has_start_plan = "zcode-v3-start-plan-0817" in plans

        if has_start_plan:
            if not is_active:
                update_connection_active(opener, conn_id, True)
                print(f"  [CLAIMED & ACTIVATED] {email} -> Start Plan active, enabled in 9router!")
                claimed_count += 1
            else:
                healthy_count += 1
        else:
            if is_active:
                update_connection_active(opener, conn_id, False)
                print(f"  [NO PLAN - DISABLED] {email} -> Quota 0, disabled in 9router to protect routing!")
                disabled_count += 1
            else:
                print(f"  [PENDING MATURATION] {email} -> Quota 0, kept disabled until upstream grants plan.")
                pending_count += 1
        time.sleep(0.5)

    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Sweep summary: {healthy_count} healthy, {claimed_count} newly claimed, {disabled_count} newly disabled, {pending_count} pending maturation.")


if __name__ == "__main__":
    run_sync()
