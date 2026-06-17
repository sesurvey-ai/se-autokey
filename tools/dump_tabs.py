"""Discovery: สำรวจโครงสร้างหน้ารายละเอียดเคลม ISURVEY ทุก tab
เก็บ field id / label / grid / ตัวอย่างข้อมูลในตาราง + สแกนหาปุ่ม export

รัน:    python tools/dump_tabs.py --claim 2026013105763
ผลลัพธ์: runs/discovery_<เลขเคลม>.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium.webdriver.common.by import By  # noqa: E402

from autokey import isurvey  # noqa: E402
from autokey.browser import log, make_driver  # noqa: E402
from autokey.config import load_config  # noqa: E402

# ดึง input/textarea/select ทั้งหมดของ tab นั้น พร้อม label ของ ExtJS
JS_DUMP_FIELDS = r"""
const pref = arguments[0];
const out = [];
document.querySelectorAll("input[id], textarea[id], select[id]").forEach(e => {
  if (!e.id.startsWith(pref)) return;
  let label = "";
  const lab = document.getElementById(e.id.replace("-inputEl", "-labelEl"));
  if (lab) label = lab.innerText.trim();
  out.push({id: e.id, tag: e.tagName.toLowerCase(), type: e.type || "",
            label: label, value: (e.value || "").slice(0, 80)});
});
return out;
"""

# หา element ที่เป็น grid ของ tab นั้น
JS_DUMP_GRIDS = r"""
const pref = arguments[0];
const out = [];
document.querySelectorAll("[id]").forEach(e => {
  if (e.id.startsWith(pref) && e.id.includes("grid")) {
    out.push({id: e.id, cls: (e.className || "").toString().slice(0, 80)});
  }
});
return out;
"""

# อ่านแถวตัวอย่างจาก grid (ทุก cell ของแต่ละแถว)
JS_GRID_ROWS = r"""
const g = document.getElementById(arguments[0]);
if (!g) return null;
const rows = [];
g.querySelectorAll("table").forEach(t => {
  const cells = [];
  t.querySelectorAll("tr:first-child td").forEach(td =>
    cells.push(td.innerText.trim().slice(0, 60)));
  rows.push(cells);
});
return rows.slice(0, 5);
"""

# สแกนทั้งหน้า (รวมส่วนที่ซ่อน) หา control ที่น่าจะเป็นปุ่ม export/ดาวน์โหลด
JS_FIND_EXPORT = r"""
const kw = ["export", "Export", "EXPORT", "ดาวน์โหลด", "โหลดทั้งหมด", "zip", "Zip", "ZIP", "download", "Download"];
const out = [];
document.querySelectorAll("a, button, span, div").forEach(e => {
  const t = (e.innerText || "").trim();
  const tip = (e.getAttribute && (e.getAttribute("data-qtip") || e.getAttribute("title"))) || "";
  if ((!t || t.length > 60) && !tip) return;
  for (const k of kw) {
    if (t.includes(k) || tip.includes(k)) {
      out.push({id: e.id || "", tag: e.tagName, text: t.slice(0, 60),
                tip: tip.slice(0, 80), cls: (e.className || "").toString().slice(0, 70)});
      break;
    }
  }
});
return out.slice(0, 60);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim", required=True)
    ap.add_argument("--invoice", default="")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config()
    driver = make_driver(detach=False)  # ปิด browser เองตอนจบ
    result = {"claim": args.claim, "tabs": {}, "export_buttons": []}

    try:
        isurvey.ensure_logged_in(driver, cfg)
        isurvey.open_case_list(driver)
        isurvey.find_and_open_claim(driver, args.claim, args.invoice)

        for n in range(1, 9):
            if n > 1:
                driver.find_element(
                    By.XPATH, isurvey.TAB_LINK_XPATH.format(n=n)
                ).click()
            time.sleep(3)  # รอ tab โหลดข้อมูล

            pref = f"tab{n}_"
            fields = driver.execute_script(JS_DUMP_FIELDS, pref)
            grids = driver.execute_script(JS_DUMP_GRIDS, pref)
            grid_rows = {}
            for g in grids:
                if g["id"].endswith("-body"):
                    rows = driver.execute_script(JS_GRID_ROWS, g["id"])
                    if rows:
                        grid_rows[g["id"]] = rows
            result["tabs"][f"tab{n}"] = {
                "fields": fields, "grids": grids, "grid_rows": grid_rows,
            }
            log(f"tab{n}: fields={len(fields)} grids={len(grids)} "
                f"grid_rows={sum(len(v) for v in grid_rows.values())}")

        result["export_buttons"] = driver.execute_script(JS_FIND_EXPORT)
        log(f"ปุ่มที่เข้าข่าย export/ดาวน์โหลด: {len(result['export_buttons'])}")
    finally:
        out = cfg.runs_dir / f"discovery_{args.claim}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nบันทึกผล discovery → {out}")
        driver.quit()


if __name__ == "__main__":
    main()
