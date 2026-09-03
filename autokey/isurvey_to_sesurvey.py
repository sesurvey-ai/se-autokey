# -*- coding: utf-8 -*-
"""แปลงเคส ISURVEY (อ่านสดผ่าน API) → โครงเคสของ se-survey

ใช้กับ flow ใหม่: ดึงงานตอน ISURVEY ยังเป็นสถานะ **"รอตรวจข้อมูล"** (ช่างส่งงานแล้ว
หัวหน้ายังไม่ตรวจ) เข้าเว็บ se-survey ให้หัวหน้าตรวจ/กรอกยอด/อนุมัติที่นั่นแทน

ผลลัพธ์เป็น dict รูปแบบเดียวกับ `XmlImportResult` ของ backend แล้วส่งเข้า
`POST /api/integrations/cases/import` ซึ่งเรียก `importFromXml()` ตัวเดียวกับที่หน้า
อัปโหลด XML ใช้ — เส้นทางสร้างเคสจึงมีเส้นเดียว

⚠️ ที่ต้องระวัง (พิสูจน์กับข้อมูลจริง 16/08/69 อย่าแก้ให้ง่ายลงโดยไม่วัดก่อน):

 1. **รหัสจังหวัดสองระบบไม่ใช่ชุดเดียวกัน** — ISURVEY 65 = พิษณุโลก / EMCS 65 = สุพรรณบุรี
    ส่งรหัสดิบข้ามระบบ = เคสไปโผล่ผิดจังหวัดแบบเงียบ ๆ → แปลงผ่าน isurvey_emcs_map เสมอ
 2. **อำเภอต้องจับคู่ด้วยชื่อ ห้ามใช้ลำดับรหัส** — ลำดับอำเภอของสองระบบไม่ตรงกัน
    (ผิด 186/924 · ดูรายละเอียดที่ `district_name`) ส่วนชื่อก็เขียนคนละแบบอีก:
    ISURVEY 'บางบ่อ' / se-survey 'อำเภอบางบ่อ' · ISURVEY 'เมืองสมุทรปราการ' / 'อำเภอเมือง'
 3. **ผลคดี ISURVEY มี 8 ค่า EMCS มี 7** — แมปตามที่ user สรุปไว้ 06/08/69 (ดู CAUSE_RADIO
    ใน emcs.py) · `'ไม่มีคู่กรณี'` **ไม่มีช่องที่ตรงกัน** → ปล่อยว่าง + เตือน ห้ามเดา
 4. **ประเภทรถของรถประกันเก็บเป็น "รหัสตัวอักษร" (A/E/T/V/W/M/O) ส่วนของคู่กรณีเก็บเป็น
    "ชื่อไทย"** — ไม่สมมาตรแต่เป็นสัญญาเดิมของ se-survey (ดู CaseDetail.tsx name="car_type")
 5. **วันที่ต้องเป็น พ.ศ.** รูปแบบ 'dd/mm/yyyy' และวัน+เวลาคู่กันคั่นด้วย '|'
 6. ISURVEY คืน `None` เมื่อค่าว่าง (ไม่ใช่ '') → ต้อง coerce ทุกช่อง
"""
import re

from . import isurvey_emcs_map as emcs_map
from .emcs_names import DISTRICT_NAME, PROVINCE_NAME
from .emcs_insurers import to_emcs_insurer

# ── คำศัพท์ปลายทาง (ต้องตรง dropdown ของเว็บ se-survey เป๊ะ) ──────────────────

#: ผลคดี ISURVEY (masterClaimVerdict) → ป้ายของ se-survey
#: กติกา user 06/08/69 — 'รอคำตัดสิน' กับ 'รถประกันเป็นฝ่ายถูก' คือเรื่องเดียวกับของ EMCS
#: แค่สะกดคนละแบบ · 'ไม่มีคู่กรณี' จงใจไม่ใส่ (ดูหัวไฟล์ข้อ 3)
VERDICT_TO_FAULT = {
    "รถประกันเป็นฝ่ายผิด": "รถประกันเป็นฝ่ายผิด",
    "ประมาทร่วม": "ประมาทร่วม",
    "รถประกันเป็นฝ่ายถูกและผิด": "รถประกันเป็นฝ่ายถูกและผิด",
    "ไปถึงแล้วไม่พบ": "ไปถึงแล้วไม่พบ",
    "ยกเลิกการเคลม": "ยกเลิกการเคลม",
    "รอคำตัดสิน": "รอสรุปผลคดี",
    "รถประกันเป็นฝ่ายถูก": "รถคู่กรณีเป็นฝ่ายผิด",
}

