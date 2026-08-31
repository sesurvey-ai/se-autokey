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


# ── ประเภทกรมธรรม์: รหัสใน XML → คำอ่านที่จะกรอกลง EMCS ────────────────────────
# XML เก็บเป็นรหัส (01/02/03/04/05/10/52/53 ตามตาราง masterPolicyType ของ ISURVEY)
# และช่อง txtPolicy_Type บน EMCS ทุกวันนี้เป็นรหัส **เพราะ XML ยัดรหัสเข้าไป**
# ไม่ใช่เพราะระบบต้องการรหัส — ช่องนี้เป็นช่องข้อความอิสระ 150 ตัวอักษร ไม่มีตัวเลือก
# และ EMCS โชว์ค่าดิบโดยไม่มีคำแปลให้ คนเปิดดูจึงอ่านไม่ออก
# → user ตัดสิน 30/08/69: ให้กรอก "ค่าจริงของฟิลด์" ลงไป ไม่ใช่รหัส
#
# ⚠️ 52/53 ไม่เขียน "ประเภท 2+" เพราะ **EMCS กลืนเครื่องหมายบวกตอนบันทึก**
#    ('ประเภท 2+' → 'ประเภท 2' = ผิดความคุ้มครอง) · ถ้าปล่อยให้ _emcs_safe แปลงเองจะได้
#    'ประเภท 2บวก' ติดกันอ่านยาก → เขียนเป็นคำเต็มมีเว้นวรรคตั้งแต่ต้น
#    ⛔ ใช้คำว่า 'บวก' (user เคาะ 30/08/69) — ต้องตรงกับ PLUS_WORD ใน browser.py
POLICY_TYPE_NAME = {
    "01": "ประเภท 1",
    "02": "ประเภท 2",
    "03": "ประเภท 3",
    "04": "พรบ.",
    "05": "ประเภท 5",
    "10": "ไม่พบความคุ้มครอง",
    "52": "ประเภท 2 บวก",
    "53": "ประเภท 3 บวก",
}


def policy_type_name(value) -> str:
    """รหัสประเภทกรมธรรม์ → คำอ่าน · ไม่ใช่รหัสที่รู้จัก = คืนค่าเดิม (ห้ามเดา)

    เติมศูนย์นำหน้าให้ก่อนเทียบ — เจอของจริงบน EMCS ที่คนคีย์ '1' แทน '01'
    ค่าที่เป็นคำอยู่แล้ว (เส้น ISURVEY อ่านจาก tab-7) ผ่านตรงนี้แล้วไม่เปลี่ยน
    """
    s = str(value or "").strip()
    if not s:
        return ""
    return POLICY_TYPE_NAME.get(s.zfill(2) if s.isdigit() else s, s)


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
                # ⚠️ ประเภทกรมธรรม์ของ "รถประกัน" ไม่ได้อยู่ในบล็อกรถ (ตรงนั้นว่างเสมอ)
                #    แต่อยู่ระดับรายงาน — เติมทีหลังจาก TXN_SURV_REPORT ข้างล่าง
                "policy_type": "",
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
            # ความสัมพันธ์ผู้ขับขี่กับเจ้าของรถ — XML ให้มาเป็น "รหัส EMCS" ตรง ๆ
            # (19 = ญาติ, 13 = เจ้าของรถ) ต่างจากฝั่ง API ที่ให้เป็นชื่อ
            # เดิมไม่ได้อ่านเลย ช่อง ddlDri_Relation_ID จึงว่างทุกเคสที่มาทางนี้
            "relation_id": _text(car, "DRI_RELATION"),
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
            # ประเภทกรมธรรม์ = **รหัส** ที่ระบบประกันใช้ (01/02/03/52) ไม่ใช่คำอ่าน —
            # ฝั่ง se-survey แปลงให้แล้วด้วย policyTypeCode() ตอน export XML
            # (ยืนยันจากเคสจริง 000098: <POLICY_TYPE>01</POLICY_TYPE> และช่องบนหน้าเป็น '01')
            "insure_type": policy_type_name(_text(car, "POLICY_TYPE")),
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
            # tag EMCS canonical ยืนยันจาก gold reference (2026-07-23) — dict key เดิม (bot ไม่แก้)
            "work_place": _text(inj, "WORK_PLACE"),
            "position": _text(inj, "POSITION"),
            "income": _text(inj, "INCOME"),
            "treat_from": _text(inj, "FROM_DATE"),     # ISO ค.ศ. → iso_to_thai_date ในบอท
            "treat_to": _text(inj, "TO_DATE"),
            "relation": _text(inj, "DRI_RELATION_ID"),  # รหัส (ddlDri_Relation_ID value)
        })

    # ประเภทกรมธรรม์ของรถประกัน — อยู่ระดับรายงาน (TXN_SURV_REPORT/POLICY_TYPE)
    # ไฟล์จริงห่อด้วย <INSERT_SURV_REPORT_XML> → TXN_SURV_REPORT เป็นลูก
    # แต่บางไฟล์/บางเทสส่ง TXN_SURV_REPORT มาเป็น root เลย — find(".//") หา "ตัวเอง" ไม่เจอ
    rep_el = root if root.tag == "TXN_SURV_REPORT" else root.find(".//TXN_SURV_REPORT")
    if rep_el is not None and out.get("insured") is not None:
        out["insured"]["policy_type"] = policy_type_name(_text(rep_el, "POLICY_TYPE"))

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


