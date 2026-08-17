"""สกัดรายชื่อบริษัทประกันของ **EMCS จริง** → ตัวเลือก dropdown ฝั่งเว็บ se-survey

ทำไมต้องเป็นลิสต์ของ EMCS ไม่ใช่ของ ISURVEY:
    ปลายทางที่บอทต้องไปเลือกคือ `ddlHave_Insurance` บนหน้า EMCS ซึ่ง **value = ชื่อบริษัท
    ตรง ๆ** ถ้าเว็บเราให้เลือกชื่อที่ EMCS ไม่มี บอทจะเลือกไม่เจอแล้วข้ามเงียบ ๆ
    หรือแย่กว่านั้นคือ fuzzy ไปโดนบริษัทใกล้เคียง = เคลมไปผูกกับบริษัทผิด

    ISURVEY มีลิสต์ของตัวเอง (119 ชื่อ สะกดคนละแบบ) → ต้องแปลงเป็นชื่อ EMCS
    **ตอนนำเข้า** ซึ่งยังมีคนตรวจอยู่ ไม่ใช่ปล่อยให้บอทเดาตอนกรอกซึ่งไม่มีใครดู

ที่มาของข้อมูล: หน้า EMCS ที่เซฟไว้ในโฟลเดอร์เคส 000098 (ไม่ต้องแตะระบบจริง)

รัน:  python tools/build_insurer_options.py
"""
import glob
import io
import os
import re
import sys

CASE_DIR = r"C:\Users\i9\Desktop\21BR10AVD-6906-000098"
OUT_TS = r"C:\Users\i9\Desktop\se-survey\web\src\components\cases\insurerOptions.ts"
OUT_PY = os.path.join(os.path.dirname(__file__), "..", "autokey", "emcs_insurers.py")

