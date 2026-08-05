"""se-autokey: ดึงข้อมูลเคลมจาก ISURVEY แล้วกรอกลง EMCS ในคำสั่งเดียว

ตัวอย่างการใช้งาน:
    python main.py --claim 2026013105763
    python main.py --claim 2026013105763 --invoice SEABI-213260100295
    python main.py --claim 2026013105763 --read-only          # อ่านอย่างเดียว ไม่กรอก EMCS
    python main.py --claims 111,222 --read-only               # อ่านหลายเคลม ไม่แตะ EMCS
    python main.py --claims 111,222                           # ⛔ อ่าน+กรอก EMCS ทีละเคลม
    python main.py --claims-file claims.txt                   # รายการเคลมจากไฟล์ (บรรทัดละเคลม)
    python main.py --data-json runs/2026013105763.json        # กรอก EMCS จากข้อมูลที่อ่านไว้แล้ว

⛔ **หลายเคลมไม่ได้แปลว่าอ่านอย่างเดียว** — `batch_fill = len(targets) > 1 and not read_only`
   ใส่ --claims เฉย ๆ = อ่านแล้วกรอก EMCS ทุกเคลมต่อกัน (มีถามยืนยันก่อน เว้นแต่ใส่ -y
   ซึ่งจะข้ามคำถามแล้วลุยเลย). จะอ่านอย่างเดียวต้องใส่ --read-only ทุกครั้ง
   (บรรทัดนี้เคยเขียนว่า "อ่านหลายเคลม = อ่านอย่างเดียวเสมอ" ซึ่งไม่จริงและอันตราย)

- เคลมสด: API อ่านคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินไม่ได้ (tab-4/5/6) → ต้องใช้ --scrape
  ไม่งั้นข้อมูลคู่กรณีหายทั้งชุดโดยไม่มี error (ยืนยันกับเคลมจริง 2026-08-03)
- ปุ่มที่กด: บันทึกหน้าหลัก (btnUpdate/btnSave) · บันทึกความเสียหายใน popup ·
  **บันทึกหน้าค่าใช้จ่าย (ปุ่ม "บันทึกราคา")** — user อนุมัติ 2026-07-27
  เพราะเลขที่ใบแจ้งหนี้+วันที่วางบิลต้องบันทึกด้วยปุ่มนี้ปุ่มเดียว
  (id ปุ่มเปลี่ยนตามสถานะงาน: draft ใหม่ = btnSurveySave / เปิดมาแก้ = btnSurvey_Update
   บอทลองทั้งคู่ — เดิมรู้จักแค่ตัวหลัง จึงหาไม่เจอเวลาเป็น draft ใหม่)
- หน้าค่าใช้จ่ายกรอก **ครบทุกช่องที่มีข้อมูล** (หัวบิล + 3 ช่องสรุปความเห็น + ตารางราคา
  คอลัมน์ "เสนอ") — ไม่มีข้อมูลต้นทาง = ข้ามช่องนั้น ไม่ทับของเดิม/ไม่เขียนเลขมั่ว
  ปิดเฉพาะตารางราคาด้วย --no-save-price
  ⛔ ไม่กด "ส่งงานใหม่" (wuFlow1_cmdSendNew) และ "ส่งผลงานต่อเนื่อง" (cmdSendFollow) เด็ดขาด
  → งานจบเป็น draft รอหัวหน้าตรวจ ใส่เรทราคา เขียนความเห็น แล้วกดส่งเอง
- log ทุกครั้งเก็บที่ runs/logs/ พร้อม screenshot อัตโนมัติเมื่อเกิด error
"""
import argparse
import os
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from selenium.common.exceptions import UnexpectedAlertPresentException

from autokey import emcs, isurvey, isurvey_api, joblog
from autokey.browser import (
    announce_send_failed,
    announce_sent,
    log,
    log_plain,
    make_driver,
    save_debug_snapshot,
    set_log_file,
    wait_for_submit,
)
from autokey import isurvey_report, sekey_client
from autokey.claim_data import ClaimData
from autokey.config import load_config
from autokey.images import (
    archive_old_images,
    categories_from_export,
    claim_zips,
    download_xml_export,
    extract_zip_images,
    images_from_zip,
    list_images,
    prepare_images,
)
from autokey.surv_xml import enrich_claim_from_xml


def banner(text: str):
    log_plain(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


def parse_args():
    p = argparse.ArgumentParser(
        description="ดึงข้อมูลเคลมจาก ISURVEY แล้วกรอกลง EMCS อัตโนมัติ"
    )
    p.add_argument("--claim", default="", help="เลขเคลม เช่น 2026013105763")
    p.add_argument("--invoice", default="",
                   help="เลขเซอร์เวย์ (ใส่เมื่อผลค้นหามีหลายแถว เช่น SEABI-213260100295)")
    p.add_argument("--claims", default="",
                   help="หลายเคลมคั่นด้วย comma เช่น 111,222,333 "
                        "⛔ กรอก EMCS ด้วย! ใส่ --read-only ถ้าต้องการอ่านอย่างเดียว")
    p.add_argument("--claims-file", type=Path, default=None,
                   help="ไฟล์รายการเคลม บรรทัดละเคลม (รูปแบบ: เลขเคลม [เลขเซอร์เวย์])")
    p.add_argument("--data-json", type=Path, default=None,
                   help="ข้ามการอ่าน ISURVEY — โหลดข้อมูลจากไฟล์ JSON ที่บันทึกไว้")
    p.add_argument("--read-only", action="store_true",
                   help="อ่าน ISURVEY + โหลดรูปอย่างเดียว ไม่กรอก EMCS")
    p.add_argument("--skip-images", action="store_true",
                   help="ไม่โหลด/ไม่อัปโหลดรูปภาพ")
    p.add_argument("--threshold", type=float, default=0.75,
                   help="เกณฑ์ template matching ตอนจัดชื่อรูป (default 0.75)")
    p.add_argument("--images-from", choices=["zip", "panel"], default="zip",
                   help="แหล่งรูป: zip = ปุ่มดาวน์โหลดรูปภาพ (ครบ+เร็ว, default), "
                        "panel = โหลดทีละรูปจาก Tab 2/3 แบบเดิม")
    p.add_argument("--no-xml", action="store_true",
                   help="ไม่ต้องดาวน์โหลดไฟล์ XML ของเคลมเก็บไว้")
    p.add_argument("--check-license", action="store_true",
                   help="ตรวจหา+อ่านใบขับขี่รถผู้เอาประกันจากชุดรูป (OCR ในเครื่อง "
                        "ด้วย easyocr) แล้วบันทึก <เคลม>_license.json + เทียบ "
                        "เลขใบขับขี่/เลขบัตรกับข้อมูลเคลม (ปิดเป็น default)")
    p.add_argument("--loss-type", default="auto",
                   help="ลักษณะความเสียหาย (default 'auto' = เลือกตามข้อมูล: "
                        "ไม่มีคู่กรณี→เคลมแห้ง, มีคู่กรณี→ตามผลคดี / "
                        "ระบุชื่อเองได้ / ใส่ \"\" เพื่อข้าม)")
    p.add_argument("--image-type", default="รูปรถประกัน",
                   help="ประเภทรูปตอนอัปโหลด (default 'รูปรถประกัน')")
    p.add_argument("--driver-title", default="",
                   help="คำนำหน้าผู้ขับขี่รถประกัน (นาย/นาง/นางสาว/...) — ใส่เมื่อ "
                        "ISURVEY ไม่มีข้อมูลและอนุมานจากชื่อผู้เอาประกันไม่ได้ "
                        "(ไม่ใส่ = บอทหยุดรอให้เลือกบนหน้า EMCS)")
    p.add_argument("--severity", choices=["เบา", "หนัก"], default="เบา",
                   help="รถเสียหาย หนัก/เบา (field บังคับของ EMCS, default เบา)")
    p.add_argument("--force-new", action="store_true",
                   help="สร้างเรื่องใหม่แม้เคลมนี้จะมีเรื่องใน EMCS อยู่แล้ว "
                        "(ปกติระบบจะหยุดกันเปิดเรื่องซ้ำ)")
    p.add_argument("--fill-existing", action="store_true",
                   help="เรื่องมีอยู่แล้วบน EMCS → เปิดเรื่องเดิม กด 'แก้ไข' แล้วกรอกต่อ "
                        "(ไม่สร้างเรื่องใหม่ ไม่ต้องยกเลิกของเดิม). ระบุเรื่องด้วย "
                        "--esurvey ถ้ามีหลายเรื่อง. ⚠️ ใช้กับเรื่องที่ยังไม่ได้เติม "
                        "คู่กรณี/ความเสียหาย/รูป ไม่งั้นอาจเพิ่มรายการซ้ำ")
    # --no-save-price = ชื่อเดิม (คงไว้ให้คำสั่งที่จดไว้ยังใช้ได้) — ความหมายจริงคือ
    # "กรอกหน้าค่าใช้จ่ายแบบย่อ" ไม่ได้แปลว่าไม่กดบันทึก (กดเสมอ ไม่งั้นหัวบิลไม่ติด)
    p.add_argument("--no-full-billing", "--no-save-price", action="store_true",
                   dest="no_save_price",
                   help="หน้าค่าใช้จ่ายกรอกแค่ 2 ช่อง (เลขที่ใบแจ้งหนี้ + วันที่วางบิล) "
                        "เหมือนงานที่มาจาก se-survey — ไม่แตะความเห็น/ตารางราคา "
                        "แต่ยังกด 'บันทึกราคา' ให้ (หัวบิลถึงจะติด + ปลดล็อกเรื่อง)")
    # ไม่ต้องใช้แล้วตั้งแต่ 2026-08-03 (ถอดด่านเคลมแห้งออก) — คงไว้ให้คำสั่ง/สคริปต์
    # ที่จดไว้เดิมยังรันได้ ไม่ error
    p.add_argument("--allow-fresh", action="store_true",
                   help="(เลิกใช้แล้ว — ไม่ต้องใส่) เดิมใช้ปลดด่าน 'เคลมแห้งเท่านั้น' "
                        "ซึ่งถอดออกแล้ว เพราะตอนนี้อ่านคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน "
                        "ครบทุกประเภทเคลม")
    p.add_argument("-y", "--yes", action="store_true",
                   help="ไม่ต้องหยุดถามก่อนเริ่มกรอก EMCS")
    p.add_argument("--api", action="store_true",
                   help="(ค่าเริ่มต้นแล้ว) อ่าน ISURVEY ผ่าน HTTP API — ไม่ต้องใส่ก็เป็น API")
    p.add_argument("--scrape", action="store_true",
                   help="บังคับใช้วิธีเดิม (Selenium scrape เปิด browser) แทน API — "
                        "ใช้เป็น fallback ถ้า API มีปัญหา หรืออ่านเคลมสดให้ครบคู่กรณี")
    p.add_argument("--compare", action="store_true",
                   help="อ่านทั้งสองทาง (scrape + API) แล้วเทียบ field ทีละตัว "
                        "ไม่กรอก EMCS — ใช้ตรวจว่า API ให้ผลตรงกับ scrape")
    p.add_argument("--images-only", action="store_true",
                   help="เติมรูปเข้า 'เรื่องเดิม' (draft) ที่มีอยู่แล้ว ไม่สร้างเรื่องใหม่/"
                        "ไม่แตะข้อมูลอื่น — ปกติอัปเฉพาะรูปรถคู่กรณี (tp_veh/) "
                        "ใช้ตอนกรอกเรื่อง+อัปรูปรถประกันไปแล้ว เหลือเติมรูปคู่กรณี")
    p.add_argument("--esurvey", default="",
                   help="ใช้กับ --images-only: ระบุเลข e-Survey (Sxxx) เจาะจงเรื่องที่จะเติมรูป "
                        "(ไม่ระบุ = เลือกเรื่อง draft อัตโนมัติ)")
    p.add_argument("--include-main-images", action="store_true",
                   help="ใช้กับ --images-only: อัปรูปรถประกัน (โฟลเดอร์หลัก) ด้วย "
                        "(ปกติอัปเฉพาะรูปรถคู่กรณี กันอัปซ้ำที่อัปไปแล้ว)")
    p.add_argument("--import-xml", action="store_true",
                   help="โหมดนำเข้า XML: ให้ EMCS import ฟอร์มหลักจาก SURV_REPORT XML "
                        "(ปุ่ม 'นำเข้าข้อมูลแบบ XML') แทนการกรอกเอง แล้วบอทอุดช่องว่าง/แก้ "
                        "+ ความเสียหายลงช่อง free-text 20 ช่อง (รองรับ >8 ดีกว่า) — "
                        "ต้องอ่านเคลมแบบมี XML (ไม่ใช้ --no-xml)")
    p.add_argument("--report-isurvey", action="store_true",
                   help="แจ้ง ISURVEY ว่าเคลม 'ส่งงานแล้ว' — ตรวจ EMCS ว่ากดส่งงานใหม่จริง "
                        "ก่อน (gate) ถ้ายังไม่ส่งจะไม่ยิง (ไม่อ่าน/ไม่กรอกฝั่งหน้า)")
    p.add_argument("--dry-run", action="store_true",
                   help="ใช้กับ --report-isurvey: ตรวจ gate + โชว์ payload แต่ไม่ยิงจริง")
    p.add_argument("--sesurvey-case", default="",
                   help="ดึงงานจากระบบ se-survey ด้วยเลขเคส (case id) หรือเลขเซอร์เวย์ (SETP-...; auto-detect): "
                        "โหลด SURV_REPORT XML จาก api.sesurvey.cloud แล้วเข้า flow นำเข้า XML ของ EMCS — "
                        "default = dry-run (หยุดก่อนแตะ EMCS); ใส่ --sesurvey-live เพื่อ import จริง")
    p.add_argument("--sesurvey-live", action="store_true",
                   help="⛔ เปิดโหมด import จริงเข้า EMCS สำหรับ --sesurvey-case "
                        "(default ไม่ใส่ = dry-run). ยังคงวินัย draft-only: บอทหยุดที่ draft "
                        "คนกดส่งเอง. ใช้เมื่อสรุปการทดสอบร่วมกันแล้วเท่านั้น")
    p.add_argument("--sesurvey-fill-existing", action="store_true",
                   help="เปิด draft ที่ import ไว้แล้ว (เคสที่ mark emcs_imported แล้ว) มาเติม "
                        "หน้าหลัก/คู่กรณี/รูป/ค่าใช้จ่าย + บันทึก — ไม่ import ซ้ำ ไม่สร้าง draft ใหม่ "
                        "ไม่กดส่งงาน (ใช้เมื่อ btnUpdate เคยล้มเพราะข้อมูลหน้าหลักไม่ครบ)")
    p.add_argument("--sesurvey-images-only", action="store_true",
                   help="เปิด draft เดิม แล้วอัปเฉพาะ 'รูป' (แยกตามประเภทรูป EMCS ตาม category) — "
                        "ไม่แตะหน้าหลัก/คู่กรณี/ค่าใช้จ่าย (กันเขียนทับที่ผู้ตรวจแก้ไว้) ไม่กดส่งงาน. "
                        "ใช้ตอนต้องอัปรูปใหม่ให้แยกประเภท (ลบรูปเก่าใน EMCS ก่อน)")
    p.add_argument("--sesurvey-injured-only", action="store_true",
                   help="เปิด draft เดิม แล้วเติม 'เฉพาะบล็อกผู้บาดเจ็บ' + บันทึก (re-save หน้าหลักเพื่อ "
                        "ปลดล็อกเมนู) — ไม่แตะคู่กรณี/ความเสียหาย/ทรัพย์สิน/รูป/ค่าใช้จ่าย (กันเพิ่ม row "
                        "ซ้ำ+อัปรูปซ้ำ) ไม่กดส่งงาน. ใช้เมื่อผู้บาดเจ็บ save ไม่ผ่านตอน import (เช่น รพ.ว่าง)")
    p.add_argument("--emcs-images", default="",
                   help="เปิดเรื่องเดิมใน EMCS แล้ว 'ดูรายการรูปที่แนบไว้' (อ่านอย่างเดียว) — "
                        "ใส่เลขเคลม; ระบุเรื่องด้วย --esurvey (ไม่ระบุ = เลือก draft อัตโนมัติ). "
                        "ใช้คู่กับ --emcs-delete-image เพื่อลบรูปที่หลุดขึ้นไป")
    p.add_argument("--emcs-delete-image", default="",
                   help="ใช้กับ --emcs-images: ลบรูปตาม 'ชื่อไฟล์เป๊ะ ๆ' ในคอลัมน์ 'รายการ' "
                        "(หลายใบคั่นด้วย ,). ต้องเจอชื่อละพอดี 1 แถว ไม่งั้นหยุดไม่ลบเลย")
    args = p.parse_args()

    if not (args.claim or args.claims or args.claims_file or args.data_json
            or args.sesurvey_case or args.emcs_images):
        p.error("ต้องระบุ --claim / --claims / --claims-file / --data-json / "
                "--sesurvey-case / --emcs-images อย่างน้อยหนึ่งอย่าง")
    return args


def build_targets(args) -> list:
    """รวมรายการ (เลขเคลม, เลขเซอร์เวย์) จากทุกแหล่ง กันซ้ำโดยรักษาลำดับ"""
    targets = []
    if args.claim:
        targets.append((args.claim.strip(), args.invoice.strip()))
    for c in args.claims.split(","):
        if c.strip():
            targets.append((c.strip(), ""))
    if args.claims_file:
        for line in args.claims_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"[,\s]+", line)
            targets.append((parts[0], parts[1] if len(parts) > 1 else ""))

    seen, uniq = set(), []
    for t in targets:
        if t[0] not in seen:
            seen.add(t[0])
            uniq.append(t)
    return uniq