#: ประเภทรถ ISURVEY (masterVehType) → รหัสตัวอักษรของ se-survey (ใช้กับ "รถประกัน")
VEHTYPE_TO_CODE = {
    "1": "A",   # เก๋งเอเซีย  → เก๋งเอเชีย (สะกด ซ/ช ต่างกัน)
    "2": "E",   # เก๋งยุโรป
    "3": "T",   # กระบะ
    "4": "V",   # รถตู้เอเซีย → รถตู้
    "5": "V",   # รถตู้ยุโรป  → รถตู้
    "6": "M",   # รถจักรยานยนต์
    "7": "O",   # รถอื่นๆ
}
#: รหัสตัวอักษร → ชื่อไทย (ใช้กับ "คู่กรณี" ซึ่งเก็บเป็นชื่อ ไม่ใช่รหัส)
CODE_TO_CAR_TYPE = {
    "A": "เก๋งเอเชีย", "E": "เก๋งยุโรป", "T": "กระบะ", "V": "รถตู้",
    "W": "รถบรรทุก", "M": "รถจักรยานยนต์", "O": "รถอื่นๆ",
}

#: ประเภทผู้บาดเจ็บ ISURVEY (masterRelateAccident) → ป้ายของ se-survey
#: ⭐ เส้นทางนี้ได้ครบ 5 แบบ ต่างจากทาง XML ที่ export ยุบเหลือ 3 (DV/PV/ON)
#: ทำให้ "ผู้ขับขี่/ผู้โดยสารรถคู่กรณี" ที่เมื่อก่อนกู้ไม่ได้ กลับมาได้ครบ
PERSON_TYPE_MAP = {
    "2": "ผู้ขับขี่ - รถประกัน", "10": "ผู้ขับขี่ - รถประกัน",
    "3": "ผู้โดยสาร - รถประกัน", "11": "ผู้โดยสาร - รถประกัน",
    "17": "ผู้โดยสาร - รถประกัน",
    "4": "ผู้ขับขี่ - รถคู่กรณี",
    "5": "ผู้โดยสาร - รถคู่กรณี",
    "6": "บุคคลภายนอกรถ", "18": "บุคคลภายนอกรถ", "19": "บุคคลภายนอกรถ",
}

#: ระดับการบาดเจ็บ — ISURVEY เก็บเป็นรหัสตัวอักษรและ **ไม่มีตาราง master ให้เปิด**
#: (ลอง 6 ชื่อ endpoint แล้ว 404 ทั้งหมด) ค่าที่เจอจริง: 'I' = บาดเจ็บ · 'D' = เสียชีวิต
#: 'I' ใช้ตามที่ตาราง WOUNDED_TYPE_TO_EMCS ของบอทใช้อยู่แล้ว (I → 02 ปานกลาง)
#: ⛔ 'D' ไม่แมป — EMCS แยก "เสียชีวิตก่อนรักษา" กับ "หลังรักษา" ซึ่งข้อมูลไม่มีทางบอกได้
#:    เดาแล้วผิดคือเรื่องใหญ่ ปล่อยว่าง + เตือนให้ผู้ตรวจเลือกเอง
WOUND_MAP = {"I": "บาดเจ็บ - ปานกลาง"}

#: ประเภทเคลม ISURVEY (masterClaimMType) → รหัส radio ของ se-survey (F/D/A/C)
#: 04 'เจรจาสินไหม' ไม่มีคู่ในฝั่งเรา → ปล่อยว่าง + เตือน
CLAIM_MTYPE_MAP = {"01": "F", "02": "D", "03": "C"}

#: ความสัมพันธ์ ISURVEY (masterRelation) → ป้ายของ se-survey
#: ที่ไม่มีคู่ตรง ๆ ('คนรู้จัก' / 'อื่นๆ') **ปล่อยว่าง** ให้ผู้ตรวจเลือกเอง — เดาแล้วผิด
#: จะไปโผล่ในสำนวนประกันโดยไม่มีใครทัน
RELATION_MAP = {
    "01": "เจ้าของรถ", "02": "ญาติ", "03": "เพื่อน", "04": "ลูกจ้าง", "06": "ผู้เช่า",
}

GENDER_MAP = {"M": "ชาย", "F": "หญิง", "W": "หญิง"}
TITLES = ("นาย", "นางสาว", "นาง", "ด.ช.", "ด.ญ.", "คุณ")


