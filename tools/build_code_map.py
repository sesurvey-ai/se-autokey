"""สร้างตารางแปลงรหัส ISURVEY → EMCS (จังหวัด / ประเภทใบขับขี่)

ทำไมต้องมี: สองระบบใช้ "รหัส" คนละชุดสำหรับของอย่างเดียวกัน
  ชลบุรี  → ISURVEY 20 (รหัสมาตรฐานราชการ) / EMCS 9 (เรียงตามตัวอักษร)
  ใบขับขี่รถยนต์ส่วนบุคคล → ISURVEY 15 / EMCS 19
ถ้าส่งรหัส ISURVEY เข้า EMCS ตรง ๆ จะได้ "จังหวัด/ประเภทผิด" แบบเงียบ ๆ ไม่มี error

วิธี: จับคู่ด้วย "ชื่อ" (label) ที่คนอ่าน — ไม่ใช่เดารหัส
  ฝั่ง EMCS อ่านจาก runs/emcs_spec.json (dropdown จริงที่ emcs_spec.py สกัดไว้)
  ฝั่ง ISURVEY อ่านจากตาราง master ผ่าน API (ต้อง login)

รัน:  python tools/build_code_map.py           # เขียนทับ autokey/isurvey_emcs_map.py
      python tools/build_code_map.py --dry     # ดูผลอย่างเดียว ไม่เขียนไฟล์
"""
import argparse
import difflib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey.config import load_config          # noqa: E402
from autokey.isurvey_api import ISurveyAPI      # noqa: E402

SPEC = Path("runs/emcs_spec.json")
OUT = Path("autokey/isurvey_emcs_map.py")


def norm(s: str) -> str:
    """ยุบให้เทียบชื่อได้: ตัดช่องว่าง/คำนำหน้า + แก้ชื่อที่เขียนต่างกันแต่หมายถึงที่เดียวกัน"""
    s = re.sub(r"\s+", "", (s or "").strip())
    s = s.replace("จังหวัด", "").replace("ฯ", "")
    return "กรุงเทพมหานคร" if s.startswith("กรุงเทพ") else s


def match(isv: dict, emcs_opts: list, label: str, cutoff: float = 0.86):
    """{รหัส ISURVEY: รหัส EMCS} — จับคู่ตรงตัวก่อน ที่เหลือใช้ fuzzy แล้ว log ให้ตรวจ

    fuzzy จำเป็นเพราะ EMCS สะกดผิดในตัวเลือกเอง เช่น
    'ใบขับขี่รถยนต์ส่วนบุคคคล' (ค เกิน) ซึ่งคู่กับ 'ใบขับขี่รถยนต์ส่วนบุคคล' ของ ISURVEY
    """
    by_name = {norm(o["label"]): o["value"] for o in emcs_opts}
    out, fuzzy, miss = {}, [], []
    for code, name in isv.items():
        n = norm(name)
        if n in by_name:
            out[code] = by_name[n]
            continue
        near = difflib.get_close_matches(n, list(by_name), n=1, cutoff=cutoff)
        if near:
            out[code] = by_name[near[0]]
            fuzzy.append((code, name, near[0], by_name[near[0]]))
        else:
            miss.append((code, name))
    print(f"\n=== {label}: ISURVEY {len(isv)} → จับคู่ได้ {len(out)} "
          f"(ตรงตัว {len(out) - len(fuzzy)} · ใกล้เคียง {len(fuzzy)})")
    for code, name, got, ev in fuzzy:
        print(f"   ~ ISURVEY {code} '{name}' → EMCS {ev} '{got}'  (ชื่อไม่ตรงเป๊ะ — ตรวจด้วย)")
    for code, name in miss:
        print(f"   ❌ ISURVEY {code} '{name}' — ไม่มีคู่ใน EMCS (จะไม่ถูกส่งเข้า EMCS)")
    return out


