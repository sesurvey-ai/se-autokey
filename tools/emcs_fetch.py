# -*- coding: utf-8 -*-
"""emcs_fetch.py — เปิด EMCS อ่านหน้าเคสจริงมาเก็บเป็นไฟล์ HTML  **อ่านอย่างเดียว**

ทำแทนการนั่งกด Ctrl+S ทีละหน้า จากนั้นส่งต่อให้ emcs_dump.py แปลงเป็น XML
(ตัวนี้ไม่ parse อะไรเลย หน้าที่เดียวคือ "ไปเอาหน้ามา" — logic การอ่านอยู่ที่ emcs_dump)

    python tools/emcs_fetch.py --claim 21BR10AVD-6906-000098            # ดูว่ามีเรื่องอะไรบ้าง
    python tools/emcs_fetch.py --claim <เลขเคลม> --fetch                # เปิดเรื่อง + เซฟทุกหน้า
    python tools/emcs_fetch.py --claim <เลขเคลม> --fetch --dump         # เซฟแล้วแปลงเป็น XML เลย

⛔ วินัยความปลอดภัย — EMCS เป็นระบบของบริษัทประกัน
 1) **ไม่มีการเขียนใด ๆ** ไม่กรอกช่อง ไม่กดบันทึก/แก้ไข/ส่งงาน — คลิกได้เฉพาะ id
    ที่อยู่ใน CLICK_ALLOWLIST เท่านั้น (ลิงก์เปิดเรื่อง · ปุ่มสลับหน้า · ปุ่มออกจากเรื่อง)
 2) **default = ไม่เปิดเรื่อง** แค่ login + ค้นหา + บอกว่ามีเรื่องอะไร ต้องใส่ --fetch
    ถึงจะเปิดจริง (วินัยเดียวกับ dry-run ของบอท)
 3) **เปิดเรื่อง = EMCS ล็อกเรื่องนั้น** คนอื่นเปิดต่อไม่ได้จนกว่าจะออก → ตัวนี้กด
    'ออกจากเรื่อง' (wuMenuPage1_imbReturn_In_Out) ใน finally เสมอ แม้ระหว่างทางพัง
    และปิดเบราว์เซอร์เอง (detach=False) ไม่ปล่อยค้างไว้
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium.webdriver.common.by import By  # noqa: E402

from autokey import emcs  # noqa: E402
from autokey.browser import log, make_driver  # noqa: E402
from autokey.config import load_config  # noqa: E402

# หน้าในเรื่องหนึ่ง ๆ : (ชื่อไฟล์, id ปุ่มเมนู, ช่องที่ต้องโผล่ถึงจะถือว่าโหลดเสร็จ)
# หน้าแรกคือหน้าที่เปิดมาเลย ไม่ต้องกดเมนู
PAGES = [
    ("1-ข้อมูลทั่วไป", "wuMenuPage1_imbGeneral_Survey", "txtSurv_JobNo"),
    ("2-ทรัพย์สิน", "wuMenuPage1_imbAsset", "ddlAsset_Count"),
    ("3-ผู้บาดเจ็บ", "wuMenuPage1_imbInjure_Person", "ddlInj_Count"),
    ("4-ใบค่าใช้จ่าย", "wuMenuPage1_imbSpend", "txtBill_No"),
]

# id เดียวที่อนุญาตให้คลิก — อย่างอื่นห้ามแตะ (ปุ่มบันทึก/ส่งงานอยู่นอกลิสต์นี้ทั้งหมด)
CLICK_ALLOWLIST = {p[1] for p in PAGES} | {"wuMenuPage1_imbReturn_In_Out"}


def _click(driver, element_id: str):
    if element_id not in CLICK_ALLOWLIST:
        raise RuntimeError(f"ปฏิเสธการคลิก {element_id} — ไม่อยู่ใน allowlist "
                           "(เครื่องมือนี้อ่านอย่างเดียว ห้ามกดปุ่มที่เขียนข้อมูล)")
    emcs.click_retry(driver, By.ID, element_id)


def fetch(driver, claim: str, esurvey: str, outdir: Path) -> list:
    """เปิดเรื่อง → ไล่เซฟทุกหน้า → ออกจากเรื่อง (ปลดล็อก) คืนลิสต์ไฟล์ที่เซฟ"""
    outdir.mkdir(parents=True, exist_ok=True)
    saved = []
    # เปิดเรื่อง (ฟังก์ชันนี้เช็คให้ด้วยว่าเรื่องถูกล็อกโดยคนอื่นอยู่หรือเปล่า)
    emcs.open_report_images(driver, claim, esurvey)
    try:
        for name, menu_id, marker in PAGES:
            try:
                _click(driver, menu_id)
                emcs.wait_present(driver, By.ID, marker, 25)
                time.sleep(1.0)                    # เผื่อ postback เติมค่าท้าย ๆ
                f = outdir / f"{name}.html"
                f.write_text(driver.page_source, encoding="utf-8")
                saved.append(f)
                log(f"   ✓ {name}  ({f.stat().st_size:,} bytes)")
            except Exception as e:
                log(f"   ⚠️ ข้าม {name} — {type(e).__name__}: {e}")
    finally:
        # ออกจากเรื่องเสมอ ไม่งั้นเรื่องค้างล็อก คนอื่นเปิดต่อไม่ได้
        try:
            _click(driver, "wuMenuPage1_imbReturn_In_Out")
            try:
                emcs.accept_alert(driver, timeout=10)
            except Exception:
                pass
            log("EMCS: ออกจากเรื่องแล้ว — ปลดล็อก คนอื่นเปิดต่อได้")
        except Exception as e:
            log(f"   ⛔ กดออกจากเรื่องไม่สำเร็จ ({type(e).__name__}) — "
                f"เรื่อง {esurvey} อาจค้างล็อก ให้เปิด EMCS กดกลับ Inbox เองด้วย")
    return saved


def main():
    ap = argparse.ArgumentParser(description="อ่านหน้าเคสจริงจาก EMCS (read-only)")
    ap.add_argument("--claim", required=True, help="เลขเคลม (REF_CLAIM_NO)")
    ap.add_argument("--esurvey", default="", help="เลข e-Survey ถ้ามีหลายเรื่อง")
    ap.add_argument("--fetch", action="store_true",
                    help="เปิดเรื่องแล้วเซฟทุกหน้าจริง (ไม่ใส่ = แค่ค้นหาแล้วรายงาน)")
    ap.add_argument("--dump", action="store_true", help="แปลงเป็น XML ต่อด้วย emcs_dump")
    ap.add_argument("--out", default="", help="โฟลเดอร์ปลายทาง (default runs/fetch_<เลขเคลม>)")
    a = ap.parse_args()

    cfg = load_config()
    outdir = Path(a.out or f"runs/fetch_{a.claim.replace('/', '-')}")
    driver = make_driver(detach=False)             # ต้องปิดเอง ไม่ปล่อยค้างล็อกเรื่อง
    try:
        emcs.login(driver, cfg)
        reports = emcs.find_existing_reports(driver, a.claim)
        if not reports:
            sys.exit(f"ไม่พบเรื่องของเคลม {a.claim} ใน EMCS")
        log(f"เจอ {len(reports)} เรื่อง:")
        for r in reports:
            log(f"   {r['esurvey']}  {r['row'][:100]}")

        if not a.fetch:
            log("\n(โหมดดูอย่างเดียว — ยังไม่เปิดเรื่อง ไม่ล็อกอะไร) "
                "ใส่ --fetch ถ้าจะเซฟหน้าจริง")
            return

        target = a.esurvey or (reports[0]["esurvey"] if len(reports) == 1 else "")
        if not target:
            sys.exit("มีหลายเรื่อง — ระบุ --esurvey ว่าจะอ่านเรื่องไหน")
        log(f"\nEMCS: เปิดเรื่อง {target} (อ่านอย่างเดียว)")
        saved = fetch(driver, a.claim, target, outdir)
    finally:
        driver.quit()

    log(f"\n✓ เซฟ {len(saved)} หน้า ที่ {outdir}")
    if a.dump and saved:
        subprocess.run([sys.executable, str(Path(__file__).with_name("emcs_dump.py")),
                        str(outdir / "*.html"), "--xml", str(outdir / "case.xml")],
                       check=False)


if __name__ == "__main__":
    main()