# ── ตัวช่วยรูปแบบข้อมูล ──────────────────────────────────────────────────────

def _gender_mf(v) -> str:
    """เพศ **ผู้ขับขี่รถประกัน** — se-survey (มือถือ + เว็บ + XML) เก็บเป็น 'M'/'F'
    ต่างจาก `gender` ของคู่กรณี/ผู้บาดเจ็บที่เว็บใช้คำไทย (ดู GENDER_MAP)
    เดิมส่ง 'หญิง' ไป → radio หน้าตรวจไม่ติ๊ก หัวหน้าเห็น "ยังขาด 1 ช่อง" แต่หาไม่เจอ (เคส #221, 03/09/69)"""
    s = _s(v)
    u = s.upper()
    if u == "M" or s == "ชาย":
        return "M"
    if u in ("F", "W") or s == "หญิง":
        return "F"
    return ""


def _s(v) -> str:
    """ISURVEY คืน None เมื่อว่าง — และ 'null' เป็นสตริงในบางช่อง (เจอที่ DD_OTH_COND)

    ขึ้นบรรทัดต้องเป็น '\\n' ล้วน: API คืน CRLF ส่วนทางไฟล์ XML ให้ LF — ถ้าไม่ปรับ
    ข้อความหลายบรรทัดของสองเส้นทางจะไม่มีวันเท่ากัน (เจอตอนเทียบ round-trip 16/08/69)
    """
    if v is None:
        return ""
    s = str(v).replace("\r\n", "\n").replace("\r", "\n")
    # ช่องว่างแปลก ๆ ที่ตาเห็นเหมือนเว้นวรรคแต่ EMCS ไม่รับ — เจอจริง 03/09/69
    # เคส #221 (SEABI-220260800746): ISURVEY ส่งชื่อผู้สำรวจมาเป็น "นาย\xa0มี" (NBSP)
    # → เว็บทาแดงช่อง "ผู้สำรวจภัย" ทั้งที่ดูครบ และ noTyping ของ EMCS จะล้างทั้งช่องทิ้ง
    # แปลงเป็นเว้นวรรคปกติตั้งแต่ตอนดึง ทุกช่อง (ไม่มีข้อมูลไหนต้องเก็บ NBSP ไว้)
    for ch in ("\u00a0", "\u2007", "\u202f", "\u3000"):
        s = s.replace(ch, " ")
    for ch in ("\u200b", "\u200c", "\u200d", "\ufeff"):   # zero-width มองไม่เห็นเลย
        s = s.replace(ch, "")
    s = s.strip()
    return "" if s.lower() in ("null", "none", "-") else s


def be_date(iso) -> str:
    """'2025-11-15' → '15/11/2568' (พ.ศ.) · ปี >= 2400 ถือว่าเป็น พ.ศ. อยู่แล้ว"""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", _s(iso))
    if not m:
        return ""
    y = int(m.group(1))
    return f"{m.group(3)}/{m.group(2)}/{y if y >= 2400 else y + 543}"


def _hhmm(v) -> str:
    """'13:02' / '01:58:07' → 'HH:MM' · 00:00 = ไม่ทราบเวลา (ตรงกับ splitXmlDate)"""
    m = re.match(r"^(\d{1,2}):(\d{2})", _s(v))
    if not m:
        return ""
    hh, mm = m.group(1).zfill(2), m.group(2)
    return "" if (hh, mm) == ("00", "00") else f"{hh}:{mm}"


def be_datetime(iso_date, time_val) -> str:
    """รูปแบบที่ se-survey เก็บวัน+เวลาคู่กัน: 'dd/mm/yyyy|HH:MM' (ไม่มีเวลา = วันอย่างเดียว)"""
    d = be_date(iso_date)
    if not d:
        return ""
    t = _hhmm(time_val)
    return f"{d}|{t}" if t else d


def _num(v):
    """ตัวเลขหรือ None — คอลัมน์ numeric รับสตริงว่างไม่ได้"""
    s = _s(v).replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() else f


def split_name(full: str):
    """'นาย นิพันธ์ เหมือนกรุง' → ('นาย', 'นิพันธ์', 'เหมือนกรุง')

    ISURVEY เก็บชื่อรวมช่องเดียวและมักมีคำนำหน้าติดมา ส่วน se-survey บังคับแยก 3 ช่อง
    ไม่แยก = ดอกจันแดงค้างทั้งที่ชื่อครบ (เจอกับเส้น XML มาแล้ว)
    """
    s = _s(full)
    title = next((t for t in TITLES if s.startswith(t)), "")
    rest = s[len(title):].strip() if title else s
    parts = rest.split()
    return title, (parts[0] if parts else ""), " ".join(parts[1:])