def main():
    ap = argparse.ArgumentParser(description="สร้างตารางแปลงรหัส ISURVEY → EMCS")
    ap.add_argument("--dry", action="store_true", help="ไม่เขียนไฟล์ แค่โชว์ผล")
    a = ap.parse_args()

    if not SPEC.exists():
        raise SystemExit(f"ไม่พบ {SPEC} — รัน tools/emcs_spec.py ก่อน")
    dd = json.loads(SPEC.read_text(encoding="utf-8"))[0]["dropdowns"]

    api = ISurveyAPI(load_config())
    api.login()

    prov = match(api.master("masterProvince", "provinceID", "provincename"),
                 dd["ddlCar_Province"], "จังหวัด")
    lic = match(api.master("masterDrvLicense", "dvlTID", "dvl_type"),
                dd["ddlEmcs_License_Type"], "ประเภทใบขับขี่")

    if a.dry:
        print("\n(--dry: ไม่เขียนไฟล์)")
        return

    body = f'''"""ตารางแปลงรหัส ISURVEY → EMCS — **ไฟล์นี้ถูกสร้างอัตโนมัติ อย่าแก้ด้วยมือ**

สร้างโดย tools/build_code_map.py เมื่อ {datetime.now():%Y-%m-%d %H:%M}
จับคู่ด้วย "ชื่อ" จาก dropdown จริงของ EMCS (runs/emcs_spec.json) กับตาราง master ของ ISURVEY

ทำไมต้องแปลง: สองระบบใช้รหัสคนละชุดกับของอย่างเดียวกัน —
ชลบุรี ISURVEY 20 / EMCS 9 · ใบขับขี่รถยนต์ส่วนบุคคล ISURVEY 15 / EMCS 19
ส่งรหัสดิบข้ามระบบ = เลือกผิดแบบเงียบ ๆ ไม่มี error
"""

# {{รหัสจังหวัด ISURVEY: รหัสจังหวัด EMCS}}
PROVINCE_TO_EMCS = {json.dumps(prov, ensure_ascii=False, indent=4)}

# {{รหัสประเภทใบขับขี่ ISURVEY: รหัส EMCS}}
LICENSE_TO_EMCS = {json.dumps(lic, ensure_ascii=False, indent=4)}


def province(isurvey_code) -> str:
    """รหัสจังหวัด ISURVEY → EMCS ('' เมื่อแปลงไม่ได้ — อย่าเดา ปล่อยว่างให้คนเลือก)"""
    return PROVINCE_TO_EMCS.get(str(isurvey_code or "").strip(), "")


def license_type(isurvey_code) -> str:
    """รหัสประเภทใบขับขี่ ISURVEY → EMCS ('' เมื่อแปลงไม่ได้)"""
    return LICENSE_TO_EMCS.get(str(isurvey_code or "").strip(), "")


def district(isurvey_amphur_id, isurvey_province_id="") -> str:
    """รหัสอำเภอ ISURVEY → EMCS

    ทั้งสองระบบใช้รูปแบบเดียวกัน = <รหัสจังหวัดของระบบนั้น><ลำดับอำเภอ 2 หลัก>
    (ISURVEY 3607 = จ.36 อำเภอลำดับ 07 · EMCS 7101 = จ.71 อำเภอลำดับ 01)
    ลำดับอำเภอเรียงเหมือนกัน (ทั้งคู่เรียงตามลำดับราชการ) → เปลี่ยนแค่ส่วนรหัสจังหวัด
    คืน '' เมื่อรูปแบบไม่ตรงหรือแปลงจังหวัดไม่ได้"""
    a = str(isurvey_amphur_id or "").strip()
    if not a.isdigit() or len(a) < 3:
        return ""
    seq, isv_prov = a[-2:], a[:-2]
    if isurvey_province_id and str(isurvey_province_id).strip() != isv_prov:
        return ""          # อำเภอไม่ได้อยู่ในจังหวัดที่ระบุ = ข้อมูลไม่สอดคล้อง
    ep = province(isv_prov)
    return f"{{ep}}{{seq}}" if ep else ""
'''
    OUT.write_text(body, encoding="utf-8")
    print(f"\n✓ เขียน {OUT} แล้ว (จังหวัด {len(prov)} · ใบขับขี่ {len(lic)})")


if __name__ == "__main__":
    main()
