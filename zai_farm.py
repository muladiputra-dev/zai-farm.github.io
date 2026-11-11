#!/usr/bin/env python3
"""
Z.ai (GLM) Farm Worker - Automated Registration & 9router Linker
Standalone Package (Sanitized for Multi-tenant & Community use)

Features:
- Headless Chromium with stealth args via Playwright
- Aliyun Slider Captcha solver (OpenCV Canny Edge + Template Matching + Eased Drag)
- Configurable Cloudflare Tempmail worker integration (no personal domains hardcoded)
- Automated OAuth2 device authorization on chat.z.ai
- Auto-link & claim to 9router/xrouter
"""

import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
import asyncio
import random
import base64
import math
import json
import urllib.request
import urllib.parse
import re
import os
import quopri
import html
import http.cookiejar
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List
import cv2
import numpy as np
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "accounts.db"
TXT_FILE = BASE_DIR / "accounts.txt"
CONFIG_FILE = BASE_DIR / "config.json"

COOKIE_JAR = http.cookiejar.CookieJar()
OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(COOKIE_JAR))


def load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "xrouter_domain": "your-9router.example.com",
        "xrouter_proto": "https",
        "xrouter_pass": "YourPassword123!",
        "tempmail_domains": ["tempmail.example.com"],
        "tempmail_worker_url": "https://your-tempmail-worker.workers.dev/api/raw?email=",
        "account_password": "SecurePassword123!#",
        "user_display_name": "AI Developer"
    }
    # 1. Baca config.json jika ada
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                raw_cfg = json.load(f)
                xr = raw_cfg.get("xrouter", {})
                tm = raw_cfg.get("tempmail", {})
                fm = raw_cfg.get("farm", {})
                if xr.get("domain"):
                    cfg["xrouter_domain"] = xr["domain"]
                if xr.get("proto"):
                    cfg["xrouter_proto"] = xr["proto"]
                if xr.get("password"):
                    cfg["xrouter_pass"] = xr["password"]
                if tm.get("domains"):
                    cfg["tempmail_domains"] = tm["domains"]
                if tm.get("worker_url"):
                    cfg["tempmail_worker_url"] = tm["worker_url"]
                if fm.get("account_password"):
                    cfg["account_password"] = fm["account_password"]
                if fm.get("user_display_name"):
                    cfg["user_display_name"] = fm["user_display_name"]
        except Exception as e:
            print(f"[!] Warning reading config.json: {e}")

    # 2. Override via environment variables
    cfg["xrouter_domain"] = os.environ.get("XROUTER_DOMAIN", cfg["xrouter_domain"])
    cfg["xrouter_proto"] = os.environ.get("XROUTER_PROTO", cfg["xrouter_proto"])
    cfg["xrouter_pass"] = os.environ.get("XROUTER_PASS", cfg["xrouter_pass"])
    if os.environ.get("TEMPMAIL_DOMAINS"):
        cfg["tempmail_domains"] = [d.strip() for d in os.environ["TEMPMAIL_DOMAINS"].split(",") if d.strip()]
    cfg["tempmail_worker_url"] = os.environ.get("TEMPMAIL_WORKER_URL", cfg["tempmail_worker_url"])
    cfg["account_password"] = os.environ.get("ZAI_ACCOUNT_PASSWORD", cfg["account_password"])
    cfg["user_display_name"] = os.environ.get("ZAI_DISPLAY_NAME", cfg["user_display_name"])

    return cfg


CONFIG = load_config()

if not CONFIG.get("account_password"):
    print("[!] account_password belum diisi (config.json / env ZAI_ACCOUNT_PASSWORD).")
    sys.exit(1)
if CONFIG.get("account_password") == "SecurePassword123!#":
    print("[!] Warning: account_password masih menggunakan nilai contoh dari config.example.json.")
if CONFIG["xrouter_domain"] == "your-9router.example.com":
    print("[!] Konfigurasi belum lengkap: isi xrouter.domain di config.json atau set variabel lingkungan XROUTER_DOMAIN.")
    sys.exit(1)
