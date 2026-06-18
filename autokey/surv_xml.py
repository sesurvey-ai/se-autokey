"""แปลงไฟล์ SURV_REPORT XML (ปุ่ม 'ดาวน์โหลด XML' ของ ISURVEY) เป็นข้อมูลเคลม

ใช้เป็นแหล่งข้อมูลหลักของ คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน เพราะครบและนิ่งกว่า
การอ่านจากหน้าจอ (Tab 4-6 แสดงผลแบบ async และบางเคลมไม่ยอมแสดง)
"""
import xml.etree.ElementTree as ET
from pathlib import Path

from .browser import log


def _text(el, tag: str) -> str:
    child = el.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _clean_brand(value: str) -> str:
    """CMFG ในไฟล์ขึ้นต้นด้วยรหัสหมวด 'A' เช่น ATOYOTA, AMITSUBISHI — ตัดออก"""
    if len(value) > 2 and value[0] == "A" and value[1].isalpha():
        return value[1:]
    return value


def parse_surv_report(path) -> dict:
    """อ่านไฟล์ SURV_REPORT_*.txt → {'third_parties': [...], 'injuries': [...],
    'assets': [...]} (รถ TYPE 0 คือรถประกัน — ไม่นับเป็นคู่กรณี)"""
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    root = ET.fromstring(raw)

    out = {"third_parties": [], "injuries": [], "assets": [], "insured": {}}

    for car in root.iter("TXN_SURV_CAR"):
        if _text(car, "TYPE") == "0":
            # รถประกันเอง — เก็บข้อมูลที่หน้าจอ ISURVEY ไม่มี (เพศ/คำนำหน้า)
            out["insured"] = {
                "gender": _text(car, "DRI_GENDER"),
                "title_id": _text(car, "DRI_TITLE_ID"),
                "idcard": _text(car, "DRI_CARDID"),
            }
            continue
        out["third_parties"].append({
            "opo_name": _text(car, "OPO_NAME"),
            "opo_address": _text(car, "OPO_ADDRESS"),
            "opo_type": _text(car, "OPO_TYPE"),
            "plate_no": _text(car, "CAR_REGNO"),
            "plate_province_id": _text(car, "CAR_PROVINCE"),
            "car_brand": _clean_brand(_text(car, "CMFG")),
            "car_model": _text(car, "CMODEL"),
            "chassis_no": _text(car, "CHASSISNO"),
            "engine_no": _text(car, "ENGINENO"),
            "veh_type_code": _text(car, "CTYPECODE"),
            "drv_name": _text(car, "DRI_NAME"),
            "gender": _text(car, "DRI_GENDER"),
            "age": _text(car, "DRI_AGE"),
            "birthdate": _text(car, "DRI_BIRTHDAY"),
            "idcard": _text(car, "DRI_CARDID"),
            "phone": _text(car, "DRI_TELNO"),
            "address": _text(car, "DRI_ADDRESS"),
            "district_id": _text(car, "DRI_DISTRICTID"),
            "province_id": _text(car, "DRI_PROVINCEID"),
            "lic_no": _text(car, "DRI_DRVID"),
            "lic_type": _text(car, "DRI_DRVTYPE"),
            "lic_place": _text(car, "DRI_DRVPLACE"),
            "lic_issue_date": _text(car, "DRI_DRVDATE_START"),
            "lic_expire_date": _text(car, "DRI_DRVDATE_END"),
            "insurer": _text(car, "HAVE_INSURANCE"),
            "policy_no": _text(car, "POLICYNO"),
            "claim_no": _text(car, "CLAIMNO"),
            "cost_damage": _text(car, "COST_DAMAGE"),
            "damage_list": _text(car, "DAMAGE_LIST"),
            "repairer": _text(car, "REPAIRER_NAME"),
            "has_kfk": _text(car, "HAS_KFK"),
        })

    for a in root.iter("TXN_SURV_ASSET"):
        out["assets"].append({
            "seq": _text(a, "ASSET_SEQ"),
            "name": _text(a, "ASSET_DESC"),
            "damage_detail": _text(a, "ASSET_DAMAGE"),
            "damage_cause": _text(a, "ASSET_DAMAGE_CAUSE"),
            "damage_cost": _text(a, "COST_DAMAGE"),
            "owner_name": _text(a, "OWNER"),
            "owner_address": _text(a, "ADDRESS"),
            "owner_phone": _text(a, "TEL_NO"),
        })

    # ผู้บาดเจ็บ: tag จริงคือ TXN_SURV_INJ (ยืนยันจากเคลม 2026013144960 — 2 คน)
    # PERSON_TYPE: DV=ผู้ขับขี่รถประกัน, ON=คู่กรณี/บุคคลอื่น
    for inj in root.iter("TXN_SURV_INJ"):
        out["injuries"].append({
            "seq": _text(inj, "INJ_SEQ"),
            "name": _text(inj, "NAME"),
            "age": _text(inj, "AGE"),
            "citizen_id": _text(inj, "CITIZEN_ID"),
            "job": _text(inj, "JOB"),
            "car_regno": _text(inj, "CAR_REGNO"),
            "address": _text(inj, "ADDRESS"),
            "tel_no": _text(inj, "TEL_NO"),
            "hospital": _text(inj, "HOS_NAME"),
            "cost": _text(inj, "COST"),
            "injure": _text(inj, "INJURE"),
            "gender": _text(inj, "GENDER"),
            "person_type": _text(inj, "PERSON_TYPE"),
            "wounded_type": _text(inj, "WOUNDED_TYPE"),
        })

    # ค่าสำรวจ (ฝั่ง "เสนอ" ของบริษัทสำรวจ) — ใช้กรอกตารางราคาหน้า Debit Note
    bill_el = root.find(".//TXN_SURV_BILL")
    if bill_el is not None:
        out["bill"] = {
            "invest": _text(bill_el, "SUR_INVEST"),          # ค่าบริการ
            "invest_num": _text(bill_el, "INVEST_NUM"),
            "trans": _text(bill_el, "SUR_TRANS"),            # ค่าเดินทาง
            "trans_num": _text(bill_el, "TRANS_NUM"),
            "photo": _text(bill_el, "SUR_PHOTO"),            # ค่ารูปถ่าย (รวม)
            "photo_num": _text(bill_el, "PHOTO_NUM"),        # จำนวนรูป
            "tel": _text(bill_el, "SUR_TEL"),                # ค่าโทรศัพท์
            "insure": _text(bill_el, "SUR_INSURE"),
            "claim": _text(bill_el, "SUR_CLAIM"),
            "claim_percent": _text(bill_el, "SUR_PERCENT_CLAIM"),
            "daily": _text(bill_el, "SUR_DAILY"),            # ค่าคัดประจำวัน
            "other": _text(bill_el, "SUR_OTHER"),            # ค่าใช้จ่ายอื่นๆ
            "other_desc": _text(bill_el, "OTHER_DESC"),
        }
    else:
        out["bill"] = {}

    return out


