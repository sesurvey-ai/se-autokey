r"""Probe ตัวเลือกประเภทรูป (ddlImage_Type_Html5) บน draft ที่กรอกข้อมูลแล้ว — อ่านอย่างเดียว

dropdown นี้เป็น dynamic: เพิ่มตัวเลือกตามจำนวนคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินที่กรอก
(เช่น 'รูปรถคู่กรณี คันที่ N', 'รูปบาดเจ็บคนที่ N') — เปิด draft → หน้ารูป → dump options

ใช้: python tools\probe_imgtype.py [เลขเคลม]   (default 2026013048453)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support.ui import Select  # noqa: E402

from autokey import emcs  # noqa: E402
from autokey.browser import (  # noqa: E402
    click_retry, log, log_plain, make_driver, wait_clickable, wait_present,
)
from autokey.config import load_config  # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    claim = sys.argv[1] if len(sys.argv) > 1 else "2026013048453"
    cfg = load_config()
    driver = make_driver(detach=True)
    try:
        emcs.login(driver, cfg)
        reports = emcs.find_existing_reports(driver, claim)
        if not reports:
            log(f"❌ ไม่พบ draft ของเคลม {claim}")
            return
        target = emcs._pick_draft_report(reports)
        log(f"เปิด draft {target} → หน้ารูป (อ่าน options ประเภทรูป)")
        wait_clickable(
            driver, By.XPATH, f"//a[normalize-space(text())='{target}']", 20
        ).click()
        wait_present(driver, By.ID, "wuMenuPage1_imbImage", 20)
        click_retry(driver, By.ID, "wuMenuPage1_imbImage")
        wait_present(driver, By.ID, "ddlImage_Type_Html5", 20)

        sel = Select(driver.find_element(By.ID, "ddlImage_Type_Html5"))
        log_plain("=== ddlImage_Type_Html5 options (dynamic) ===")
        for o in sel.options:
            v = o.get_attribute("value")
            log_plain(f"   {v:>4} : {o.text.strip()}")
        log("จบ (อ่านอย่างเดียว) — browser เปิดค้าง")
    finally:
        pass


if __name__ == "__main__":
    main()
