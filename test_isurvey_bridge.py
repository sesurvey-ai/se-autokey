# -*- coding: utf-8 -*-
"""เทส round-trip: ตัวแปลง "อ่านสดจาก API" ต้องได้ค่าตรงกับไฟล์ XML ที่ ISURVEY export เอง

ทำไมต้องเทียบแบบนี้: ตัวแปลง `isurvey_to_sesurvey.py` อ่าน **API** ส่วนเส้นทางเดิมอ่าน
**ไฟล์ XML** — สองทางนี้ควรได้ข้อมูลชุดเดียวกันของเคสเดียวกัน ถ้าไม่ตรงแปลว่าแมปผิด
ไฟล์ XML คือ "คำตอบเฉลย" เพราะ ISURVEY เป็นคนสร้างเอง ไม่ใช่เราตีความ

ต้องต่อเน็ตและมี ISURVEY_USERNAME/PASSWORD ใน .env (อ่านอย่างเดียว ไม่แตะข้อมูล)

    python test_isurvey_bridge.py            # เทียบทุกไฟล์ที่หาเจอ
    python test_isurvey_bridge.py --offline  # เทสเฉพาะฟังก์ชันแปลงค่า ไม่ต่อเน็ต
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from autokey.emcs_names import DISTRICT_NAME, PROVINCE_NAME          # noqa: E402
from autokey.isurvey_to_sesurvey import (                            # noqa: E402
    _bare, be_date, be_datetime, split_name, surveyor_code,
)

failed = 0
checked = 0


def check(label, ok, note=""):
    global failed, checked
    checked += 1
    if not ok:
        failed += 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({note})" if note else ""))


# ───────────────────────── ส่วนที่ไม่ต้องต่อเน็ต ─────────────────────────

print("── ฟังก์ชันแปลงค่า ──")
check("วันที่ ค.ศ. → พ.ศ.", be_date("2025-11-15") == "15/11/2568")
check("วันที่ที่เป็น พ.ศ. อยู่แล้ว ไม่บวกซ้ำ", be_date("2568-11-15") == "15/11/2568")
check("วันที่ว่าง → ''", be_date(None) == "" and be_date("") == "")
check("วัน+เวลา คั่นด้วย |", be_datetime("2026-08-13", "13:02") == "13/08/2569|13:02")
check("เวลา 00:00 = ไม่ทราบเวลา → เหลือแต่วัน",
      be_datetime("2026-08-13", "00:00") == "13/08/2569")
check("เวลามีวินาที ตัดเหลือ HH:MM", be_datetime("2026-08-13", "01:58:07") == "13/08/2569|01:58")

check("แยกชื่อที่มีคำนำหน้า", split_name("นาย นิพันธ์ เหมือนกรุง") == ("นาย", "นิพันธ์", "เหมือนกรุง"))
check("แยกชื่อที่ไม่มีคำนำหน้า", split_name("นิพันธ์ เหมือนกรุง") == ("", "นิพันธ์", "เหมือนกรุง"))
check("นามสกุลหลายคำไม่หาย", split_name("นางสาว ก ข ค")[2] == "ข ค")

check("รหัสผู้สำรวจแบบ SE", surveyor_code("SE272 นายสมชาย ใจดี") == "SE272")
# ต่างจังหวัดใช้ SEC — เส้น XML จับแค่ SE\d+ ทำให้งานต่างจังหวัดไม่เคยถูกมอบหมายอัตโนมัติ
check("รหัสผู้สำรวจแบบ SEC (ต่างจังหวัด)", surveyor_code("SEC423 สมชาติ หอมมาลา") == "SEC423")
check("ไม่มีรหัส → ''", surveyor_code("สมชาติ หอมมาลา") == "")

check("ตัดคำนำหน้าอำเภอ", _bare("อำเภอบางบ่อ") == "บางบ่อ")
check("ตัดคำนำหน้าเขต", _bare("เขตสาทร") == "สาทร")
check("ตัด 'กิ่งอำเภอ'", _bare("กิ่งอำเภอสามชัย") == "สามชัย")
check("'เมือง<จังหวัด>' ยุบเป็น 'เมือง'", _bare("เมืองสมุทรปราการ", "สมุทรปราการ") == "เมือง")

print("\n── ตารางชื่อจังหวัด/อำเภอ ──")
check("มีจังหวัดครบ", len(PROVINCE_NAME) >= 77, f"{len(PROVINCE_NAME)}")
check("กรุงเทพเขียนแบบมีเว้นวรรค (ตรง dropdown)", PROVINCE_NAME.get("2") == "กรุงเทพ ฯ")
check("อำเภอครบเกือบทุกจังหวัด", len(DISTRICT_NAME) >= 77, f"{len(DISTRICT_NAME)}")
check("ตัวอย่างอำเภอถูก", DISTRICT_NAME.get("36", {}).get("3606") == "อำเภอวังทอง")
# หลักฐานว่าทำไม district_name ต้องจับคู่ด้วย "ชื่อ": ลำดับอำเภอสองระบบไม่ตรงกัน
# ISURVEY 6508 = วังทอง แต่ EMCS 3608 = เนินมะปราง (วังทองอยู่ที่ 3606)
check("ลำดับอำเภอสองระบบไม่ตรงกันจริง (กันคนเผลอกลับไปแปลงด้วยรหัส)",
      DISTRICT_NAME.get("36", {}).get("3608") != "อำเภอวังทอง")


# ───────────────── ชื่อบริษัทประกัน: ISURVEY → ชื่อที่ EMCS มีจริง ─────────────────
#
# ลิสต์บนเว็บ se-survey ต้องเป็นของ **EMCS** ไม่ใช่ของ ISURVEY เพราะปลายทางที่บอท
# ต้องไปเลือกคือ ddlHave_Insurance บนหน้า EMCS (value = ชื่อบริษัทตรง ๆ)
print("\n── ชื่อบริษัทประกัน (ISURVEY → EMCS) ──")
from autokey.emcs_insurers import EMCS_INSURERS, ISURVEY_TO_EMCS, to_emcs_insurer

check("มีลิสต์บริษัทของ EMCS", len(EMCS_INSURERS) >= 50, f"{len(EMCS_INSURERS)} บริษัท")
check("ปลายทางของทุกการจับคู่มีอยู่จริงในลิสต์ EMCS",
      all(v in EMCS_INSURERS for v in ISURVEY_TO_EMCS.values()),
      f"{len(ISURVEY_TO_EMCS)} คู่")
check("ชื่อที่ตรงกับ EMCS อยู่แล้ว ส่งผ่านไม่แก้",
      to_emcs_insurer(EMCS_INSURERS[0]) == EMCS_INSURERS[0])
check("แปลงชื่อฝั่ง ISURVEY ได้",
      to_emcs_insurer("วิริยะประกันภัย") == "บริษัท วิริยะประกันภัย จำกัด (มหาชน)")
check("แปลงไม่ได้ = คืนชื่อเดิม ไม่เดา",
      to_emcs_insurer("กมล ประกันภัย") == "กมล ประกันภัย")
# ⛔ กับดักจริงที่เจอ 17/08/69: ตัดคำว่า "ประกันภัย" ออกแล้วเทียบแบบสับสตริง
#    ทำให้ 'กรุงเทพประกัน*สุขภาพ*' จับคู่ไปเป็น 'กรุงเทพ*ประกันภัย*' คนละบริษัทกัน
#    = เคลมไปผูกกับบริษัทผิดโดยไม่มีอะไรฟ้อง
check("ห้ามจับคู่ 'ประกันสุขภาพ' ไปเป็น 'ประกันภัย'",
      to_emcs_insurer("กรุงเทพประกันสุขภาพ จำกัด") != "บริษัท กรุงเทพประกันภัย จำกัด (มหาชน)")
check("ไม่มีคู่ไหนที่ชื่อต้นทาง/ปลายทางเป็นคนละประเภทประกัน",
      not [1 for k, v in ISURVEY_TO_EMCS.items()
           if ("สุขภาพ" in k) != ("สุขภาพ" in v) or ("ชีวิต" in k) != ("ชีวิต" in v)])

# ลิสต์ฝั่งเว็บต้องเป็นชุดเดียวกับที่ generate ไว้ — แก้ที่เดียวไม่ครบคือลิสต์เพี้ยนเงียบ ๆ
_ts = Path(__file__).resolve().parent.parent / "se-survey" / "web" / "src" / "components" / "cases" / "insurerOptions.ts"
_ts_names = re.findall(r'^  "(.+)",$', _ts.read_text(encoding="utf-8"), re.M) if _ts.exists() else []
check("ลิสต์ฝั่งเว็บตรงกับลิสต์ EMCS ทุกชื่อ",
      _ts_names == EMCS_INSURERS, f"เว็บ {len(_ts_names)} · EMCS {len(EMCS_INSURERS)}")

if "--offline" in sys.argv:
    print(f"\n{'✅ ผ่านทั้งหมด' if failed == 0 else f'❌ ล้มเหลว {failed} รายการ'}  ({checked} ข้อ)")
    raise SystemExit(1 if failed else 0)


# ───────────────── เทียบกับไฟล์ XML จริงที่ ISURVEY export ─────────────────

def xml_tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([\s\S]*?)</{tag}>", text)
    if not m:
        return ""
    # ขึ้นบรรทัดใน XML มาเป็น CRLF ที่เข้ารหัสครึ่งเดียว: '&#13;' แล้วตามด้วย LF ตัวจริง
    # แทน &#13; ด้วย \n ตรง ๆ จะได้ 2 บรรทัดต่อ 1 การขึ้นบรรทัด (กติกาเดียวกับ decode()
    # ของ xmlImport.service.ts — ถ้าไม่ทำเหมือนกัน เทสจะฟ้องผิดเองทั้งที่โค้ดถูก)
    v = re.sub(r"&#13;\r?\n", "\n", m.group(1))
    v = (v.replace("&#13;", "\n").replace("&amp;", "&")
         .replace("&lt;", "<").replace("&gt;", ">").replace("\r\n", "\n").strip())
    return "" if v == "-" else v


def xml_blocks(text: str, tag: str):
    return re.findall(rf"<{tag}>([\s\S]*?)</{tag}>", text)


def find_xml_files():
    """ไฟล์ SURV_REPORT ที่มีในเครื่อง (runs/xml + โฟลเดอร์เคสบน Desktop — อ่านอย่างเดียว)"""
    out = list((Path(__file__).parent / "runs" / "xml").glob("*SURV_REPORT*.txt"))
    desktop = Path(__file__).resolve().parent.parent
    for d in desktop.iterdir():
        if d.is_dir() and re.match(r"^(21BR10A|20\d{2}0130)", d.name):
            out += list(d.glob("SURV_REPORT_*.txt"))
    return out


print("\n── เทียบกับไฟล์ XML จริง ──")
files = find_xml_files()
print(f"  พบไฟล์ {len(files)} ไฟล์")
if not files:
    check("มีไฟล์ XML ให้เทียบ", False, "ไม่พบไฟล์ SURV_REPORT_*.txt")
    raise SystemExit(1)

from autokey.config import load_config                               # noqa: E402
from autokey.isurvey_api import ISurveyAPI                           # noqa: E402
from autokey.isurvey_to_sesurvey import build_case                   # noqa: E402

api = ISurveyAPI(load_config())
api.login()

#: ช่องที่สองทางต้องตรงกัน — (ชื่อคอลัมน์ฝั่งเรา, แท็บ XML, วิธีแปลงค่าฝั่ง XML)
DIRECT = [
    ("survey_job_no", "SURV_JOBNO", None),
    ("claim_no", "REF_CLAIM_NO", None),
    ("claim_ref_no", "ACC_CLAIMREF_NO", None),
    ("policy_no", "ACC_POLICY_NO", None),
    ("assured_name", "ASSURED_NAME", None),
    ("acc_place", "ACC_PLACE", lambda v: v[:200]),
    ("acc_detail", "ACC_DETAIL", None),
]

compared = 0
for f in sorted(files):
    text = f.read_text(encoding="utf-8", errors="replace")
    rep = (xml_blocks(text, "TXN_SURV_REPORT") or [""])[0]
    claim = xml_tag(rep, "REF_CLAIM_NO")
    job = xml_tag(rep, "SURV_JOBNO")
    if not claim:
        continue
    try:
        case = api.find_case(claim, job)
        got = build_case(api, case["caseID"], case)["report"]
    except Exception as e:
        print(f"  ⚠️  {f.name}: อ่านสดไม่ได้ ({type(e).__name__}: {str(e)[:80]}) — ข้าม")
        continue
    compared += 1
    print(f"\n  เคลม {claim} ({f.name})")
    for col, tag, conv in DIRECT:
        want = xml_tag(rep, tag)
        if conv:
            want = conv(want)
        mine = str(got.get(col) or "")
        # ช่องที่ไฟล์ไม่มีค่า ไม่ถือว่าผิด (API อาจมีมากกว่า — ดีกว่า ไม่แย่กว่า)
        if not want:
            continue
        check(f"{col}", mine == want, f"XML={want!r} API={mine!r}" if mine != want else "")

    # จังหวัด/อำเภอ: XML เก็บเป็น "รหัส EMCS" ฝั่งเราเก็บเป็นชื่อ → แปลงก่อนเทียบ
    pcode = xml_tag(rep, "ACC_PROVINCEID")
    if pcode:
        check("acc_province (แปลงรหัส EMCS → ชื่อ)",
              str(got.get("acc_province") or "") == PROVINCE_NAME.get(pcode, ""),
              f"XML={pcode}:{PROVINCE_NAME.get(pcode)!r} API={got.get('acc_province')!r}")
        dcode = xml_tag(rep, "ACC_DISTRICTID").lstrip("0")
        if dcode:
            check("acc_district (แปลงรหัส EMCS → ชื่อ)",
                  str(got.get("acc_district") or "") == DISTRICT_NAME.get(pcode, {}).get(dcode, ""),
                  f"XML={dcode}:{DISTRICT_NAME.get(pcode, {}).get(dcode)!r} "
                  f"API={got.get('acc_district')!r}")

    # วันที่เกิดเหตุ — XML ส่ง 'yyyy-mm-dd hh:mm:ss' ฝั่งเราแยกวัน/เวลาและเป็น พ.ศ.
    raw = xml_tag(rep, "ACC_DATE")
    if raw:
        check("acc_date", str(got.get("acc_date") or "") == be_date(raw),
              f"XML={raw!r} → {be_date(raw)!r} · API={got.get('acc_date')!r}")

    # รถประกัน (บล็อก TYPE=0)
    ins = next((c for c in xml_blocks(text, "TXN_SURV_CAR") if xml_tag(c, "TYPE") == "0"), "")
    if ins:
        for col, tag in (("license_plate", "CAR_REGNO"), ("chassis_no", "CHASSISNO"),
                         ("engine_no", "ENGINENO"), ("car_model", "CMODEL")):
            want = xml_tag(ins, tag)
            if want:
                check(col, str(got.get(col) or "") == want,
                      f"XML={want!r} API={got.get(col)!r}")
        ctype = xml_tag(ins, "CTYPECODE")
        if ctype:
            check("car_type (รหัสตัวอักษร)", str(got.get("car_type") or "") == ctype,
                  f"XML={ctype!r} API={got.get('car_type')!r}")

if compared == 0:
    check("เทียบได้อย่างน้อย 1 เคลม", False, "อ่านสดจาก ISURVEY ไม่สำเร็จสักเคลม")

print(f"\n{'✅ ผ่านทั้งหมด' if failed == 0 else f'❌ ล้มเหลว {failed} รายการ'}"
      f"  ({checked} ข้อ · เทียบ {compared} เคลม)")
raise SystemExit(1 if failed else 0)