SELECT_RE = re.compile(r'<select[^>]*id="([^"]*ddlHave_Insurance)"[^>]*>(.*?)</select>', re.S | re.I)
OPTION_RE = re.compile(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', re.S | re.I)


def read_html(path):
    raw = io.open(path, "rb").read()
    # หน้า EMCS เป็น TIS-620 — ลอง utf-8 ก่อนเผื่ออนาคตเปลี่ยน
    for enc in ("utf-8", "cp874", "tis-620"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp874", errors="replace")


def emcs_names():
    """ชื่อบริษัทจาก ddlHave_Insurance (ตัดตัว placeholder ออก) — เรียงตามที่ EMCS เรียง"""
    for path in sorted(glob.glob(os.path.join(CASE_DIR, "*.html"))):
        txt = read_html(path)
        for _sid, body in SELECT_RE.findall(txt):
            out = []
            for value, label in OPTION_RE.findall(body):
                name = " ".join(re.sub(r"<[^>]+>", "", label).split())
                if value == "0" or not name or name.startswith("--"):
                    continue
                # EMCS เก็บ value = ชื่อเต็ม ถ้าไม่ตรงกับ label แปลว่าอ่านผิดหน้า
                if " ".join(value.split()) != name:
                    raise SystemExit(f"value != label ที่ {path}: {value!r} vs {name!r}")
                out.append(name)
            if out:
                return out, os.path.basename(path)
    raise SystemExit("หา ddlHave_Insurance ในหน้าที่เซฟไว้ไม่เจอ")


def norm(s):
    """ตัดเฉพาะคำที่เป็น 'รูปแบบการเขียน' ไม่ใช่ตัวตนของบริษัท

    ⛔ **ห้ามตัด 'ประกันภัย'/'ประเทศไทย' และห้ามเทียบแบบสับสตริง**
       เคยตัดแล้วเทียบสับสตริง ผลคือ 'กรุงเทพประกัน*สุขภาพ*' ไปจับคู่กับ
       'กรุงเทพ*ประกันภัย*' ซึ่งคนละบริษัทกัน = เคลมไปผูกบริษัทผิดแบบเงียบ ๆ
       ชื่อบริษัทประกันต่างกันที่คำท้าย ๆ พอดี (ประกันภัย / ประกันชีวิต / ประกันสุขภาพ)
       จับคู่ไม่ได้ไม่เสียหาย — หัวหน้าเลือกเองบนเว็บ · จับคู่ผิดคือเสียหายจริง
    """
    s = str(s or "")
    for token in ("บริษัท", "จำกัด", "จํากัด", "(มหาชน)", "มหาชน",
                  "(", ")", " ", "\t", "\u00a0"):
        s = s.replace(token, "")
    return s.strip()


def main():
    names, src_file = emcs_names()
    print(f"อ่านจาก {src_file} — EMCS มี {len(names)} บริษัท")

    # ── จับคู่ชื่อฝั่ง ISURVEY → ชื่อ EMCS ──────────────────────────────────
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from autokey.isurvey_emcs_map import COMPANY_NAME

    by_norm = {}
    for n in names:
        by_norm.setdefault(norm(n), n)

    mapping, unmatched = {}, []
    for isv in sorted(set(" ".join(v.split()) for v in COMPANY_NAME.values() if v and v.strip())):
        # ตรงตัวหลัง normalize เท่านั้น — ไม่มี fallback แบบเดา (ดูเหตุผลใน norm())
        hit = by_norm.get(norm(isv))
        if hit:
            if hit != isv:
                mapping[isv] = hit
        else:
            unmatched.append(isv)

    print(f"ISURVEY → EMCS: จับคู่ได้ {len(mapping)} ชื่อที่สะกดต่างกัน · "
          f"จับคู่ไม่ได้ {len(unmatched)} ชื่อ")

    # ── ไฟล์ตัวเลือกฝั่งเว็บ ────────────────────────────────────────────────
    with io.open(OUT_TS, "w", encoding="utf-8", newline="\n") as f:
        f.write('/**\n')
        f.write(' * รายชื่อบริษัทประกันสำหรับช่อง "มีประกันภัยที่" — **ลิสต์ของ EMCS เป๊ะ ๆ**\n')
        f.write(' *\n')
        f.write(' * บน EMCS ช่องนี้คือ `ddlHave_Insurance` และ **value = ชื่อบริษัทตรง ๆ**\n')
        f.write(' * ถ้าเว็บเราให้เลือกชื่อที่ EMCS ไม่มี บอทจะเลือกไม่เจอแล้วข้ามเงียบ ๆ\n')
        f.write(' * หรือ fuzzy ไปโดนบริษัทใกล้เคียง = เคลมไปผูกกับบริษัทผิด\n')
        f.write(' *\n')
        f.write(' * ⚙️ generate จากหน้า EMCS ที่เซฟไว้ (เคส 000098) ด้วย\n')
        f.write(' *    se-autokey/tools/build_insurer_options.py — อย่าแก้ด้วยมือ\n')
        f.write(' */\n')
        f.write('export const INSURER_COMPANY_OPTIONS: string[] = [\n')
        for n in names:
            f.write('  "%s",\n' % n.replace('\\', '\\\\').replace('"', '\\"'))
        f.write('];\n\n')
        f.write('/**\n')
        f.write(' * ตัวเลือกสำหรับเคสหนึ่ง ๆ — ค่าเดิมที่ไม่อยู่ในลิสต์ EMCS ให้พ่วงไว้หัวลิสต์\n')
        f.write(' * ⛔ ห้ามตัดทิ้ง: งานเก่ามีชื่อสะกดนอกลิสต์อยู่จริง ถ้าไม่พ่วงไว้ select จะเด้ง\n')
        f.write(' *    เป็นค่าว่าง แล้วกดบันทึกทีเดียวชื่อประกันหายทั้งช่อง\n')
        f.write(' *    (ผู้ตรวจต้องเลือกใหม่ให้ตรงลิสต์ EMCS ก่อนอนุมัติ — ดูป้ายเตือนข้างช่อง)\n')
        f.write(' */\n')
        f.write('export const insurerOptions = (current: string): string[] => {\n')
        f.write("  const c = String(current ?? '').trim();\n")
        f.write('  return c && !INSURER_COMPANY_OPTIONS.includes(c)\n')
        f.write('    ? [c, ...INSURER_COMPANY_OPTIONS]\n')
        f.write('    : INSURER_COMPANY_OPTIONS;\n')
        f.write('};\n\n')
        f.write('/** ชื่อนี้เลือกบน EMCS ได้จริงไหม — ใช้ทาแดงเตือนผู้ตรวจ */\n')
        f.write('export const isEmcsInsurer = (v: string): boolean =>\n')
        f.write("  !String(v ?? '').trim() || INSURER_COMPANY_OPTIONS.includes(String(v).trim());\n")

    # ── ตารางแปลงชื่อฝั่งบอท ────────────────────────────────────────────────
    with io.open(OUT_PY, "w", encoding="utf-8", newline="\n") as f:
        f.write('"""ชื่อบริษัทประกัน: ฝั่ง ISURVEY → ชื่อที่ EMCS มีจริง\n\n')
        f.write("⚙️ generate ด้วย tools/build_insurer_options.py — อย่าแก้ด้วยมือ\n\n")
        f.write("แปลง **ตอนนำเข้า** (ยังมีคนตรวจอยู่) ไม่ใช่ตอนบอทกรอก (ไม่มีใครดู)\n")
        f.write("ชื่อที่จับคู่ไม่ได้ ปล่อยผ่านไปตามเดิม แล้วให้หัวหน้าเลือกเองบนเว็บ\n")
        f.write('"""\n\n')
        f.write("EMCS_INSURERS = [\n")
        for n in names:
            f.write("    %r,\n" % n)
        f.write("]\n\n")
        f.write("ISURVEY_TO_EMCS = {\n")
        for k, v in sorted(mapping.items()):
            f.write("    %r: %r,\n" % (k, v))
        f.write("}\n\n\n")
        f.write("def to_emcs_insurer(name):\n")
        f.write('    """ชื่อบริษัทที่เลือกบน EMCS ได้ (คืนค่าเดิมถ้าแปลงไม่ได้)"""\n')
        f.write("    s = ' '.join(str(name or '').split())\n")
        f.write("    if not s or s in EMCS_INSURERS:\n")
        f.write("        return s\n")
        f.write("    return ISURVEY_TO_EMCS.get(s, s)\n")

    print(f"เขียน {OUT_TS}")
    print(f"เขียน {OUT_PY}")
    if unmatched:
        print(f"\nชื่อฝั่ง ISURVEY ที่ยังจับคู่ไม่ได้ {len(unmatched)} ชื่อ (หัวหน้าเลือกเองบนเว็บ):")
        for u in unmatched[:15]:
            print("   ", u)
        if len(unmatched) > 15:
            print(f"    ... อีก {len(unmatched) - 15} ชื่อ")


if __name__ == "__main__":
    main()