def resolve_images_dir(cfg, claim: str, for_read: bool) -> Path:
    """โฟลเดอร์รูปของเคลมนี้ — แยกโฟลเดอร์ต่อเคลม
    (ตอนกรอก EMCS: ถ้าไม่มีโฟลเดอร์ของเคลม ใช้ downloaded_images เดิมแทน
    เพื่อให้ข้อมูลที่อ่านไว้ก่อนหน้านี้ยังใช้ได้)"""
    per_claim = cfg.download_dir / claim
    if for_read or per_claim.exists():
        return per_claim
    return cfg.download_dir


def check_license(cfg, data, img_dir, claim: str):
    """ตรวจหา+อ่านใบขับขี่รถผู้เอาประกันจากชุดรูป (OCR ในเครื่อง)
    บันทึกผลลง <เคลม>_license.json + log สรุป + เทียบกับข้อมูลเคลม
    เป็นงานเสริม: ถ้า easyocr ไม่พร้อม/พลาด จะไม่ทำให้ flow หลักล้ม"""
    import json
    from autokey import license_ocr
    try:
        res = license_ocr.find_and_read_license(img_dir)
    except Exception as e:
        log(f"   ⚠️ ตรวจใบขับขี่ไม่สำเร็จ ({type(e).__name__}: {e})")
        return
    if not res.get("available"):
        return  # ยังไม่ได้ลง easyocr — get_reader() log แจ้งแล้ว

    if res.get("found"):
        res["cross_check"] = license_ocr.cross_check(res["fields"], data)
        f = res["fields"]
        log("📇 ใบขับขี่ผู้เอาประกัน: "
            f"เลขที่ {f['license_no'] or '-'} | บัตรปชช. {f['id_no'] or '-'} | "
            f"{f['name_en'] or '-'}"
            + (f" | หมดอายุ {f['expiry_date']}" if f['expiry_date'] else ""))
        for c in res["cross_check"]:
            mark = "✓ ตรง" if c["match"] else "✗ ไม่ตรง"
            log(f"   {mark} {c['field']}: บัตร {c['ocr']} / เคลม {c['claim']}")
    else:
        log("📇 ไม่พบรูปใบขับขี่ในชุดรูป")

    out = cfg.runs_dir / f"{data.claim_value or claim}_license.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    log(f"   บันทึกผลตรวจใบขับขี่ → {out}")


def read_one_claim(driver, cfg, claim: str, invoice: str, args):
    """อ่านเคลมเดียวจาก ISURVEY → คืน ClaimData (โยน exception เมื่อพลาด)"""
    # ค่าเริ่มต้น = อ่านผ่าน API (เร็ว+ไม่เปิด browser ฝั่งอ่าน); --scrape เพื่อใช้วิธีเดิม
    if not getattr(args, "scrape", False):
        return read_one_claim_api(cfg, claim, invoice, args)

    img_dir = None
    if not args.skip_images:
        img_dir = resolve_images_dir(cfg, claim, for_read=True)
        archive_old_images(img_dir)

    isurvey.ensure_logged_in(driver, cfg)
    isurvey.open_case_list(driver)
    isurvey.find_and_open_claim(driver, claim, invoice)

    # โหมด panel: โหลดรูประหว่างไล่อ่าน Tab 2/3 เหมือนเดิม
    # Tab 4-6 (คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน) จะใช้จาก XML แทน — เร็วและครบกว่า
    panel_dir = img_dir if args.images_from == "panel" else None
    data = isurvey.read_all(driver, panel_dir, expect_claim=claim,
                            include_record_tabs=False)

    # โหมด zip (default): กดปุ่มดาวน์โหลดรูปภาพ ได้รูปครบทั้งเคลมในไฟล์เดียว
    # (ปุ่มอยู่แถบล่างของหน้า — กลับ Tab 1 ให้ชัวร์ก่อน)
    zip_counts = {}
    if img_dir is not None and args.images_from == "zip":
        isurvey.go_to_tab(driver, 1)
        zip_counts = images_from_zip(driver, claim, img_dir)
        if not zip_counts:
            log("   zip ใช้ไม่ได้ — เปลี่ยนไปโหลดจาก panel แทน")
            isurvey.collect_panel_images(driver, img_dir)

    # XML = แหล่งข้อมูลคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน (และเก็บไฟล์ไว้อ้างอิง)
    xml_ok = False
    if not args.no_xml:
        isurvey.go_to_tab(driver, 1)
        xml_path = download_xml_export(driver, claim, cfg.runs_dir / "xml")
        if xml_path is not None:
            xml_ok = enrich_claim_from_xml(data, xml_path)
    if not xml_ok:
        log("   ไม่มี XML — อ่านคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินจากหน้าจอแทน")
        isurvey.read_record_tabs(driver, data)

    # คู่กรณี: เติมข้อมูลที่ XML ไม่มี (ประเภทรถ/ประกันประเภท/ความเสียหายรายชิ้น)
    # จากหน้าจอ Tab 4 — เลือกทีละคันจาก dropdown (XML ให้แค่ basics + code)
    if data.third_parties:
        try:
            isurvey.go_to_tab(driver, 1)  # กลับ Tab 1 ก่อน กัน state ค้าง
            isurvey.enrich_third_parties_from_tab4(driver, data)
            data.save(cfg.runs_dir / f"{data.claim_value or claim}.json")
        except Exception as e:
            log(f"   ⚠️ เติมคู่กรณีจาก Tab 4 ไม่สำเร็จ ({type(e).__name__}: {e})")

    # จัดชื่อรูป: ใบรับงาน → 1.jpg, ที่เหลือ → รูปรถประกันN.jpg
    # (ถ้า zip บอกว่าไม่มีเอกสาร REPORTS เลย ก็ไม่มีใบรับงานให้หา — ข้าม)
    if img_dir is not None and list_images(img_dir):
        if zip_counts and zip_counts.get("REPORTS", 0) == 0:
            log("   เคลมนี้ไม่มีเอกสารใบรับงานในชุดรูป (REPORTS ว่าง) — "
                "ข้ามการตั้งชื่อ 1.jpg รูปคงชื่อเดิม")
        else:
            prepare_images(img_dir, cfg.template_path, args.threshold)

    if img_dir is not None and getattr(args, "check_license", False):
        check_license(cfg, data, img_dir, claim)

    json_path = cfg.runs_dir / f"{data.claim_value or claim}.json"
    data.save(json_path)
    log(f"บันทึกข้อมูลที่อ่านได้ → {json_path}")
    return data


def read_one_claim_api(cfg, claim: str, invoice: str, args=None):
    """อ่านเคลมผ่าน HTTP API (ไม่เปิด browser) → ClaimData + โหลดรูป + บันทึก JSON
    (รูปโหลดผ่าน get-images API แล้วจัดวาง/ตั้งชื่อแบบเดียวกับ flow scrape)"""
    api = isurvey_api.ISurveyAPI(cfg)
    api.login()
    data = api.read_claim(claim, invoice, expect_claim=claim)

    if args is not None and not args.skip_images:
        img_dir = resolve_images_dir(cfg, data.claim_value or claim, for_read=True)
        archive_old_images(img_dir)
        counts = api.download_images(api.last_case_id, img_dir)
        # จัดชื่อรูป: ใบรับงาน → 1.jpg (เหมือน flow scrape; REPORTS ว่าง = ไม่มีใบรับงาน)
        if list_images(img_dir):
            if counts.get("REPORTS", 0) == 0:
                log("   เคลมนี้ไม่มีเอกสารใบรับงาน (REPORTS ว่าง) — ข้ามตั้งชื่อ 1.jpg")
            else:
                prepare_images(img_dir, cfg.template_path, args.threshold)

        if getattr(args, "check_license", False):
            check_license(cfg, data, img_dir, claim)

    json_path = cfg.runs_dir / f"{data.claim_value or claim}.json"
    data.save(json_path)
    log(f"บันทึกข้อมูลที่อ่านได้ (API) → {json_path}")
    return data


def _num_or_none(x):
    try:
        return float(str(x).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return None


def diff_claim_data(scrape, api) -> list:
    """เทียบ ClaimData สองตัว (dict) field ต่อ field — คืน list ของ
    (ชื่อ field, ค่า scrape, ค่า api) เฉพาะที่ต่างจริง
    (ตัวเลข/เงินถือว่าเท่ากันถ้าค่าเท่ากัน เช่น '' = '0.00' = 0)"""
    diffs = []

    def eq(a, b):
        if a == b:
            return True
        na, nb = _num_or_none(a), _num_or_none(b)
        return na is not None and nb is not None and abs(na - nb) < 0.01

    keys = set(scrape) | set(api)
    keys.discard("xml_file")          # ไฟล์ XML มีเฉพาะฝั่ง scrape — ไม่นับ
    for k in sorted(keys):
        sv, av = scrape.get(k), api.get(k)
        if k == "bill":
            sb, ab = sv or {}, av or {}
            for bk in sorted(set(sb) | set(ab)):
                if not eq(sb.get(bk), ab.get(bk)):
                    diffs.append((f"bill.{bk}", sb.get(bk), ab.get(bk)))
        elif not eq(sv, av):
            diffs.append((k, sv, av))
    return diffs


def run_compare(cfg, args):
    """อ่านทั้ง scrape + API แล้วเทียบ field ทีละตัว (ไม่กรอก EMCS)"""
    import copy
    targets = build_targets(args)
    scrape_args = copy.copy(args)
    scrape_args.scrape = True          # ฝั่งนี้บังคับใช้ Selenium scrape
    scrape_args.skip_images = True     # เทียบข้อมูล ไม่ต้องโหลดรูป
    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())
    driver = make_driver(detach=True, download_dir=per_run_dl)
    try:
        for claim, invoice in targets:
            banner(f"COMPARE เคลม {claim} (scrape ⟷ API)")
            try:
                driver.switch_to.window(driver.current_window_handle)
                s = read_one_claim(driver, cfg, claim, invoice, scrape_args)
                a = isurvey_api.read_claim_api(cfg, claim, invoice, expect_claim=claim)
            except Exception as e:
                log(f"❌ เทียบไม่ได้: {type(e).__name__}: {e}")
                continue
            diffs = diff_claim_data(asdict(s), asdict(a))
            total = len(asdict(s))
            if not diffs:
                log_plain(f"✅ ตรงกันทุก field ({total} field) — API ใช้แทน scrape ได้")
            else:
                log_plain(f"⚠️ ต่างกัน {len(diffs)} field:")
                for name, sv, av in diffs:
                    log_plain(f"   • {name}:")
                    log_plain(f"       scrape = {str(sv)[:120]!r}")
                    log_plain(f"       api    = {str(av)[:120]!r}")
    finally:
        driver.quit()


def run_api_readonly(cfg, args):
    """อ่านผ่าน API ล้วน (ไม่เปิด browser เลย) — บันทึก JSON + แสดงสรุป"""
    targets = build_targets(args)
    ok = 0
    for claim, invoice in targets:
        banner(f"อ่านผ่าน API: เคลม {claim}")
        try:
            d = read_one_claim_api(cfg, claim, invoice, args)
        except Exception as e:
            log(f"❌ อ่านไม่สำเร็จ: {type(e).__name__}: {e}")
            continue
        ok += 1
        log_plain("")
        log_plain(d.summary())
        log_plain("")
        log_plain(d.validation_report())
    if len(targets) > 1:
        banner(f"อ่านผ่าน API สำเร็จ {ok}/{len(targets)} เคลม")