# ── จังหวัด/อำเภอ ────────────────────────────────────────────────────────────

def province_name(isv_code) -> str:
    """รหัสจังหวัด ISURVEY → ชื่อไทยที่ se-survey ใช้ (ผ่านรหัส EMCS เสมอ — หัวไฟล์ข้อ 1)"""
    ep = emcs_map.PROVINCE_TO_EMCS.get(_s(isv_code))
    return PROVINCE_NAME.get(ep, "") if ep else ""


def _bare(name: str, province: str = "") -> str:
    """ตัดคำนำหน้าอำเภอ/เขต ให้เทียบชื่อข้ามระบบได้

    ISURVEY เขียน 'บางบ่อ' / se-survey เขียน 'อำเภอบางบ่อ' · และอำเภอเมืองก็ต่างกันอีก:
    ISURVEY 'เมืองสมุทรปราการ' / se-survey 'อำเภอเมือง' → ตัดชื่อจังหวัดท้าย 'เมือง' ทิ้ง
    """
    n = _s(name)
    for p in ("กิ่งอำเภอ", "อำเภอ", "เขต"):
        if n.startswith(p):
            n = n[len(p):]
            break
    p = _s(province)
    if p and n == "เมือง" + p:
        n = "เมือง"
    return n.strip()


def district_name(api, isv_amphur, isv_province) -> str:
    """อำเภอของ ISURVEY → ชื่ออำเภอที่ se-survey ใช้ ('' = แปลงไม่ได้ ให้คนเลือกเอง)

    ⛔ **จับคู่ด้วยชื่อ ห้ามใช้ `emcs_map.district()`** ที่แปลงด้วยลำดับรหัส —
    สมมติฐาน "ลำดับอำเภอเรียงเหมือนกันทั้งสองระบบ" ของตัวนั้น **ผิด 186 จาก 924 อำเภอ**
    (วัดจริง 16/08/69) เช่น ชัยนาท: ISURVEY 'วัดสิงห์' ไปโผล่เป็น EMCS 'อำเภอหันคา'
    จับคู่ด้วยชื่อได้ 926 ถูก 0 ผิด · ที่เหลือ 78 เป็นชื่อที่ ISURVEY มีอยู่ฝ่ายเดียว
    ('เทศบาลตำบลแหลมฉบัง*', 'สาขาตำบล...') ซึ่งไม่มีใน EMCS จริง ๆ → ว่างถูกแล้ว
    """
    ep = emcs_map.PROVINCE_TO_EMCS.get(_s(isv_province))
    ec = emcs_map.district(_s(isv_amphur), _s(isv_province))
    if not (ep and ec):
        return ""
    # ตาราง DISTRICT_NAME เก็บรหัสแบบตัดศูนย์นำ
    return DISTRICT_NAME.get(ep, {}).get(str(ec).lstrip("0") or "0", "")


def surveyor_code(name_with_code: str) -> str:
    """'SEC423 สมชาติ หอมมาลา' → 'SEC423'

    รับทั้ง SE (กทม.) และ SEC (ต่างจังหวัด) — เส้น XML จับแค่ `SE\\d+` ทำให้งาน
    ต่างจังหวัดไม่เคยถูกมอบหมายอัตโนมัติเลย
    """
    m = re.match(r"^\s*(SE[A-Z]*\d+)", _s(name_with_code), re.I)
    return m.group(1).upper() if m else ""


# ── ตัวแปลงหลัก ──────────────────────────────────────────────────────────────

