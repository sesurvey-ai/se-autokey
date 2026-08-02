# -*- coding: utf-8 -*-
"""emcs_spec.py — สกัด "สเปกของฟอร์ม EMCS" จากไฟล์ HTML ที่เซฟไว้  READ-ONLY ล้วน

ทำไมต้องมี: ทุกครั้งที่ EMCS แก้ตัวเลือกหรือกติกาช่องบังคับ เราต้องมานั่งสกัดด้วยมือ
(ทำมาแล้วหลายรอบ: 350 ยี่ห้อ · 55 สี · 40 ความสัมพันธ์ · 79 สาเหตุ · 21 ลักษณะความเสียหาย
· อำเภอ · ช่องบังคับ 5 หน้า) เครื่องมือนี้ทำให้รอบหน้าเป็นคำสั่งเดียว

รัน:
    python tools/emcs_spec.py "C:/Users/i9/Desktop/21BR10AVD-6906-000098/*.html"
    python tools/emcs_spec.py <ไฟล์...> --out runs/emcs_spec.json
    python tools/emcs_spec.py <ไฟล์...> --diff        # เทียบกับลิสต์ที่ใช้อยู่ในโปรเจกต์

สิ่งที่สกัด (ต่อไฟล์)
  dropdowns : id → [{value,label}]  ตัวเลือกทั้งหมด
  formats   : id → [{insurer,regex,example}]  แพตเทิร์นข้อความที่แต่ละบริษัทบังคับ
  required  : ฟังก์ชัน vlid* → {base: {...}, per_insurer: {รหัส: {...}}}
              แต่ละช่อง = {label, cond} · cond ว่าง = บังคับเสมอ · มีค่า = บังคับเมื่อ...
  elements  : id/tag/type ของทุก input/select/textarea

⚠️ กับดักที่เจอมาแล้ว — โค้ดนี้กันไว้หมดแล้ว อย่าลบออก
 1) ฟังก์ชัน validation ถูก comment ทิ้งทั้งก้อนแล้วมี "ตัวจริง" อยู่ที่อื่นในไฟล์เดียวกัน
    (หน้าทรัพย์สิน: vlidAsset 2 นิยาม ตัวแรก // ทั้งหมด) → เลือกตัวที่มี Check*Valid มากสุด
 2) ชื่อฟังก์ชันซ้ำข้ามหน้า — vlidSurvey หน้าหลักตรวจ 66 ช่อง แต่บนหน้าค่าใช้จ่ายตรวจ 5 ช่อง
    → ผลลัพธ์แยกตามไฟล์เสมอ ห้ามเอามารวมกัน
 3) ช่องบังคับส่วนใหญ่อยู่ใน switch(getInsurerID()) → ต้องแยก base ออกจาก case
    ถ้าไม่แยกจะสรุปว่า "บังคับทุกบริษัท" เกินจริง (เคยพลาดมาแล้ว)
 4) ดอกจันแดงในหน้า ≠ บังคับ — 427 จาก 432 จุดเป็นของฝังตาย มีแค่ span id="req*" ที่
    setSome_ReqField() สลับตามบริษัท → เครื่องมือนี้จึงไม่อ่านดอกจันเลย อ่านแต่ Check*Valid
 5) CheckTextBoxCitizenValid ใส่ "เลขคนที่" ('1','2',...) ในตำแหน่งป้าย ไม่ใช่ชื่อช่อง
    → ช่องเดียวกันมีอีกบรรทัดที่ป้ายจริง เลือกอันที่ยาวกว่า
 6) บางช่องบังคับเฉพาะเมื่อเข้าเงื่อนไข เช่น txtPrb_Number บังคับต่อเมื่อติ๊ก chkHas_Prb
    → เก็บ cond ไว้ด้วย ไม่งั้นสรุปว่า "บังคับเสมอ" เกินจริง
"""
import argparse
import json
import re
import sys
from pathlib import Path

CHECK_RE = re.compile(
    r"Check(?:InputBox|DropDown|RadioBtn|TextBoxCitizen)Valid\('([A-Za-z_0-9]+)'"
    r"[^,]*,[^,]*,'([^']*)'")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _balanced(src: str, start: int) -> str:
    """คืนข้อความตั้งแต่ start ถึงปีกกาปิดที่สมดุล"""
    depth = 0
    for m in re.finditer(r"[{}]", src[start:start + 400_000]):
        depth += 1 if m.group(0) == "{" else -1
        if depth == 0 and m.group(0) == "}":
            return src[start:start + m.end()]
    return src[start:start + 400_000]