def run_images_only(cfg, args):
    """เติมรูปเข้า 'เรื่องเดิม' (draft) ที่มีอยู่แล้วใน EMCS — ไม่สร้างเรื่องใหม่
    ไม่แตะข้อมูลอื่น (ใช้ตอนกรอกเรื่อง+อัปรูปรถประกันไปแล้ว เหลือเติมรูปรถคู่กรณี)

    ได้ data จาก --data-json (ใช้รูปที่โหลดไว้แล้ว) หรือ --claim (อ่านสด+โหลดรูปก่อน)
    ปกติอัปเฉพาะรูปรถคู่กรณี (tp_veh/) — เพิ่ม --include-main-images ถ้าจะอัปรูปหลักด้วย"""
    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())
    driver = make_driver(detach=True, download_dir=per_run_dl)
    try:
        if args.data_json:
            banner(f"โหลดข้อมูลจากไฟล์ {args.data_json}")
            data = ClaimData.load(args.data_json)
            if data.xml_file and Path(data.xml_file).exists():
                enrich_claim_from_xml(data, data.xml_file)
        else:
            targets = build_targets(args)
            if len(targets) != 1:
                raise SystemExit("โหมด --images-only ทำได้ทีละเคลม "
                                 "(ระบุ --claim หรือ --data-json อันเดียว)")
            claim, invoice = targets[0]
            data = read_one_claim(driver, cfg, claim, invoice, args)
            driver.switch_to.new_window("tab")   # เปิด tab ใหม่สำหรับ EMCS

        log_plain(data.summary())
        n_opo = len(data.third_parties or [])
        if n_opo == 0 and not args.include_main_images:
            log("⚠️ เคลมนี้ไม่มีคู่กรณี (third_parties ว่าง) — ไม่มีรูปรถคู่กรณีให้เติม "
                "(ถ้าจะอัปรูปรถประกันด้วย ใส่ --include-main-images)")
        images_folder = resolve_images_dir(cfg, data.claim_value, for_read=False)

        esurvey = emcs.add_images_only(
            driver, cfg, data, images_folder,
            image_type=args.image_type,
            include_main=args.include_main_images,
            esurvey=args.esurvey)
        save_debug_snapshot(driver, cfg.runs_dir / "logs",
                            tag=f"images_only_{data.claim_value}")
        banner(f"เติมรูปเข้าเรื่อง {esurvey} เสร็จ — ตรวจบน EMCS แล้วกด 'ส่งงาน' "
               "เองเมื่อพร้อม (browser เปิดค้างให้)")
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs",
                            tag="error_images_only")
        raise


def run_import_xml(cfg, args):
    """โหมดนำเข้า XML: อ่านเคลม (ต้องมี SURV_REPORT XML) → ให้ EMCS import ฟอร์มหลัก →
    บอทอุดช่องว่าง/แก้ + กรอกความเสียหาย/คู่กรณี/ฯลฯ → เสนอส่งงาน (เหมือน flow ปกติ)

    ได้ data จาก --data-json (ใช้ XML ที่โหลดไว้) หรือ --claim (อ่านแบบ scrape เพื่อโหลด
    XML + เติมคู่กรณีให้ครบ). import มีประโยชน์เด่นกับเคลมความเสียหายเยอะ (ฟอร์ม import
    มีช่อง free-text 20 ช่อง vs cmdNewReport 8)"""
    import copy
    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())
    driver = make_driver(detach=True, download_dir=per_run_dl)
    data = None
    try:
        if args.data_json:
            banner(f"โหลดข้อมูลจากไฟล์ {args.data_json}")
            data = ClaimData.load(args.data_json)
            if data.xml_file and Path(data.xml_file).exists():
                enrich_claim_from_xml(data, data.xml_file)
        else:
            targets = build_targets(args)
            if len(targets) != 1:
                raise SystemExit("โหมด --import-xml ทำได้ทีละเคลม "
                                 "(ระบุ --claim หรือ --data-json อันเดียว)")
            claim, invoice = targets[0]
            # import ต้องมี XML → อ่านแบบ scrape (ดาวน์โหลด XML + เติมคู่กรณีครบ)
            read_args = copy.copy(args)
            read_args.scrape = True
            banner(f"อ่านเคลม {claim} (scrape — เพื่อโหลด XML + คู่กรณีครบ)")
            data = read_one_claim(driver, cfg, claim, invoice, read_args)
            driver.switch_to.new_window("tab")   # เปิด tab ใหม่สำหรับ EMCS

        # ต้องมีไฟล์ XML — ถ้า data ไม่ชี้ ลองหาในโฟลเดอร์ runs/xml/ ของเคลมนี้
        if not (data.xml_file and Path(data.xml_file).exists()):
            cands = sorted((cfg.runs_dir / "xml").glob(
                f"{data.claim_value or ''}*SURV_REPORT*.txt"))
            if cands:
                data.xml_file = str(cands[-1])
                enrich_claim_from_xml(data, data.xml_file)
            else:
                raise SystemExit(
                    "โหมด --import-xml ต้องมีไฟล์ SURV_REPORT XML — "
                    "อ่านเคลมใหม่โดยไม่ใช้ --no-xml (ฝั่งอ่านจะดาวน์โหลด XML ให้)")

        log_plain(data.summary())
        log_plain("")
        log_plain(data.validation_report())

        # ด่านกันทำซ้ำ (se-key) เหมือน flow ปกติ
        dup = _sekey_dup_skip(cfg, data)
        if dup:
            banner("หยุด: เลขเซอร์เวย์นี้ทำไปแล้ว — ไม่กรอก EMCS")
            log_plain(f"  {dup}")
            driver.quit()
            return

        if not args.yes:
            input("\n>> ตรวจข้อมูลด้านบน แล้วกด Enter เพื่อนำเข้า XML + กรอก EMCS "
                  "(Ctrl+C เพื่อยกเลิก) << ")

        banner("นำเข้า XML → กรอก EMCS (draft)")
        images_folder = (None if args.skip_images else
                         resolve_images_dir(cfg, data.claim_value, for_read=False))
        esurvey = emcs.run_import(
            driver, cfg, data, images_folder=images_folder,
            loss_type=args.loss_type, image_type=args.image_type,
            severity=args.severity, force_new=args.force_new,
            full_billing=not args.no_save_price)
        save_debug_snapshot(driver, cfg.runs_dir / "logs",
                            tag=f"done_import_{data.claim_value}")
        banner("กรอกครบทุกหน้าแล้ว (draft, นำเข้า XML)"
               + (f" | e-Survey {esurvey}" if esurvey else ""))
        joblog.record("draft", data.claim_value, data.invoice_value, esurvey,
                      note="นำเข้า XML")
        _offer_submit(driver, cfg, data, esurvey=esurvey)
    except Exception:
        save_debug_snapshot(
            driver, cfg.runs_dir / "logs",
            tag=f"error_import_{getattr(data, 'claim_value', '') or 'x'}")
        raise


# ประเภทรถ code (se-survey) → ป้าย ddlCType ของ EMCS แบบ verbatim (fuzzy_select ใช้ชื่อไทย ไม่ใช่ code)
# ⚠️ ห้ามย่อ: เดิม A และ E เป็น 'เก๋ง' เหมือนกัน → WRatio ได้ 90 เท่ากันเป๊ะทั้ง 'เก๋งเอเชีย' และ
# 'เก๋งยุโรป' → extractOne คืนตัวแรกเสมอ = รถยุโรปกลายเป็นเก๋งเอเชียทุกคัน แล้วลิสต์ยี่ห้อที่
# cascade มาก็เป็นของเอเชีย (ไม่มี BENZ/BMW/AUDI) → ช่องยี่ห้อ (บังคับ) ว่างต้องรอคนเลือก
_CAR_TYPE_TH = {'A': 'เก๋งเอเชีย', 'E': 'เก๋งยุโรป', 'M': 'รถจักรยานยนต์', 'O': 'รถอื่นๆ',
                'T': 'กระบะ', 'V': 'รถตู้', 'W': 'รถบรรทุก'}


def _money(v) -> str:
    """ค่าเงินจาก se-survey → รูปแบบที่ EMCS รับ (num() ใช้ regex ^\\d+$ | ^\\d+\\.\\d+$
    ไม่ผ่าน = EMCS ล้างช่องทิ้งเงียบ ๆ, maxlength=10). กู้ไม่ได้ → '' (set_text ข้ามให้)"""
    s = str(v or "").replace(",", "").replace(" ", "").strip().rstrip(".")
    if not s or not re.fullmatch(r"\d+(\.\d+)?", s):
        return ""
    s = s.rstrip("0").rstrip(".") if "." in s else s     # 8000.00 → 8000
    return s if len(s) <= 10 else ""

# ── ความเสียหาย (แผนภาพมือถือ) → รายการที่ fill_damage_list/fill_opponent_damage กรอกได้ ──
_DMG_POS_TH = {'L': 'ซ้าย', 'R': 'ขวา', 'A': ''}          # pos → ต่อท้ายชื่อชิ้นส่วน (ให้ EMCS อ่านซ้าย/ขวา)
_DMG_LVL_RANK = {'L': 'A', 'M': 'B', 'H': 'C', 'X': 'D'}  # ระดับ ต่ำ/กลาง/สูง/สูงมาก → rank A-D (rdoDam_Lavel)


# pos ของแอป → index radio rdoDam_Left_Right ของ EMCS ('0'=ซ้าย '1'=ขวา '2'=ทั้งคู่)
_DMG_POS_IDX = {'L': '0', 'R': '1', 'A': '2'}


def _report_damage_items(raw):
    """[{part, pos:L/R/A, level:L/M/H/X}] (แผนภาพความเสียหายมือถือ) →
    [(ชื่อชิ้นส่วน, rank A-D, side '0'/'1'/'2')] ใช้ร่วมกันทั้งรถประกันและคู่กรณี

    ชื่อชิ้นส่วนบนแอปตรงกับ checklist ของ EMCS verbatim (22 ชิ้น) และ **ไม่มีข้างในชื่อ**
    เพราะ EMCS แยก "ด้าน" เป็น radio ต่างหาก — ส่ง side ไปตรง ๆ ไม่ต้องเดาจากชื่อ
    (ของเดิมต่อ 'ซ้าย/ขวา' ท้ายชื่อ ทำให้ match checklist ไม่ได้เลย ตกช่องอิสระทั้งหมด)"""
    out = []
    for it in (raw or []):
        if not isinstance(it, dict):
            continue
        part = str(it.get('part') or '').strip()
        if not part:
            continue
        pos = str(it.get('pos') or '').strip().upper()
        rank = _DMG_LVL_RANK.get(str(it.get('level') or '').strip().upper(), '')
        out.append((part, rank, _DMG_POS_IDX.get(pos, '2')))
    return out


def _populate_third_parties_from_report(data, rep):
    """สร้าง data.third_parties จาก opposing_parties (ค่าไทยของ se-survey) แทน XML (ที่ให้ code) —
    fill_third_parties อ่าน veh_type (ไทย เช่น 'เก๋ง') + insurer (ชื่อเต็ม) เพื่อเลือก dropdown บังคับ
    ของคู่กรณี (ประเภทรถ/มีประกันภัยที่); XML ให้ veh_type_code ('A') ซึ่ง fuzzy_select ไม่ match"""
    opp = rep.get("opposing_parties")
    if not isinstance(opp, list) or not opp:
        return
    # third_parties จาก XML (enrich ก่อนหน้า) มี province_id/district_id เป็นรหัส 4 หลัก
    # (จาก DRI_PROVINCEID/DRI_DISTRICTID) — คงไว้ให้ fill_third_parties เลือก ddlDri_Province/DistrictID
    xml_tps = list(data.third_parties or [])
    tps = []
    for i, o in enumerate(opp):
        if not isinstance(o, dict):
            continue
        first = str(o.get("first_name") or "").strip()
        last = str(o.get("last_name") or "").strip()
        # ชื่อ key ต้องตรงกับที่ fill_third_parties อ่าน (emcs.py): idcard/lic_no/lic_issue_date
        # (report ใช้ cid/license_no/license_start — remap ให้ตรง ไม่งั้นช่องบัตร/ใบขับขี่ว่าง)
        tp = {
            "opo_type": "รถคู่กรณี",
            "plate_no": str(o.get("plate") or "").strip(),
            # จังหวัดทะเบียนรถคู่กรณี (* บังคับ): report ให้ชื่อ 'province' (fill_third_parties เลือกด้วย fuzzy)
            "plate_province": str(o.get("plate_province") or o.get("province") or "").strip(),
            "veh_type": str(o.get("car_type") or "").strip(),
            "car_brand": str(o.get("car_brand") or "").strip(),
            "car_model": str(o.get("car_model") or "").strip(),
            "car_color": str(o.get("car_color") or "").strip(),
            "car_reg_year": str(o.get("reg_year") or "").strip(),   # พ.ศ. (แปลงตอนกรอก)
            "km_no": str(o.get("mileage") or "").strip(),
            # EMCS มี ddlEv_Type ให้คู่กรณีจริง และ emcs.py:_fill_ev เดินสายรออยู่แล้ว
            # แต่เดิม main.py ไม่เคยใส่ key นี้ → ว่าง '-- ระบุ --' ทุกเคส
            "ev_type": str(o.get("ev_type") or "").strip(),
            # ความสัมพันธ์ผู้ขับขี่กับเจ้าของรถคู่กรณี — แอปบังคับกรอก แต่เดิมไม่ถูกส่งต่อ
            "relation": str(o.get("relation") or "").strip(),
            "chassis_no": str(o.get("vin") or o.get("chassis_no") or "").strip(),
            # EMCS ฝั่งคู่กรณีไม่มี dropdown คำนำหน้าที่ใช้จริง (ddlDri_Title_ID อยู่ในเลย์เอาต์
            # AXA ที่ซ่อน) — งานจริงของพนักงานใส่คำนำหน้า "ในชื่อ" เลย เช่น 'นาย พาสกรณ์ มากพูน'
            # แอปบังคับให้เลือกคำนำหน้าอยู่แล้ว (opponent_editor.dart) แต่เดิมถูกทิ้งทั้งค่า
            # → ต่อหน้าชื่อให้ตรงธรรมเนียม (ช่วยให้ resolve_gender อนุมานเพศได้ด้วย)
            "drv_name": " ".join(
                x for x in (str(o.get("title") or "").strip(), first, last) if x),
            "opo_name": str(o.get("owner_name") or "").strip(),
            # ที่อยู่ "เจ้าของรถ" — เดิมไม่ map ทำให้ตกไป fallback = ที่อยู่ผู้ขับขี่ (คนละคนได้)
            "opo_address": str(o.get("owner_address") or "").strip(),
            "gender": str(o.get("gender") or "").strip(),
            "age": str(o.get("age") or "").strip(),
            "birthdate": str(o.get("birthdate") or "").strip(),
            "address": str(o.get("address") or "").strip(),
            "phone": str(o.get("phone") or "").strip(),
            "idcard": str(o.get("cid") or "").strip(),
            "lic_no": str(o.get("license_no") or "").strip(),
            "lic_issue_date": str(o.get("license_start") or "").strip(),
            "insurer": str(o.get("insurer") or "").strip(),
            "policy_no": str(o.get("policy_no") or "").strip(),
            "claim_no": str(o.get("claim_no") or "").strip(),
            # ประเภทกรมธรรม์คู่กรณี: report ไม่มีช่องนี้ → "-" (บาง EMCS บังคับ ไม่งั้น validForm ฟ้อง)
            "insure_type": str(o.get("insure_type") or o.get("policy_type") or "-").strip(),
            # ความเสียหายคู่กรณี (แผนภาพมือถือ) → fill_opponent_damage (ต่อคัน ≤ MAX_DAMAGE_ITEMS)
            # เดิมไม่ใส่ key นี้ → tp.get('damages') ว่าง → ฟอร์มความเสียหายคู่กรณีเปล่าทุกคัน
            "damages": [{"part": p, "level": r, "side": sd}
                        for p, r, sd in _report_damage_items(o.get("damage"))],
            # ค่าเสียหายประมาณ + เข้าสัญญา KFK — มือถือเก็บครบและ XML ก็มี (COST_DAMAGE/HAS_KFK)
            # แต่ dict นี้เขียนทับ third_parties ที่ enrich จาก XML → เดิมตกไปทั้งคู่
            # (emcs.py:374/377 อ่าน cost_damage / has_kfk) = ช่องเงินว่าง + ไม่เคยติ๊ก KFK
            "cost_damage": _money(o.get("estimated_cost")),
            "has_kfk": "1" if o.get("kfk") is True else "",
        }
        # คงรหัสจากXML: จังหวัด/อำเภอ (ddlDri_*ID) + ประเภทใบขับขี่ (ddlEmcs_License_Type)
        # — fill_third_parties เลือกด้วยรหัส ไม่ใช่ชื่อไทย; report ให้ label ตัดออก ต้องดึงจาก XML
        if i < len(xml_tps):
            for _k in ("province_id", "district_id", "lic_type"):
                if str(xml_tps[i].get(_k) or "").strip():
                    tp[_k] = xml_tps[i][_k]
        tps.append(tp)
    if tps:
        data.third_parties = tps


