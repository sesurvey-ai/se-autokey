# -*- coding: utf-8 -*-
"""สร้าง autokey/emcs_names.py — ตาราง "รหัส EMCS → ชื่อไทย" ของจังหวัด/อำเภอ

ทำไมต้องมีฝั่ง Python ด้วย: ตัวแปลง ISURVEY → se-survey (isurvey_to_sesurvey.py)
ต้องส่ง **ชื่อไทย** ให้ตรง dropdown ของเว็บเป๊ะ ไม่งั้นผู้ตรวจเปิดหน้าเคสมาแล้วช่องว่าง

ทำไมไม่ใช้ชื่อจาก master ของ ISURVEY ตรง ๆ: ชื่ออำเภอสองระบบเขียนคนละแบบ —
ISURVEY 'บางบ่อ' / EMCS 'อำเภอบางบ่อ' · ISURVEY 'เมืองสมุทรปราการ' / EMCS 'อำเภอเมือง'
(ตรงกันแค่ 50 จาก 1,004 ซึ่งคือเขตของ กทม. ล้วน — วัดจริง 16/08/69)

ที่มา: se-survey/backend/src/data/emcsDistricts.ts (capture จากพอร์ทัลจริง)
      + ตาราง PROVINCE_BY_CODE ใน xmlImport.service.ts
      + master ของ ISURVEY (ต้องต่อเน็ต — ใช้ทำตารางแปลงรหัสอำเภอ ISURVEY → EMCS)

รันใหม่เมื่อฝั่ง se-survey อัปเดตตารางพวกนั้น:
    python tools/build_emcs_names.py
"""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # ให้ import autokey ได้

SE_SURVEY = Path(r"C:\Users\i9\Desktop\se-survey")
OUT = Path(__file__).resolve().parent.parent / "autokey" / "emcs_names.py"

PAIR = re.compile(r"'?([\w\u0E00-\u0E7F /]+?)'?\s*:\s*'([^']*)'")


def _table(src: str, name: str) -> dict:
    i = src.index(f"const {name}")
    j = src.index("};", i)
    return {m.group(1): m.group(2) for m in PAIR.finditer(src[src.index("{", i) + 1:j])}


