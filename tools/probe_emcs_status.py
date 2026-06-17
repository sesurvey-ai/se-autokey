# -*- coding: utf-8 -*-
"""probe: หาสัญญาณ "ส่งงานแล้ว" vs "draft" ในหน้าค้นหา EMCS (READ-ONLY)
เทียบเคลมที่ส่งงานจริงแล้ว กับ draft ที่บอทเพิ่งกรอก — ดูคอลัมน์/สถานะของแถว
ไม่กดอะไร ไม่บันทึก ไม่ส่งงาน ไม่ยิง ISURVEY
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")
from selenium.webdriver.common.by import By

from autokey import emcs
from autokey.browser import make_driver, log
from autokey.config import load_config

# ดึงทุก td + header ของแถวที่มีเลข e-Survey ตรงเลขเคลม (ไม่ตัดสั้น)
JS = r"""
const claim = arguments[0];
const out = [];
document.querySelectorAll("a").forEach(a => {
  const t = (a.innerText || "").trim();
  if (!/^S\d{9,13}$/.test(t)) return;
  const row = a.closest("tr");
  if (!row) return;
  const rowText = row.innerText.replace(/\s+/g, " ").trim();
  if (claim && !rowText.includes(claim)) return;
  const cells = [...row.querySelectorAll("td")].map(td => (td.innerText||"").trim());
  const table = row.closest("table");
  let headers = [];
  if (table) {
    const hr = table.querySelector("tr");
    if (hr) headers = [...hr.querySelectorAll("td,th")].map(c => (c.innerText||"").trim());
  }
  out.push({esurvey: t, rowText: rowText, cells: cells, headers: headers});
});
return out;
"""

CLAIMS = [
    ("2026013145915", "ส่งงานแล้วจริง (user ยืนยัน)"),
    ("2026013146155", "draft วันนี้ S68426063937"),
    ("2026013145682", "draft วันนี้ S68426063938"),
]


def search(driver, claim):
    el = driver.find_element(By.ID, "txtRef_Claim_No")
    el.clear()
    el.send_keys(claim)
    driver.find_element(By.ID, "btnSearch").click()
    time.sleep(3)
    return driver.execute_script(JS, claim)


def main():
    cfg = load_config()
    driver = make_driver(detach=True, download_dir=cfg.download_dir / "_dl" / "emcsprobe")
    emcs.login(driver, cfg)
    log("EMCS: login แล้ว — เริ่ม probe สถานะ")
    for claim, tag in CLAIMS:
        print("\n" + "=" * 60)
        print(f"  {claim}  [{tag}]")
        print("=" * 60)
        try:
            rows = search(driver, claim)
        except Exception as e:
            print(f"  ค้นหา error: {type(e).__name__}: {e}")
            continue
        if not rows:
            print("  (ไม่เจอแถว)")
            continue
        for r in rows:
            print(f"  e-Survey: {r['esurvey']}")
            print(f"  headers : {r['headers']}")
            print(f"  cells   : {r['cells']}")
    print("\nเสร็จ — Chrome เปิดค้างไว้ให้ส่องต่อเอง")


if __name__ == "__main__":
    main()