def _is_commented(src: str, pos: int) -> bool:
    """ตำแหน่งนี้อยู่บนบรรทัดที่ขึ้นต้นด้วย // ไหม"""
    line_start = src.rfind("\n", 0, pos) + 1
    return src[line_start:pos].lstrip().startswith("//")


def _conditions(body: str, pos: int) -> list:
    """เงื่อนไข if ที่ครอบตำแหน่งนี้อยู่ — ว่าง = บังคับเสมอ

    กับดัก 6: หลายช่องบังคับ "เฉพาะเมื่อ" เช่น txtPrb_Number บังคับก็ต่อเมื่อ
    ติ๊ก chkHas_Prb → ถ้านับรวมกับช่องบังคับเสมอ จะสรุปเกินจริงอีกแบบ
    """
    stack = []
    for m in re.finditer(r"[{}]", body[:pos]):
        if _is_commented(body, m.start()):
            continue
        if m.group(0) == "{":
            stack.append(_if_before(body, m.start()))
        elif stack:
            stack.pop()
    return [_pretty_cond(c) for c in stack if c]


def _if_before(body: str, brace: int):
    """ถ้า { นี้เป็นของ if (...) คืนเงื่อนไขข้างใน — ไล่วงเล็บย้อนกลับ ไม่ใช้ regex
    เพราะ if ซ้อนกันทำให้ regex แบบ greedy คร่อมข้าม if ตัวนอกไปด้วย"""
    i = brace - 1
    while i >= 0 and body[i].isspace():
        i -= 1
    if i < 0 or body[i] != ")":
        return None
    depth, j = 0, i
    while j >= 0:
        if body[j] == ")":
            depth += 1
        elif body[j] == "(":
            depth -= 1
            if depth == 0:
                break
        j -= 1
    if j < 0:
        return None
    k = j - 1
    while k >= 0 and body[k].isspace():
        k -= 1
    if body[k - 1:k + 1] != "if" or (k - 2 >= 0 and (body[k - 2].isalnum()
                                                     or body[k - 2] == "_")):
        return None
    return re.sub(r"\s+", " ", body[j + 1:i]).strip()


def _pretty_cond(c: str) -> str:
    """ย่อเงื่อนไขที่ยาวและซ้ำ ๆ ให้อ่านออก — ที่เจอบ่อยคือ 'มีรายการอย่างน้อย 1'"""
    m = re.match(r"document\.getElementById\('(\w+)'\)\.options\[.*\]\.value\s*>=\s*1$", c)
    return f"เลือก {m.group(1)} ตั้งแต่ 1 รายการขึ้นไป" if m else c


def validators(src: str) -> dict:
    """ทุกฟังก์ชัน vlid*/valid* พร้อมช่องที่ตรวจ แยก base / รายบริษัท"""
    out = {}
    for m in re.finditer(r"function\s+(v[Ll]id\w+|validate\w+)\s*\(", src):
        name = m.group(1)
        if _is_commented(src, m.start()):
            continue                                    # กับดัก 1
        body = _balanced(src, m.start())
        # กับดัก 1 (ต่อ): ชื่อซ้ำในไฟล์เดียว → เก็บตัวที่ตรวจเยอะกว่า = ตัวที่ทำงานจริง
        prev = out.get(name)
        if prev and prev["_n"] >= len(CHECK_RE.findall(body)):
            continue
        sw = body.find("switch")                        # กับดัก 3
        base_src, rest = (body[:sw], body[sw:]) if sw > 0 else (body, "")

        def grab(t):
            # ยุบ ctl00/ctl01/... เป็น ctlNN — หน้าคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน ตรวจซ้ำ
            # ทุกแถว (ผู้บาดเจ็บ 32 คน × 7 ช่อง = 224 รายการ) ซึ่งเป็นช่องชุดเดียวกัน
            d = {}
            for m2 in CHECK_RE.finditer(t):
                if _is_commented(t, m2.start()):
                    continue                            # กับดัก 1 (ระดับบรรทัด)
                cid, lab = m2.group(1), m2.group(2)
                key = re.sub(r"ctl\d+", "ctlNN", cid)
                lab = re.sub(r"\s*::.*$", "", lab).strip()
                # กับดัก 5: CheckTextBoxCitizenValid ใส่ "เลขคนที่" ในช่องป้าย ('1','2',...)
                # ไม่ใช่ชื่อช่อง — ช่องเดียวกันมีอีกบรรทัดที่ป้ายจริง เลือกป้ายที่ยาวกว่า
                if lab.isdigit():
                    lab = ""
                cond = _conditions(t, m2.start())       # กับดัก 6
                cur = d.get(key)
                if cur is None or len(lab) > len(cur["label"]):
                    d[key] = {"label": lab, "cond": cond}
            return d

        per = {}
        parts = re.split(r"case\s*'([^']+)'\s*:", rest)
        for i in range(1, len(parts), 2):
            g = grab(parts[i + 1])
            if g:
                per.setdefault(parts[i], {}).update(g)
        out[name] = {"base": grab(base_src), "per_insurer": per,
                     "_n": len(CHECK_RE.findall(body))}
    for v in out.values():
        v.pop("_n", None)
    return out


