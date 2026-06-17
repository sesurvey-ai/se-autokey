# -*- coding: utf-8 -*-
"""probe_capture.py — จับ request "เปิดเคลม detail" จริงด้วย selenium-wire (READ-ONLY)

ต่างจาก probe_network.py: ใช้ selenium-wire (proxy ดัก) แทน Chrome perf-log
จึงไม่ทำ Chrome ช้า → ExtJS เปิดเมนู/เปิดเคลมได้จริง → จับ request พร้อม
request body + response body เต็มได้ (perf-log ทำไม่ได้)

ทำ: login → เปิดหน้า list → ค้น+เปิดเคลม → อ่านทุก tab → export XML
    → ดัมพ์ทุก request *.php บน isurvey พร้อม param + response (อ่านอย่างเดียว)

ใช้:  python tools/probe_capture.py <เลขเคลม> [เลขเซอร์เวย์]
"""
import json
import re
import sys
import time
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seleniumwire import webdriver               # Chrome ที่ดัก request
from seleniumwire.utils import decode as sw_decode
from selenium.webdriver.chrome.options import Options

from autokey import isurvey, images
from autokey.browser import log, log_plain
from autokey.config import load_config

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REDACT = re.compile(r"(?i)(password|passwd|pwd|token)=[^&\s]*")


def make_capturing_driver(download_dir: Path):
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
    sw_opts = {"disable_encoding": True}          # ขอ response ไม่บีบอัด อ่าน body ง่าย
    d = webdriver.Chrome(options=options, seleniumwire_options=sw_opts)
    d.scopes = [r".*isurvey\.mobi.*"]             # จับเฉพาะ isurvey ลดขยะ
    return d


def body_text(resp, limit=30000) -> str:
    if resp is None or not resp.body:
        return ""
    try:
        raw = sw_decode(resp.body, resp.headers.get("Content-Encoding", "identity"))
        return raw.decode("utf-8", "replace")[:limit]
    except Exception as e:
        return f"(decode ไม่ได้: {e})"


def main():
    if len(sys.argv) < 2:
        print("ใช้:  python tools/probe_capture.py <เลขเคลม> [เลขเซอร์เวย์]")
        sys.exit(1)
    claim = sys.argv[1].strip()
    invoice = sys.argv[2].strip() if len(sys.argv) > 2 else ""

    cfg = load_config()
    logs_dir = cfg.runs_dir / "logs"
    dl_dir = cfg.download_dir / "_probe"
    driver = make_capturing_driver(dl_dir)

    log_plain("=" * 60)
    log_plain(f"  PROBE CAPTURE (selenium-wire, read-only) — เคลม {claim}")
    log_plain("=" * 60)

    try:
        isurvey.ensure_logged_in(driver, cfg)
        isurvey.open_case_list(driver)
        # เคลียร์ request ช่วง login/menu ออก เหลือเฉพาะช่วง "เปิดเคลม" ให้หาง่าย
        del driver.requests
        log("PROBE: ── เริ่มจับ request ตั้งแต่ตรงนี้ (เปิดเคลม) ──")
        isurvey.find_and_open_claim(driver, claim, invoice)
        log("PROBE: เปิดเคลมแล้ว — อ่านทุก tab ให้ยิง API ครบ")
        try:
            isurvey.read_all(driver, download_dir=None, expect_claim=claim,
                             include_record_tabs=False)
        except Exception as e:
            log(f"   (read_all สะดุด: {type(e).__name__} — เก็บ request ต่อ)")
        try:
            images.download_xml_export(driver, claim, dl_dir)
        except Exception as e:
            log(f"   (export XML สะดุด: {type(e).__name__})")
        time.sleep(2)
    except Exception as e:
        log(f"PROBE: flow สะดุดกลางคัน: {type(e).__name__}: {e}")

    # ---------- รวบรวม request *.php บน isurvey ----------
    items = []
    for r in driver.requests:
        if ".php" not in r.url or "isurvey" not in r.url:
            continue
        if r.response is None:
            continue
        req_body = ""
        if r.body:
            try:
                req_body = REDACT.sub(r"\1=***", r.body.decode("utf-8", "replace")[:1000])
            except Exception:
                req_body = "(binary)"
        items.append({
            "method": r.method,
            "url": REDACT.sub(r"\1=***", r.url),
            "req_body": req_body,
            "status": r.response.status_code,
            "ctype": r.response.headers.get("Content-Type", ""),
            "resp": body_text(r.response),
        })

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = logs_dir / f"isurvey_capture_{claim}_{ts}.json"
    logs_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    log_plain("\n" + "=" * 60)
    log_plain(f"  จับ request *.php บน isurvey ได้ {len(items)} ตัว")
    log_plain(f"  ดัมพ์เต็ม (พร้อม response body) ที่: {out}")
    log_plain("=" * 60)

    # โชว์ get_data_report + ตัวที่มีเลขเคลมใน url/body ก่อน (ตัวที่น่าจะเป็น detail)
    def score(it):
        s = 0
        if "get_data_report" in it["url"]:
            s += 2
        if claim in it["url"] or claim in it["req_body"]:
            s += 3
        if "json" in it["ctype"].lower() or "xml" in it["ctype"].lower():
            s += 1
        return -s
    for it in sorted(items, key=score):
        log_plain(f"\n[{it['method']}] {it['status']} ({it['ctype']})")
        log_plain(f"   URL: {it['url'][:160]}")
        if it["req_body"]:
            log_plain(f"   req body: {it['req_body'][:300]}")
        snip = re.sub(r"\s+", " ", it["resp"]).strip()
        log_plain(f"   resp: {snip[:400]}")

    log_plain("\nเสร็จ — Chrome เปิดค้างไว้ (selenium-wire proxy ยังทำงานจนปิด Chrome)")


if __name__ == "__main__":
    main()
