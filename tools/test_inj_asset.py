r"""ทดสอบ fill_injuries + fill_assets บน draft เดิม (เร็ว — ไม่ต้องรัน full fill)

เปิด draft ที่บันทึกหน้าหลักแล้ว (Tab 5/6 ปลดล็อก) → เรียก fill_injuries + fill_assets
→ ดูผล (ไม่กดส่งงาน). draft โดนแก้ (เพิ่มผู้บาดเจ็บ/ทรัพย์สิน) = test write ปลอดภัย

ใช้: python tools\test_inj_asset.py [เลขเคลม]   (default 2026013048453)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium.webdriver.common.by import By  # noqa: E402

from autokey import emcs  # noqa: E402
from autokey.browser import (  # noqa: E402
    log, make_driver, save_debug_snapshot, wait_clickable, wait_present,
)
from autokey.claim_data import ClaimData  # noqa: E402
from autokey.config import load_config  # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    claim = sys.argv[1] if len(sys.argv) > 1 else "2026013048453"
    cfg = load_config()
    data = ClaimData.load(f"runs/{claim}.json")
    log(f"ข้อมูล: ผู้บาดเจ็บ {len(data.injuries)} / ทรัพย์สิน {len(data.assets)}")

    driver = make_driver(detach=True)
    try:
        emcs.login(driver, cfg)
        reports = emcs.find_existing_reports(driver, claim)
        if not reports:
            log(f"❌ ไม่พบ draft ของเคลม {claim} — สร้าง draft ก่อน")
            return
        target = emcs._pick_draft_report(reports)
        log(f"เปิด draft {target} เพื่อทดสอบกรอกผู้บาดเจ็บ/ทรัพย์สิน (ไม่ส่งงาน)")
        wait_clickable(
            driver, By.XPATH, f"//a[normalize-space(text())='{target}']", 20
        ).click()
        wait_present(driver, By.ID, "wuMenuPage1_imbInjure_Person", 20)

        # เผื่อเปิด draft มาเป็น view-mode → ขอแก้ไขข้อมูลก่อน (ถ้ามีปุ่ม)
        for bid in ("wuFlow1_cmdEdit",):
            try:
                b = driver.find_element(By.ID, bid)
                if b.is_displayed() and b.is_enabled():
                    log(f"   กด {bid} (ขอแก้ไขข้อมูล) เข้าโหมดแก้")
                    b.click()
                    import time
                    time.sleep(2)
                    emcs.accept_alert(driver, timeout=5)
            except Exception:
                pass

        emcs.fill_injuries(driver, data)
        emcs.fill_assets(driver, data)
        save_debug_snapshot(driver, cfg.runs_dir / "logs",
                            tag=f"test_injasset_{claim}")
        log("จบทดสอบ Tab 5/6 — ตรวจบน browser (ไม่กดส่งงาน) browser เปิดค้างไว้")
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs",
                            tag=f"error_injasset_{claim}")
        raise


if __name__ == "__main__":
    main()
