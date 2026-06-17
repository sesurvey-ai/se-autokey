"""Probe หาเงื่อนไขปลดล็อกส่วนรถคู่กรณีบนฟอร์ม EMCS — ไม่บันทึกใดๆ

เช็คสถานะ disabled ของ control โซนคู่กรณี, หา script ที่อ้างถึง ddlOpo_Count,
และลอง toggle ที่ปลอดภัย (checkbox/dropdown = viewstate เท่านั้น)
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium.webdriver.common.by import By  # noqa: E402

from autokey import emcs  # noqa: E402
from autokey.browser import log, make_driver, wait_visible  # noqa: E402
from autokey.claim_data import ClaimData  # noqa: E402
from autokey.config import load_config  # noqa: E402

WATCH_IDS = [
    "ddlOpo_Count", "chkOpo_Result_0", "chkOpo_Result_1", "chkOpo_Result_2",
    "txtOpo_Pay", "txtOpo_Recovery_Amount", "btnSave_Opponent",
    "dtlOpo_ctl00_wuOpo_txtOpo_Name", "dtlOpo_ctl00_wuOpo_txtCar_RegNo",
    "dtlOpo_ctl00_wuOpo_ddlCmfg", "dtlOpo_ctl00_wuOpo_chkHas_KFK",
]

JS_STATE = r"""
return arguments[0].map(id => {
  const e = document.getElementById(id);
  if (!e) return id + ": missing";
  return id + ": disabled=" + e.disabled +
         " visible=" + (e.offsetParent !== null);
});
"""

JS_LABEL_NEAR = r"""
const kw = arguments[0];
const out = [];
document.querySelectorAll("td").forEach(td => {
  const t = (td.innerText || "").trim();
  if (t.includes(kw) && t.length < 60) {
    const row = td.parentElement;
    const inp = row ? row.querySelector("select, input") : null;
    out.push({label: t, control: inp ? inp.id : "",
              disabled: inp ? inp.disabled : null});
  }
});
return out.slice(0, 8);
"""


def dump_state(driver, tag):
    log(f"--- สถานะ ({tag}) ---")
    for line in driver.execute_script(JS_STATE, WATCH_IDS):
        log(f"   {line}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    cfg = load_config()
    data = ClaimData.load("runs/2026013144130.json")
    driver = make_driver(detach=False)

    try:
        emcs.login(driver, cfg)
        emcs.new_report(driver)
        wait_visible(driver, By.ID, "rdoSurv_Claim_Type", 30)
        time.sleep(1)

        dump_state(driver, "ฟอร์มเปล่า")

        # control ใกล้ label สำคัญ
        for kw in ("เงื่อนไขฝ่ายถูก", "จำนวนรถคู่กรณี", "เสียหายหนัก"):
            log(f"ใกล้ '{kw}': "
                + json.dumps(driver.execute_script(JS_LABEL_NEAR, kw),
                             ensure_ascii=False))

        # script ที่พูดถึง ddlOpo_Count (หา trigger ฝั่ง client)
        src = driver.page_source
        hits = [m.start() for m in re.finditer("ddlOpo_Count", src)]
        log(f"ddlOpo_Count โผล่ใน HTML {len(hits)} ครั้ง")
        for h in hits[:6]:
            snippet = src[max(0, h - 160):h + 120]
            snippet = re.sub(r"\s+", " ", snippet)
            log(f"   ...{snippet}...")

        # ลองเลือกประเภทเคลม + ผลคดี + ลักษณะความเสียหาย 'ชนคู่กรณีเสียหาย'
        emcs.fill_claim_type(driver, "1")
        time.sleep(2)
        emcs.fill_verdict(driver, data)
        time.sleep(2)
        try:
            from selenium.webdriver.support.ui import Select
            Select(driver.find_element(By.ID, "ddlLoss_ID")) \
                .select_by_visible_text("ชนคู่กรณีเสียหาย")
            time.sleep(2)
            log("เลือกลักษณะความเสียหาย 'ชนคู่กรณีเสียหาย' แล้ว")
        except Exception as e:
            log(f"เลือก ddlLoss_ID ไม่ได้: {type(e).__name__}")

        dump_state(driver, "หลังเลือกประเภท+ผลคดี+ลักษณะความเสียหาย")

        # toggle checkbox รับหลักฐานจากคู่กรณี (viewstate เท่านั้น)
        try:
            driver.find_element(By.ID, "chkOpo_Result_1").click()
            time.sleep(2)
            log("ติ๊ก chkOpo_Result_1 แล้ว")
        except Exception as e:
            log(f"ติ๊ก chkOpo_Result_1 ไม่ได้: {type(e).__name__}")

        dump_state(driver, "หลัง toggle checkbox")
    finally:
        driver.quit()  # ไม่บันทึก
        print("\nจบ probe (ไม่มีการบันทึก)")


if __name__ == "__main__":
    main()