if not CONFIG.get("tempmail_domains"):
    print("[!] Daftar domain email sementara belum diisi (tempmail.domains).")
    sys.exit(1)

DOMAINS: List[str] = CONFIG["tempmail_domains"]
_domain_idx = random.randint(0, max(1, len(DOMAINS)) - 1)


def record_account(email: str, password: str, status: str = "created", notes: str = ""):
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_FILE))
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS farm_accounts (
                email TEXT PRIMARY KEY,
                password TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'glm',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
        """)
        c.execute("""
            INSERT INTO farm_accounts (email, password, provider, status, notes)
            VALUES (?, ?, 'glm', ?, ?)
            ON CONFLICT(email) DO UPDATE SET status = excluded.status, notes = excluded.notes
        """, (email, password, status, notes))
        conn.commit()
        conn.close()

        try:
            existing = set()
            if TXT_FILE.exists():
                existing = {x.split("|")[0].strip() for x in open(TXT_FILE, encoding="utf-8") if x.strip()}
            if email not in existing:
                with open(TXT_FILE, "a", encoding="utf-8") as f:
                    f.write(f"{email} | {password}\n")
        except Exception:
            pass
    except Exception as e:
        print(f"[db-error] {e}")


def next_domain() -> str:
    global _domain_idx
    if not DOMAINS:
        return "example.com"
    d = DOMAINS[_domain_idx % len(DOMAINS)]
    _domain_idx += 1
    return d


def get_worker_for_domain(domain: str) -> str:
    # URL worker diambil dari konfigurasi (single worker multi-domain atau default endpoint)
    base_url = CONFIG["tempmail_worker_url"]
    if not base_url.endswith("=") and "?" not in base_url:
        if base_url.endswith("/"):
            base_url += "api/raw?email="
        else:
            base_url += "/api/raw?email="
    return base_url


def get_proxy_file() -> Optional[Path]:
    candidates = [
        BASE_DIR / "proxies.txt",
        Path("/data/home/proxies.txt"),
        Path("/root/proxies.txt")
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


_proxy_idx = random.randint(0, 50)


def next_proxy() -> Optional[Dict[str, Any]]:
    global _proxy_idx
    p_file = get_proxy_file()
    if not p_file:
        return None
    try:
        lines = [l.strip() for l in open(p_file, encoding="utf-8") if l.strip() and not l.startswith("#")]
        if not lines:
            return None
        raw = lines[_proxy_idx % len(lines)]
        _proxy_idx += 1
        p = urllib.parse.urlparse(raw if "://" in raw else f"http://{raw}")
        d = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username:
            d["username"] = p.username
        if p.password:
            d["password"] = p.password
        return d
    except Exception as e:
        print(f"[!] Error loading proxy: {e}")
        return None


def solve_drag_distance(gap_x):
    a = 0.00355
    b = 0.0769
    c = -gap_x
    d = b * b - 4 * a * c
    if d < 0:
        return gap_x
    return (-b + math.sqrt(d)) / (2 * a)


async def do_drag(cdp, sx, sy, dist_x):
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": sx, "y": sy, "buttons": 0})
    await asyncio.sleep(0.05)
    await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": sx, "y": sy, "button": "left", "buttons": 1, "clickCount": 1})
    await asyncio.sleep(0.08)

    steps = random.randint(22, 30)
    curr_x = sx
    for step in range(1, steps + 1):
        t = step / steps
        ease = 1 - math.pow(1 - t, 3)
        target_x = sx + dist_x * ease
        jitter_y = sy + random.uniform(-1.0, 1.0)
        await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": target_x, "y": jitter_y, "buttons": 1})
        curr_x = target_x
        await asyncio.sleep(random.uniform(0.012, 0.024))

    await asyncio.sleep(0.12)
    await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": curr_x, "y": sy, "button": "left", "buttons": 0, "clickCount": 1})


def get_xrouter_device_code():
    domain = CONFIG["xrouter_domain"]
    password = CONFIG["xrouter_pass"]
    login_url = f"{CONFIG.get('xrouter_proto', 'https')}://{domain}/api/auth/login"
    login_data = json.dumps({"password": password}).encode()
    req = urllib.request.Request(
        login_url,
        data=login_data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    OPENER.open(req)

    code_url = f"{CONFIG.get('xrouter_proto', 'https')}://{domain}/api/oauth/glm/device-code"
    req_code = urllib.request.Request(code_url, headers={"User-Agent": "Mozilla/5.0"})
    resp_code = OPENER.open(req_code)
    return json.loads(resp_code.read().decode())


async def poll_tempmail_async(email: str, worker_url: str, timeout_s: int = 60) -> Optional[str]:
    url = f"{worker_url}{urllib.parse.quote(email)}"
    for _ in range(timeout_s // 3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
                raw = (data.get("raw") or "") + "\n" + (data.get("html") or "")
                if raw.strip():
                    clean_qp = quopri.decodestring(raw.encode()).decode("utf-8", errors="ignore")
                    m = re.search(r'href=[\'"](https://chat\.z\.ai/auth/verify_email\?[^\'"]+)[\'"]', clean_qp)
                    if m:
                        return html.unescape(m.group(1))
                    m2 = re.search(r'(https://chat\.z\.ai/auth/verify_email\?[^\s<>"\'\)]+)', clean_qp)
                    if m2:
                        return html.unescape(m2.group(1))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print(f"  [tempmail] HTTP {e.code}")
        except Exception:
            pass
        await asyncio.sleep(3)
    return None


async def solve_aliyun(page, cdp, last_verify):
    try:
        if await page.locator("#captcha-element").count() > 0:
            try:
                await page.click("#captcha-element", timeout=3000)
            except Exception:
                await page.evaluate('() => { const el = document.getElementById("captcha-element"); if (el) el.click(); }')
            await page.wait_for_timeout(1500)

        body_el = await page.wait_for_selector("#aliyunCaptcha-captcha-body, #aliyunCaptcha-captcha-left, #aliyunCaptcha-captcha-wrapper", timeout=12000)
        try:
            await body_el.click(timeout=2000)
        except Exception:
            pass
        await page.wait_for_timeout(2500)
    except Exception as e:
        print(f"  [aliyun] wait captcha body err: {e}")
        return False

    handle = await page.evaluate('''() => {
        const s = document.getElementById("aliyunCaptcha-sliding-slider");
        if (!s) return null;
        const r = s.getBoundingClientRect();
        return { x: r.x, y: r.y, w: r.width, h: r.height };
    }''')
    if not handle:
        print("  [aliyun] slider handle not found")
        return False

    sx = handle["x"] + handle["w"] / 2
    sy = handle["y"] + handle["h"] / 2

    def decode_img_src(src: str):
        if not src:
            return None
        try:
            if src.startswith("data:"):
                data = base64.b64decode(src.split(",", 1)[1])
            else:
                req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = r.read()
            return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
        except Exception as e:
            print(f"  [aliyun] decode error for {src[:30]}: {e}")
            return None

    for p_attempt in range(1, 5):
        sources = await page.evaluate('''() => ({
            img: document.getElementById("aliyunCaptcha-img") ? document.getElementById("aliyunCaptcha-img").src : null,
            puzzle: document.getElementById("aliyunCaptcha-puzzle") ? document.getElementById("aliyunCaptcha-puzzle").src : null
        })''')
        if not sources.get("img") or not sources.get("puzzle"):
            print(f"  [aliyun] puzzle #{p_attempt} img or puzzle src missing")
            await asyncio.sleep(1.5)
            continue

        bg = decode_img_src(sources["img"])
        piece = decode_img_src(sources["puzzle"])
        if bg is None or piece is None:
            print(f"  [aliyun] puzzle #{p_attempt} image decode failed")
            await asyncio.sleep(1.5)
            continue

        try:
            alpha = piece[:, :, 3]
            y_idx, x_idx = np.where(alpha > 30)
            crop_piece = piece[y_idx.min():y_idx.max()+1, x_idx.min():x_idx.max()+1]
            edges_bg = cv2.Canny(bg, 100, 200)
            edges_piece = cv2.Canny(crop_piece[:, :, :3], 100, 200)
            res = cv2.matchTemplate(edges_bg[:, 35:220], edges_piece, cv2.TM_CCOEFF)
            gap_x = cv2.minMaxLoc(res)[3][0] + 35
            center = int(round(solve_drag_distance(gap_x)))
            print(f"  [aliyun] puzzle #{p_attempt} gap_x: {gap_x} -> center: {center}px")
        except Exception as e:
            print(f"  [aliyun] puzzle #{p_attempt} match err: {e}")
            center = 175

        tries = [center, center - 2, center + 2, center - 4, center + 4]
        for drag_px in tries:
            last_verify["code"] = None
            await do_drag(cdp, sx, sy, drag_px)
            for _ in range(35):
                await asyncio.sleep(0.1)
                if last_verify["code"]:
                    break
            code = last_verify["code"]
            print(f"  [aliyun] #{p_attempt} drag {drag_px}px -> {code}")
            if code == "T001":
                print(f"  [aliyun] T001 HIT at {drag_px}px on puzzle #{p_attempt}!")
                return True

            for _ in range(15):
                await asyncio.sleep(0.2)
                cur_x = await page.evaluate('''() => {
                    const s = document.getElementById("aliyunCaptcha-sliding-slider");
                    return s ? s.getBoundingClientRect().x : 0;
                }''')
                if abs(cur_x - handle["x"]) < 5:
                    break
            await asyncio.sleep(0.3)

        print(f"  [aliyun] puzzle #{p_attempt} failed, clicking refresh...")
        await page.evaluate('''() => {
            const b = document.getElementById("aliyunCaptcha-btn-refresh");
            if (b) b.click();
        }''')
        await asyncio.sleep(2.0)

    return False


def claim_and_protect_connection(conn: dict, email: str):
    domain = CONFIG["xrouter_domain"]
    account_pass = CONFIG["account_password"]
    conn_id = conn.get("id")
    psd = conn.get("providerSpecificData", {})
    jwt = psd.get("zcodeJwtToken")
    user_id = psd.get("userId") or str(uuid.uuid4())

    if not jwt or not conn_id:
        return

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
    has_plan = False
    for ep in endpoints:
        req = urllib.request.Request(ep, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                res = json.loads(r.read())
                if "balance" in ep:
                    for p in res.get("data", {}).get("plans", []):
                        if p.get("plan_id") == "zcode-v3-start-plan-0817":
                            has_plan = True
        except Exception as e:
            print(f"[ZCode Handshake] {ep.split('/')[-1]} err: {e}")

    if has_plan:
        print(f"[ZCode Handshake] SUCCESS: {email} Start Plan active (8M daily tokens)!")
        record_account(email, account_pass, status="active_claimed", notes="Linked & Plan Active")
    else:
        print(f"[ZCode Handshake] Plan not granted yet for {email}. Disabling in 9router to protect user traffic...")
        try:
            url = f"{CONFIG.get('xrouter_proto', 'https')}://{domain}/api/providers/{conn_id}"
            req = urllib.request.Request(
                url,
                data=json.dumps({"isActive": False}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0",
                    "Origin": f"{CONFIG.get('xrouter_proto', 'https')}://{domain}",
                    "Referer": f"{CONFIG.get('xrouter_proto', 'https')}://{domain}/providers"
                },
                method="PUT"
            )
            OPENER.open(req)
            record_account(email, account_pass, status="pending_plan", notes="Disabled pending claim")
            print(f"[ZCode Handshake] Successfully disabled {conn_id} ({email}) in 9router.")
        except Exception as e:
            print(f"[ZCode Handshake] Failed to disable {conn_id}: {e}")


async def harvest_one_zai(use_proxy: bool = True):
    print(">>> 1. Getting device code from xrouter...")
    dc = get_xrouter_device_code()
    device_code = dc["device_code"]
    verify_uri = dc["verification_uri"]
    poll_token = dc.get("_zcodePollToken")
    family = dc.get("_zcodeFamily", "zai")
    print(f"Device code: {device_code}")
    print(f"Verify URI: {verify_uri}")

    cur_domain = next_domain()
    worker_url = get_worker_for_domain(cur_domain)
    email = f"user_{random.randint(10000, 99999)}@{cur_domain}"
    password = CONFIG["account_password"]
    display_name = CONFIG["user_display_name"]
    print(f">>> 2. Target Account: {email} (Domain: {cur_domain})")

    launch_kwargs = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--window-size=1920,1080",
        ]
    }
    proxy_dict = next_proxy() if use_proxy else None
    if proxy_dict:
        print(f">>> Using proxy: {proxy_dict.get('server')}")
        launch_kwargs["proxy"] = proxy_dict

    async with async_playwright() as p:
        b = await p.chromium.launch(**launch_kwargs)
        ctx = await b.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)

        last_verify = {"code": None}
        async def handle_resp(r):
            if "verify" in r.url.lower():
                try:
                    data = json.loads(await r.text())
                    code = data.get("Result", {}).get("VerifyCode") or data.get("data", {}).get("VerifyCode")
                    if code:
                        last_verify["code"] = code
                except:
                    pass
        page.on("response", lambda r: asyncio.create_task(handle_resp(r)))

        # A. SIGN UP
        print(">>> 3. Navigating to /auth for Signup...")
        try:
            await page.goto("https://chat.z.ai/auth", wait_until="domcontentloaded", timeout=35000)
            await page.wait_for_selector("button", timeout=15000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            print(f"Navigation error: {e}")
            await b.close()
            return False

        # Click Continue with Email if present
        for _ in range(10):
            clicked_email = await page.evaluate("""() => {
                const b = Array.from(document.querySelectorAll('button')).find(x => x.innerText.toLowerCase().includes('email'));
                if (b) { b.click(); return true; }
                return false;
            }""")
            if clicked_email:
                await page.wait_for_timeout(2000)
                break
            await page.wait_for_timeout(1000)

        # Switch to Sign up tab via JS
        switched = False
        for _ in range(5):
            switched = await page.evaluate('''() => {
                const btn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.trim() === 'Sign up');
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            }''')
            if switched:
                break
            await page.wait_for_timeout(1000)
        print(f"Switched to Sign up tab: {switched}")
        await page.wait_for_timeout(2000)

        # Wait for form inputs
        await page.wait_for_selector('input[name="email"]', timeout=15000)
        name_input = page.locator('input[autocomplete="name"]').first
        if await name_input.count() > 0:
            await name_input.fill(display_name)
        await page.locator('input[name="email"]').fill(email)
        await page.locator('input[name="new-password"]').fill(password)
        await page.wait_for_timeout(1000)

        print(">>> Solving Signup Aliyun Captcha...")
        if not await solve_aliyun(page, cdp, last_verify):
            print("Signup captcha failed!")
            await b.close()
            return False

        await page.wait_for_timeout(2000)
        print(">>> Submitting Create Account...")
        await page.locator('button[type="submit"]').first.click()
        await page.wait_for_timeout(4000)

        # B. EMAIL ACTIVATION
        print(f">>> 4. Polling activation link for {email}...")
        vlink = await poll_tempmail_async(email, worker_url, timeout_s=60)
        if not vlink:
            print("Activation link not found in tempmail!")
            await b.close()
            return False
        print(f"Got activation link: {vlink[:65]}...")

        # C. COMPLETE REGISTRATION (SET PASSWORD)
        print(">>> 5. Opening activation link to complete registration...")
        await page.goto(vlink, wait_until="load", timeout=25000)
        await page.wait_for_timeout(3000)

        await page.locator("#password").fill(password)
        await page.locator("#confirmPassword").fill(password)
        await page.wait_for_timeout(1000)

        print(">>> Clicking Complete Registration button...")
        await page.locator('button:has-text("Complete Registration")').click()
        await page.wait_for_timeout(5000)

        print(f"URL after Complete Registration: {page.url}")
        token = await page.evaluate('() => localStorage.token || ""')
        print(f"LocalStorage token present: {bool(token)}")

        if not token:
            print("Token missing after Complete Registration, aborting!")
            await b.close()
            return False

        # D. OAUTH AUTHORIZE (NOW WE ARE FULLY AUTHENTICATED ON CHAT.Z.AI)
        print(">>> 6. Approving OAuth via in-page API call...")
        approve_res = await page.evaluate(f'''async () => {{
            const verifyUri = "{verify_uri}";
            const urlObj = new URL(verifyUri);
            const bodyParams = new URLSearchParams();
            bodyParams.append("client_id", urlObj.searchParams.get("client_id") || "client_P8X5CMWmlaRO9gyO-KSqtg");
            bodyParams.append("redirect_uri", urlObj.searchParams.get("redirect_uri") || "https://zcode.z.ai/api/v1/oauth/cli/callback/zai");
            bodyParams.append("state", urlObj.searchParams.get("state") || "");
            bodyParams.append("response_type", urlObj.searchParams.get("response_type") || "code");
            bodyParams.append("action", "approve");

            const token = localStorage.token || "";
            const headers = {{"Content-Type": "application/x-www-form-urlencoded"}};
            if (token) headers["Authorization"] = "Bearer " + token;

            try {{
                const res = await fetch("/api/oauth/authorize", {{
                    method: "POST",
                    headers: headers,
                    credentials: "include",
                    body: bodyParams.toString()
                }});
                return {{ ok: true, status: res.status, data: await res.json() }};
            }} catch (e) {{
                return {{ ok: false, error: String(e) }};
            }}
        }}''')
        print(f"Approve result: {approve_res}")

        if approve_res.get("ok") and approve_res.get("data", {}).get("redirect_url"):
            red_url = approve_res["data"]["redirect_url"]
            print(f"Following redirect: {red_url}")
            await page.goto(red_url, wait_until="networkidle")
            await page.wait_for_timeout(3000)

        # E. POLL XROUTER
        print(">>> 7. Polling xrouter to complete link...")
        domain = CONFIG["xrouter_domain"]
        poll_payload = json.dumps({
            "deviceCode": device_code,
            "extraData": {
                "_zcodePollToken": poll_token,
                "_zcodeFamily": family
            }
        }).encode()

        linked = False
        for attempt in range(15):
            req = urllib.request.Request(
                f"{CONFIG.get('xrouter_proto', 'https')}://{domain}/api/oauth/glm/poll",
                data=poll_payload,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            try:
                poll_resp = OPENER.open(req)
                pdata = json.loads(poll_resp.read().decode())
                if pdata.get("success") is True:
                    print(f"SUCCESS! Connection linked: {pdata.get('connection')}")
                    record_account(email, password, status="active_linked", notes="Linked to 9router")
                    conn_obj = pdata.get("connection") or {}
                    claim_and_protect_connection(conn_obj, email)
                    linked = True
                    break
            except Exception as e:
                print(f"Poll attempt {attempt+1} err: {e}")
            await asyncio.sleep(2)

        await b.close()
        return linked


if __name__ == "__main__":
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Z.ai Farm Runner (Sanitized Edition)")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--no-proxy", action="store_true", help="Run without proxy")
    parser.add_argument("--interval", type=int, default=None, help="Seconds between runs (default: farm.interval_seconds in config.json, else 1800s / 30m)")
    args = parser.parse_args()

    use_proxy = not args.no_proxy

    if args.once:
        success = asyncio.run(harvest_one_zai(use_proxy=use_proxy))
        print(f"FINISHED: success={success}")
        sys.exit(0 if success else 1)
    else:
        print(f"Starting Z.ai Farm Loop (interval: {args.interval}s, use_proxy={use_proxy})...")
        while True:
            try:
                success = asyncio.run(harvest_one_zai(use_proxy=use_proxy))
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Cycle completed. Success: {success}")
            except Exception as e:
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Cycle error: {e}")
            try:
                from claim_and_sync import run_sync
                run_sync()
            except Exception as sync_err:
                print(f"[Claim & Sync] error: {sync_err}")
            wait_time = args.interval if args.interval else CONFIG["farm"].get("interval_seconds", 1800)
            print(f"Sleeping {wait_time}s until next cycle...")
            time.sleep(wait_time)
