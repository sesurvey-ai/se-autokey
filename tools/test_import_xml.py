"""ทดสอบโหมดนำเข้า XML กับหน้า EMCS จริง — เรียก emcs.run_import ตรง
(ข้ามด่าน se-key/นโยบายของ main.py ที่บล็อกเลขซ้ำ เพื่อ verify กลไก import จริง)

สร้าง draft ใหม่จาก SURV_REPORT XML → อุดช่องว่าง/แก้ฟอร์มหลัก → คู่กรณี →
ความเสียหาย (free-text 20 ช่อง) → ค่าใช้จ่าย; **ไม่กดส่งงาน** (เปิด browser ค้างให้ตรวจ)

  python tools\\test_import_xml.py --data-json runs\\2026013144715.json
  python tools\\test_import_xml.py --data-json runs\\X.json --with-images --save-price
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autokey import emcs  # noqa: E402
from autokey.browser import log, make_driver, save_debug_snapshot  # noqa: E402
from autokey.claim_data import ClaimData  # noqa: E402
from autokey.config import load_config  # noqa: E402
from autokey.surv_xml import enrich_claim_from_xml  # noqa: E402


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--data-json", required=True, type=Path)
    ap.add_argument("--with-images", action="store_true",
                    help="อัปรูปด้วย (default ข้าม เพื่อโฟกัส flow import/ฟอร์ม)")
    ap.add_argument("--save-price", action="store_true",
                    help="กดบันทึกราคาด้วย (default ไม่กด)")
    ap.add_argument("--loss-type", default="auto",
                    help="ลักษณะความเสียหาย (ddlLoss_ID) — ใส่ค่าเพื่อข้าม pause "
                         "ตอนทดสอบ headless (เคลมสด auto = ''→รอคนเลือก)")
    args = ap.parse_args()

    cfg = load_config()
    data = ClaimData.load(args.data_json)
    if data.xml_file and Path(data.xml_file).exists():
        enrich_claim_from_xml(data, data.xml_file)
    if not (data.xml_file and Path(data.xml_file).exists()):
        raise SystemExit(f"ไม่มีไฟล์ XML: {data.xml_file!r} — โหมด import ต้องมี XML")

    images_folder = None
    if args.with_images:
        images_folder = cfg.download_dir / (data.claim_value or "")
        if not images_folder.exists():
            images_folder = cfg.download_dir

    log(f"ทดสอบ import เคลม {data.claim_value} / {data.invoice_value} "
        f"(force_new, {'อัปรูป' if args.with_images else 'ข้ามรูป'}, "
        f"{'เซฟราคา' if args.save_price else 'ไม่เซฟราคา'})")
    driver = make_driver(detach=True)
    try:
        es = emcs.run_import(driver, cfg, data, images_folder=images_folder,
                             loss_type=args.loss_type, severity="เบา",
                             force_new=True, save_price=args.save_price)
        save_debug_snapshot(driver, cfg.runs_dir / "logs", tag="test_import")
        log(f"✅ เสร็จ — e-Survey {es or '(ไม่ทราบเลข)'} (draft, ไม่ส่งงาน) — "
            "ตรวจบน Chrome ที่เปิดค้าง")
    except Exception:
        save_debug_snapshot(driver, cfg.runs_dir / "logs", tag="error_test_import")
        raise


if __name__ == "__main__":
    main()
