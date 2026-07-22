"""Contract test — consumer (se-autokey/surv_xml.py) ↔ producer (se-survey xmlExport.service.ts)

ล็อกว่า parse_surv_report() อ่าน XML ที่ se-survey สร้าง (generateSurveyXml) ได้ครบทุกบล็อก:
รถประกัน(TYPE=0) · คู่กรณี(TYPE=20) · ผู้บาดเจ็บ(TXN_SURV_INJ) · ทรัพย์สิน(TXN_SURV_ASSET)
ถ้าฝั่งใดแก้ชื่อ tag/format สัญญานี้จะพัง → เทสฟ้อง (แทน import พังเงียบ)

⚠️ ต้อง sync กับ se-survey/backend/tests/xmlExport.contract.test.ts (ใช้ค่าชุดเดียวกัน)
รัน: runtime\\python.exe test_surv_contract.py
"""
import sys
import tempfile
import pathlib

sys.stdout.reconfigure(encoding="utf-8")

from autokey import surv_xml  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


# golden XML — โครง/ชื่อ tag/ค่า ตรงกับที่ generateSurveyXml() ของ se-survey ปล่อยออกมา
# (ดูฝั่ง producer: se-survey/backend/src/services/xmlExport.service.ts)
GOLDEN = """<?xml version="1.0" encoding="UTF-8"?>
<INSERT_SURV_REPORT_XML><TXN_SURV_REPORT><SURV_JOBNO>SETP-CONTRACT-001</SURV_JOBNO><REF_CLAIM_NO>CLAIM-CONTRACT-001</REF_CLAIM_NO><ACC_CLAIMREF_NO>REF-CONTRACT-001</ACC_CLAIMREF_NO><ACC_POLICY_NO>POL-CONTRACT-001</ACC_POLICY_NO><ASSURED_NAME>บริษัท ทดสอบสัญญา จำกัด</ASSURED_NAME><ACC_DETAIL>รายละเอียดทดสอบสัญญา</ACC_DETAIL></TXN_SURV_REPORT><TXN_SURV_CAR><TYPE>0</TYPE><OPO_TYPE>รถประกัน</OPO_TYPE><CAR_REGNO>ษข9066</CAR_REGNO><CMFG>ATOYOTA</CMFG><CHASSISNO>CHASSIS123</CHASSISNO><DRI_NAME>ผู้ขับ ทดสอบ</DRI_NAME><DRI_TITLE_ID>1</DRI_TITLE_ID><DRI_CARDID>1234567890123</DRI_CARDID><DRI_GENDER>ชาย</DRI_GENDER><COST_DAMAGE>3500</COST_DAMAGE></TXN_SURV_CAR><TXN_SURV_CAR><TYPE>20</TYPE><OPO_NAME>เจ้าของคู่กรณี</OPO_NAME><OPO_TYPE>รถคู่กรณี</OPO_TYPE><CAR_REGNO>7ชน807</CAR_REGNO><CAR_PROVINCE>2</CAR_PROVINCE><CMFG>AHONDA</CMFG><CMODEL>CITY</CMODEL><CHASSISNO>OPPVIN1</CHASSISNO><CTYPECODE>A</CTYPECODE><DRI_NAME>คู่กรณี ทดสอบ</DRI_NAME><DRI_TELNO>0888888888</DRI_TELNO><DRI_CARDID>9876543210987</DRI_CARDID><HAVE_INSURANCE>1</HAVE_INSURANCE><POLICYNO>OPPPOL</POLICYNO><CLAIMNO>OPPCLAIM</CLAIMNO><COST_DAMAGE>0</COST_DAMAGE></TXN_SURV_CAR><TXN_SURV_ASSET><ASSET_SEQ>1</ASSET_SEQ><ASSET_DESC>รั้วบ้าน</ASSET_DESC><ASSET_DAMAGE>รั้วหัก</ASSET_DAMAGE><ASSET_DAMAGE_CAUSE>ถูกชน</ASSET_DAMAGE_CAUSE><COST_DAMAGE>2000</COST_DAMAGE><OWNER>เจ้าของทรัพย์</OWNER><ADDRESS>ที่อยู่ทรัพย์</ADDRESS><TEL_NO>0666666666</TEL_NO></TXN_SURV_ASSET><TXN_SURV_INJ><INJ_SEQ>1</INJ_SEQ><NAME>ผู้บาดเจ็บ ทดสอบ</NAME><AGE>25</AGE><CITIZEN_ID>1111111111111</CITIZEN_ID><JOB>พนักงาน</JOB><CAR_REGNO>ษข9066</CAR_REGNO><ADDRESS>ที่อยู่ผู้บาดเจ็บ</ADDRESS><TEL_NO>0777777777</TEL_NO><HOS_NAME>รพ.ทดสอบ</HOS_NAME><COST>5000</COST><INJURE>ฟกช้ำ</INJURE><GENDER>ชาย</GENDER><PERSON_TYPE>DV</PERSON_TYPE><WOUNDED_TYPE>01</WOUNDED_TYPE></TXN_SURV_INJ><TXN_SURV_BILL><SUR_INVEST>0.00</SUR_INVEST></TXN_SURV_BILL></INSERT_SURV_REPORT_XML>"""

