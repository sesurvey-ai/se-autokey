"""แม็พชื่อบริษัทประกัน (ข้อความไทยจากระบบ se-survey) → รหัส ddlInsurerNameMajor ของ EMCS

หน้า "นำเข้าข้อมูลแบบ XML" (frmFileImportXML.aspx) บังคับเลือกบริษัทก่อนอัปโหลด XML
— เดิม se-autokey hardcode ไอโออิ (1059) เพราะรับเฉพาะงาน ISURVEY ของไอโออิ
งานจาก se-survey มีหลายบริษัท จึงต้องแม็พชื่อ → รหัส

⚠️ ตาราง INSURER_CODE ยังไม่ครบ: รหัสของแต่ละบริษัทต้องดูจาก dropdown ddlInsurerNameMajor
ของหน้า EMCS จริง (บันทึกหน้าเป็น HTML แล้วสกัด value ของแต่ละ option) — เติมเพิ่มที่นี่
resolve_insurer_code คืน None เมื่อไม่รู้จัก → ฝั่งเรียกต้อง "หยุด+ฟ้อง" ไม่ import มั่ว
(กัน import เข้าบริษัทผิด)
"""

# ชื่อบริษัท (normalize แล้ว) → รหัส ddlInsurerNameMajor
# key ผ่าน _norm() แล้ว (ตัด "บริษัท"/"จำกัด"/"(มหาชน)"/ช่องว่าง) — เทียบแบบยืดหยุ่น
# รหัสอ่านจาก dropdown หน้า frmFileImportXML.aspx จริง 2026-07-17 (บัญชี SE มี 4 บริษัท):
#   1059=ไอโออิกรุงเทพประกันภัย · 1232=ฟอลคอนประกันภัย · 2424=เจมาร์ทประกันภัย · 2429=ไทยไพบูลย์ประกันภัย
INSURER_CODE = {
    "ไอโออิกรุงเทพประกันภัย": "1059",   # ยืนยันจากงานจริง (INSURER_MAJOR_ID เดิม)
    "ไอโออิ": "1059",
    "ฟอลคอนประกันภัย": "1232",
    "เจมาร์ทประกันภัย": "2424",
    "ไทยไพบูลย์ประกันภัย": "2429",
    # เติมครบทั้ง dropdown 2026-07-25 (สกัดจากหน้า EMCS ที่เซฟไว้ — ddlInsurerNameMajor
    # มี 7 บริษัท) เดิมขาด 3 ตัวนี้ → resolve_insurer_code คืน None = นำเข้าล้มทั้งเคส
    "ประกันภัยทดสอบ": "1",
    "เดอะวันประกันภัย": "4",
    "อลิอันซ์อยุธยาประกันภัย": "1723",
}


def _norm(name) -> str:
    """ตัดคำประกอบชื่อบริษัทให้เหลือแก่นเทียบง่าย"""
    s = str(name or "")
    for token in ("บริษัท", "จำกัด", "จํากัด", "(มหาชน)", "มหาชน", "(", ")", " ", "\t", " "):
        s = s.replace(token, "")
    return s.strip()


def resolve_insurer_code(company_name):
    """คืนรหัส ddlInsurerNameMajor ของ EMCS จากชื่อบริษัท (ไทย) — None ถ้าไม่รู้จัก"""
    if not company_name:
        return None
    n = _norm(company_name)
    if n in INSURER_CODE:
        return INSURER_CODE[n]
    # เทียบแบบหลวม: ชื่อในตารางเป็นสับสตริงของชื่อที่ส่งมา (หรือกลับกัน)
    for key, code in INSURER_CODE.items():
        if key and (key in n or n in key):
            return code
    return None


# ── เลขเซอร์เวย์ → บริษัท ────────────────────────────────────────────────────
# ISURVEY **ไม่ส่งชื่อบริษัทของรถประกันมาเลย** (ตรวจ payload จริง 15/08/69 — 104 คีย์
# ไม่มีสักคีย์ที่บอกบริษัทผู้รับประกัน) สิ่งเดียวที่บอกได้คือ prefix ของเลขเซอร์เวย์
#
# ตรงกับ INSURER_BY_JOB_PREFIX ฝั่งเว็บ se-survey — แก้ที่ไหนต้องแก้อีกที่ด้วย
# (user ยืนยัน 13/08/69: SETP = ไทยไพบูลย์ · SEABI = ไอโออิ)
INSURER_BY_JOB_PREFIX = {
    "SETP": "ไทยไพบูลย์ประกันภัย",
    "SEABI": "ไอโออิกรุงเทพประกันภัย",
}


def resolve_insurer_code_by_job_no(survey_no):
    """คืนรหัสบริษัทจาก prefix ของเลขเซอร์เวย์ (SETP-69050083 → 2429) — None ถ้าไม่รู้จัก

    ⛔ คืน None แล้วผู้เรียกต้อง "หยุด+ฟ้อง" ห้าม fallback เป็นบริษัทใดบริษัทหนึ่ง —
       เดาผิดคือยื่นสำนวนเข้าบริษัทที่ไม่ใช่เจ้าของงาน ซึ่งถอยไม่ได้
    """
    prefix = str(survey_no or "").split("-")[0].strip().upper()
    name = INSURER_BY_JOB_PREFIX.get(prefix)
    return resolve_insurer_code(name) if name else None