FORMAT_RE = re.compile(r"validFormat\(\s*/(.+?)/\s*,\s*'([A-Za-z_0-9]+)'\s*,\s*'(.*?)'",
                       re.S)


def formats(src: str) -> dict:
    """กติการูปแบบข้อความต่อบริษัท — validFormat(/regex/, 'id', 'ตัวอย่างที่ถูก')

    สำคัญกับบอท: เลขที่รับแจ้งของแต่ละบริษัทมีแพตเทิร์นคนละแบบ
    (12-123456 · 08-001-NMOT-012345 · I09081234 · ACD001-A1901-000000 ...)
    ส่งผิดแพตเทิร์น EMCS เด้งทันทีแม้ข้อมูลจะถูก
    """
    out = {}
    for m in FORMAT_RE.finditer(src):
        if _is_commented(src, m.start()):
            continue
        rx, cid, msg = m.group(1), m.group(2), re.sub(r"\s+", " ", m.group(3)).strip()
        ins = None
        head = src[max(0, m.start() - 6000):m.start()]
        c = re.findall(r"case\s*'([^']+)'\s*:", head)
        if c:
            ins = c[-1]
        out.setdefault(cid, []).append({"insurer": ins, "regex": rx, "example": msg})
    return out


def dropdowns(src: str) -> dict:
    """ทุก <select> → ตัวเลือก (ตัด placeholder '-- ระบุ --' ออก)"""
    out = {}
    for m in re.finditer(r'<select[^>]*id="([^"]+)"(.*?)</select>', src, re.S):
        sid, body = m.group(1), m.group(2)
        opts = [{"value": v, "label": t.strip()}
                for v, t in re.findall(r'value="([^"]*)"[^>]*>([^<]*)<', body)
                if t.strip() and not t.strip().startswith(("--", "---"))]
        if opts:
            out.setdefault(re.sub(r"ctl\d+", "ctlNN", sid), opts)
    return out


def elements(src: str) -> list:
    out, seen = [], set()
    for m in re.finditer(r"<(input|select|textarea)\b([^>]*)>", src):
        a = m.group(2)
        idm = re.search(r'id="([^"]+)"', a)
        if not idm:
            continue
        eid = re.sub(r"ctl\d+", "ctlNN", idm.group(1))
        if eid in seen:
            continue
        seen.add(eid)
        typ = re.search(r'type="([^"]*)"', a)
        out.append({"id": eid, "tag": m.group(1), "type": typ.group(1) if typ else ""})
    return out


def scan(path: Path) -> dict:
    src = _read(path)
    return {"file": path.name, "bytes": len(src),
            "validators": validators(src), "formats": formats(src),
            "dropdowns": dropdowns(src), "elements": elements(src)}


# ── เทียบกับลิสต์ที่ใช้อยู่จริงในโปรเจกต์ (หา drift) ────────────────────────────
SE = Path(r"C:/Users/i9/Desktop/se-survey")
# dropdown id ของ EMCS → (ไฟล์ในโปรเจกต์, ชื่อตัวแปร) ที่ควรมีป้ายชุดเดียวกัน
TRACKED = {
    "ddlClm_Cause":        (SE / "backend/src/services/xmlExport.service.ts", "CAUSE"),
    "ddlLoss_ID":          (SE / "backend/src/services/xmlExport.service.ts", "LOSS"),
    "ddlDri_Relation_ID":  (SE / "backend/src/services/xmlExport.service.ts", "RELATION"),
    "ddlEmcs_License_Type": (SE / "backend/src/services/xmlExport.service.ts", "LICENSE_TYPE"),
    "ddlCar_Color":        (SE / "backend/src/services/xmlExport.service.ts", "COLOR"),
}