def enrich_claim_from_xml(data, xml_path, *, xml_bill_is_approved: bool = False) -> bool:
    """เติมข้อมูลคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินจาก XML ลง ClaimData
    คืน False เมื่อ parse ไม่ได้ (ผู้เรียกควร fallback ไปอ่านหน้าเว็บ)

    xml_bill_is_approved: ยอดในไฟล์ XML **คือชุดที่หัวหน้าอนุมัติแล้ว** หรือไม่
      · เส้น se-survey = True  — XML สร้างจาก `survey_expenses` ที่หัวหน้ากรอกบนเว็บเรา
        แล้วกดอนุมัติ ไม่มี "หน้าจอ" อื่นที่ถูกต้องกว่านี้
      · เส้น ISURVEY  = False — ยอดหลักคือ INS_* ที่อ่านจากหน้าจอ ISURVEY
        ชุด SUR_ ในไฟล์เป็นยอด "เสนอ" เดิม อาจไม่ตรงกับที่อนุมัติจริง จึงต้องเตือน
    """
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

    # ประเภทกรมธรรม์รถประกัน — เส้น se-survey ไม่เคยเซ็ตช่องนี้เลย (ว่างเสมอ → set_text ข้าม)
    # ⚠️ "เฉพาะตอนยังว่าง" เหมือน driver_gender — เส้น ISURVEY อ่านจาก tab-7 มาแล้ว
    #    (ให้เป็นคำอ่าน เช่น 'ประเภท 1') ห้าม XML ไปทับของเดิม
    if not str(getattr(data, "insure_type", "")).strip()             and insured.get("policy_type", "").strip():
        data.insure_type = insured["policy_type"].strip()

    # ค่าสำรวจ — ความหมายของ "ยอดใน XML" ต่างกันตามต้นทาง (ดู xml_bill_is_approved)
    # ⛔ ห้ามเตือนแบบเหมารวม: เส้น se-survey ยอดใน XML คือชุดที่อนุมัติแล้ว การขึ้น
    #    "ยอดอาจไม่ตรงชุดอนุมัติ" ทุกรอบทั้งที่ตรงเป๊ะ = คำเตือนที่คนอ่านจนชินแล้วเลิกอ่าน
    #    (อาการเดียวกับตัวตรวจกลับที่เคยร้องผิดทุกรอบ — แก้ไปแล้ว 29/08/69)
    if not data.bill:
        data.bill = parsed.get("bill", {})
        if data.bill and xml_bill_is_approved:
            log("   ✓ ค่าสำรวจจากชุดที่หัวหน้าอนุมัติบนเว็บ se-survey")
        elif data.bill:
            log("   ⚠️ ใช้ค่าสำรวจจาก XML (ไม่มีข้อมูลหน้าจอ) — "
                "ยอดอาจไม่ตรงชุดอนุมัติ ตรวจก่อนบันทึก")

    log(f"   ✓ ข้อมูลจาก XML: คู่กรณี {len(data.third_parties)} / "
        f"ผู้บาดเจ็บ {len(data.injuries)} / ทรัพย์สิน {len(data.assets)}"
        + (f" / เพศผู้ขับขี่ {data.driver_gender}" if data.driver_gender else ""))
    return True
