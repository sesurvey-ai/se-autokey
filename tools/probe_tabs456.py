"""Probe รอบ 2: เจาะ Tab 4-6 (diff id ก่อน/หลังคลิก) + สถานะ tab bar
+ ลองเปิด context menu (คลิกขวา) บนแถวเคลม ดูว่ามีเมนู export ไหม

รัน:    python tools/probe_tabs456.py --claim 2026013105763
ผลลัพธ์: runs/probe456_<เลขเคลม>.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium.webdriver.common.action_chains import ActionChains  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.common.keys import Keys  # noqa: E402

from autokey import isurvey  # noqa: E402
from autokey.browser import log, make_driver  # noqa: E402
from autokey.config import load_config  # noqa: E402

JS_ALL_IDS = (
    'return Array.from(document.querySelectorAll('
    '"input[id],textarea[id],select[id]")).map(e => e.id);'
)

JS_TAB_BAR = (
    'return Array.from(document.querySelectorAll("a.x-tab")).map(a => ({'
    'id: a.id, text: (a.innerText || "").trim(), cls: a.className}));'
)

JS_VISIBLE_MENU = (
    'return Array.from(document.querySelectorAll(".x-menu-item-text"))'
    '.filter(e => e.offsetParent !== null)'
    '.map(e => e.innerText.trim());'
)

JS_FIELD_INFO = r"""
const ids = arguments[0];
return ids.map(id => {
  const e = document.getElementById(id);
  if (!e) return {id: id};
  let label = "";
  const lab = document.getElementById(id.replace("-inputEl", "-labelEl"));
  if (lab) label = lab.innerText.trim();
  return {id: id, tag: e.tagName.toLowerCase(), label: label,
          value: (e.value || "").slice(0, 60)};
});
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
    driver = make_driver(detach=False)
    result = {"claim": args.claim, "row_context_menu": [], "tab_bar": [],
              "tab_diffs": {}}

    try:
        isurvey.ensure_logged_in(driver, cfg)
        isurvey.open_case_list(driver)

        # --- 1) คลิกขวาบนแถวเคลม ดู context menu (ห้ามกดอะไรในเมนู) ---
        isurvey._submit_search(driver, args.claim)
        row = None
        for _ in range(60):
            for r, c, v in isurvey._scan_rows(driver):
                if c == args.claim:
                    row = r
                    break
            if row is not None:
                break
            time.sleep(1)

        if row is not None:
            try:
                ActionChains(driver).context_click(row).perform()
                time.sleep(1.5)
                result["row_context_menu"] = driver.execute_script(JS_VISIBLE_MENU)
                log(f"context menu: {result['row_context_menu']}")
                ActionChains(driver).send_keys(Keys.ESCAPE).perform()
                time.sleep(0.5)
            except Exception as e:
                log(f"คลิกขวาไม่สำเร็จ: {e}")

        # --- 2) เปิดเคลม แล้วดู tab bar ---
        isurvey.find_and_open_claim(driver, args.claim, args.invoice)
        time.sleep(2)
        result["tab_bar"] = driver.execute_script(JS_TAB_BAR)
        for t in result["tab_bar"]:
            log(f"tab: '{t['text']}' disabled={'x-item-disabled' in t['cls']}")

        # --- 3) diff รายการ id ก่อน/หลังคลิก tab 4,5,6 ---
        for n in (4, 5, 6):
            before = set(driver.execute_script(JS_ALL_IDS))
            try:
                driver.find_element(
                    By.XPATH, isurvey.TAB_LINK_XPATH.format(n=n)
                ).click()
            except Exception as e:
                result["tab_diffs"][f"tab{n}"] = {"error": str(e)}
                log(f"tab{n}: คลิกไม่ได้ ({e})")
                continue
            time.sleep(4)
            after = set(driver.execute_script(JS_ALL_IDS))
            new_ids = sorted(after - before)[:250]
            fields = driver.execute_script(JS_FIELD_INFO, new_ids) if new_ids else []
            result["tab_diffs"][f"tab{n}"] = {"new_ids": new_ids, "fields": fields}
            log(f"tab{n}: id ใหม่ {len(new_ids)} ตัว")
    finally:
        out = cfg.runs_dir / f"probe456_{args.claim}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nบันทึกผล probe → {out}")
        driver.quit()


if __name__ == "__main__":
    main()