def build_case(api, case_id: str, listrow: dict | None = None) -> dict:
    """อ่านทุกแท็บของเคส แล้วประกอบเป็นโครง XmlImportResult ของ se-survey

    `api` = ISurveyAPI ที่ login แล้ว · `listrow` = แถวจากรายงาน (ถ้ามี ใช้เสริมบางช่อง)
    """
    warnings: list[str] = []
    row = listrow or {}

    t1 = api.get_tab(case_id, 1) or {}
    t2 = api.get_tab(case_id, 2) or {}
    t3 = api.get_tab(case_id, 3) or {}
    t7 = api.get_tab(case_id, 7) or {}
    t8 = api.get_tab(case_id, 8) or {}

    claim = t1.get("Claim") or {}
    disp = t1.get("Dispatch") or {}
    bill = t1.get("bill") or {}
    acc = t2.get("Accident") or {}
    pol = t7.get("Policy") or {}
    noti = t8.get("Accident") or {}
    drv = t3.get("Driver") or {}

    acc_prov_code = _s(acc.get("acc_provinceID")) or _s(claim.get("acc_provinceID"))
    acc_amph_code = _s(acc.get("acc_amphurID")) or _s(claim.get("acc_amphurID"))

    # ── ผลคดี ──
    verdict = api.master("masterClaimVerdict", "cvdID", "claim_verdict").get(
        _s(claim.get("acc_verdictID")), "")
    acc_fault = VERDICT_TO_FAULT.get(verdict, "")
    if verdict and not acc_fault:
        warnings.append(
            f'ผลคดีจาก ISURVEY คือ "{verdict}" ซึ่งระบบประกันไม่มีตัวเลือกที่ตรงกัน '
            "— ต้องเลือกผลคดีเองบนหน้าเว็บก่อนอนุมัติ")

    # ── ประเภทเคลม → radio F/D/A/C ──
    mtype = _s(claim.get("claim_MtypeID")).zfill(2)
    claim_type = CLAIM_MTYPE_MAP.get(mtype, "")
    if mtype and not claim_type:
        label = api.master("masterClaimMType", "clMTID", "claim_Mtype").get(mtype, mtype)
        warnings.append(f'ประเภทเคลม "{label}" ไม่มีตัวเลือกที่ตรงกันบนเว็บ — เลือกเองก่อนอนุมัติ')

    surv_name = _s(claim.get("surveyor_name"))
    sv_code = surveyor_code(surv_name)

    report: dict = {
        "survey_job_no": _s(claim.get("survey_no")) or _s(row.get("survey_no")),
        "claim_no": _s(claim.get("claim_no")) or _s(row.get("claim_no")),
        # เลขที่รับแจ้ง — EMCS บังคับช่องนี้ และเส้น XML ไม่เคยส่งมาเลย
        "claim_ref_no": _s(claim.get("notify_no")) or _s(noti.get("notify_no")),
        "policy_no": _s(pol.get("policy_no")) or _s((t1.get("Policy") or {}).get("policy_no")),
        "assured_name": _s(pol.get("assured_name")),
        "policy_type": _s(pol.get("policy_TypeID")),
        "policy_start": be_date(pol.get("effective_date") or t3.get("effective_date")),
        "policy_end": be_date(pol.get("expiry_date") or t3.get("expiry_date")),
        "acc_date": be_date(acc.get("acc_date")),
        "acc_time": _hhmm(acc.get("acc_time")),
        "acc_place": _s(acc.get("acc_place"))[:200],
        "acc_province": province_name(acc_prov_code),
        "acc_district": district_name(api, acc_amph_code, acc_prov_code),
        "acc_detail": _s(acc.get("acc_detail")),
        "acc_fault": acc_fault,
        "acc_cause": _s(acc.get("acc_type_desc")) or _s(row.get("acc_type_desc")),
        "claim_type": claim_type,
        "acc_surveyor": surv_name,
        "surveyor_name": surv_name,
        "acc_surveyor_phone": _s(row.get("emp_phone")),
        # ── ไทม์ไลน์ 4 จุด ──
        "acc_customer_report_date": be_datetime(noti.get("notified_date"), noti.get("notified_time")),
        "acc_insurance_notify_date": be_datetime(disp.get("dispatch_date"), disp.get("dispatch_time")),
        "acc_survey_arrive_date": be_datetime(disp.get("arrive_date"), disp.get("arrive_time")),
        "acc_survey_complete_date": be_datetime(disp.get("finish_date"), disp.get("finish_time")),
        # ── ตำรวจ ──
        "acc_police_name": _s(acc.get("police_name")),
        "acc_police_station": _s(acc.get("police_station")),
        "acc_police_date": be_datetime(acc.get("police_rdate"), acc.get("police_rtime")),
        # ── ความเห็น ──
        # 'บันทึกความเห็นหัวหน้างาน' (แท็บ 1) = ความเห็นของผู้ตรวจสอบ (กติกา user 13/08/69)
        "review_comment": _s(t1.get("accident_summary")),
        "surveyor_comment": _s(acc.get("surveyor_comment")),
        "notes": "",
    }

    # ── รถประกัน + ผู้ขับขี่ ──
    veh = _s(t3.get("vehTID"))
    dtitle, dfirst, dlast = split_name(drv.get("drv_name"))
    drv_prov = _s(drv.get("drv_provinceID"))
    report.update({
        "license_plate": _s(t3.get("plate_no")),
        "car_province": province_name(t3.get("plate_provinceID")),
        "car_type": VEHTYPE_TO_CODE.get(veh, ""),
        "car_brand": _s(t3.get("car_brand")),
        "car_model": _s(t3.get("car_model")),
        "car_color": _s(t3.get("car_color")),
        "chassis_no": _s(t3.get("chassis_no")),
        "engine_no": _s(t3.get("engine_no")),
        "estimated_cost": _num(t3.get("D_TOTAL_COST")),
        "driver_title": dtitle or ("คุณ" if dfirst else ""),
        "driver_name": _s(drv.get("drv_name")),
        "driver_first_name": dfirst,
        "driver_last_name": dlast,
        "driver_age": _num(drv.get("age")),
        "driver_gender": _gender_mf(drv.get("drv_gender")),
        "driver_address": _s(drv.get("address")),
        "driver_province": province_name(drv_prov),
        "driver_district": district_name(api, drv.get("drv_amphurID"), drv_prov),
        "driver_phone": _s(drv.get("drv_phone")),
        "driver_id_card": _s(drv.get("IDcard_no")),
        "driver_license_no": _s(drv.get("lic_no")),
        "driver_license_place": province_name(drv.get("lic_issue_provinceID")),
        "driver_license_start": be_date(drv.get("lic_issueDate")),
        "driver_license_end": be_date(drv.get("lic_expireDate")),
        "driver_birthdate": be_date(drv.get("birthdate")),
        "driver_license_type": api.master("masterDrvLicense", "dvlTID", "dvl_type").get(
            _s(drv.get("lic_typeID")), ""),
        "driver_relation": RELATION_MAP.get(_s(drv.get("relation")), ""),
    })
    if not veh:
        warnings.append('ISURVEY ไม่ได้ระบุ "ประเภทรถ" ของรถประกัน — เลือกเองบนหน้าเว็บ')

    # ── ความเสียหายรถประกัน ──
    # ⭐ เส้นทางนี้ได้รายการความเสียหายมาด้วย — ไฟล์ XML ของ ISURVEY ปล่อยว่างเสมอ (6/6 ไฟล์)
    parts = api.get_parts(case_id) or []
    report["insured_damage"] = [{
        "part": _s(p.get("partname")),
        "type": _s(p.get("damage_type_detail")),
        "level": _s(p.get("damaged_level")),
        "cost": _s(p.get("LABOUR_COST")),
    } for p in parts if _s(p.get("partname"))]

    # ── คู่กรณี / ผู้บาดเจ็บ / ทรัพย์สิน ──
    report["opposing_parties"] = _third_parties(api, case_id)
    report["has_opponents"] = bool(report["opposing_parties"])
    report["injured_persons"] = _injuries(api, case_id, warnings)
    report["has_injured"] = bool(report["injured_persons"])
    report["damaged_property"] = _assets(api, case_id)
    report["has_property"] = bool(report["damaged_property"])

    # "คู่กรณีคันที่" — EMCS บังคับเมื่อผลคดี = คู่กรณีผิด · เติมให้ได้เฉพาะตอนมีคันเดียว
    # (มากกว่านั้นห้ามเดา — ไม่มีอะไรบอกว่าคันไหนผิด)
    if acc_fault == "รถคู่กรณีเป็นฝ่ายผิด" and len(report["opposing_parties"]) == 1:
        report["acc_fault_opponent_no"] = "1"

    # ── คำเตือนก่อนสร้างเคส ──
    if not report["claim_no"]:
        warnings.append('งานนี้ยังไม่มี "เลขเคลม" ใน ISURVEY — ระบบประกันใช้เลขนี้เป็นกุญแจ '
                        "ต้องรอต้นทางออกเลขก่อนจึงนำเข้าได้")
    if not report["claim_ref_no"]:
        warnings.append('ไม่มี "เลขที่รับแจ้ง" — EMCS บังคับช่องนี้')
    if sv_code and not surv_name:
        warnings.append("อ่านชื่อผู้สำรวจไม่ได้")
    if not report["insured_damage"]:
        warnings.append('ยังไม่มี "รายการความเสียหาย" ของรถประกัน — กรอกเองบนเว็บก่อนนำเข้า EMCS')

    return {
        "caseFields": {
            "customer_name": report["assured_name"] or "(ไม่ระบุชื่อผู้เอาประกัน)",
            "incident_location": report["acc_place"] or "(ไม่ระบุสถานที่)",
        },
        "report": report,
        "expenses": _bill(bill),
        "surveyorCode": sv_code,
        "warnings": warnings,
        "source": "isurvey_live",
    }


