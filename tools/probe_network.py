# -*- coding: utf-8 -*-
"""probe_network.py — ดักดู network request ของ ISURVEY ตอนรันอ่านจริง (READ-ONLY)

เป้าหมาย: ประเมินว่า "ยิง API ฝั่งอ่านตรงๆ" แทนการ scrape browser ได้แค่ไหน
ทำอะไร:
  1. เปิด Chrome พร้อม performance log (CDP Network) — ดักทุก request/response
  2. login → เปิดเคลม → อ่านครบทุก tab → กดปุ่ม export XML  (ไม่เขียน ไม่ยุ่ง EMCS)
  3. สรุป endpoint ที่น่าสนใจ (XHR/Fetch/JSON/XML) + บันทึกดิบลงไฟล์
  4. ลอง "replay" เฉพาะ GET ที่เป็น json/xml ด้วย cookie ผ่าน urllib
     → ถ้าได้ข้อมูลกลับมา = ยิง API ตรงนอก browser ได้จริง (ฝั่งอ่านเปลี่ยนเป็น HTTP ได้)

ใช้:  python tools/probe_network.py <เลขเคลม> [เลขเซอร์เวย์]
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # ให้ import autokey ได้

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from autokey import isurvey, images
from autokey.browser import log, log_plain
from autokey.config import load_config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def make_logging_driver(download_dir: Path):
    """Chrome ที่เปิด performance log (Network domain) เพื่อดักทุก request"""
    download_dir.mkdir(parents=True, exist_ok=True)
    options = Options()
    options.add_experimental_option(
        "excludeSwitches", ["disable-popup-blocking", "enable-automation"])
    options.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "download.prompt_for_download": False,
        "download.default_directory": str(download_dir),
        "profile.default_content_setting_values.automatic_downloads": 1,
    })
    options.add_argument("--start-maximized")
    options.add_experimental_option("detach", True)
    # เปิด performance log + ให้รวม event ของ Network domain
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    options.add_experimental_option(
        "perfLoggingPrefs", {"enableNetwork": True, "enablePage": False})
    return webdriver.Chrome(options=options)


def collect_requests(driver) -> dict:
    """ดึง performance log แล้วรวมเป็น map: requestId -> ข้อมูล request/response"""
    reqs = {}
    for entry in driver.get_log("performance"):
        try:
            msg = json.loads(entry["message"])["message"]
        except Exception:
            continue
        method = msg.get("method", "")
        p = msg.get("params", {})
        rid = p.get("requestId")
        if not rid:
            continue
        slot = reqs.setdefault(rid, {})
        if method == "Network.requestWillBeSent":
            r = p.get("request", {})
            slot.setdefault("url", r.get("url"))
            slot.setdefault("method", r.get("method"))
            if r.get("postData"):
                # redact รหัสผ่าน/secret ไม่ให้ลง log/ไฟล์ dump
                pd = re.sub(r"(?i)(password|passwd|pwd|token)=[^&\s]*",
                            r"\1=***", r["postData"][:500])
                slot["postData"] = pd
            if p.get("type"):
                slot["type"] = p.get("type")
        elif method == "Network.responseReceived":
            resp = p.get("response", {})
            slot["status"] = resp.get("status")
            slot["mimeType"] = resp.get("mimeType")
            slot["type"] = p.get("type", slot.get("type"))
            if resp.get("url"):
                slot.setdefault("url", resp.get("url"))
    return reqs


def is_data_candidate(info: dict) -> bool:
    """endpoint ที่น่าจะเป็น 'ข้อมูล' (ไม่ใช่รูป/css/js)"""
    mime = (info.get("mimeType") or "").lower()
    url = (info.get("url") or "").lower()
    typ = (info.get("type") or "").lower()
    if any(k in mime for k in ("json", "xml")):
        return True
    if typ in ("xhr", "fetch"):
        return True
    if any(k in url for k in ("export", "getdata", "ajax", ".php?", "list", "report")):
        # ตัด static ออก
        if not any(url.endswith(ext) for ext in (".js", ".css", ".png", ".jpg", ".gif", ".woff", ".woff2")):
            return True
    return False


def short(u: str, n: int = 110) -> str:
    return u if len(u) <= n else u[:n] + "…"


def replay_get(url: str, cookie_header: str, ua: str) -> str:
    """ลองยิง GET ซ้ำด้วย cookie ของ browser → คืนสรุปผล (READ-ONLY)"""
    try:
        req = urllib.request.Request(url, headers={
            "Cookie": cookie_header,
            "User-Agent": ua,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/xml, */*",
        })
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read(1200)
            ctype = r.headers.get("Content-Type", "")
            txt = body.decode("utf-8", "replace").strip().replace("\n", " ")
            return f"HTTP {r.status} [{ctype}] {len(body)}B+ → {short(txt, 200)}"
    except Exception as e:
        return f"replay ไม่ได้: {type(e).__name__}: {e}"


def main():
    if len(sys.argv) < 2:
        print("ใช้:  python tools/probe_network.py <เลขเคลม> [เลขเซอร์เวย์]")
        sys.exit(1)
    claim = sys.argv[1].strip()
    invoice = sys.argv[2].strip() if len(sys.argv) > 2 else ""

    cfg = load_config()
    logs_dir = cfg.runs_dir / "logs"
    dl_dir = cfg.download_dir / "_probe"
    driver = make_logging_driver(dl_dir)

    log_plain("=" * 60)
    log_plain(f"  PROBE NETWORK ISURVEY (read-only) — เคลม {claim}")
    log_plain("=" * 60)

    try:
        # ลำดับเดียวกับ main.read_one_claim: login → เปิดหน้า list → ค้น+เปิดเคลม
        isurvey.ensure_logged_in(driver, cfg)
        isurvey.open_case_list(driver, attempts=8)  # อดทนขึ้น (perf-log ทำ Chrome ช้าลง)
        isurvey.find_and_open_claim(driver, claim, invoice)
        log("PROBE: เปิดเคลมแล้ว — อ่านทุก tab เพื่อให้เกิด network call ครบ")
        try:
            isurvey.read_all(driver, download_dir=None, expect_claim=claim,
                             include_record_tabs=False)
        except Exception as e:
            log(f"   (read_all สะดุด: {type(e).__name__} — ไม่เป็นไร เก็บ network ต่อ)")
        log("PROBE: กด export XML เพื่อจับ endpoint ข้อมูลโครงสร้าง")
        try:
            images.download_xml_export(driver, claim, dl_dir)
        except Exception as e:
            log(f"   (export XML สะดุด: {type(e).__name__})")
        time.sleep(2)
    except Exception as e:
        log(f"PROBE: flow สะดุดกลางคัน: {type(e).__name__}: {e}")
        log("   → เก็บ network เท่าที่ดักได้ไว้วิเคราะห์ต่อ")

    # เก็บ network เสมอ — แม้ flow ไม่ครบ capture บางส่วนก็มีค่า (detach ไม่ปิด browser)
    reqs = collect_requests(driver)
    cookies = driver.get_cookies()
    ua = driver.execute_script("return navigator.userAgent")

    # ---------- สรุป ----------
    cookie_header = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    cands = [i for i in reqs.values() if i.get("url") and is_data_candidate(i)]
    # จัดเรียง: json/xml ก่อน แล้วตาม url
    def keyf(i):
        mime = (i.get("mimeType") or "").lower()
        rank = 0 if ("json" in mime or "xml" in mime) else 1
        return (rank, i.get("url"))
    cands.sort(key=keyf)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = logs_dir / f"isurvey_net_{claim}_{ts}.json"
    logs_dir.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps({
        "claim": claim,
        "cookie_names": [c["name"] for c in cookies],
        "user_agent": ua,
        "requests": list(reqs.values()),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    log_plain("\n" + "=" * 60)
    log_plain(f"  พบ request ทั้งหมด {len(reqs)} ตัว | ตัวที่น่าจะเป็น 'ข้อมูล' {len(cands)} ตัว")
    log_plain(f"  cookie session: {[c['name'] for c in cookies]}")
    log_plain(f"  บันทึกดิบไว้ที่: {out_file}")
    log_plain("=" * 60)

    log_plain("\n--- ENDPOINT ที่น่าสนใจ (เรียง json/xml ขึ้นก่อน) ---")
    for i in cands[:40]:
        log_plain(f"\n[{i.get('method','?')}] {i.get('status','?')} "
                  f"({i.get('type','?')} / {i.get('mimeType','?')})")
        log_plain(f"   {short(i.get('url',''), 140)}")
        if i.get("postData"):
            log_plain(f"   POST data: {short(i['postData'], 140)}")

    # ---------- replay เฉพาะ GET json/xml (พิสูจน์ว่า API ยิงตรงได้) ----------
    log_plain("\n--- ลอง REPLAY GET (json/xml) ด้วย cookie ผ่าน urllib (อ่านอย่างเดียว) ---")
    replayed = 0
    seen = set()
    for i in cands:
        url = i.get("url", "")
        mime = (i.get("mimeType") or "").lower()
        if i.get("method") != "GET":
            continue
        if not ("json" in mime or "xml" in mime):
            continue
        if url in seen:
            continue
        seen.add(url)
        log_plain(f"\n→ {short(url, 140)}")
        log_plain(f"   {replay_get(url, cookie_header, ua)}")
        replayed += 1
        if replayed >= 8:
            break
    if replayed == 0:
        log_plain("   (ไม่มี GET ที่เป็น json/xml ให้ลอง — ข้อมูลอาจมาทาง POST,"
                  " ดู endpoint ด้านบน + ไฟล์ดิบเพื่อวิเคราะห์ params)")

    # ---------- ส่อง report_*.js หา endpoint ฝั่งอ่าน (ExtJS store/proxy url) ----------
    # ExtJS นิยาม Ext.data.Store proxy: {url:'web/php/xxx.php'} ในไฟล์เหล่านี้
    # → grep .php ในไฟล์ JS = เห็น endpoint อ่าน/เขียนทั้งหมด แม้ไม่ได้เปิดเคลม
    log_plain("\n--- ส่อง report_*.js หา endpoint ฝั่งอ่าน (grep .php ในไฟล์ ExtJS) ---")
    js_urls = sorted({i.get("url", "") for i in reqs.values()
                      if "report_" in i.get("url", "") and i.get("url", "").split("?")[0].endswith(".js")})
    php_in_js = {}   # endpoint.php -> set(ไฟล์ js ที่อ้างถึง)
    for ju in js_urls:
        try:
            req = urllib.request.Request(ju, headers={"Cookie": cookie_header, "User-Agent": ua})
            with urllib.request.urlopen(req, timeout=20) as r:
                txt = r.read().decode("utf-8", "replace")
        except Exception as e:
            log_plain(f"   โหลด {ju.split('/')[-1]} ไม่ได้: {type(e).__name__}")
            continue
        name = ju.split("/")[-1].split("?")[0]
        for m in re.findall(r'''["']([^"']*\.php[^"']*)["']''', txt):
            ep = m.split("?")[0]
            php_in_js.setdefault(ep, set()).add(name)
    if php_in_js:
        log_plain(f"   เจอ endpoint .php {len(php_in_js)} ตัวที่ ExtJS อ้างถึง:")
        for ep in sorted(php_in_js):
            log_plain(f"   • {ep}   ←  {', '.join(sorted(php_in_js[ep]))}")
    else:
        log_plain("   (ไม่เจอ .php ในไฟล์ JS — อาจ build url แบบ dynamic, ดูไฟล์ดิบ)")

    log_plain("\nเสร็จ — Chrome เปิดค้างไว้ให้ส่องต่อ (DevTools → Network ได้)")


if __name__ == "__main__":
    main()
