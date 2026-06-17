"""Probe ฟอร์ม "สร้างงานใหม่" ของ EMCS — dump โครงสร้าง field ทั้งหมด

สำคัญ: เปิดฟอร์มเปล่าแล้วอ่าน DOM เท่านั้น **ไม่กดบันทึก/Update/GEN ใดๆ**
จึงไม่มีข้อมูลถูกสร้างในระบบ (ASP.NET สร้าง record ตอนกด Save เท่านั้น)

รัน:    python tools/probe_emcs.py
ผลลัพธ์: runs/emcs_form_dump.json
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium.webdriver.common.by import By  # noqa: E402

from autokey import emcs  # noqa: E402
from autokey.browser import log, make_driver, wait_visible  # noqa: E402
from autokey.config import load_config  # noqa: E402

JS_DUMP_FIELDS = r"""
const out = [];
document.querySelectorAll("input[id], select[id], textarea[id]").forEach(e => {
  const f = {id: e.id, tag: e.tagName.toLowerCase(), type: e.type || "",
             value: (e.value || "").slice(0, 40)};
  if (e.tagName === "SELECT") {
    f.options = Array.from(e.options).slice(0, 40).map(o => o.text.trim());
  }
  if (e.type === "radio" || e.type === "checkbox") { f.checked = e.checked; }
  out.push(f);
});
return out;
"""

JS_DUMP_BUTTONS = r"""
const out = [];
document.querySelectorAll(
  "input[type=button], input[type=submit], input[type=image], button, a[id]"
).forEach(e => {
  const txt = (e.value || e.innerText || e.title || e.alt || "").trim();
  if (!e.id && !txt) return;
  out.push({id: e.id || "", tag: e.tagName.toLowerCase(),
            text: txt.slice(0, 50)});
});
return out;
"""

JS_SECTION_TEXTS = r"""
const kw = arguments[0];
const seen = new Set();
const out = [];
document.querySelectorAll("td, span, legend, b, div").forEach(e => {
  const t = (e.innerText || "").trim();
  if (!t || t.length > 80) return;
  for (const k of kw) {
    if (t.includes(k) && !seen.has(t)) { seen.add(t); out.push(t); break; }
  }
});
return out.slice(0, 60);
"""


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config()
    driver = make_driver(detach=False)
    result = {}

    try:
        emcs.login(driver, cfg)
        emcs.new_report(driver)
        wait_visible(driver, By.ID, "rdoSurv_Claim_Type", 30)
        time.sleep(2)

        # dump สถานะเริ่มต้นก่อน (ยังไม่เลือกประเภทเคลม)
        result["fields_initial"] = driver.execute_script(JS_DUMP_FIELDS)
        log(f"ฟอร์มเริ่มต้น: {len(result['fields_initial'])} fields")

        # เลือกประเภทเคลม "1" (เคลมสด) เพื่อให้ section ที่ซ่อนอยู่โผล่
        # (เป็นแค่ postback เปลี่ยนหน้าจอ ไม่มีการบันทึก)
        try:
            container = driver.find_element(By.ID, "rdoSurv_Claim_Type")
            radios = container.find_elements(By.TAG_NAME, "input")
            radios[0].click()
            time.sleep(3)
            log("เลือกประเภทเคลมช่องแรก (เคลมสด) แล้ว")
        except Exception as e:
            log(f"เลือกประเภทเคลมไม่ได้: {e}")

        result["fields_after_type1"] = driver.execute_script(JS_DUMP_FIELDS)
        result["buttons"] = driver.execute_script(JS_DUMP_BUTTONS)
        result["sections"] = driver.execute_script(
            JS_SECTION_TEXTS,
            ["คู่กรณี", "KFK", "ลักษณะความเสียหาย", "ฝ่ายถูก", "ฝ่ายผิด",
             "เรียกร้อง", "ทรัพย์สิน", "บาดเจ็บ", "ประเภทเคลม"],
        )

        ids_before = {f["id"] for f in result["fields_initial"]}
        new_after = [f for f in result["fields_after_type1"]
                     if f["id"] not in ids_before]
        result["fields_new_after_type1"] = new_after

        log(f"หลังเลือกประเภทเคลม: {len(result['fields_after_type1'])} fields "
            f"(ใหม่ {len(new_after)})")
        log(f"ปุ่ม: {len(result['buttons'])} / section ที่เกี่ยว: "
            f"{len(result['sections'])}")

        # คัด field ที่ชื่อส่อว่าเกี่ยวกับคู่กรณี
        import re as _re
        pat = _re.compile(r"(opo|oppo|other|tp_|_tp|kfk|loss|cau)", _re.I)
        result["third_party_candidates"] = [
            f for f in result["fields_after_type1"] if pat.search(f["id"])
        ]
        log(f"field ที่เข้าข่ายคู่กรณี/KFK/Loss: "
            f"{len(result['third_party_candidates'])}")
    finally:
        out = cfg.runs_dir / "emcs_form_dump.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nบันทึกผล probe → {out}")
        driver.quit()  # ไม่บันทึกอะไรทั้งสิ้น — ปิดทิ้งได้เลย


if __name__ == "__main__":
    main()
