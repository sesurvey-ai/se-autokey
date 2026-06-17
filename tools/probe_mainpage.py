"""Probe หน้ารายการงาน EMCS (frmMainPage) — หาช่องค้นหา + โครงตาราง
เพื่อสร้างด่านกันเปิดเรื่องซ้ำ (อ่านอย่างเดียว ไม่บันทึก/ไม่สร้างอะไร)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autokey import emcs  # noqa: E402
from autokey.browser import log, make_driver  # noqa: E402
from autokey.config import load_config  # noqa: E402

JS_INPUTS = r"""
return Array.from(document.querySelectorAll("input[id], select[id]"))
  .filter(e => e.offsetParent !== null)
  .map(e => ({id: e.id, tag: e.tagName.toLowerCase(), type: e.type || "",
              value: (e.value || "").slice(0, 30)}));
"""

JS_BUTTONS = r"""
return Array.from(document.querySelectorAll(
  "input[type=button], input[type=submit], input[type=image], button, a[id]"))
  .filter(e => e.offsetParent !== null)
  .map(e => ({id: e.id || "", text: (e.value || e.innerText || e.title || "")
              .trim().slice(0, 40)}))
  .filter(b => b.id || b.text);
"""

# โครงตารางรายการงาน: หา table ที่มีลิงก์ e-Survey (S684...)
JS_GRID = r"""
const out = [];
document.querySelectorAll("table[id]").forEach(t => {
  const txt = (t.innerText || "");
  if (txt.includes("e-Survey") || /S\d{11}/.test(txt)) {
    const rows = t.querySelectorAll("tr");
    const sample = [];
    for (let i = 0; i < Math.min(rows.length, 3); i++) {
      sample.push(Array.from(rows[i].querySelectorAll("td, th"))
        .map(c => c.innerText.trim().slice(0, 25)));
    }
    out.push({id: t.id, rows: rows.length, sample: sample});
  }
});
return out.slice(0, 5);
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
        emcs.login(driver, cfg)  # จบที่ frmMainPage
        import time
        time.sleep(3)

        result["inputs"] = driver.execute_script(JS_INPUTS)
        result["buttons"] = driver.execute_script(JS_BUTTONS)
        result["grids"] = driver.execute_script(JS_GRID)

        log(f"inputs: {len(result['inputs'])} / buttons: {len(result['buttons'])} "
            f"/ grид candidates: {len(result['grids'])}")
        for i in result["inputs"][:25]:
            log(f"   INPUT {i['id']} ({i['type']}) = '{i['value']}'")
        for b in result["buttons"][:20]:
            log(f"   BTN   {b['id']} :: {b['text']}")
        for g in result["grids"]:
            log(f"   GRID  {g['id']} rows={g['rows']}")
    finally:
        out = cfg.runs_dir / "emcs_mainpage_dump.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nบันทึก → {out}")
        driver.quit()


if __name__ == "__main__":
    main()
