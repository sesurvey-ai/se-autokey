# -*- coding: utf-8 -*-
"""probe_api_js.py — อ่าน source ของ ExtJS report_*.js เพื่อถอดสัญญา API ฝั่งอ่าน
(ดึงไฟล์ static ตรงๆ ไม่ต้องเปิด browser/เมนู) — หาว่า get_data_report.php
ถูกเรียกด้วยพารามิเตอร์อะไร + store/model นิยามอย่างไร  READ-ONLY ล้วน
"""
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://cloud.isurvey.mobi/web/extjs/"
FILES = ["report_fn.js", "report_summary.js", "report_claim.js", "report_surveyor.js"]
KEYS = ["get_data_report", "getExportXML", "export", ".php", "extraParams", "Ext.Ajax.request", "proxy"]


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.status, r.read().decode("utf-8", "replace")


for f in FILES:
    url = BASE + f
    try:
        st, txt = fetch(url)
    except Exception as e:
        print(f"\n### {f}: โหลดไม่ได้ — {type(e).__name__}: {e}")
        continue
    print("\n" + "=" * 64)
    print(f"### {f}  (HTTP {st}, {len(txt):,} bytes)")
    print("=" * 64)

    # 1) บริบทรอบ get_data_report (เห็นพารามิเตอร์ที่ส่ง)
    for i, m in enumerate(re.finditer(r"get_data_report", txt)):
        if i >= 3:
            print("   ...(เจอ get_data_report เพิ่มอีก — ตัดให้ดู 3 จุดแรก)")
            break
        s = max(0, m.start() - 500)
        e = min(len(txt), m.end() + 250)
        snippet = re.sub(r"\s+", " ", txt[s:e]).strip()
        print(f"\n[get_data_report #{i+1}] …{snippet}…")

    # 2) endpoint .php ทั้งหมดในไฟล์
    phps = sorted({p.split("?")[0] for p in re.findall(r'''["']([^"']*\.php[^"']*)["']''', txt)})
    if phps:
        print("\n   .php ที่อ้างถึงในไฟล์นี้:")
        for p in phps:
            print(f"     • {p}")

    # 3) ชื่อ field ใน model/columns (เดาโครงสร้างข้อมูลที่ได้กลับมา)
    fields = re.findall(r'''(?:dataIndex|name)\s*:\s*["']([A-Za-z_][A-Za-z0-9_]{2,})["']''', txt)
    uniq = sorted(set(fields))
    if uniq:
        print(f"\n   field/dataIndex ที่พบ {len(uniq)} ตัว (ตัวอย่าง 40 ตัวแรก):")
        print("     " + ", ".join(uniq[:40]))