def _third_parties(api, case_id) -> list:
    out = []
    for row in api.list_records(case_id, 4):
        ikey = row.get("ikey")
        if not ikey:
            continue
        r = api.get_record(case_id, 4, ikey) or {}
        d = r.get("driver") or {}
        title, first, last = split_name(d.get("drv_name"))
        veh = _s(r.get("vehTID"))
        car_prov = _s(r.get("plate_provinceID"))
        home_prov = _s(d.get("drv_provinceID"))
        out.append({
            "title": title,
            "first_name": first,
            "last_name": last,
            "gender": GENDER_MAP.get(_s(d.get("drv_gender")), ""),
            "age": _s(d.get("age")),
            "birthdate": be_date(d.get("birthdate")),
            "cid": _s(d.get("IDcard_no")),
            "phone": _s(d.get("drv_phone")),
            "address": _s(d.get("address")),
            # 2 จังหวัดคนละความหมาย: province = ป้ายทะเบียน · home_province = ภูมิลำเนา
            "province": province_name(car_prov),
            "home_province": province_name(home_prov),
            "district": district_name(api, d.get("drv_amphurID"), home_prov),
            "plate": _s(r.get("plate_no")),
            "car_type": CODE_TO_CAR_TYPE.get(VEHTYPE_TO_CODE.get(veh, ""), ""),
            "car_brand": _s(r.get("car_brand")),
            "car_model": _s(r.get("car_model")),
            "car_color": _s(r.get("car_color")),
            "vin": _s(r.get("chassis_no")),
            "owner_name": _s(r.get("owner_name")),
            "owner_address": _s(r.get("owner_address")),
            # แปลงชื่อบริษัทเป็นชื่อที่ EMCS มีจริง **ตั้งแต่ตอนนำเข้า** (ยังมีคนตรวจอยู่)
            # ไม่ใช่ปล่อยให้บอท fuzzy เดาตอนกรอกซึ่งไม่มีใครดู · แปลงไม่ได้ = ปล่อยชื่อเดิม
            # ไปให้หัวหน้าเลือกเองบนเว็บ (ช่องจะขึ้นเตือนว่าเลือกบน EMCS ไม่ได้)
            "insurer": to_emcs_insurer(_s(r.get("oth_insure_company_name"))),
            "policy_no": _s(r.get("oth_policy_no")),
            "claim_no": _s(r.get("oth_accident_no")),
            "policy_type": api.master("masterPolicyType", "poTID", "policy_type").get(
                _s(r.get("oth_insure_typeID")), ""),
            "license_no": _s(d.get("lic_no")),
            "license_type": api.master("masterDrvLicense", "dvlTID", "dvl_type").get(
                _s(d.get("lic_typeID")), ""),
            "relation": RELATION_MAP.get(_s(d.get("relation")), ""),
            "license_place": province_name(d.get("lic_issue_provinceID")),
            "license_start": be_date(d.get("lic_issueDate")),
            "license_end": be_date(d.get("lic_expireDate")),
            # ค่าเสียหายรวม: API ไม่มีช่องยอดรวม ต้องบวกเอง (อะไหล่+ค่าแรง+อื่นๆ)
            # ⚠️ ห้ามใช้ 'total' จาก list_records — คนละยอด (พิสูจน์แล้วเคลม 2026013058298)
            "estimated_cost": _sum_damage(r),
            "damage": [],
            "kfk": False,
        })
    return out


