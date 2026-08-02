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

# หน้าในเรื่องหนึ่ง ๆ : (ชื่อไฟล์, id ปุ่มเมนู, ช่องที่ต้องโผล่ถึงจะถือว่าโหลดเสร็จ, วินาทีที่รอ)
# ⚠️ หน้าข้อมูลทั่วไปใหญ่ ~3 MB (อีก 3 หน้าแค่ 0.15-1 MB) ใช้เวลาโหลดจริง 30-45 วิ
#    เคยตั้ง 20 วิเท่ากันหมดแล้วหน้านี้หลุดทุกรอบ ทั้งที่หน้ามาถูกต้อง
PAGES = [
    ("1-ข้อมูลทั่วไป", "wuMenuPage1_imbGeneral_Survey", "txtSurv_JobNo", 90),
    ("2-ทรัพย์สิน", "wuMenuPage1_imbAsset", "ddlAsset_Count", 25),
    ("3-ผู้บาดเจ็บ", "wuMenuPage1_imbInjure_Person", "ddlInj_Count", 40),
    ("4-ใบค่าใช้จ่าย", "wuMenuPage1_imbSpend", "txtBill_No", 25),
]

# id เดียวที่อนุญาตให้คลิก — อย่างอื่นห้ามแตะ (ปุ่มบันทึก/ส่งงานอยู่นอกลิสต์นี้ทั้งหมด)
CLICK_ALLOWLIST = {p[1] for p in PAGES} | {"wuMenuPage1_imbReturn_In_Out"}


def _click(driver, element_id: str):
    if element_id not in CLICK_ALLOWLIST:
        raise RuntimeError(f"ปฏิเสธการคลิก {element_id} — ไม่อยู่ใน allowlist "
                           "(เครื่องมือนี้อ่านอย่างเดียว ห้ามกดปุ่มที่เขียนข้อมูล)")
    emcs.click_retry(driver, By.ID, element_id)


def _goto(driver, menu_id: str, marker: str, timeout: int, tries: int = 2):
    """กดเมนูสลับหน้าแล้วรอช่องประจำหน้านั้นโผล่ — คลิกที่ไม่ติดจะเงียบ (ไม่ error
    แต่ก็ไม่เปลี่ยนหน้า) จึงต้องยืนยันด้วย marker

    กดซ้ำได้แค่กรณีที่ยังไม่ขยับจริง ๆ — การกดซ้ำ = postback หน้า 3 MB ใหม่
    ซึ่งทำให้ช้าลงกว่าเดิม เคยตั้ง retry แล้วยิ่งแย่ จึงรอให้ครบ timeout ก่อนเสมอ

    ⚠️ EMCS disable ปุ่มเมนูของ "หน้าที่ยืนอยู่" — และพอเปิดเรื่องมันพามาที่หน้า
    ข้อมูลทั่วไปให้เลย กดปุ่มนั้นซ้ำจึงรอ clickable จนหมดเวลาแล้ว error ทั้งที่หน้าถูกอยู่แล้ว
    → เช็ค marker ก่อน ถ้าอยู่หน้านั้นแล้วไม่ต้องกดอะไร (marker แยกหน้าได้ 1:1 ตรวจแล้ว)"""
    if driver.find_elements(By.ID, marker):
        return
    last = None
    for i in range(tries):
        try:
            _click(driver, menu_id)
            emcs.wait_present(driver, By.ID, marker, timeout)
            time.sleep(1.0)                        # เผื่อ postback เติมค่าท้าย ๆ
            return
        except Exception as e:
            last = e
            if i + 1 < tries:
                log(f"      (รอ {timeout} วิแล้วยังไม่ถึงหน้า — กด {menu_id} ซ้ำ)")
                time.sleep(2.0)
    raise last


def fetch(driver, claim: str, esurvey: str, outdir: Path) -> list:
    """เปิดเรื่อง → ไล่เซฟทุกหน้า → ออกจากเรื่อง (ปลดล็อก) คืนลิสต์ไฟล์ที่เซฟ"""
    outdir.mkdir(parents=True, exist_ok=True)
    saved = []
    try:
        # เปิดเรื่อง (ฟังก์ชันนี้เช็คให้ด้วยว่าเรื่องถูกล็อกโดยคนอื่นอยู่หรือเปล่า)
        # ⚠️ ต้องอยู่ "ใน" try — ถ้าคลิกลิงก์ติดแล้วหน้าโหลดไม่ทันจน raise
        # เรื่องจะเปิดค้างล็อกทันที ต้องให้ finally ได้กดออกจากเรื่องเสมอ
        emcs.open_report_images(driver, claim, esurvey)
        time.sleep(2.0)      # หน้าเพิ่งเปิดยังไม่นิ่ง คลิกทันทีจะไม่ติด (เจอจริงกับหน้าแรก)
        for name, menu_id, marker, timeout in PAGES:
            try:
                _goto(driver, menu_id, marker, timeout)
                f = outdir / f"{name}.html"
                f.write_text(driver.page_source, encoding="utf-8")
                saved.append(f)
                log(f"   ✓ {name}  ({f.stat().st_size:,} bytes)")
            except Exception as e:
                # เซฟหน้าที่ไปโผล่จริงไว้วินิจฉัย — ไม่งั้นได้แค่ TimeoutException เปล่า ๆ
                dbg = outdir / f"{name}-FAILED.html"
                try:
                    dbg.write_text(driver.page_source, encoding="utf-8")
                except Exception:
                    dbg = None
                log(f"   ⚠️ ข้าม {name} — {type(e).__name__}"
                    + (f" (เซฟหน้าที่โผล่จริงไว้ที่ {dbg.name})" if dbg else ""))
    finally:
        # ออกจากเรื่องเสมอ ไม่งั้นเรื่องค้างล็อก คนอื่นเปิดต่อไม่ได้
        if not _inside_report(driver):
            # เปิดเรื่องไม่สำเร็จตั้งแต่แรก (เช่นคนอื่นล็อกอยู่) = ไม่มีอะไรต้องปลด
            log("   (ไม่ได้เข้าไปในเรื่อง — ไม่มีล็อกให้ปลด)")
        else:
            try:
                _click(driver, "wuMenuPage1_imbReturn_In_Out")
                try:
                    emcs.accept_alert(driver, timeout=10)
                except Exception:
                    pass
                log("EMCS: กลับหน้า Inbox/Outbox แล้ว = ออกจากเรื่อง ปลดล็อก คนอื่นเปิดต่อได้")
            except Exception as e:
                log(f"   ⛔ กดกลับหน้า Inbox/Outbox ไม่สำเร็จ ({type(e).__name__}) — "
                    f"เรื่อง {esurvey} ค้างล็อกอยู่ **ต้องเปิด EMCS กดปุ่มกลับเองทันที**")
    return saved


def _inside_report(driver) -> bool:
    """ตอนนี้อยู่ในเรื่อง (= ถือล็อกอยู่) หรือเปล่า — ดูจากเมนูของหน้าเรื่อง"""
    try:
        return bool(driver.find_elements(By.ID, "wuMenuPage1_imbReturn_In_Out"))
    except Exception:
        return False


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
