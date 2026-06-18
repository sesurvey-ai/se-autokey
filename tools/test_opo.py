r"""ทดสอบ fill_third_parties (คู่กรณี) บน draft เดิม — เน้นเคสคู่กรณีไม่มีประกัน

เปิด draft ที่บันทึกหน้าหลักแล้ว (ส่วนคู่กรณีปลดล็อก) → fill_third_parties
(เลือกจำนวน → กรอก → 'ไม่มีบริษัทประกันภัย' ถ้าไม่มีข้อมูลประกัน → บันทึก) → ดูผล
ไม่กดส่งงาน

ใช้: python tools\test_opo.py [เลขเคลม]   (default 2026013048453)
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
    tp = data.third_parties[0] if data.third_parties else {}
    log(f"คู่กรณี {len(data.third_parties)} ราย | insurer={tp.get('insurer','')!r} "
        f"policy={tp.get('policy_no','')!r} insure_type={tp.get('insure_type','')!r}")

    driver = make_driver(detach=True)
    try:
        emcs.login(driver, cfg)
        reports = emcs.find_existing_reports(driver, claim)
        if not reports:
            log(f"❌ ไม่พบ draft ของเคลม {claim}")
            return
        # ระบุ e-Survey เจาะจงได้ (arg 2) — กันสับสนเมื่อมี draft ทดสอบหลายเรื่อง
        target = sys.argv[2] if len(sys.argv) > 2 else emcs._pick_draft_report(reports)
        log(f"เปิด draft {target} เพื่อทดสอบกรอกคู่กรณี (ไม่ส่งงาน)")
        wait_clickable(
            driver, By.XPATH, f"//a[normalize-space(text())='{target}']", 20
        ).click()
        wait_present(driver, By.ID, "ddlOpo_Count", 20)
        # draft เปิดซ้ำ = view-mode → กด 'แก้ไข' (btnUpdate) เข้าโหมดแก้ก่อน (ตามที่ user บอก)
        import time
        try:
            b = driver.find_element(By.ID, "btnUpdate")
            if b.is_displayed() and b.is_enabled():
                log("   กด 'แก้ไข' (btnUpdate) เข้าโหมดแก้ draft")
                b.click()
                time.sleep(2)
                try:
                    emcs.accept_alert(driver, timeout=5)
                except Exception:
                    pass
                wait_present(driver, By.ID, "ddlOpo_Count", 20)
        except Exception:
            pass
        emcs.fill_third_parties(driver, data)
        save_debug_snapshot(driver, cfg.runs_dir / "logs", tag=f"test_opo_{claim}")
        log("จบทดสอบคู่กรณี — ตรวจบน browser (ไม่กดส่งงาน)")
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs",
                            tag=f"error_opo_{claim}")
        raise


if __name__ == "__main__":
    main()
