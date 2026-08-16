# -*- coding: utf-8 -*-
"""สร้าง autokey/emcs_names.py — ตาราง "รหัส EMCS → ชื่อไทย" ของจังหวัด/อำเภอ

ทำไมต้องมีฝั่ง Python ด้วย: ตัวแปลง ISURVEY → se-survey (isurvey_to_sesurvey.py)
ต้องส่ง **ชื่อไทย** ให้ตรง dropdown ของเว็บเป๊ะ ไม่งั้นผู้ตรวจเปิดหน้าเคสมาแล้วช่องว่าง

ทำไมไม่ใช้ชื่อจาก master ของ ISURVEY ตรง ๆ: ชื่ออำเภอสองระบบเขียนคนละแบบ —
ISURVEY 'บางบ่อ' / EMCS 'อำเภอบางบ่อ' · ISURVEY 'เมืองสมุทรปราการ' / EMCS 'อำเภอเมือง'
(ตรงกันแค่ 50 จาก 1,004 ซึ่งคือเขตของ กทม. ล้วน — วัดจริง 16/08/69)

ที่มา: se-survey/backend/src/data/emcsDistricts.ts (capture จากพอร์ทัลจริง)
      + ตาราง PROVINCE_BY_CODE ใน xmlImport.service.ts

รันใหม่เมื่อฝั่ง se-survey อัปเดตตารางพวกนั้น:
    python tools/build_emcs_names.py
"""
import io
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

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

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ เขียน {OUT.name}: จังหวัด {len(provinces)} · อำเภอ "
          f"{sum(len(v) for v in districts.values())} รายการ ใน {len(districts)} จังหวัด")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