def _sum_damage(rec: dict) -> str:
    total = sum(float(_s(rec.get(k)) or 0) for k in ("D_SPRP", "D_LABOUR", "D_OTH"))
    return f"{total:g}" if total else ""


def _flat(rec: dict) -> dict:
    """ยุบ dict ซ้อนให้เป็นชั้นเดียว — ISURVEY ห่อ record ของ tab-5 ไว้ใต้คีย์ 'patient'
    (tab-4 ห่อคนขับไว้ใต้ 'driver') อ่านชั้นบนตรง ๆ จะได้ค่าว่างหมดทุกช่องแบบไม่มี error"""
    out = {}
    for k, v in (rec or {}).items():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out.setdefault(k2, v2)
        elif not isinstance(v, list):
            out[k] = v
    for k, v in (rec or {}).items():          # คีย์ชั้นบนชนะเสมอ
        if not isinstance(v, (dict, list)):
            out[k] = v
    return out


def _injuries(api, case_id, warnings: list) -> list:
    out = []
    for row in api.list_records(case_id, 5):
        ikey = row.get("ikey")
        if not ikey:
            continue
        r = _flat(api.get_record(case_id, 5, ikey))
        who = _s(r.get("person_name")) or "(ไม่ระบุชื่อ)"
        ptype = PERSON_TYPE_MAP.get(_s(r.get("related_accidentID")), "")
        if _s(r.get("related_accidentID")) and not ptype:
            warnings.append(f'ประเภทผู้บาดเจ็บของ "{who}" แปลงไม่ได้ — เลือกเองบนหน้าเว็บ')
        wound = WOUND_MAP.get(_s(r.get("injury_type")), "")
        if _s(r.get("injury_type")) and not wound:
            warnings.append(
                f'ระดับการบาดเจ็บของ "{who}" ({_s(r.get("injury_detail")) or "ไม่ระบุ"}) '
                "ระบุแทนไม่ได้ — เลือกเองบนหน้าเว็บ")
        out.append({
            "person_type": ptype,
            "name": _s(r.get("person_name")),
            "age": _s(r.get("age")),
            "cid": _s(r.get("IDcard_no")),
            "gender": GENDER_MAP.get(_s(r.get("gender")), ""),
            "occupation": _s(r.get("occupation")),
            # ที่อยู่เต็ม: API แยกตำบล/อำเภอ/จังหวัดคนละคอลัมน์ ส่งแค่ address จะได้ '15/9 ม.9'
            "address": _full_address(api, r),
            "phone": _s(r.get("person_phone")),
            "work_place": _s(r.get("work_place")),
            "income": _s(r.get("salary")),
            "hospital": _s(r.get("hospital")),
            "treat_cost": _s(r.get("medical_cost")),
            "symptom": _s(r.get("injury_detail")),
            "wound_level": wound,
        })
    return out