def _populate_injuries_from_report(data, rep):
    """เขียนทับ data.injuries ด้วยค่าจาก report ของ se-survey (ป้ายไทยจากแอป)

    เดิมผู้บาดเจ็บมาจาก XML ทางเดียว (surv_xml) ซึ่ง PERSON_TYPE มีแค่ 3 รหัส DV/PV/ON
    → แยก "ฝั่งคู่กรณี" (02/04) ไม่ได้ ทั้งที่ EMCS มี 5 ตัวเลือกและงานจริงใช้ 02
    ทำแบบเดียวกับคู่กรณี (_populate_third_parties_from_report) ที่เขียนทับด้วยป้ายไทย
    key ต้องตรงกับที่ fill_injuries อ่าน (citizen_id / job / car_regno / tel_no /
    cost / injure / wounded_type) ไม่ใช่ชื่อคอลัมน์ของแอป"""
    raw = rep.get('injured_persons')
    if not isinstance(raw, list) or not raw:
        return
    out = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        g = lambda k: str(p.get(k) or "").strip()   # noqa: E731
        if not (g('name') or g('cid')):
            continue
        out.append({
            "name": g('name'),
            "age": g('age'),
            "citizen_id": g('cid'),
            "job": g('occupation'),
            "car_regno": g('car_reg'),
            "address": g('address'),
            "tel_no": g('phone'),
            "hospital": g('hospital'),
            "cost": g('treat_cost'),
            "injure": g('symptom'),
            "gender": g('gender'),
            "person_type": g('person_type'),      # ป้ายไทย → PERSON_TYPE_LABEL
            "wounded_type": g('wound_level'),
            "work_place": g('work_place'),
            "position": g('position'),
            "income": g('income'),
            "relation": g('relation'),
            "treat_from": g('treat_from'),
            "treat_to": g('treat_to'),
        })
    if out:
        data.injuries = out


def _populate_claim_from_report(data, rep):
    """เติม ClaimData จาก report (ค่าไทยของ se-survey) ให้ fill_* กรอกหน้าหลัก EMCS ได้ครบ —
    XML import ตั้งค่าไว้บางส่วน แต่ fill_* ต้องมีค่าไทยใน ClaimData เพื่อเลือก dropdown บังคับ
    (ประเภทรถ/จังหวัด/ยี่ห้อ/คำนำหน้า/ลักษณะเหตุ). คืน loss_type (ลักษณะความเสียหาย) สำหรับ run_import"""
    def gv(k):
        return str(rep.get(k) or '').strip()

    def split_dt(k):
        v = gv(k)
        return (v.split('|', 1)[0].strip(), v.split('|', 1)[1].strip()) if '|' in v else (v, '')

    ct = gv('car_type').upper()
    data.prb_car_type = _CAR_TYPE_TH.get(ct, gv('car_type'))
    data.plate_province = gv('car_province')
    data.car_brand = gv('car_brand')
    data.car_color = gv('car_color')
    data.car_reg_year = gv('car_reg_year')
    data.ev_type = gv('ev_type')
    data.ev_battery_no = gv('ev_battery_no')
    data.ev_charger_no = gv('ev_charger_no')
    data.ev_battery_start = gv('ev_battery_start')
    data.insure_plate = gv('license_plate')
    data.insure_model = gv('car_model')
    data.insure_chassis = gv('chassis_no')
    data.insure_engine = gv('engine_no')
    data.insure_name = gv('assured_name')
    # ผู้ขับขี่ (se-survey มีคำนำหน้า/เพศตรง ๆ)
    data.driver_title = gv('driver_title')
    data.driver_name = gv('driver_first_name') or data.driver_name
    data.driver_surname = gv('driver_last_name') or data.driver_surname
    data.driver_gender = gv('driver_gender') or data.driver_gender
    data.driver_relation = gv('driver_relation')
    data.driver_age = gv('driver_age')
    data.driver_address = gv('driver_address')
    data.driver_province = gv('driver_province')
    data.driver_amphur = gv('driver_district')
    data.driver_phone = gv('driver_phone')
    data.driver_idcard = gv('driver_id_card')
    data.driver_license_no = gv('driver_license_no')
    data.driver_license_place = gv('driver_license_place')
    data.driver_license_type = gv('driver_license_type')
    data.driver_birthdate = gv('driver_birthdate')
    data.license_issue_date = gv('driver_license_start')
    data.license_expiry_date = gv('driver_license_end')
    # อุบัติเหตุ
    data.acc_province = gv('acc_province')
    data.acc_amphur = gv('acc_district')
    data.acc_type_desc = gv('acc_cause')
    data.acc_place = gv('acc_place')
    data.acc_detail = gv('acc_detail') or gv('survey_result')
    data.acc_date = gv('acc_date')
    data.acc_time = gv('acc_time')
    data.acc_result = gv('acc_fault')
    # EMCS บังคับ 2 อย่างนี้เมื่อผลคดี = "รถคู่กรณีเป็นฝ่ายผิด" — ไม่มี = save draft ไม่ผ่าน
    data.acc_fault_opponent_no = gv('acc_fault_opponent_no')
    data.opo_results = gv('acc_claim_opponent')          # comma-separated จากแอป
    data.opo_pay = _money(gv('acc_claim_amount'))
    data.opo_recovery = _money(gv('acc_claim_total_amount'))
    data.followup_type = gv('acc_followup')
    data.followup_count = gv('acc_followup_count')
    data.followup_detail = gv('acc_followup_detail')
    data.followup_date = gv('acc_followup_date')
    # ความเห็น/ผลสำรวจ → EMCS หน้าหลัก (ช่องมาร์ค 'not used' แต่ user ขอให้เติม; se-survey มีข้อความครบ)
    # ยุบ \n → เว้นวรรค: txtAcc_result/txtAcc_Comment เป็น input บรรทัดเดียว — ส่ง \n ผ่าน send_keys
    # = กด Enter อาจ trigger postback ก่อนเวลา
    # คงบรรทัดใหม่ไว้ — ปลายทางหลักคือ textarea 3 ช่องบนหน้าค่าใช้จ่าย และงานจริงของพนักงาน
    # เขียนเป็น bullet ~20 บรรทัด (ตัวอย่างจริงไอโออิ: ผลการดำเนินงาน 1,453 ตัวอักษร)
    # ยุบบรรทัดทิ้ง = อ่านยาก/ผิดรูปแบบสำนวน. ช่องชื่อเดียวกันบนหน้า 1 เป็น input บรรทัดเดียว
    # → ฝั่งนั้นยุบบรรทัดตอนกรอก (กัน Enter trigger postback)
    def _keep_lines(v):
        return "\n".join(" ".join(ln.split()) for ln in str(v or "").splitlines()).strip()

    data.accident_summary = _keep_lines(gv('survey_result'))   # → ผลการดำเนินงาน (txtAcc_result)
    data.review_comment = _keep_lines(gv('review_comment'))    # → ความเห็นของผู้ตรวจสอบ (txtAcc_Comment)
    # → ความเห็นของเซอร์เวย์ (txtSurv_Comment) — fallback ไป notes ให้ตรงกับ xmlExport.service.ts
    # (SURV_COMMENT = surveyor_comment || notes) ไม่งั้น 2 เส้นทางส่งไม่เท่ากัน
    data.surveyor_comment = _keep_lines(gv('surveyor_comment')) or _keep_lines(gv('notes'))
    data.surveyor_name = gv('acc_surveyor') or gv('surveyor_name')
    data.surveyor_phone = gv('acc_surveyor_phone') or gv('surveyor_phone')
    data.mileage = gv('mileage')
    data.police_name = gv('acc_police_name')
    data.police_station = gv('acc_police_station')
    data.police_comment = gv('acc_police_comment')
    data.police_date = gv('acc_police_date')
    data.police_book_no = gv('acc_police_book_no')
    data.alcohol_test = gv('acc_alcohol_test')
    data.alcohol_result = gv('acc_alcohol_result')
    data.assured_email = gv('assured_email')
    data.deductible = gv('deductible')
    data.model_no = gv('model_no')
    data.driver_by_policy = gv('driver_by_policy')
    data.driver_ticket = gv('driver_ticket')      # → txtDri_Order
    data.car_lost = bool(rep.get('car_lost'))     # → chkLost_Car
    data.damage_estimate = gv('estimated_cost')
    data.prb_number = gv('prb_number')
    data.notify_value = gv('claim_ref_no')   # เลขที่รับแจ้ง (บังคับ * — se-survey มีรูปแบบถูก)
    data.noti_date, data.noti_time = split_dt('acc_insurance_notify_date')
    # ลูกค้าแจ้ง บ.ประกัน = คนละเวลากับ บ.ประกันแจ้งสำรวจ (se-survey เก็บแยก, XML ส่งแยกถูกแล้ว)
    data.call_date, data.call_time = split_dt('acc_customer_report_date')
    data.arrive_date, data.arrive_time = split_dt('acc_survey_arrive_date')
    data.finish_date, data.finish_time = split_dt('acc_survey_complete_date')
    # คู่กรณี: เขียนทับ third_parties ด้วยค่าไทยจาก report (XML ให้ code — fill_third_parties เลือก dropdown ไม่ได้)
    _populate_third_parties_from_report(data, rep)
    _populate_injuries_from_report(data, rep)
    # ความเสียหายรถประกัน (แผนภาพ structured) → รายการ EMCS ให้ fill_damage_list กรอก popup ได้
    # se-survey เก็บ insured_damage = [{part, pos:L/R/A, level:L/M/H/X}] (ไม่มีประเภท ครูด/บุบ แยก)
    # ก่อนหน้านี้ data.damage ว่างเสมอ → ฟอร์มความเสียหาย EMCS เปล่า ต้องติ๊กเองทุกเคส
    dmg = _report_damage_items(rep.get('insured_damage'))
    if dmg:
        data.damage = [p for p, _, _ in dmg]
        data.type_damage = [''] * len(dmg)   # แอปไม่มีประเภทความเสียหายแยก (fill ใช้ชิ้นส่วน+ระดับพอ)
        data.rank_damage = [r for _, r, _ in dmg]
        data.side_damage = [sd for _, _, sd in dmg]   # ด้าน (radio แยกของ EMCS)
        data.cost_damage = [''] * len(dmg)
    # ลักษณะความเสียหาย: se-survey มี acc_damage_type → ใช้เลย; ไม่มี → 'auto' (resolve_loss_type เดิม)
    return gv('acc_damage_type') or 'auto'


def _is_arrival_photo(name: str) -> bool:
    """รูป "ยืนยันถึงที่เกิดเหตุ" ที่แอปบังคับถ่ายก่อนเริ่มสำรวจ — เป็นหลักฐานภายในของ
    se-survey (พิสูจน์ว่าผู้สำรวจไปถึงจริง) **ไม่ใช่รูปประกอบสำนวนของประกัน**
    กติกา user 2026-07-26: ห้ามส่งเข้า EMCS. ชื่อไฟล์จาก backend = arrival.jpg
    (case_<id>/job_<id>/arrival.jpg); เผื่ออนาคตมีหลายใบ arrival_1.jpg ฯลฯ"""
    stem = str(name or "").rsplit(".", 1)[0].strip().lower()
    return stem == "arrival" or stem.startswith("arrival_")


def _images_from_zip_drop(cfg, claim_no, img_dir, quiet: bool = False):
    """เคสไม่มีรูปในแอป → ใช้ zip export ของเคลมที่วางไว้ใน SESURVEY_ZIP_DIR เป็นแหล่งรูป

    ทำไม: เคสที่ข้อมูลมาจาก XML export ของพอร์ทัลล้วน (พนักงานไม่ได้ถ่ายผ่านแอป) รูปจะอยู่
    ในไฟล์ zip ที่โหลดมาจากพอร์ทัล ซึ่งแยกโฟลเดอร์ตามหมวดมาให้แล้ว (PICTURES/INS, /TP_VEH,
    /ACC_MAP, ...) — เดิม zip ถูกใช้เป็นแค่ "แหล่งหมวด" ของรูปที่ดึงมาจาก API เท่านั้น
    ไม่มีรูปจาก API = ไม่มีรูปขึ้น EMCS เลย

    extract_zip_images() แตกรูป + เขียน _categories.json ให้ครบในตัว
    คืน path โฟลเดอร์รูป (None ถ้าไม่เจอ zip)"""
    if not claim_no:
        return None
    zip_dir = Path(str(getattr(cfg, "sesurvey_zip_dir", "") or (cfg.base_dir / "zip_import")))
    # จับคู่ด้วย claim_matches (ขอบเขตชัด + เลขต้องยาวพอ) ไม่ใช่ glob substring —
    # เลขเคลมสั้นเคยไปคว้า zip ของเคลมอื่นมาทั้งก้อน (เจอสด 2026-08-01 เลข '11')
    cands = claim_zips(zip_dir, claim_no)
    if not cands:
        if not quiet:
            log(f"   (ไม่พบ zip export ของเคลมนี้ใน {zip_dir} → ไม่มีรูปส่งขึ้น EMCS)")
        return None
    zp = cands[0]
    try:
        counts = extract_zip_images(zp, img_dir)
    except Exception as e:
        log(f"   ⚠️ แตก zip {zp.name} ไม่ได้ ({type(e).__name__}: {e})")
        return None
    if not counts:
        log(f"   ⚠️ zip {zp.name} ไม่มีไฟล์รูป")
        return None
    log(f"✓ ใช้รูปจาก zip export {zp.name} → {img_dir} "
        f"({', '.join(f'{k}:{v}' for k, v in sorted(counts.items()))})")
    return str(img_dir)