with tempfile.TemporaryDirectory() as tmp:
    p = pathlib.Path(tmp) / "contract.txt"
    p.write_text(GOLDEN, encoding="utf-8")
    parsed = surv_xml.parse_surv_report(p)

# รถประกัน (TYPE=0) — ไม่นับเป็นคู่กรณี, เก็บ gender/title/idcard
ins = parsed.get("insured", {})
check("รถประกัน TYPE=0 ไม่ถูกนับเป็นคู่กรณี", len(parsed["third_parties"]) == 1)
check("insured: เพศ/คำนำหน้า/บัตร ปชช.",
      ins.get("gender") == "ชาย" and ins.get("title_id") == "1"
      and ins.get("idcard") == "1234567890123", str(ins))

# คู่กรณี (TYPE=20)
tp = parsed["third_parties"][0] if parsed["third_parties"] else {}
check("คู่กรณี: ทะเบียน 7ชน807", tp.get("plate_no") == "7ชน807")
check("คู่กรณี: ยี่ห้อ HONDA (_clean_brand ตัด A ออกจาก AHONDA)", tp.get("car_brand") == "HONDA")
check("คู่กรณี: ประเภทรถ A", tp.get("veh_type_code") == "A")
check("คู่กรณี: มีประกัน (HAVE_INSURANCE=1)", tp.get("insurer") == "1")
check("คู่กรณี: กรมธรรม์/เคลม", tp.get("policy_no") == "OPPPOL" and tp.get("claim_no") == "OPPCLAIM")
check("คู่กรณี: ชื่อผู้ขับ", tp.get("drv_name") == "คู่กรณี ทดสอบ")

# ผู้บาดเจ็บ (ข้อ 3)
check("ผู้บาดเจ็บ: 1 คน", len(parsed["injuries"]) == 1)
inj = parsed["injuries"][0] if parsed["injuries"] else {}
check("ผู้บาดเจ็บ: ชื่อ/บัตร/ค่ารักษา/อาการ",
      inj.get("name") == "ผู้บาดเจ็บ ทดสอบ" and inj.get("citizen_id") == "1111111111111"
      and inj.get("cost") == "5000" and inj.get("injure") == "ฟกช้ำ", str(inj))
check("ผู้บาดเจ็บ: PERSON_TYPE=DV (ผู้ขับขี่รถประกัน)", inj.get("person_type") == "DV")
check("ผู้บาดเจ็บ: WOUNDED_TYPE=01 (เล็กน้อย)", inj.get("wounded_type") == "01")

# ทรัพย์สิน
check("ทรัพย์สิน: 1 รายการ", len(parsed["assets"]) == 1)
asset = parsed["assets"][0] if parsed["assets"] else {}
check("ทรัพย์สิน: ชื่อ/รายละเอียด/ราคา/เจ้าของ",
      asset.get("name") == "รั้วบ้าน" and asset.get("damage_detail") == "รั้วหัก"
      and asset.get("damage_cost") == "2000" and asset.get("owner_name") == "เจ้าของทรัพย์", str(asset))

print(f"\n{'✅ ผ่านทั้งหมด' if not failures else '❌ ล้มเหลว: ' + ', '.join(failures)}")
sys.exit(0 if not failures else 1)