def _full_address(api, rec: dict) -> str:
    """บ้านเลขที่ + ตำบล + อำเภอ + จังหวัด (รูปแบบเดียวกับที่ isurvey_api ประกอบให้ XML)"""
    parts = [
        _s(rec.get("address")),
        api._tumbon(_s(rec.get("tumbonID")) or _s(rec.get("drv_tumbonID"))),
        api._amphur(_s(rec.get("amphurID")) or _s(rec.get("drv_amphurID"))),
        api._prov(_s(rec.get("provinceID")) or _s(rec.get("drv_provinceID"))),
    ]
    return " ".join(p for p in parts if p)


def _assets(api, case_id) -> list:
    out = []
    for row in api.list_records(case_id, 6):
        ikey = row.get("ikey")
        if not ikey:
            continue
        r = api.get_record(case_id, 6, ikey) or {}
        out.append({
            "item": _s(r.get("prop_name")),
            "detail": _s(r.get("prop_damage_detail")),
            "estimated_cost": _s(r.get("damage_cost")),
            "owner_name": _s(r.get("owner_name")),
            "owner_address": _s(r.get("owner_address")),
            "owner_phone": _s(r.get("owner_phone")),
        })
    return out


def _bill(bill: dict):
    """ยอดเรียกเก็บประกันที่ ISURVEY มีอยู่แล้ว → คอลัมน์ survey_expenses

    ใช้ฝั่ง **INS_*** (ยอดที่ประกันอนุมัติ) ตรงกับที่เส้น XML ใช้ · ว่างทั้งก้อน = None
    (งาน "รอตรวจข้อมูล" ยอดมักยังไม่ถูกกรอก — หัวหน้ากรอกบนเว็บเราแทน)
    """
    out = {
        "service_fee_count": _num(bill.get("INVEST_NUM")),
        "service_fee_price": _num(bill.get("INS_INVEST")),
        "travel_fee_count": _num(bill.get("TRANS_NUM")),
        "travel_fee_price": _num(bill.get("INS_TRANS")),
        "photo_fee_count": _num(bill.get("PHOTO_NUM")),
        "photo_fee_price": _num(bill.get("INS_PHOTO")),
        "phone_fee": _num(bill.get("INS_TEL")),
        "bail_fee": _num(bill.get("INS_INSURE")),
        "claim_fee_price": _num(bill.get("INS_CLAIM")),
        "daily_record_fee": _num(bill.get("INS_DAILY")),
        "other_fee_price": _num(bill.get("INS_OTHER")),
    }
    return out if any((v or 0) > 0 for v in out.values()) else None