def _download_case_photos(cfg, case_id, hdrs, claim_no):
    """โหลดรูปของเคสจาก se-survey ลง downloaded_images/<เลขเคลม>/ — คืน path โฟลเดอร์ (None ถ้าไม่มีรูป)

    เขียน sidecar `_categories.json` = {ชื่อไฟล์: หมวดไทย} คู่มาด้วย — se-survey เก็บ category
    ต่อรูป (ป้ายไทยชุดเดียวกับ 'ประเภทรูป' EMCS) → upload_images อ่านไฟล์นี้จัดกลุ่มอัปแยกประเภท
    (แทนที่จะอัปทั้งกองเป็น 'รูปรถประกัน' ประเภทเดียว)"""
    import requests, json
    try:
        pr = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/photos",
                          headers=hdrs, timeout=30)
        pr.raise_for_status()
        photos = (pr.json().get("data") or {}).get("photos") or []
    except Exception as e:
        log(f"⚠️ ดึงรายการรูปไม่ได้: {e} — ข้ามขั้นโหลดรูป")
        return None
    img_dir = cfg.download_dir / (claim_no or f"sesurvey_{case_id}")
    if not photos:
        log("(เคสนี้ไม่มีรูปบน server)")
        # ไม่มีรูปในแอป → ใช้ zip export ของเคลมเป็น "แหล่งรูป" (ไม่ใช่แค่แหล่งหมวด)
        # เคสที่ข้อมูลมาจาก XML export ล้วน (ไม่ได้ถ่ายผ่านแอป) จะได้รูปครบพร้อมหมวด
        return _images_from_zip_drop(cfg, claim_no, img_dir)
    img_dir.mkdir(parents=True, exist_ok=True)
    got = 0
    names = []     # ชื่อไฟล์ทั้งหมด (ไว้จับคู่หมวดจาก zip export ถ้า API ไม่มี category)
    cat_map = {}   # ชื่อไฟล์ → หมวดไทย (ประเภทรูป EMCS) สำหรับ upload_images จัดกลุ่ม
    skipped_arrival = 0
    for ph in photos:
        rel = str(ph.get("file_path") or "")
        name = rel.split("/")[-1]
        if not name:
            continue
        if _is_arrival_photo(name):
            skipped_arrival += 1
            continue
        names.append(name)
        cat = str(ph.get("category") or "").strip()
        if cat:
            cat_map[name] = cat
        dest = img_dir / name
        if dest.exists() and dest.stat().st_size > 0:
            got += 1
            continue
        try:
            fr = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/files",
                              params={"path": rel}, headers=hdrs, timeout=60)
            fr.raise_for_status()
            dest.write_bytes(fr.content)
            got += 1
        except Exception as e:
            log(f"   ⚠️ โหลดรูป {name} ไม่ได้: {e}")
    # API ไม่มี category (รูปที่โหลด/รับมาจากภายนอก เช่น LINE/อีเมล/ระบบอื่น ไม่ได้ถ่ายในแอปจึงไม่ถูก tag)
    # → ลองเติมหมวดจาก zip export ของเคลม
    # (ISURVEY/EMCS "ดาวน์โหลดรูปภาพ") ที่วางไว้ใน SESURVEY_ZIP_DIR (default base_dir/zip_import)
    if not cat_map:
        zip_dir = str(getattr(cfg, "sesurvey_zip_dir", "") or (cfg.base_dir / "zip_import"))
        exp = categories_from_export(zip_dir, claim_no)
        if exp:
            for n in names:
                if n in exp:
                    cat_map[n] = exp[n]
            log(f"   API ไม่มีหมวด → เติมจาก zip export {len(cat_map)}/{got} รูป (drop: {zip_dir})")
        else:
            log(f"   API ไม่มีหมวด + ไม่พบ zip export ({zip_dir}) → อัปเป็น 'รูปประกอบ'")

    # zip export ของเคลม = "แหล่งรูปเพิ่ม" ด้วย ไม่ใช่แค่แหล่งหมวด — เคสที่พนักงานไม่ได้ถ่าย
    # ผ่านแอป (งานที่รับมาเป็น zip+xml จากพอร์ทัล) API จะมีแค่รูปยืนยันถึงที่เกิดเหตุใบเดียว
    # ถ้ารอเงื่อนไข "ไม่มีรูปเลย" จะไม่มีวันเข้าเงื่อนไข → รวมรูปจาก zip เสมอเมื่อมี zip วางไว้
    # (หมวดจาก API ชนะเสมอ; zip เติมเฉพาะไฟล์ที่ API ไม่มี)
    zres = _images_from_zip_drop(cfg, claim_no, img_dir, quiet=True)
    if zres:
        try:
            zcat = json.loads((img_dir / "_categories.json").read_text(encoding="utf-8"))
        except Exception:
            zcat = {}
        added = {k: v for k, v in zcat.items() if k not in cat_map}
        cat_map = {**added, **cat_map}      # ของ API ทับของ zip
        log(f"   + รวมรูปจาก zip export อีก {len(added)} ใบ (พร้อมหมวด)")

    # sidecar หมวดรูป (ให้ upload_images จัดกลุ่มตามประเภท); ไม่มีหมวดเลย = ไม่เขียน (flow เดิมยังทำงาน)
    if cat_map:
        try:
            (img_dir / "_categories.json").write_text(
                json.dumps(cat_map, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log(f"   ⚠️ เขียน _categories.json ไม่ได้: {e}")
    if skipped_arrival:
        log(f"   – ข้ามรูปยืนยันถึงที่เกิดเหตุ {skipped_arrival} ใบ (ไม่ต้องส่งเข้า EMCS)")
    log(f"✓ รูปเคส {got}/{len(photos) - skipped_arrival} ไฟล์ → {img_dir} "
        f"(มีหมวด {len(cat_map)} รูป)")
    return str(img_dir)


def _run_fill_existing(cfg, args, case_id, hdrs, meta):
    """เปิด draft ที่ import ไว้แล้วมาเติมหน้าหลัก/คู่กรณี/รูป/ค่าใช้จ่าย + บันทึก —
    ไม่ import ซ้ำ ไม่สร้าง draft ใหม่ ไม่ mark ซ้ำ ไม่กดส่งงาน"""
    import requests
    import xml.etree.ElementTree as ET
    from autokey import emcs
    from autokey.claim_data import ClaimData
    from autokey.surv_xml import enrich_claim_from_xml

    if not meta.get("emcs_imported_at"):
        raise SystemExit(f"เคส #{case_id} ยังไม่เคย import เข้า EMCS — ไม่มี draft ให้เติม "
                         "(ใช้ --sesurvey-live เพื่อ import สร้าง draft ก่อน)")
    esurvey = str(meta.get("emcs_esurvey_no") or "").strip()

    # ดึง XML (.txt) เพื่อ enrich คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน + เลขเคลม/เซอร์เวย์
    r = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/export-xml",
                     headers=hdrs, timeout=30)
    r.raise_for_status()
    xml_dir = cfg.runs_dir / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    xml_path = xml_dir / f"sesurvey_case_{case_id}.txt"
    xml_path.write_bytes(r.content)
    rep_el = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace")).find("TXN_SURV_REPORT")
    gt = lambda t: (rep_el.findtext(t) or "").strip() if rep_el is not None else ""
    claim_no = gt("REF_CLAIM_NO")

    data = ClaimData()
    data.claim_value = claim_no
    data.invoice_value = gt("SURV_JOBNO")
    data.xml_file = str(xml_path)
    enrich_claim_from_xml(data, xml_path)
    loss_type = "auto"
    severity = "เบา"
    try:
        rr = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/report",
                          headers=hdrs, timeout=20)
        rr.raise_for_status()
        _rep = rr.json().get("data") or {}
        loss_type = _populate_claim_from_report(data, _rep)
        severity = str(_rep.get('damage_level') or '').strip() or 'เบา'
        log(f"✓ เติมข้อมูลหน้าหลักจาก report (ประเภทรถ {data.prb_car_type!r}, "
            f"ลักษณะความเสียหาย {loss_type!r}, รถเสียหาย {severity!r}, ชิ้นส่วน {len(data.damage)})")
    except Exception as e:
        log(f"⚠️ ดึง report มาเติม ClaimData ไม่ได้ ({e}) — fill_* อาจหยุดรอบางช่อง")

    # --skip-images: กรณีกลับมาแก้ฟิลด์บน draft ที่อัปรูปไปแล้ว — อัปซ้ำ = รูปซ้ำทั้งชุด
    # (EMCS ไม่ dedupe ชื่อไฟล์) ต้องสั่งข้ามเอง
    img_folder = None if getattr(args, "skip_images", False) else         _download_case_photos(cfg, case_id, hdrs, claim_no)
    if img_folder is None and getattr(args, "skip_images", False):
        log("--skip-images: ข้ามการอัปรูป (draft นี้มีรูปอยู่แล้ว)")

    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())
    driver = make_driver(detach=True, download_dir=per_run_dl)
    banner(f"FILL-EXISTING: เปิด draft เดิมเคส #{case_id} (e-Survey {esurvey or '?'}) เติมข้อมูล — ไม่ import ซ้ำ")
    try:
        emcs.fill_existing_report(driver, cfg, data, esurvey=esurvey,
                                  images_folder=img_folder, loss_type=loss_type,
                                  severity=severity, full_billing=False)
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs", tag=f"fill_existing_{case_id}")
        raise
    banner(f"FILL-EXISTING: เติม+บันทึก draft เสร็จ (e-Survey {esurvey}) — "
           "ตรวจงาน + กรอกค่าใช้จ่าย + กดส่งเอง (บอทไม่กดส่ง)")


def _run_images_only(cfg, args, case_id, hdrs, meta):
    """เปิด draft เดิม แล้วอัปเฉพาะ 'รูป' แยกตามประเภทรูป EMCS (ตาม category ของ se-survey) —
    ไม่แตะหน้าหลัก/คู่กรณี/ค่าใช้จ่าย (กันเขียนทับที่ผู้ตรวจแก้) ไม่ import ซ้ำ ไม่ mark ไม่กดส่งงาน"""
    import requests
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from autokey import emcs
    from autokey.images import list_images

    if not meta.get("emcs_imported_at"):
        raise SystemExit(f"เคส #{case_id} ยังไม่เคย import เข้า EMCS — ไม่มี draft ให้เติมรูป")
    esurvey = str(meta.get("emcs_esurvey_no") or "").strip()

    # ต้องรู้เลขเคลมเพื่อค้นเรื่องเดิมใน EMCS
    r = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/export-xml",
                     headers=hdrs, timeout=30)
    r.raise_for_status()
    xml_dir = cfg.runs_dir / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    xml_path = xml_dir / f"sesurvey_case_{case_id}.txt"
    xml_path.write_bytes(r.content)
    rep_el = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace")).find("TXN_SURV_REPORT")
    claim_no = (rep_el.findtext("REF_CLAIM_NO") or "").strip() if rep_el is not None else ""
    if not claim_no:
        raise SystemExit("อ่านเลขเคลมจาก XML ไม่ได้ — อัปรูปไม่ได้")

    img_folder = _download_case_photos(cfg, case_id, hdrs, claim_no)
    if not img_folder:
        raise SystemExit(f"เคส #{case_id} ไม่มีรูปให้อัป")

    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())
    # detach=False + quit หลังเสร็จ = ปิด browser เอง → ปลดล็อกงานทันที ให้ผู้ตรวจเปิดดู/กดส่งต่อได้
    # (EMCS ล็อกงานเมื่อ session ใดเปิดเรื่องอยู่ — เปิดค้าง = session อื่นเปิดไม่ได้ e-Survey link ถูก disable)
    driver = make_driver(detach=False, download_dir=per_run_dl)
    banner(f"IMAGES-ONLY: เปิด draft เดิมเคส #{case_id} (e-Survey {esurvey or '?'}) อัปรูปแยกประเภท — ไม่แตะฟอร์ม")
    try:
        emcs.login(driver, cfg)
        if esurvey:
            emcs.open_report_images(driver, claim_no, esurvey)
        else:
            reports = emcs.find_existing_reports(driver, claim_no)
            if not reports:
                raise RuntimeError(f"ไม่พบเรื่องเดิมของเคลม {claim_no} ใน EMCS")
            emcs.open_report_images(driver, claim_no, emcs._pick_draft_report(reports, ""))
        # อัปทุกรูป (only=รายชื่อไฟล์ทั้งหมด = ไม่เปิด webui ให้เลือก) — upload_images จัดกลุ่มตามหมวดเอง
        names = list_images(Path(img_folder))
        emcs.upload_images(driver, img_folder, only=names)
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs", tag=f"images_only_{case_id}")
        raise
    finally:
        try:
            driver.quit()   # ปิด browser = ปลดล็อกงานทันที
        except Exception:
            pass
    banner(f"IMAGES-ONLY: อัปรูปเสร็จ (e-Survey {esurvey}) — ตรวจ + กดส่งเอง (บอทไม่กดส่ง ไม่แตะฟอร์ม)")


