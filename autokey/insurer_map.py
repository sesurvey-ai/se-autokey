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
INSURER_CODE = {
    "ไอโออิกรุงเทพประกันภัย": "1059",   # ยืนยันจากงานจริง (INSURER_MAJOR_ID เดิม)
    "ไอโออิ": "1059",
    # เติมบริษัทอื่นที่นี่เมื่อได้รหัสจากหน้า EMCS เช่น:
    # "ไทยไพบูลย์ประกันภัย": "xxxx",
    # "วิริยะประกันภัย": "xxxx",
}


def _norm(name) -> str:
    """ตัดคำประกอบชื่อบริษัทให้เหลือแก่นเทียบง่าย"""
    s = str(name or "")
    for token in ("บริษัท", "จำกัด", "(มหาชน)", "มหาชน", "(", ")", " ", "\t", " "):
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
