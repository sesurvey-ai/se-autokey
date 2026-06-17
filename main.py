"""se-autokey: ดึงข้อมูลเคลมจาก ISURVEY แล้วกรอกลง EMCS ในคำสั่งเดียว

ตัวอย่างการใช้งาน:
    python main.py --claim 2026013105763
    python main.py --claim 2026013105763 --invoice SEABI-213260100295
    python main.py --claim 2026013105763 --read-only          # อ่านอย่างเดียว ไม่กรอก EMCS
    python main.py --claims 2026013105763,2026013105999       # อ่านหลายเคลมรวดเดียว
    python main.py --claims-file claims.txt                   # รายการเคลมจากไฟล์ (บรรทัดละเคลม)
    python main.py --data-json runs/2026013105763.json        # กรอก EMCS จากข้อมูลที่อ่านไว้แล้ว

- อ่านหลายเคลม = โหมดอ่านอย่างเดียวเสมอ (กรอก EMCS ทีละเคลมผ่าน --data-json)
- จบงานแล้วสคริปต์จะ "ไม่กดบันทึกหน้าค่าใช้จ่าย" และเปิด browser ค้างไว้ให้ตรวจเอง
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

from autokey import emcs, isurvey, isurvey_api
from autokey.browser import (
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
    download_xml_export,
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
                   help="หลายเคลมคั่นด้วย comma เช่น 111,222,333 (อ่านอย่างเดียว)")
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
    p.add_argument("--loss-type", default="auto",
                   help="ลักษณะความเสียหาย (default 'auto' = เลือกตามข้อมูล: "
                        "ไม่มีคู่กรณี→เคลมแห้ง, มีคู่กรณี→ตามผลคดี / "
                        "ระบุชื่อเองได้ / ใส่ \"\" เพื่อข้าม)")
    p.add_argument("--image-type", default="รูปรถประกัน",
                   help="ประเภทรูปตอนอัปโหลด (default 'รูปรถประกัน')")
    p.add_argument("--severity", choices=["เบา", "หนัก"], default="เบา",
                   help="รถเสียหาย หนัก/เบา (field บังคับของ EMCS, default เบา)")
    p.add_argument("--force-new", action="store_true",
                   help="สร้างเรื่องใหม่แม้เคลมนี้จะมีเรื่องใน EMCS อยู่แล้ว "
                        "(ปกติระบบจะหยุดกันเปิดเรื่องซ้ำ)")
    p.add_argument("--allow-fresh", action="store_true",
                   help="อนุญาตกรอกเคลมสด/มีคู่กรณี (นโยบายปัจจุบัน: "
                        "เคลมแห้งเท่านั้น — เคลมสดพักไว้)")
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
    p.add_argument("--report-isurvey", action="store_true",
                   help="แจ้ง ISURVEY ว่าเคลม 'ส่งงานแล้ว' — ตรวจ EMCS ว่ากดส่งงานใหม่จริง "
                        "ก่อน (gate) ถ้ายังไม่ส่งจะไม่ยิง (ไม่อ่าน/ไม่กรอกฝั่งหน้า)")
    p.add_argument("--dry-run", action="store_true",
                   help="ใช้กับ --report-isurvey: ตรวจ gate + โชว์ payload แต่ไม่ยิงจริง")
    args = p.parse_args()

    if not (args.claim or args.claims or args.claims_file or args.data_json):
        p.error("ต้องระบุ --claim / --claims / --claims-file / --data-json อย่างน้อยหนึ่งอย่าง")
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

    # จัดชื่อรูป: ใบรับงาน → 1.jpg, ที่เหลือ → รูปรถประกันN.jpg
    # (ถ้า zip บอกว่าไม่มีเอกสาร REPORTS เลย ก็ไม่มีใบรับงานให้หา — ข้าม)
    if img_dir is not None and list_images(img_dir):
        if zip_counts and zip_counts.get("REPORTS", 0) == 0:
            log("   เคลมนี้ไม่มีเอกสารใบรับงานในชุดรูป (REPORTS ว่าง) — "
                "ข้ามการตั้งชื่อ 1.jpg รูปคงชื่อเดิม")
        else:
            prepare_images(img_dir, cfg.template_path, args.threshold)

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


def _offer_submit(driver, cfg, data):
    """A1: หลังกรอกครบ (live session, ปุ่ม 'ส่งงานใหม่' พร้อม) — รอผู้ใช้ตรวจ draft
    แล้วสั่งส่ง → กด 'ส่งงานใหม่' ให้ + แจ้ง ISURVEY. ไม่สั่ง (EOF/ปิด) = เก็บเป็น draft"""
    if not wait_for_submit(data.claim_value):
        log("เก็บเป็น draft — ยังไม่ส่งงาน (browser เปิดค้าง ตรวจ/กดส่งเองได้)")
        return
    ok, msg = emcs.submit_report(driver, cfg, data.claim_value)
    if not ok:
        log(f"❌ ส่งงานไม่สำเร็จ: {msg} — ตรวจบน EMCS เอง (ยังไม่แจ้ง ISURVEY)")
        return
    log(f"✅ {msg}")
    keyer = isurvey_report.keyer_for(data.claim_value)
    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    res = isurvey_report.report_sent(cfg, data.claim_value, data.invoice_value,
                                     keyer=keyer, when=when)
    log((f"✅ แจ้ง ISURVEY สำเร็จ (คนคีย์ {keyer})" if res["ok"]
         else "❌ แจ้ง ISURVEY ไม่สำเร็จ") + f" — {res['text'][:140]}")

    # บันทึกงานที่เสร็จลงฐานข้อมูลกลาง se-key (mark "ส่งแล้ว" ถ้าแจ้ง ISURVEY สำเร็จ)
    if sekey_client.enabled(cfg):
        sk = sekey_client.save_record(
            cfg, data.claim_value, data.invoice_value,
            keyer=keyer, mark_sent=res["ok"])
        log((f"✅ บันทึกลง se-key DB (id {sk.get('record_id')}, "
             f"{'ส่งแล้ว' if sk.get('sent') else 'รอส่ง'})") if sk["ok"]
            else f"❌ บันทึกลง se-key DB ไม่สำเร็จ — {sk['text'][:140]}")


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

    # --report-isurvey: แจ้งสถานะ "ส่งงานแล้ว" กลับ ISURVEY (gate ด้วยสถานะ EMCS) แล้วจบ
    if args.report_isurvey:
        run_report_isurvey(cfg, args)
        return
    # --compare: อ่านสองทางเทียบกัน (เปิด browser ฝั่ง scrape เท่านั้น) แล้วจบ
    if args.compare:
        run_compare(cfg, args)
        return
    # read-only (ไม่ใช่ --scrape): อ่านผ่าน API ล้วน ไม่เปิด browser เลย แล้วจบ
    if args.read_only and not args.scrape and not args.data_json:
        run_api_readonly(cfg, args)
        return

    # โฟลเดอร์ดาวน์โหลด default แยกต่อ process — กันไฟล์ export ปนกันเมื่อรันหลายงานพร้อมกัน
    per_run_dl = cfg.download_dir / "_dl" / str(os.getpid())
    driver = make_driver(detach=True, download_dir=per_run_dl)
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
        isurvey_handle = driver.current_window_handle
        emcs_handle = None
        emcs_mainpage = ""

        def _dismiss_alert():
            try:
                driver.switch_to.alert.accept()
            except Exception:
                pass

        def _read_with_retry(claim, invoice):
            """อ่านเคลม (retry 1 รอบเมื่อ session โดนเตะ) → (data | None, err)"""
            for attempt in (1, 2):
                try:
                    return read_one_claim(driver, cfg, claim, invoice, args), ""
                except UnexpectedAlertPresentException:
                    _dismiss_alert()
                    log(f"   ⚠️ session หลุด (มี login ซ้อนจากที่อื่น) — "
                        f"login ใหม่แล้วลองอีกครั้ง ({attempt}/2)")
                    if attempt == 2:
                        return None, "session หลุดซ้ำ — มีคนใช้บัญชีเดียวกันอยู่?"
                except Exception as e:
                    log(f"❌ อ่านเคลม {claim} ล้มเหลว: {type(e).__name__}: {e}")
                    save_debug_snapshot(driver, cfg.runs_dir / "logs",
                                        tag=f"error_{claim}")
                    return None, f"{type(e).__name__}: {e}"
            return None, "ไม่ทราบสาเหตุ"

        for i, (claim, invoice) in enumerate(targets, 1):
            banner(f"[{i}/{len(targets)}] เคลม {claim}")
            driver.switch_to.window(isurvey_handle)

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
            block_reason = d.dry_claim_block_reason()
            if block_reason and not args.allow_fresh:
                results.append((claim, "⏭️",
                                f"ข้าม: {block_reason} — นโยบายเคลมแห้งเท่านั้น"))
                continue
            dup = _sekey_dup_skip(cfg, d)
            if dup:
                log(f"⏭️ ข้าม — {dup}")
                results.append((claim, "⏭️", f"ข้าม: {dup}"))
                continue
            try:
                if emcs_handle is not None:
                    try:
                        driver.switch_to.window(emcs_handle)
                    except Exception:
                        emcs_handle = None
                if emcs_handle is None:
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
                driver.quit()  # โหมดอ่านไม่เปิด browser ค้าง (กัน session ชน)
            return

        # ---------- เคลมเดียว: ไปต่อทางเดิม ----------
        if last_data is None:
            driver.quit()
            raise SystemExit(1)
        data = last_data

    if args.read_only:
        banner("จบโหมดอ่านอย่างเดียว (--read-only)")
        driver.quit()
        return

    # ---------------- ส่วนที่ 2: กรอกข้อมูลลง EMCS ----------------
    # นโยบายปัจจุบัน (user 2026-06-11): ใช้กับเคลมแห้งเท่านั้น — เช็คจาก
    # "ประเภทเคลม" ตรงๆ (ไม่พึ่งข้อมูลคู่กรณีอย่างเดียว เพราะ XML อาจโหลด
    # พลาดทำให้ด่านหลวม) + ฟอร์ม EMCS ของเคลมสด layout ต่างจากเคลมแห้ง
    block_reason = data.dry_claim_block_reason()
    if block_reason and not args.allow_fresh:
        banner("หยุด: ไม่ใช่เคลมแห้ง — ไม่กรอก EMCS")
        log_plain(
            f"  {block_reason}\n"
            "  นโยบายปัจจุบันใช้กับเคลมแห้งเท่านั้น (เคลมสดพักไว้)\n"
            "  ข้อมูลที่อ่านได้ถูกเก็บครบใน runs/ แล้ว — "
            "ถ้าต้องการกรอกจริงให้รันใหม่พร้อม --allow-fresh"
        )
        driver.quit()
        return
    if block_reason:
        log_plain(f"\nℹ️ --allow-fresh: {block_reason} — กรอกต่อตามคำสั่ง "
                  "ตรวจละเอียดก่อนส่งงาน (ผู้บาดเจ็บ/ทรัพย์สินต้องกรอกเอง)")

    # ตรวจเลขเซอร์เวย์ซ้ำกับ se-key DB — ซ้ำ = หยุด ไม่กรอก EMCS (กันทำงานซ้ำ)
    dup = _sekey_dup_skip(cfg, data)
    if dup:
        banner("หยุด: เลขเซอร์เวย์นี้ทำไปแล้ว — ไม่กรอก EMCS")
        log_plain(f"  {dup}\n"
                  "  (กันทำซ้ำ — ถ้าต้องการทำซ้ำจริง ลบ/แก้สถานะใน se-key admin ก่อน)")
        driver.quit()
        return

    if not args.yes:
        input("\n>> ตรวจสอบข้อมูลด้านบน แล้วกด Enter เพื่อเริ่มกรอก EMCS "
              "(Ctrl+C เพื่อยกเลิก) << ")

    banner("ส่วนที่ 2: กรอกข้อมูลลง EMCS")
    if not args.data_json:
        driver.switch_to.new_window("tab")  # เปิด tab ใหม่ คง ISURVEY ไว้ดูเทียบได้

    images_folder = None
    if not args.skip_images:
        images_folder = resolve_images_dir(cfg, data.claim_value, for_read=False)

    try:
        esurvey = emcs.run_fill(
            driver, cfg, data,
            images_folder=images_folder,
            loss_type=args.loss_type,
            image_type=args.image_type,
            severity=args.severity,
            force_new=args.force_new,
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
    # A1: เสนอกด "ส่งงาน + แจ้ง ISURVEY" — เฉพาะเคลมแห้ง (live session ปุ่มส่งงานพร้อม)
    if data.dry_claim_block_reason() == "":
        _offer_submit(driver, cfg, data)
    else:
        log("ตรวจบน browser แล้วกด 'ส่งงานใหม่' เองเมื่อพร้อม — เคลมนี้ไม่ใช่เคลมแห้ง "
            "จึงไม่เสนอส่งอัตโนมัติ (browser เปิดค้างไว้ให้)")


if __name__ == "__main__":
    main()
