"""จำลอง "บอทจะหยุดตรงไหน" จากข้อมูลเคลมจริง — โดย **ไม่แตะ EMCS เลย**

ทำไมต้องมี
----------
`--read-only` อ่าน ISURVEY อย่างเดียว เลยไม่เห็นว่าตอนกรอก EMCS จะติดตรงไหน
ส่วนการรันจริงก็สร้าง draft บนระบบบริษัทประกันซึ่งลบไม่ได้ (ยกเลิกได้อย่างเดียว)
สคริปต์นี้อยู่ตรงกลาง: อ่าน ISURVEY จริง แล้วเอา **ฟังก์ชันตัดสินใจตัวเดียวกับที่
บอทใช้** มารันกับ **ตัวเลือกจริงของ EMCS** ที่ดัมพ์ไว้ใน runs/emcs_spec.json

ครอบคลุมจุดหยุดที่ "ตัดสินจากข้อมูลได้" — จุดที่ EMCS เป็นคนตัดสินตอนกดบันทึก
(validation ฟ้อง / ตัวเลือกไม่โหลด) ทำนายล่วงหน้าไม่ได้ บอกไว้ท้ายรายงาน

ใช้:  runtime\\python.exe tools\\dry_stops.py <เลขเคลม> [เลขเซอร์เวย์]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rapidfuzz import fuzz, process

from autokey.browser import FUZZY_MIN_SCORE, _is_placeholder_option
from autokey.car_brand import BRAND_MIN_SCORE, normalize_brand
from autokey.config import load_config
from autokey.emcs import (CAUSE_RADIO, EMCS_TITLE, TITLE_MIN_SCORE,
                          _derive_insured_title, resolve_gender,
                          resolve_loss_type)
from autokey.isurvey_api import read_claim_api

SPEC = Path(__file__).resolve().parents[1] / "runs" / "emcs_spec.json"


def spec_options(dropdown_id: str) -> list:
    """ตัวเลือกจริงของ dropdown จากสเปกที่ดัมพ์ไว้ (ไม่รวม placeholder)"""
    try:
        spec = json.loads(SPEC.read_text(encoding="utf-8"))
    except Exception:
        return []
    for f in spec:
        opts = (f.get("dropdowns") or {}).get(dropdown_id)
        if opts:
            return [o["label"] for o in opts
                    if o.get("label") and not _is_placeholder_option(o["label"])]
    return []


def check_select(label, dropdown_id, value, min_score=FUZZY_MIN_SCORE,
                 required=True, cascade=False):
    """จำลอง fuzzy_select — คืน (ok, ข้อความอธิบาย)

    cascade=True: ตัวเลือกของ dropdown นี้ถูก "กรองตามช่องก่อนหน้า" ตอนรันจริง
      (อำเภอ←จังหวัด, ยี่ห้อ←ประเภทรถ) แต่ในสเปกเป็น snapshot ของชุดเดียว —
      ตรงเป๊ะยังเชื่อได้ ส่วน fuzzy เทียบข้ามชุดไม่มีความหมาย → คืน None
    """
    value = str(value or "").strip()
    if not value:
        return (not required,
                "ต้นทางว่าง" + (" (ช่องบังคับ → หยุด)" if required else " → ข้าม"))
    opts = spec_options(dropdown_id)
    if not opts:
        return None, f"ไม่มีสเปกของ {dropdown_id} — ทำนายไม่ได้"
    if value in opts:
        return True, f"'{value}' ตรงเป๊ะ" + (" (ลิสต์ผูกช่องก่อนหน้า)" if cascade else "")
    best, score, _ = process.extractOne(value, opts, scorer=fuzz.WRatio)
    if cascade:
        return None, (f"'{value}' ไม่มีในสเปก snapshot — ลิสต์จริงผูกกับช่องก่อนหน้า "
                      "ทำนายไม่ได้")
    if score >= min_score:
        return True, f"'{value}' → '{best}' (score {score:.0f})"
    return False, f"'{value}' ใกล้สุด '{best}' แค่ {score:.0f} < {min_score} → หยุด"


def check_verdict(acc_result):
    res = " ".join(str(acc_result or "").split())
    if not res:
        return None, "ไม่มีผลคดี — บอทข้าม (ไม่หยุด แต่ช่องจะว่าง)"
    if res in CAUSE_RADIO:
        return True, f"'{res}' ตรงเป๊ะ"
    best, score, _ = process.extractOne(res, list(CAUSE_RADIO), scorer=fuzz.WRatio)
    tie = [k for k in CAUSE_RADIO
           if fuzz.WRatio(res, k) >= score - 1 and CAUSE_RADIO[k] != CAUSE_RADIO[best]]
    if tie:
        return False, f"'{res}' คลุมเครือ (เสมอกับ {tie}) → หยุด"
    return True, f"'{res}' → '{best}' (score {score:.0f})"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    claim, invoice = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")

    cfg = load_config()
    d = read_claim_api(cfg, claim, invoice, expect_claim=claim)

    rows = []          # (ชื่อจุด, ok, รายละเอียด)

    # ---- รถประกัน ----
    rows.append(("ประเภทรถ (ddlCType)",
                 *check_select("ประเภทรถ", "ddlCType", d.prb_car_type)))
    rows.append(("ยี่ห้อรถประกัน (ddlCMFG)",
                 *check_select("ยี่ห้อ", "ddlCMFG", normalize_brand(d.car_brand),
                               min_score=BRAND_MIN_SCORE, cascade=True)))
    rows.append(("จังหวัดทะเบียน (ddlCar_Province)",
                 *check_select("จังหวัดรถ", "ddlCar_Province", d.plate_province)))

    # ---- อุบัติเหตุ ----
    rows.append(("ลักษณะการเกิดเหตุ (ddlClm_Cause)",
                 *check_select("ลักษณะการเกิดเหตุ", "ddlClm_Cause", d.acc_type_desc)))
    rows.append(("จังหวัดเกิดเหตุ (ddlAcc_ProvinceID)",
                 *check_select("จังหวัดเกิดเหตุ", "ddlAcc_ProvinceID", d.acc_province)))
    rows.append(("อำเภอเกิดเหตุ (ddlAcc_DistrictID)",
                 *check_select("อำเภอเกิดเหตุ", "ddlAcc_DistrictID", d.acc_amphur,
                               cascade=True)))
    _loss = resolve_loss_type(d, "auto")
    rows.append(("ลักษณะความเสียหาย (ddlLoss_ID)",
                 *check_select("ลักษณะความเสียหาย", "ddlLoss_ID", _loss)))
    rows.append(("ผลคดี (radio)", *check_verdict(d.acc_result)))

    # ---- ผู้ขับขี่ ----
    _g = resolve_gender(d.driver_gender, f"{d.driver_name} {d.driver_surname}")
    rows.append(("เพศผู้ขับขี่ (radio)", bool(_g),
                 f"{'ชาย' if _g == 'M' else 'หญิง' if _g == 'W' else 'ไม่รู้ → หยุด'}"))
    _title, _src = _derive_insured_title(d)
    rows.append(("คำนำหน้าผู้ขับขี่ (ddlDri_Title_ID)",
                 *(check_select("คำนำหน้า", "ddlDri_Title_ID",
                                EMCS_TITLE.get(_title, _title),
                                min_score=TITLE_MIN_SCORE)
                   if _title else (False, "หาไม่ได้ → หยุด"))))
    if _title:
        rows[-1] = (rows[-1][0], rows[-1][1], f"{rows[-1][2]}  [{_src}]")

    # ---- คู่กรณี (ยี่ห้อคือจุดที่พลาดบ่อยสุด เพราะลิสต์ถูกกรองตามประเภทรถ) ----
    for i, tp in enumerate(d.third_parties or [], 1):
        rows.append((f"คู่กรณี #{i} ประเภทรถ",
                     *check_select("ประเภทรถคู่กรณี", "ddlCType",
                                   tp.get("veh_type"))))
        rows.append((f"คู่กรณี #{i} ยี่ห้อ",
                     *check_select("ยี่ห้อคู่กรณี", "ddlCMFG",
                                   normalize_brand(tp.get("car_brand")),
                                   min_score=BRAND_MIN_SCORE, cascade=True)))

    # ---- รายงาน ----
    print(f"\n{'='*78}")
    print(f"จำลองจุดหยุด — เคลม {d.claim_value} / {d.invoice_value}")
    print(f"ประเภทเคลม {d.claim_type_name()} · คู่กรณี {len(d.third_parties or [])} · "
          f"ผู้บาดเจ็บ {len(d.injuries or [])} · ทรัพย์สิน {len(d.assets or [])}")
    print(f"{'='*78}")
    stops = 0
    for name, ok, detail in rows:
        icon = "✅" if ok else ("⛔" if ok is False else "❔")
        if ok is False:
            stops += 1
        print(f" {icon} {name:<38} {detail}")
    print(f"{'-'*78}")
    print(f" จุดที่จะหยุด (ทำนายได้): {stops}")
    print(" ทำนายไม่ได้: EMCS ฟ้อง validation ตอนกดบันทึก / ตัวเลือกยี่ห้อไม่โหลด "
          "(cascade race) / ช่องบังคับก่อนเข้าหน้าค่าใช้จ่าย")
    print(" *** ไม่ได้แตะ EMCS เลย — อ่าน ISURVEY อย่างเดียว ***\n")


if __name__ == "__main__":
    main()