def _run_injured_only(cfg, args, case_id, hdrs, meta):
    """เปิด draft เดิม → เติม 'เฉพาะบล็อกผู้บาดเจ็บ' + บันทึก (re-save หน้าหลักเพื่อปลดล็อกเมนู) —
    ไม่แตะคู่กรณี/ความเสียหาย/ทรัพย์สิน/รูป/ค่าใช้จ่าย (กันเพิ่ม row ซ้ำ + อัปรูปซ้ำ) ไม่กดส่งงาน.
    ใช้เมื่อบล็อกผู้บาดเจ็บ save ไม่ผ่านตอน import (เช่น รพ.ว่าง ติด required-gate) แล้วแก้ด้วย _dash '-'"""
    import requests
    import xml.etree.ElementTree as ET
    from autokey import emcs
    from autokey.claim_data import ClaimData
    from autokey.surv_xml import enrich_claim_from_xml

    if not meta.get("emcs_imported_at"):
        raise SystemExit(f"เคส #{case_id} ยังไม่เคย import เข้า EMCS — ไม่มี draft ให้เติมผู้บาดเจ็บ")
    esurvey = str(meta.get("emcs_esurvey_no") or "").strip()

    r = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/export-xml",
                     headers=hdrs, timeout=30)
    r.raise_for_status()
    xml_dir = cfg.runs_dir / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    xml_path = xml_dir / f"sesurvey_case_{case_id}.txt"
    xml_path.write_bytes(r.content)
    rep_el = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace")).find("TXN_SURV_REPORT")
    gt = lambda t: (rep_el.findtext(t) or "").strip() if rep_el is not None else ""
    claim_no = gt("REF_CLAIM_NO")

    data = ClaimData()
    data.claim_value = claim_no
    data.invoice_value = gt("SURV_JOBNO")
    data.xml_file = str(xml_path)
    enrich_claim_from_xml(data, xml_path)
    if not data.injuries:
        raise SystemExit(f"เคส #{case_id} ไม่มีผู้บาดเจ็บใน XML — ไม่มีอะไรให้เติม")
    loss_type = "auto"
    severity = "เบา"
    try:
        rr = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/report",
                          headers=hdrs, timeout=20)
        rr.raise_for_status()
        _rep = rr.json().get("data") or {}
        loss_type = _populate_claim_from_report(data, _rep)
        severity = str(_rep.get('damage_level') or '').strip() or 'เบา'
        log(f"✓ เติมข้อมูลหน้าหลักจาก report (ผู้บาดเจ็บ {len(data.injuries)} คน)")
    except Exception as e:
        log(f"⚠️ ดึง report มาเติม ClaimData ไม่ได้ ({e}) — re-save หน้าหลักอาจหยุดรอบางช่อง")

    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())
    # detach=False + quit หลังเสร็จ = ปิด browser → ปลดล็อกงานให้ผู้ตรวจเปิดต่อได้
    driver = make_driver(detach=False, download_dir=per_run_dl)
    banner(f"INJURED-ONLY: เปิด draft เดิมเคส #{case_id} (e-Survey {esurvey or '?'}) เติมเฉพาะผู้บาดเจ็บ — ไม่แตะส่วนอื่น")
    try:
        emcs.fill_injured_only_existing(driver, cfg, data, esurvey=esurvey,
                                        loss_type=loss_type, severity=severity)
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs", tag=f"injured_only_{case_id}")
        raise
    finally:
        try:
            driver.quit()   # ปิด browser = ปลดล็อกงานทันที
        except Exception:
            pass
    banner(f"INJURED-ONLY: เติม+บันทึกผู้บาดเจ็บเสร็จ (e-Survey {esurvey}) — ตรวจงาน + กดส่งเอง (บอทไม่กดส่ง)")


def _resolve_case_id_by_survey(cfg, hdrs, survey_no):
    """resolve เลขเซอร์เวย์ (survey_job_no เช่น SETP-69060062) → case id ผ่าน /api/integrations/cases.
    เลขเซอร์เวย์ SETP unique → เจอตัวเดียว. list คืนเฉพาะเคส surveyed/reviewed ล่าสุด 100 เคส
    (เก่ากว่านั้นใส่ case id ตรง ๆ). คืน case_id (str) หรือ SystemExit ถ้าไม่เจอ/ซ้ำ"""
    import requests
    sv = str(survey_no).strip()
    try:
        r = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases", headers=hdrs, timeout=20)
        r.raise_for_status()
        cases = ((r.json() or {}).get("data") or {}).get("cases") or []
    except Exception as e:
        raise SystemExit(f"ค้นเลขเซอร์เวย์ '{sv}' ไม่ได้ (ดึงรายการเคส se-survey ล้มเหลว: {e})")
    hits = [c for c in cases
            if str(c.get("survey_job_no") or "").strip().lower() == sv.lower()]
    if not hits:
        raise SystemExit(f"ไม่พบเลขเซอร์เวย์ '{sv}' (ค้นจากเคส surveyed/reviewed ล่าสุด {len(cases)} เคส) "
                         "— เช็คเลข/สิทธิ์ token หรือใส่ case id แทน")
    if len(hits) > 1:
        ids = ", ".join(str(c.get("id")) for c in hits)
        raise SystemExit(f"เลขเซอร์เวย์ '{sv}' ตรงหลายเคส (id {ids}) — ผิดปกติ (SETP ควร unique) ใช้ case id แทน")
    cid = str(hits[0].get("id"))
    log(f"✓ เลขเซอร์เวย์ {sv} → เคส #{cid} (claim {hits[0].get('claim_no') or '?'})")
    return cid


def _mark_emcs_imported(cfg, case_id, hdrs, esurvey: str):
    """แจ้ง se-survey ว่าเคสนี้มี draft ใน EMCS แล้ว (ปิด loop กันกดนำเข้าซ้ำ)"""
    import requests
    try:
        mr = requests.post(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/emcs-imported",
                           headers=hdrs, json={"esurvey_no": esurvey or ""}, timeout=20)
        if mr.ok:
            d = mr.json().get("data") or {}
            log("✓ แจ้ง se-survey ว่านำเข้าแล้ว" + (" (mark ไว้ก่อนแล้ว)" if d.get("already") else ""))
        else:
            log(f"⚠️ แจ้ง se-survey (emcs-imported) ไม่สำเร็จ: HTTP {mr.status_code} — "
                "mark ด้วยมือภายหลัง กันปุ่มนำเข้าถูกกดซ้ำ")
    except Exception as e:
        log(f"⚠️ แจ้ง se-survey (emcs-imported) ไม่ได้: {e} — mark ด้วยมือภายหลัง")


def run_emcs_images(cfg, args):
    """ดู (และลบ) รูปที่แนบไว้ในเรื่องเดิมของ EMCS — ไม่แตะข้อมูลส่วนอื่นเลย

    ไม่ระบุ --emcs-delete-image = อ่านอย่างเดียว (list ชื่อไฟล์/ประเภท/วันที่แนบ)
    ใช้ลบรูปที่หลุดขึ้นไป เช่นรูปยืนยันถึงที่เกิดเหตุก่อน fix 727411f"""
    claim = args.emcs_images.strip()
    names = [n.strip() for n in (args.emcs_delete_image or "").split(",") if n.strip()]
    banner(f"EMCS รูปแนบ: เคลม {claim}" + (f" — ลบ {len(names)} ใบ" if names else " (อ่านอย่างเดียว)"))
    driver = make_driver(detach=True,
                         download_dir=cfg.download_dir / "_dl" / str(os.getpid()))
    try:
        emcs.login(driver, cfg)
        reports = emcs.find_existing_reports(driver, claim)
        if not reports:
            raise SystemExit(f"ไม่พบเรื่องของเคลม {claim} ใน EMCS")
        target = emcs._pick_draft_report(reports, args.esurvey)
        log(f"EMCS: เปิดเรื่อง {target} → หน้ารูป")
        emcs.open_report_images(driver, claim, target)
        emcs.click_retry(driver, emcs.By.ID, "wuMenuPage1_imbImage")
        emcs.time.sleep(2)
        rows = emcs.list_report_images(driver)
        log(f"รูปที่แนบไว้ {len(rows)} ใบ:")
        for r in rows:
            log(f"   [{r['seq']:>3}] {r['name']:<32} | {r['type']:<24} | ครั้งที่ {r['round']} | {r['added']}")
        if not names:
            log("(อ่านอย่างเดียว — ใส่ --emcs-delete-image \"ชื่อไฟล์\" เพื่อลบ)")
            return
        emcs.delete_report_images(driver, names)
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs", tag=f"emcsimg_{claim}")
        raise
    finally:
        driver.quit()


def run_sesurvey_import(cfg, args):
    """โหมดงานจาก se-survey: ดึง SURV_REPORT XML ของเคสจาก api.sesurvey.cloud
    (แอปสำรวจของเราเอง — ข้อมูลครบกว่า XML ของ ISURVEY) → ตรวจ/parse → นำเข้า EMCS

    ⚠️ ตอนนี้ยัง DRY-RUN เสมอ: หยุดหลัง parse สำเร็จ ไม่เปิด browser/ไม่แตะ EMCS
    (ตามข้อตกลง — เปิดใช้จริงหลังสรุปการทดสอบกับ EMCS ร่วมกัน โดยต่อเข้า
    flow เดียวกับ run_import_xml: emcs.run_import + _offer_submit)"""
    import requests
    from autokey.surv_xml import parse_surv_report

    raw_ref = str(args.sesurvey_case).strip()
    if not raw_ref:
        raise SystemExit("--sesurvey-case ว่าง — ใส่เลขเคส (case id) หรือเลขเซอร์เวย์ (SETP-...)")
    if not cfg.sesurvey_api_token:
        raise SystemExit("ไม่พบ SESURVEY_API_TOKEN ใน .env — ขอ token จากผู้ดูแลระบบ se-survey")

    hdrs = {"Authorization": f"Bearer {cfg.sesurvey_api_token}"}
    # auto-detect: ตัวเลขล้วน = case id (db); อื่น ๆ = เลขเซอร์เวย์ (survey_job_no เช่น SETP-...) → resolve
    case_id = raw_ref if raw_ref.isdigit() else _resolve_case_id_by_survey(cfg, hdrs, raw_ref)

    # ── ด่านกันซ้ำ (สำคัญที่สุด): เคสที่ import เข้า EMCS ไปแล้ว ห้าม import อีก ──
    # EMCS ไม่กันเลขเคลมซ้ำ — import ซ้ำ = สร้างเรื่องซ้ำที่เลขเคลมเดิม (ลบไม่ได้ ยกเลิกได้อย่างเดียว)
    # fail-closed: เช็คสถานะไม่ได้ = หยุด (โหมด import จริงจะต่อยอดจาก flow นี้ ห้ามปล่อยผ่านทั้งที่ไม่รู้สถานะ)
    try:
        meta_r = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}",
                              headers=hdrs, timeout=20)
        meta_r.raise_for_status()
        meta = meta_r.json().get("data") or {}
    except Exception as e:
        raise SystemExit(f"เช็คสถานะเคส #{case_id} จาก se-survey ไม่ได้ ({e}) — หยุดก่อนเพื่อกัน import ซ้ำ")
    # โหมดเติม draft เดิม (--sesurvey-fill-existing): เคสต้อง import แล้ว (มี esurvey) — เปิดเรื่องเดิม
    # มาเติมหน้าหลัก/รูป/ค่าใช้จ่าย ไม่ import ซ้ำ ไม่ mark ซ้ำ (draft มีอยู่แล้ว = ไม่สร้างเรื่องใหม่)
    if getattr(args, "sesurvey_fill_existing", False):
        return _run_fill_existing(cfg, args, case_id, hdrs, meta)
    # โหมดอัปรูปอย่างเดียว (--sesurvey-images-only): เปิด draft เดิม อัปรูปแยกประเภท ไม่แตะฟอร์ม
    if getattr(args, "sesurvey_images_only", False):
        return _run_images_only(cfg, args, case_id, hdrs, meta)
    # โหมดเติมเฉพาะผู้บาดเจ็บ (--sesurvey-injured-only): เปิด draft เดิม เติมบล็อกผู้บาดเจ็บ ไม่แตะส่วนอื่น
    if getattr(args, "sesurvey_injured_only", False):
        return _run_injured_only(cfg, args, case_id, hdrs, meta)

    if meta.get("emcs_imported_at"):
        banner(f"⛔ เคส #{case_id} นำเข้า EMCS ไปแล้วเมื่อ {meta['emcs_imported_at']}"
               + (f" (e-Survey {meta.get('emcs_esurvey_no')})" if meta.get("emcs_esurvey_no") else "")
               + " — ไม่ทำซ้ำ (กันเรื่องซ้ำที่เลขเคลมเดิม)")
        return

    banner(f"ดึง XML เคส #{case_id} จาก se-survey ({cfg.sesurvey_api_url})")
    url = f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/export-xml"
    resp = requests.get(url, headers=hdrs, timeout=30)
    if resp.status_code == 404:
        raise SystemExit(f"ไม่พบเคส #{case_id} หรือเคสยังไม่มีข้อมูลรายงานสำรวจ")
    if resp.status_code == 401:
        raise SystemExit("token ไม่ถูกต้อง/integration ยังไม่เปิดบน server (INTEGRATION_TOKEN)")
    resp.raise_for_status()

    xml_dir = cfg.runs_dir / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)
    # ⚠️ นามสกุลต้องเป็น .txt — EMCS (change handler ของ inpImport) รับเฉพาะ .txt เท่านั้น
    # ไฟล์ .xml จะโดน validate ล้างทิ้งทันที ("ระบบรองรับไฟล์ นามสกุล .txt เท่านั้น")
    # เนื้อหาเป็น INSERT_SURV_REPORT_XML เหมือนเดิม (ไฟล์ export ของ ISURVEY ก็เป็น .txt)
    xml_path = xml_dir / f"sesurvey_case_{case_id}.txt"
    xml_path.write_bytes(resp.content)
    log(f"✓ บันทึก {xml_path} ({len(resp.content)} bytes)")

    parsed = parse_surv_report(xml_path)
    # สรุปหัวเรื่องจากตัว XML เอง (SURV_JOBNO/REF_CLAIM_NO อยู่ใน TXN_SURV_REPORT)
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
    rep = root.find("TXN_SURV_REPORT")
    get_tag = lambda t: (rep.findtext(t) or "").strip() if rep is not None else ""
    log_plain("")
    log_plain(f"  เลขเคลม:      {get_tag('REF_CLAIM_NO')}")
    log_plain(f"  เลขเซอร์เวย์: {get_tag('SURV_JOBNO')}")
    log_plain(f"  ผู้เอาประกัน:  {get_tag('ASSURED_NAME')}")
    log_plain(f"  ผู้สำรวจ:      {get_tag('ACC_SURV')}")
    log_plain(f"  สถานที่:       {get_tag('ACC_PLACE')} (จังหวัด {get_tag('ACC_PROVINCEID')}"
              f" อำเภอ {get_tag('ACC_DISTRICTID')})")
    log_plain(f"  คู่กรณี {len(parsed['third_parties'])} / ผู้บาดเจ็บ {len(parsed['injuries'])}"
              f" / ทรัพย์สิน {len(parsed['assets'])}")

    # resolve รหัสบริษัทประกันของ EMCS (ddlInsurerNameMajor) จากชื่อบริษัทของเคส —
    # ตอน import จริง ต้องเลือกบริษัทให้ถูกก่อนอัปโหลด XML ไม่งั้นเข้าผิดบริษัท
    from autokey.insurer_map import resolve_insurer_code
    company = meta.get("insurance_company") or ""
    ins_code = resolve_insurer_code(company)
    if company:
        if ins_code:
            log(f"✓ บริษัทประกัน: {company} → รหัส EMCS {ins_code}")
        else:
            log(f"⚠️ บริษัทประกัน: {company} — ยังไม่มีรหัส EMCS ในตาราง "
                f"(เติมใน autokey/insurer_map.py ก่อนเปิดโหมดนำเข้าจริง)")
    else:
        log("⚠️ ไม่ทราบบริษัทประกันของเคส — ตรวจก่อน import")

    # โหลดรูปของเคส + หมวดรูป → downloaded_images/<เลขเคลม>/ (เขียน _categories.json ให้ upload_images
    # จัดกลุ่มตามประเภทตอน import). _download_case_photos ใช้ API category ก่อน; ไม่มี → fallback zip export
    # (SESURVEY_ZIP_DIR). ทำใน dry-run ด้วย (แค่โหลด+จัดหมวด ไม่แตะ EMCS)
    claim_no = get_tag("REF_CLAIM_NO") or f"sesurvey_{case_id}"
    img_folder = _download_case_photos(cfg, case_id, hdrs, claim_no)

    # ── GATE: default dry-run (หยุดก่อนแตะ EMCS); --sesurvey-live เท่านั้นถึงจะ import จริง ──
    if not getattr(args, "sesurvey_live", False):
        banner("DRY-RUN: ตรวจ XML + resolve บริษัท + โหลดรูปครบ — หยุดก่อนแตะ EMCS "
               f"(ถ้าเปิด --sesurvey-live จะเลือกบริษัทรหัส {ins_code or '?'} + import XML เป็น draft)")
        return

    # ===== LIVE: import จริงเข้า EMCS (draft-only — บอทหยุดที่ draft คนกดส่งเอง) =====
    if not ins_code:
        raise SystemExit(f"resolve รหัสบริษัทประกันของ '{company}' ไม่ได้ — ยกเลิก import "
                         "(กัน import เข้าผิดบริษัท) เติมใน autokey/insurer_map.py ก่อน")

    from autokey import emcs
    from autokey.claim_data import ClaimData
    from autokey.surv_xml import enrich_claim_from_xml

    # สร้าง ClaimData ให้ fill_imported ใช้: import_xml_report อ่าน data.xml_file (EMCS parse เอง);
    # enrich เติมคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน/เพศผู้ขับ จาก XML เพื่ออุดช่องที่ import ทิ้งว่าง
    data = ClaimData()
    data.claim_value = get_tag("REF_CLAIM_NO")
    data.invoice_value = get_tag("SURV_JOBNO")
    data.xml_file = str(xml_path)
    enrich_claim_from_xml(data, xml_path)

    # เติมค่าไทยจาก report ของ se-survey → fill_* กรอกหน้าหลัก EMCS (dropdown บังคับ) ได้ครบ
    # ไม่งั้น btnUpdate ไม่ผ่าน validation (ประเภทรถ/จังหวัด/ยี่ห้อ/คำนำหน้า/ลักษณะเหตุ ว่าง)
    loss_type = "auto"
    severity = "เบา"   # รถเสียหาย หนัก/เบา (HEV_CAR) — เดิมเดา 'เบา' เสมอ; เติมจาก damage_level ด้านล่าง
    try:
        rr = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/report",
                          headers=hdrs, timeout=20)
        rr.raise_for_status()
        rep = rr.json().get("data") or {}
        loss_type = _populate_claim_from_report(data, rep)
        severity = str(rep.get('damage_level') or '').strip() or 'เบา'  # หนัก/เบา (มือถือบังคับเลือก)
        log(f"✓ เติมข้อมูลหน้าหลักจาก report (ประเภทรถ {data.prb_car_type!r}, "
            f"จังหวัดเกิดเหตุ {data.acc_province!r}, ลักษณะความเสียหาย {loss_type!r}, "
            f"รถเสียหาย {severity!r}, ชิ้นส่วน {len(data.damage)})")
    except Exception as e:
        log(f"⚠️ ดึง report มาเติม ClaimData ไม่ได้ ({e}) — fill_* อาจหยุดรอกรอกมือบางช่อง")

    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())
    driver = make_driver(detach=True, download_dir=per_run_dl)
    banner(f"LIVE: นำเข้าเคส #{case_id} เข้า EMCS (บริษัทรหัส {ins_code}) — draft-only")
    try:
        # ต้นทาง se-survey → หน้าค่าใช้จ่ายกรอกแค่ 2 ช่อง (เลขที่ใบแจ้งหนี้ + วันที่วางบิล)
        # ความเห็น/เรทราคา หัวหน้ากรอกเองใน EMCS — กติกา user 2026-08-03
        esurvey = emcs.run_import(driver, cfg, data, images_folder=img_folder,
                                  insurer_code=ins_code, full_billing=False, loss_type=loss_type,
                                  severity=severity)
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs", tag=f"sesurvey_{case_id}")
        # draft อาจถูกสร้างไปแล้วก่อนพัง (ลบใน EMCS ไม่ได้) — ต้อง mark ฝั่ง se-survey
        # ให้ตรงความจริง ไม่งั้น --sesurvey-fill-existing จะปฏิเสธว่า "ยังไม่เคย import"
        # แล้วคนต้องมา mark มือเอง / หรือเผลอกด import ซ้ำจนได้ draft สองใบ
        partial = getattr(emcs.fill_imported, "last_draft_esurvey", "")
        if partial:
            log(f"⚠️ draft {partial} ถูกสร้างใน EMCS แล้วก่อนงานจะพัง — mark ฝั่ง se-survey ให้ตรงความจริง")
            _mark_emcs_imported(cfg, case_id, hdrs, partial)
            log(f"   → แก้ต้นเหตุแล้วรันต่อด้วย: --sesurvey-case {case_id} "
                f"--sesurvey-fill-existing --esurvey {partial}")
        raise

    # ── mark กลับ se-survey ทันทีที่ draft สร้างสำเร็จ (ปิด loop กันซ้ำ) ──
    # draft ถูกสร้างใน EMCS แล้ว = เลขเคลมนี้ถือว่า "นำเข้าแล้ว" ต่อให้คนยังไม่กดส่ง
    # (กัน import รอบสองมาสร้าง draft ซ้ำที่เลขเคลมเดิม)
    _mark_emcs_imported(cfg, case_id, hdrs, esurvey)

    banner(f"LIVE: สร้าง draft ใน EMCS สำเร็จ"
           + (f" (e-Survey {esurvey})" if esurvey else "")
           + " — ตรวจงาน + กรอกค่าใช้จ่าย + กดส่งเอง (บอทไม่กดส่งให้)")