def enrich_claim_from_xml(data, xml_path) -> bool:
    """เติมข้อมูลคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินจาก XML ลง ClaimData
    คืน False เมื่อ parse ไม่ได้ (ผู้เรียกควร fallback ไปอ่านหน้าเว็บ)"""
    try:
        parsed = parse_surv_report(xml_path)
    except Exception as e:
        log(f"   ⚠️ parse XML ไม่ได้: {e}")
        return False

    # เขียนทับจาก XML เฉพาะตอน "ยังว่าง" — กัน flow --data-json (โหลด JSON ที่
    # enrich Tab 4 มาแล้ว เช่น veh_type/insure_type/policy_no/damages ของคู่กรณี)
    # โดน XML (ซึ่งมีแค่ basics) ลบทิ้ง. ใน read flow ปกติ field พวกนี้ยังว่าง→เซ็ตจาก XML
    if not data.third_parties:
        data.third_parties = parsed["third_parties"]
    if not data.injuries:
        data.injuries = parsed["injuries"]
    if not data.assets:
        data.assets = parsed["assets"]
    data.xml_file = str(xml_path)

    # เพศผู้ขับขี่รถประกัน — EMCS บังคับกรอก แต่หน้าจอ ISURVEY ไม่มี
    insured = parsed.get("insured", {})
    if not data.driver_gender.strip() and insured.get("gender", "").strip():
        data.driver_gender = insured["gender"].strip()

    # ค่าสำรวจ: แหล่งหลักคือชุด INS_* จากหน้าจอ ISURVEY (อ่านใน read_tab1)
    # — XML (ชุด SUR_ ฝั่งเสนอเดิม) เป็นแค่ fallback เมื่อไม่มีข้อมูลหน้าจอ
    if not data.bill:
        data.bill = parsed.get("bill", {})
        if data.bill:
            log("   ⚠️ ใช้ค่าสำรวจจาก XML (ไม่มีข้อมูลหน้าจอ) — "
                "ยอดอาจไม่ตรงชุดอนุมัติ ตรวจก่อนบันทึก")

    log(f"   ✓ ข้อมูลจาก XML: คู่กรณี {len(data.third_parties)} / "
        f"ผู้บาดเจ็บ {len(data.injuries)} / ทรัพย์สิน {len(data.assets)}"
        + (f" / เพศผู้ขับขี่ {data.driver_gender}" if data.driver_gender else ""))
    return True