def main() -> int:
    imp = (SE_SURVEY / "backend/src/services/xmlImport.service.ts").read_text(encoding="utf-8")
    dis = (SE_SURVEY / "backend/src/data/emcsDistricts.ts").read_text(encoding="utf-8")

    provinces = _table(imp, "PROVINCE_BY_CODE")
    districts = {}
    for m in re.finditer(r"'(\d+)':\s*\{([^}]*)\}", dis):
        # เก็บกลับด้าน: รหัสอำเภอ → ชื่อ (ตัวแปลงมีรหัสอยู่แล้ว ต้องการชื่อ)
        # ตัดศูนย์นำทิ้งเป็นคีย์ — ฝั่ง export เคยตัดศูนย์นำมาแล้ว ('0227' → '227')
        districts[m.group(1)] = {
            code.lstrip("0") or "0": name for name, code in PAIR.findall(m.group(2))
        }

    if len(provinces) < 70 or len(districts) < 70:
        print(f"❌ อ่านตารางได้ไม่ครบ (จังหวัด {len(provinces)} · อำเภอ {len(districts)} จังหวัด)")
        return 1

    lines = [
        '"""ชื่อไทยของจังหวัด/อำเภอตามรหัสของ EMCS — **สร้างอัตโนมัติ อย่าแก้ด้วยมือ**',
        "",
        f"สร้างโดย tools/build_emcs_names.py เมื่อ {datetime.now():%Y-%m-%d %H:%M}",
        "ที่มา: se-survey/backend/src/data/emcsDistricts.ts + xmlImport.service.ts",
        "",
        "ชื่อพวกนี้ต้องตรงกับ dropdown บนเว็บ se-survey เป๊ะ — เพี้ยนตัวเดียวช่องจะว่าง",
        '"""',
        "",
        "# {รหัสจังหวัด EMCS: ชื่อจังหวัด}",
        "PROVINCE_NAME = {",
    ]
    for code in sorted(provinces, key=lambda c: int(c)):
        lines.append(f'    "{code}": "{provinces[code]}",')
    lines += ["}", "", "# {รหัสจังหวัด EMCS: {รหัสอำเภอ (ตัดศูนย์นำ): ชื่ออำเภอ}}", "DISTRICT_NAME = {"]
    for pcode in sorted(districts, key=lambda c: int(c)):
        inner = ", ".join(f'"{k}": "{v}"' for k, v in districts[pcode].items())
        lines.append(f'    "{pcode}": {{{inner}}},')
    lines += ["}", ""]

    # ── ตารางแปลงรหัสอำเภอ ISURVEY → EMCS (จับคู่ด้วย "ชื่อ") ──
    pair, unmatched = _district_code_map(provinces, districts)
    lines += [
        "# {รหัสอำเภอ ISURVEY: รหัสอำเภอ EMCS} — จับคู่ด้วย **ชื่อ** ไม่ใช่ลำดับรหัส",
        "#",
        "# ⛔ ห้ามกลับไปคำนวณจากลำดับ (<รหัสจังหวัด><ลำดับ 2 หลัก>) — สองระบบเรียงอำเภอ",
        "#    ไม่เหมือนกัน วิธีนั้นผิด 186 จาก 924 อำเภอ (วัดจริง 17/08/69) เช่นเชียงใหม่",
        "#    ลำดับ 01 ของ ISURVEY = 'เมืองเชียงใหม่' แต่ของ EMCS = 'อำเภอดอยเต่า'",
        "#    ผิดแบบเงียบ ๆ ไม่มี error เพราะได้ชื่ออำเภอที่มีจริงในจังหวัดนั้น แค่ผิดอำเภอ",
        "DISTRICT_TO_EMCS = {",
    ]
    for k in sorted(pair, key=lambda s: (len(s), s)):
        lines.append(f'    "{k}": "{pair[k]}",')
    lines += ["}", ""]

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ เขียน {OUT.name}: จังหวัด {len(provinces)} · อำเภอ "
          f"{sum(len(v) for v in districts.values())} รายการ ใน {len(districts)} จังหวัด")
    print(f"   ตารางแปลงอำเภอ ISURVEY→EMCS: จับคู่ได้ {len(pair)} · จับคู่ไม่ได้ {unmatched}")
    return 0


def _bare(name: str, province: str = "") -> str:
    """ตัดคำนำหน้าให้เทียบชื่อข้ามระบบได้ — ISURVEY 'บางบ่อ' / EMCS 'อำเภอบางบ่อ'
    และ ISURVEY 'เมืองสมุทรปราการ' / EMCS 'อำเภอเมือง'"""
    n = str(name or "").strip()
    for p in ("กิ่งอำเภอ", "อำเภอ", "เขต"):
        if n.startswith(p):
            n = n[len(p):]
            break
    return "เมือง" if (province and n == "เมือง" + province) else n.strip()


def _district_code_map(provinces: dict, districts: dict):
    """{รหัสอำเภอ ISURVEY: รหัสอำเภอ EMCS} — ต้องต่อเน็ตเพื่ออ่าน master ของ ISURVEY

    ที่จับคู่ไม่ได้ (~78 รายการ) เป็นชื่อที่ ISURVEY มีอยู่ฝ่ายเดียวจริง ๆ เช่น
    'เทศบาลตำบลแหลมฉบัง*' · 'ลำลูกกา (สาขาตำบลคูคต)*' → ไม่ใส่ในตาราง ปล่อยให้คนเลือกเอง
    """
    from autokey import isurvey_emcs_map as emcs_map
    from autokey.config import load_config
    from autokey.isurvey_api import ISurveyAPI

    api = ISurveyAPI(load_config())
    api.login()
    amphurs = api.master("masterAmphur", "amphurID", "amphurname")

    out, miss = {}, 0
    for aid, aname in amphurs.items():
        ep = emcs_map.PROVINCE_TO_EMCS.get(str(aid)[:2])
        if not ep:
            miss += 1
            continue
        pname = provinces.get(ep, "")
        want = _bare(aname, pname)
        hit = next((code for code, nm in districts.get(ep, {}).items()
                    if _bare(nm, pname) == want), None)
        if hit:
            out[str(aid)] = f"{ep}{hit[len(ep):]}" if hit.startswith(ep) else hit
        else:
            miss += 1
    return out, miss


if __name__ == "__main__":
    raise SystemExit(main())