def run_report_isurvey(cfg, args):
    """แจ้ง ISURVEY ว่าเคลม 'ส่งงานแล้ว' — gate ด้วยสถานะ EMCS ก่อนเสมอ
    (ถ้ายังไม่กดส่งงานใหม่ใน EMCS จะข้าม ไม่ยิง)"""
    from autokey import isurvey_report
    targets = build_targets(args)
    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())
    driver = make_driver(detach=True, download_dir=per_run_dl)
    results = []
    try:
        emcs.login(driver, cfg)
        for claim, invoice in targets:
            banner(f"แจ้ง ISURVEY: เคลม {claim}")
            info = emcs.report_status(driver, claim)
            st = (info or {}).get("status", "").strip()
            if not info:
                log("⏭️ ข้าม — ไม่พบเรื่องของเคลมนี้ใน EMCS")
                results.append((claim, "⏭️", "ไม่พบเรื่องใน EMCS"))
                continue
            if (not st) or st in emcs.DRAFT_STATUSES:
                log(f"⏭️ ข้าม — ยังไม่ได้กดส่งงานใหม่ใน EMCS (สถานะ: {st or 'อ่านไม่ได้'})")
                results.append((claim, "⏭️", f"ยังไม่ส่งงาน ({st or 'อ่านสถานะไม่ได้'})"))
                continue
            log(f"✓ EMCS ส่งงานแล้ว (สถานะ: {st})")
            survey_no = info.get("survey_no") or invoice
            keyer = isurvey_report.keyer_for(claim)
            when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            res = isurvey_report.report_sent(cfg, claim, survey_no, keyer=keyer,
                                             when=when, dry_run=args.dry_run)
            icon = ("🧪" if args.dry_run else "✅") if res["ok"] else "❌"
            log(f"{icon} แจ้ง ISURVEY (คนคีย์ {keyer or '?'}) — {res['text'][:140]}")
            results.append((claim, icon, f"{keyer} | {res['text'][:70]}"))
    finally:
        driver.quit()
    banner("สรุปการแจ้ง ISURVEY" + (" (dry-run ไม่ยิงจริง)" if args.dry_run else ""))
    for c, icon, detail in results:
        log_plain(f"  {icon} {c} — {detail}")


def _sekey_dup_skip(cfg, data) -> str:
    """ตรวจเลขเซอร์เวย์ซ้ำกับ se-key DB ก่อนกรอก EMCS
    คืนข้อความเหตุผล 'ข้าม' ถ้าซ้ำ (มีใน DB แล้วไม่ว่าสถานะไหน) — '' = ทำต่อได้
    (ไม่ได้เปิดใช้ se-key / ไม่มีเลขเซอร์เวย์ / ตรวจไม่ได้ = fail-open ทำต่อ)"""
    if not sekey_client.enabled(cfg):
        return ""
    survey_no = (data.invoice_value or "").strip()
    if not survey_no:
        return ""
    res = sekey_client.check_survey(cfg, survey_no)
    if not res["ok"]:
        log(f"   ⚠️ ตรวจซ้ำกับ se-key ไม่ได้ ({res.get('error', '')}) — ทำงานต่อ (fail-open)")
        return ""
    if res["exists"]:
        status = "ส่งแล้ว" if res["sent"] else "รอส่ง"
        return (f"เลขเซอร์เวย์ {survey_no} มีใน se-key DB แล้ว "
                f"({status}, {res['count']} แถว)")
    return ""


def _offer_submit(driver, cfg, data, esurvey: str = ""):
    """A1: หลังกรอกครบ (live session, ปุ่ม 'ส่งงานใหม่' พร้อม) — รอผู้ใช้ตรวจ draft
    แล้วสั่งส่ง → กด 'ส่งงานใหม่' ให้ + แจ้ง ISURVEY + บันทึก se-key.
    ไม่สั่ง (EOF/ปิด) = เก็บเป็น draft
    เคลมสด (มีคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน) ก็เสนอส่งได้ — แต่เตือนให้ตรวจหนักกว่า"""
    block = data.fresh_claim_note()
    reason = ("" if block == "" else
              f"⚠️ เคลมสด: {block} — ตรวจคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน + ราคา "
              "ให้ครบถูกต้องบน EMCS ก่อนกดส่ง (ส่งแล้วแก้ไม่ได้)")
    sel = wait_for_submit(data.claim_value, survey_no=data.invoice_value, reason=reason)
    if not sel:
        log("เก็บเป็น draft — ยังไม่ส่งงาน (browser เปิดค้าง ตรวจ/กดส่งเองได้)")
        return
    ok, msg = emcs.submit_report(driver, cfg, data.claim_value)
    if not ok:
        log(f"❌ ส่งงานไม่สำเร็จ: {msg} — ตรวจบน EMCS เอง (ยังไม่แจ้ง ISURVEY)")
        announce_send_failed(data.claim_value, msg)   # การ์ดต้องไม่ขึ้น 'เสร็จแล้ว ✅'
        joblog.record("send_failed", data.claim_value, data.invoice_value,
                      esurvey=esurvey, note=msg)
        return
    log(f"✅ {msg}")
    keyer = isurvey_report.keyer_for(data.claim_value)
    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # ลงสมุดงานทันทีที่ EMCS ยืนยันสถานะแล้ว — ไม่รอผลแจ้ง ISURVEY/se-key เพราะ
    # "ส่งงานบน EMCS แล้ว" เป็นข้อเท็จจริงที่ต้องเก็บไว้ ต่อให้ 2 ระบบหลังยิงพลาด
    joblog.record("sent", data.claim_value, data.invoice_value,
                  esurvey=esurvey, keyer=keyer,
                  work_type=sel["base_type"] + (" +งานรวม" if sel["batch"] else ""),
                  note=msg)
    announce_sent(data.claim_value, esurvey, keyer)   # ให้การ์ดบนหน้าเว็บปิดตัวเอง
    # SESV เคลมเงินบน iSurvey ด้วยเลข SESV ไม่ได้ → แจ้งด้วย SEABI invoice ตัวแรก (mix[0])
    report_invoice = (sel["mix"][0] if (sel["base_type"] == "SESV" and sel["mix"])
                      else data.invoice_value)
    res = isurvey_report.report_sent(cfg, data.claim_value, report_invoice,
                                     keyer=keyer, when=when)
    log((f"✅ แจ้ง ISURVEY สำเร็จ (คนคีย์ {keyer})" if res["ok"]
         else "❌ แจ้ง ISURVEY ไม่สำเร็จ") + f" — {res['text'][:140]}")

    # บันทึกงานที่เสร็จลงฐานข้อมูลกลาง se-key — ตามประเภทงานที่ผู้ใช้เลือก
    # (งานรวม/SESV = หลาย row); mark "ส่งแล้ว" ถ้าแจ้ง ISURVEY สำเร็จ
    if sekey_client.enabled(cfg):
        payloads = sekey_client.build_payloads(
            data.claim_value, data.invoice_value, keyer=keyer,
            base_type=sel["base_type"], batch=sel["batch"], mix_values=sel["mix"])
        results = sekey_client.save_many(cfg, payloads, mark_sent=res["ok"])
        ok_n = sum(1 for r in results if r["ok"])
        wt = sel["base_type"] + (" +งานรวม" if sel["batch"] else "")
        if ok_n == len(results):
            log(f"✅ บันทึกลง se-key DB {ok_n} row (work_type: {wt})")
        else:
            bad = next((r["text"] for r in results if not r["ok"]), "")
            log(f"⚠️ บันทึก se-key DB {ok_n}/{len(results)} row (work_type: {wt}) — {bad[:120]}")