def diff_project(spec: list) -> list:
    """ป้ายที่ EMCS มีแต่โปรเจกต์ยังไม่มี (= ถ้าพนักงานเลือก จะแปลงรหัสไม่ได้ ส่งค่าว่าง)"""
    merged = {}
    for f in spec:
        for sid, opts in f["dropdowns"].items():
            merged.setdefault(sid, {o["label"] for o in opts}).update(
                {o["label"] for o in opts})
    rows = []
    for sid, (fp, var) in TRACKED.items():
        if sid not in merged or not fp.exists():
            continue
        m = re.search(r"(?:export )?const %s: Record<string, string> = \{(.*?)\n\};" % var,
                      _read(fp), re.S)
        if not m:
            rows.append({"dropdown": sid, "error": f"หา {var} ใน {fp.name} ไม่เจอ"})
            continue
        have = set(re.findall(r"'([^']+)':\s*'[^']*'", m.group(1)))
        missing = sorted(merged[sid] - have)
        rows.append({"dropdown": sid, "var": var, "emcs": len(merged[sid]),
                     "project": len(have), "missing": missing})
    return rows


def main():
    ap = argparse.ArgumentParser(description="สกัดสเปกฟอร์ม EMCS จาก HTML ที่เซฟไว้")
    ap.add_argument("files", nargs="+", help="ไฟล์ .html (ใส่ได้หลายไฟล์ / glob)")
    ap.add_argument("--out", default="runs/emcs_spec.json")
    ap.add_argument("--diff", action="store_true", help="เทียบกับลิสต์ในโปรเจกต์")
    a = ap.parse_args()

    paths = []
    for pat in a.files:
        if any(c in pat for c in "*?"):
            p = Path(pat)                       # glob: parent ว่าง = โฟลเดอร์ปัจจุบัน
            paths += sorted(Path(p.parent or ".").glob(p.name))
        else:
            paths.append(Path(pat))
    paths = [p for p in paths if p.is_file()]
    if not paths:
        sys.exit("ไม่พบไฟล์")

    spec = []
    for p in paths:
        d = scan(p)
        spec.append(d)
        print(f"\n=== {d['file']}  ({d['bytes']:,} bytes) ===")
        print(f"  element {len(d['elements'])} · dropdown {len(d['dropdowns'])}"
              f" · format {sum(len(x) for x in d['formats'].values())}")
        for fn, v in d["validators"].items():
            per = " · ".join(f"{k}:{len(x)}" for k, x in v["per_insurer"].items())
            always = {k: f for k, f in v["base"].items() if not f["cond"]}
            when = {k: f for k, f in v["base"].items() if f["cond"]}
            print(f"  {fn}(): บังคับทุกบริษัท {len(always)} ช่อง"
                  + (f" (+{len(when)} มีเงื่อนไข)" if when else "")
                  + (f" | รายบริษัท → {per}" if per else ""))
            for cid, f in always.items():
                print(f"      - {f['label'] or cid}")
            for cid, f in when.items():
                print(f"      ~ {f['label'] or cid}   ← เมื่อ {' และ '.join(f['cond'])}")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n✓ เขียน {out} ({out.stat().st_size:,} bytes)")

    if a.diff:
        print("\n=== เทียบกับลิสต์ที่ใช้อยู่ในโปรเจกต์ ===")
        for r in diff_project(spec):
            if r.get("error"):
                print(f"  ⚠️ {r['dropdown']}: {r['error']}")
            elif r["missing"]:
                print(f"  ❌ {r['dropdown']} → {r['var']}: EMCS {r['emcs']} / เรา {r['project']}"
                      f" — ขาด {len(r['missing'])}: {', '.join(r['missing'][:6])}"
                      + (" ..." if len(r["missing"]) > 6 else ""))
            else:
                print(f"  ✓ {r['dropdown']} → {r['var']}: ครบ {r['emcs']} ป้าย")


if __name__ == "__main__":
    main()
