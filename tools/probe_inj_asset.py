r"""Probe หน้า "ผู้บาดเจ็บ" (Tab 5) + "ทรัพย์สิน" (Tab 6) บน EMCS — อ่านอย่างเดียว

เปิด draft เดิมของเคลม (ที่บันทึกหน้าหลักแล้ว → 2 ส่วนนี้ปลดล็อก) → คลิกเมนู
imbInjure_Person / imbAsset → dump field id/ประเภท/label/สถานะ ของ control ที่
"มองเห็น" บนหน้า + เซฟ HTML ไว้แกะ structure (บล็อกซ้ำต่อคน/ชิ้น, dropdown จำนวน,
ปุ่มบันทึก) — ไม่กรอก/ไม่บันทึก/ไม่ส่งงานใดๆ

ใช้: python tools\probe_inj_asset.py [เลขเคลม]   (default 2026013048453)
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium.webdriver.common.by import By  # noqa: E402

from autokey import emcs  # noqa: E402
from autokey.browser import (  # noqa: E402
    click_retry, log, log_plain, make_driver, wait_clickable, wait_present,
)
from autokey.config import load_config  # noqa: E402

# dump เฉพาะ control ที่ "มองเห็น" (ส่วนที่เพิ่งคลิกเปิด) — id/tag/type/label/ค่า
JS_DUMP_VISIBLE = r"""
const out = [];
document.querySelectorAll("input, select, textarea").forEach(e => {
  const id = e.id || "";
  if (!id) return;
  if (e.offsetParent === null && e.type !== "hidden") {
    // ข้ามตัวที่ซ่อน (ยกเว้น hidden ที่อาจสำคัญ — แต่ตัด hidden ทิ้งเพื่อความสะอาด)
    return;
  }
  if (e.type === "hidden") return;
  let label = "";
  const row = e.closest("tr");
  if (row) {
    const tds = [...row.querySelectorAll("td,th")]
      .map(td => (td.innerText || "").trim())
      .filter(t => t && t.length < 45);
    label = tds[0] || "";
  }
  let opts = undefined;
  if (e.tagName === "SELECT") {
    opts = [...e.options].slice(0, 30).map(o => o.text.trim());
  }
  out.push({id, tag: e.tagName, type: e.type || "",
            label, disabled: e.disabled,
            value: (e.value || "").slice(0, 30),
            options: opts});
});
return out;
"""

# ปุ่ม/dropdown ที่คาดว่ามี (id เดาจากแพทเทิร์นคู่กรณี ddlOpo_Count/btnSave_Opponent)
JS_CANDIDATES = r"""
const kws = arguments[0];
const out = [];
document.querySelectorAll("*[id]").forEach(e => {
  const id = e.id;
  if (kws.some(k => id.toLowerCase().includes(k.toLowerCase()))) {
    out.push(id + "  <" + e.tagName.toLowerCase() + ">"
             + (e.disabled !== undefined ? " disabled=" + e.disabled : "")
             + " vis=" + (e.offsetParent !== null));
  }
});
return out.slice(0, 60);
"""


def probe_section(driver, menu_id, name, kws, out_dir, ts):
    log_plain("")
    log(f"=== {name} (เมนู {menu_id}) ===")
    try:
        click_retry(driver, By.ID, menu_id)
        time.sleep(2)  # รอ section render
    except Exception as e:
        log(f"   ⚠️ คลิกเมนู {menu_id} ไม่ได้: {type(e).__name__}: {e}")
        return None

    fields = driver.execute_script(JS_DUMP_VISIBLE)
    log(f"   control ที่มองเห็น: {len(fields)} ตัว")
    for f in fields:
        opt = f" opts={f['options'][:6]}" if f.get("options") else ""
        log_plain(f"   - {f['id']:40} [{f['tag'].lower()}/{f['type']}] "
                  f"'{f['label']}' dis={f['disabled']}{opt}")

    cands = driver.execute_script(JS_CANDIDATES, kws)
    log(f"   id ที่ตรง keyword {kws}: {len(cands)} ตัว")
    for c in cands:
        log_plain(f"     · {c}")

    # เซฟ HTML + json ไว้แกะ structure ละเอียด
    html_path = out_dir / f"probe_{name}_{ts}.html"
    html_path.write_text(driver.page_source, encoding="utf-8")
    log(f"   💾 HTML → {html_path}")
    return {"menu": menu_id, "fields": fields, "candidates": cands}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    claim = sys.argv[1] if len(sys.argv) > 1 else "2026013048453"
    cfg = load_config()
    out_dir = cfg.runs_dir / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    driver = make_driver(detach=True)   # เปิดค้างไว้ให้ดูต่อ
    try:
        emcs.login(driver, cfg)
        reports = emcs.find_existing_reports(driver, claim)
        if not reports:
            log(f"❌ ไม่พบเรื่องเดิมของเคลม {claim} ใน EMCS — สร้าง draft ก่อน")
            return
        target = emcs._pick_draft_report(reports)
        log(f"เปิด draft {target} ของเคลม {claim} (อ่านอย่างเดียว ไม่แก้/ไม่ส่ง)")
        wait_clickable(
            driver, By.XPATH, f"//a[normalize-space(text())='{target}']", 20
        ).click()
        wait_present(driver, By.ID, "wuMenuPage1_imbInjure_Person", 20)

        result = {"claim": claim, "esurvey": target, "ts": ts, "sections": {}}
        result["sections"]["injure"] = probe_section(
            driver, "wuMenuPage1_imbInjure_Person", "injure",
            ["Inj", "Injure", "Wound", "Hurt", "Person", "Patient", "Hos"],
            out_dir, ts)
        result["sections"]["asset"] = probe_section(
            driver, "wuMenuPage1_imbAsset", "asset",
            ["Asset", "Prop", "Damage", "Owner"], out_dir, ts)

        json_path = out_dir / f"probe_inj_asset_{ts}.json"
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        log_plain("")
        log(f"💾 สรุป field → {json_path}")
        log("จบ probe (ไม่มีการบันทึก/ส่งงาน) — browser เปิดค้างให้ตรวจ")
    finally:
        pass  # detach=True → ไม่ quit, เปิดค้างไว้ดู


if __name__ == "__main__":
    main()