def main():
    # กัน console Windows แสดงภาษาไทยเพี้ยน
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = parse_args()
    cfg = load_config()
    # เติม PID กันชื่อชนกันเมื่อรันหลายงานพร้อมกัน (เริ่มในวินาทีเดียวกันได้)
    set_log_file(cfg.runs_dir / "logs"
                 / f"run_{datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.log")

    # --emcs-images: ดู/ลบรูปที่แนบไว้ในเรื่องเดิม (ไม่แตะข้อมูลอื่นเลย) แล้วจบ
    if args.emcs_images:
        run_emcs_images(cfg, args)
        return
    # --sesurvey-case: ดึงงานจากระบบ se-survey → นำเข้า EMCS (default dry-run; --sesurvey-live = จริง) แล้วจบ
    if args.sesurvey_case:
        run_sesurvey_import(cfg, args)
        return
    # --report-isurvey: แจ้งสถานะ "ส่งงานแล้ว" กลับ ISURVEY (gate ด้วยสถานะ EMCS) แล้วจบ
    if args.report_isurvey:
        run_report_isurvey(cfg, args)
        return
    # --compare: อ่านสองทางเทียบกัน (เปิด browser ฝั่ง scrape เท่านั้น) แล้วจบ
    if args.compare:
        run_compare(cfg, args)
        return
    # --images-only: เติมรูปเข้า draft เดิม (ไม่สร้างเรื่องใหม่/ไม่แตะข้อมูลอื่น) แล้วจบ
    if args.images_only:
        run_images_only(cfg, args)
        return
    # --import-xml: ให้ EMCS import ฟอร์มหลักจาก XML แล้วบอทอุดช่องว่าง/กรอกที่เหลือ แล้วจบ
    if args.import_xml:
        run_import_xml(cfg, args)
        return
    # read-only (ไม่ใช่ --scrape): อ่านผ่าน API ล้วน ไม่เปิด browser เลย แล้วจบ
    if args.read_only and not args.scrape and not args.data_json:
        run_api_readonly(cfg, args)
        return

    # โฟลเดอร์ดาวน์โหลด default แยกต่อ process — กันไฟล์ export ปนกันเมื่อรันหลายงานพร้อมกัน
    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())

    # เปิด Chrome "ตอนจะใช้จริง" เท่านั้น — เส้นทางปกติอ่าน ISURVEY ผ่าน HTTP API
    # ไม่ต้องมีเบราว์เซอร์เลย เดิมเปิดตั้งแต่ต้นแล้วปล่อยว่างตลอดช่วงอ่าน (~40 วิ)
    # และถ้าอ่าน ISURVEY ล้มเหลวก็เหลือหน้าต่างเปล่าค้างไว้ (detach=True ไม่ปิดเอง)
    _browser = {"d": None}

    def browser():
        """Chrome ของงานนี้ — สร้างครั้งแรกที่ถูกเรียก"""
        if _browser["d"] is None:
            _browser["d"] = make_driver(detach=True, download_dir=per_run_dl)
        return _browser["d"]

    def close_browser():
        """ปิด Chrome ถ้าเคยเปิด (ไม่เคยเปิด = ไม่ต้องทำอะไร)"""
        d = _browser["d"]
        if d is None:
            return
        _browser["d"] = None
        try:
            d.quit()
        except Exception:
            pass

    data = None

    # ---------------- ส่วนที่ 1: อ่านข้อมูลจาก ISURVEY ----------------
    if args.data_json:
        banner(f"โหลดข้อมูลจากไฟล์ {args.data_json}")
        data = ClaimData.load(args.data_json)
        # JSON เก่าอาจยังไม่มีข้อมูลจาก XML (เช่น เพศผู้ขับขี่) — เติมให้
        if data.xml_file and Path(data.xml_file).exists():
            enrich_claim_from_xml(data, data.xml_file)
        log_plain(data.summary())
        log_plain("")
        log_plain(data.validation_report())
    else:
        targets = build_targets(args)
        # หลายเคลม + ไม่ใช่ read-only = batch เต็มรูปแบบ: อ่าน→กรอก EMCS ทีละเคลม
        batch_fill = len(targets) > 1 and not args.read_only

        if batch_fill and not args.yes:
            log_plain("\nรายการเคลมที่จะทำ:")
            for c, _ in targets:
                log_plain(f"  - {c}")
            input(f"\n>> จะอ่าน + กรอก EMCS ทั้งหมด {len(targets)} เคลม "
                  "(บันทึกเป็น draft ไม่กดส่งงาน) — กด Enter เพื่อเริ่ม "
                  "/ Ctrl+C ยกเลิก << ")

        results = []  # (claim, icon, รายละเอียด)
        last_data = None
        # tab ISURVEY มีเฉพาะโหมด --scrape (เส้น API ไม่เปิดเบราว์เซอร์ตอนอ่าน)
        isurvey_handle = browser().current_window_handle if args.scrape else None
        emcs_handle = None
        emcs_mainpage = ""

        def _dismiss_alert():
            try:
                browser().switch_to.alert.accept()
            except Exception:
                pass

        def _read_with_retry(claim, invoice):
            """อ่านเคลม (retry 1 รอบเมื่อ session โดนเตะ) → (data | None, err)"""
            for attempt in (1, 2):
                try:
                    # โหมด API ไม่ใช้ driver เลย — อย่าเรียก browser() ให้เปิดฟรี
                    _d = browser() if args.scrape else None
                    return read_one_claim(_d, cfg, claim, invoice, args), ""
                except UnexpectedAlertPresentException:
                    _dismiss_alert()
                    log(f"   ⚠️ session หลุด (มี login ซ้อนจากที่อื่น) — "
                        f"login ใหม่แล้วลองอีกครั้ง ({attempt}/2)")
                    if attempt == 2:
                        return None, "session หลุดซ้ำ — มีคนใช้บัญชีเดียวกันอยู่?"
                except Exception as e:
                    log(f"❌ อ่านเคลม {claim} ล้มเหลว: {type(e).__name__}: {e}")
                    if _browser["d"] is not None:
                        save_debug_snapshot(_browser["d"], cfg.runs_dir / "logs",
                                            tag=f"error_{claim}")
                    return None, f"{type(e).__name__}: {e}"
            return None, "ไม่ทราบสาเหตุ"

        for i, (claim, invoice) in enumerate(targets, 1):
            banner(f"[{i}/{len(targets)}] เคลม {claim}")
            if isurvey_handle is not None:
                browser().switch_to.window(isurvey_handle)

            d, err = _read_with_retry(claim, invoice)
            if d is None:
                results.append((claim, "❌", f"อ่านไม่สำเร็จ — {err}"))
                continue
            last_data = d
            log_plain("")
            log_plain(d.summary())
            log_plain("")
            log_plain(d.validation_report())

            if not batch_fill:
                results.append((claim, "📖", "อ่านสำเร็จ"))
                continue

            # ---------- กรอก EMCS ต่อทันที (โหมด batch) ----------
            # ด่าน "เคลมแห้งเท่านั้น" ถอดออก 2026-08-03 (ดูเหตุผลที่ flow เคลมเดียว)
            _note = d.fresh_claim_note()
            if _note:
                log(f"ℹ️ เคลมสด: {_note} — กรอกต่อ (อ่าน tab-4/5/6 ครบแล้ว)")
            dup = _sekey_dup_skip(cfg, d)
            if dup:
                log(f"⏭️ ข้าม — {dup}")
                results.append((claim, "⏭️", f"ข้าม: {dup}"))
                continue
            # สร้าง/หยิบ Chrome ก่อนเข้า try — ให้ except ข้างล่างมี driver ใช้เก็บ snapshot แน่นอน
            driver = browser()
            try:
                if emcs_handle is not None:
                    try:
                        driver.switch_to.window(emcs_handle)
                    except Exception:
                        emcs_handle = None
                if emcs_handle is None:
                    # เปิด tab ใหม่เฉพาะตอนมี tab ISURVEY ให้คงไว้เทียบ (--scrape)
                    # เส้น API ใช้ tab แรกได้เลย ไม่งั้นเหลือ tab เปล่าค้างทุกงาน
                    if isurvey_handle is not None:
                        driver.switch_to.new_window("tab")
                    emcs_handle = driver.current_window_handle

                emcs_mainpage = emcs.goto_mainpage(driver, cfg, emcs_mainpage)
                esurvey = emcs.fill_one(
                    driver, cfg, d,
                    images_folder=(None if args.skip_images else
                                   resolve_images_dir(cfg, d.claim_value,
                                                      for_read=False)),
                    loss_type=args.loss_type, image_type=args.image_type,
                    severity=args.severity, force_new=args.force_new,
                )
                save_debug_snapshot(driver, cfg.runs_dir / "logs",
                                    tag=f"done_{d.claim_value}")
                results.append((claim, "✅",
                                f"กรอกครบ — e-Survey {esurvey or '(ไม่ทราบเลข)'}"))
            except RuntimeError as e:
                if "มีเรื่องใน EMCS" in str(e):
                    log(f"⏭️ {e}")
                    results.append((claim, "⏭️",
                                    "ข้าม: มีเรื่องใน EMCS อยู่แล้ว (กันเปิดซ้ำ)"))
                else:
                    log(f"❌ เคลม {claim}: {e}")
                    save_debug_snapshot(driver, cfg.runs_dir / "logs",
                                        tag=f"error_emcs_{claim}")
                    results.append((claim, "❌", f"กรอกไม่สำเร็จ — {e}"))
            except Exception as e:
                log(f"❌ เคลม {claim}: {type(e).__name__}: {e}")
                save_debug_snapshot(driver, cfg.runs_dir / "logs",
                                    tag=f"error_emcs_{claim}")
                results.append((claim, "❌",
                                f"กรอกไม่สำเร็จ — {type(e).__name__}: {e}"))

        # ---------- จบโหมดหลายเคลม ----------
        if len(targets) > 1:
            banner("สรุปผลทั้งหมด")
            for claim, icon, detail in results:
                log_plain(f"  {icon} {claim} — {detail}")
            if batch_fill:
                ok = sum(1 for _, icon, _ in results if icon == "✅")
                log_plain(f"\n  กรอกสำเร็จ {ok}/{len(results)} เคลม")
                log_plain("  → เข้า EMCS ตรวจทีละเรื่อง แล้วกด 'ส่งงานใหม่' เอง "
                          "(สคริปต์ไม่กดให้เด็ดขาด)")
                log("browser เปิดค้างไว้ให้ตรวจ (สคริปต์จบการทำงานแล้ว)")
            else:
                ok = sum(1 for _, icon, _ in results if icon == "📖")
                log_plain(f"\n  อ่านสำเร็จ {ok}/{len(results)} เคลม")
                log_plain("  กรอก EMCS ต่อ: python main.py --data-json "
                          "runs/<เลขเคลม>.json")
                close_browser()  # โหมดอ่านไม่เปิด browser ค้าง (กัน session ชน)
            return

        # ---------- เคลมเดียว: ไปต่อทางเดิม ----------
        if last_data is None:
            close_browser()
            raise SystemExit(1)
        data = last_data

    if args.read_only:
        banner("จบโหมดอ่านอย่างเดียว (--read-only)")
        close_browser()
        return

    # คำนำหน้าผู้ขับขี่จาก CLI (หน้าเว็บส่งมาเมื่อผู้ใช้เลือกเอง) — ทับค่าที่อนุมานไม่ได้
    if getattr(args, "driver_title", "").strip() and not (data.driver_title or "").strip():
        data.driver_title = args.driver_title.strip()
        log_plain(f"\nℹ️ ใช้คำนำหน้าผู้ขับขี่ที่ระบุมา: {data.driver_title}")

    # ---------------- ส่วนที่ 2: กรอกข้อมูลลง EMCS ----------------
    # ด่าน "เคลมแห้งเท่านั้น" (user 2026-06-11) ถอดออกแล้ว 2026-08-03 — เหตุผลเดิมคือ
    # เคลมสดอ่านผ่าน API แล้วคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินหายเงียบ ๆ ตอนนี้แก้ที่ต้นเหตุ
    # (isurvey_api อ่าน tab-4/5/6 ครบทุกประเภทเคลม) ด่านจึงไม่มีเหตุผลให้อยู่ต่อ
    _note = data.fresh_claim_note()
    if _note:
        log_plain(f"\nℹ️ เคลมสด: {_note} — อ่านคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินมาครบแล้ว "
                  "แต่ตรวจให้ละเอียดก่อนกดส่งงาน")

    # ตรวจเลขเซอร์เวย์ซ้ำกับ se-key DB — ซ้ำ = หยุด ไม่กรอก EMCS (กันทำงานซ้ำ)
    dup = _sekey_dup_skip(cfg, data)
    if dup:
        banner("หยุด: เลขเซอร์เวย์นี้ทำไปแล้ว — ไม่กรอก EMCS")
        log_plain(f"  {dup}\n"
                  "  (กันทำซ้ำ — ถ้าต้องการทำซ้ำจริง ลบ/แก้สถานะใน se-key admin ก่อน)")
        close_browser()
        return

    if not args.yes:
        input("\n>> ตรวจสอบข้อมูลด้านบน แล้วกด Enter เพื่อเริ่มกรอก EMCS "
              "(Ctrl+C เพื่อยกเลิก) << ")

    banner("ส่วนที่ 2: กรอกข้อมูลลง EMCS")
    driver = browser()          # ถึงตรงนี้ค่อยต้องใช้เบราว์เซอร์จริง
    # เปิด tab ใหม่เฉพาะตอนอ่านแบบ --scrape (มี tab ISURVEY ให้คงไว้ดูเทียบ)
    # เส้น API ไม่ได้เปิดอะไรใน tab แรก — เปิด tab ที่สองจะเหลือหน้าว่างค้างเปล่า ๆ
    if args.scrape and not args.data_json:
        driver.switch_to.new_window("tab")

    images_folder = None
    if not args.skip_images:
        images_folder = resolve_images_dir(cfg, data.claim_value, for_read=False)

    try:
        if args.fill_existing:
            # เรื่องมีอยู่แล้วบน EMCS (เช่นหน้าหลักถูกบันทึกไปแล้วแต่ส่วนที่เหลือยังว่าง)
            # → เปิดเรื่องเดิม กด "แก้ไข" แล้วกรอกต่อ ไม่ต้องยกเลิกทิ้งแล้วทำใหม่
            # (ตราบใดที่ยังไม่กด "ส่งงานใหม่" ที่หน้าค่าใช้จ่าย ยังแก้ได้ — กติกา user 2026-08-04)
            esurvey = emcs.fill_existing_report(
                driver, cfg, data,
                esurvey=args.esurvey,
                images_folder=images_folder,
                loss_type=args.loss_type,
                image_type=args.image_type,
                severity=args.severity,
                full_billing=not args.no_save_price,
            )
        else:
            esurvey = emcs.run_fill(
                driver, cfg, data,
                images_folder=images_folder,
                loss_type=args.loss_type,
                image_type=args.image_type,
                severity=args.severity,
                force_new=args.force_new,
                full_billing=not args.no_save_price,
            )
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs",
                            tag=f"error_emcs_{data.claim_value}")
        raise

    # เก็บภาพหน้าสุดท้ายไว้เป็นหลักฐานการตรวจสอบ
    save_debug_snapshot(driver, cfg.runs_dir / "logs",
                        tag=f"done_{data.claim_value}")

    banner("กรอกครบทุกหน้าแล้ว (draft)"
           + (f" | e-Survey {esurvey}" if esurvey else ""))
    joblog.record("draft", data.claim_value, data.invoice_value, esurvey)
    # A1: เสนอกด "ส่งงาน + แจ้ง ISURVEY" — ทั้งเคลมแห้งและเคลมสด (live session ปุ่มพร้อม)
    # เคลมสด: _offer_submit ใส่คำเตือนให้ตรวจคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินหนักกว่าก่อนส่ง
    # (ยังไม่กด 'ส่งงานใหม่' เองจนกว่าผู้ใช้กดปุ่มบน webui + confirm)
    _offer_submit(driver, cfg, data, esurvey=esurvey)


if __name__ == "__main__":
    main()
