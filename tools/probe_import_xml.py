"""Probe โหมด "นำเข้าข้อมูลแบบ XML" ของ EMCS (ปุ่ม imbFileImport_XML หน้ารายการงาน)

เป้าหมาย: เก็บกลไก flow การ import SURV_REPORT XML เพื่อสร้างโหมด --import-xml
(ทางเลือกแก้ปัญหาความเสียหาย >8 — ฟอร์ม import มีช่อง free-text เยอะกว่า cmdNewReport)

ปลอดภัยโดย default (--inspect-only โดยปริยาย): แค่ dump ปุ่ม + file input + ถ้า
onclick เป็น __doPostBack (ไม่เปิด OS dialog) ค่อยคลิกดูหน้า import — **ไม่อัปไฟล์
ไม่สร้าง draft**. ใส่ --do-upload เพื่อทดลองอัปจริง (จะ "สร้าง draft ใหม่ ลบไม่ได้")
→ dump ฟอร์มหลัง import: ฟิลด์ที่ระบบเติมให้ + ช่องความเสียหายทั้งหมด — ไม่กดส่งงาน

ใช้:
  python tools\\probe_import_xml.py                       # inspect อย่างเดียว (ปลอดภัย)
  python tools\\probe_import_xml.py --do-upload           # อัปไฟล์จริง (สร้าง draft)
  python tools\\probe_import_xml.py --xml runs\\xml\\<ไฟล์>.txt --do-upload
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402

from autokey import emcs  # noqa: E402
from autokey.browser import accept_alert, log, make_driver  # noqa: E402
from autokey.config import load_config  # noqa: E402

# ---- JS dumpers (อ่านอย่างเดียว) ----

# ปุ่ม/อิลิเมนต์ตาม id — เอา outerHTML + onclick + href มาดูว่ามันทำงานยังไง
JS_ELEMENT = r"""
var e = document.getElementById(arguments[0]);
if (!e) return null;
return {id: e.id, tag: e.tagName.toLowerCase(), type: e.type || "",
        onclick: e.getAttribute("onclick") || "",
        href: e.getAttribute("href") || "",
        visible: e.offsetParent !== null,
        outer: (e.outerHTML || "").slice(0, 600)};
"""

# input[type=file] ทั้งหน้า (กลไกอัปไฟล์)
JS_FILE_INPUTS = r"""
return Array.prototype.slice.call(
  document.querySelectorAll('input[type=file]')).map(function(e){
    return {id: e.id, name: e.name || "", visible: e.offsetParent !== null,
            disabled: e.disabled, onclick: e.getAttribute("onclick") || "",
            accept: e.getAttribute("accept") || "",
            outer: (e.outerHTML || "").slice(0, 300)};
  });
"""

# ปุ่ม/ลิงก์ที่เกี่ยวกับ XML / นำเข้า / ตกลง
JS_BUTTONS = r"""
return Array.prototype.slice.call(document.querySelectorAll(
  "input[type=button], input[type=submit], input[type=image], button, a[id]"))
  .filter(function(e){ return e.offsetParent !== null; })
  .map(function(e){ return {id: e.id || "",
      text: (e.value || e.innerText || e.title || e.alt || "").trim().slice(0,50),
      onclick: (e.getAttribute("onclick")||"").slice(0,120)}; })
  .filter(function(b){ return b.id || b.text; });
"""

# inputs/selects ที่มองเห็น + ค่า (สำหรับดูว่า import เติม field ไหนให้)
JS_FIELDS = r"""
return Array.prototype.slice.call(
  document.querySelectorAll("input[id], select[id], textarea[id]"))
  .filter(function(e){ return e.offsetParent !== null
      && e.type !== "hidden" && e.type !== "button" && e.type !== "image"; })
  .map(function(e){
    var v = "";
    if (e.tagName.toLowerCase() === "select") {
      var o = e.options[e.selectedIndex];
      v = o ? (o.text || "").trim() : "";
    } else if (e.type === "radio" || e.type === "checkbox") {
      v = e.checked ? "[x]" : "[ ]";
    } else { v = (e.value || ""); }
    return {id: e.id, tag: e.tagName.toLowerCase(), type: e.type || "",
            value: String(v).slice(0, 60)};
  });
"""

# ทุก element ที่คลิกได้ / img / input — รวมที่ซ่อนด้วย (หาปุ่มเลือกไฟล์ + นำเข้า)
JS_ALL_INTERACTIVE = r"""
return Array.prototype.slice.call(document.querySelectorAll(
  "input, img, a, button, [onclick]")).map(function(e){
    return {id: e.id || "", tag: e.tagName.toLowerCase(), type: e.type || "",
            name: e.name || "", visible: e.offsetParent !== null,
            src: (e.getAttribute("src")||"").split("/").pop().slice(0,30),
            alt: (e.alt||e.title||"").slice(0,40),
            value: (e.value||"").slice(0,30),
            text: (e.innerText||"").trim().slice(0,40),
            onclick: (e.getAttribute("onclick")||"").slice(0,160)}; })
  .filter(function(e){ return e.id || e.onclick || e.alt || e.src || e.text; })
  .slice(0, 120);
"""

# ทุก input/select/textarea รวมที่ซ่อน (ดู field ทั้งหมดของหน้า import)
JS_ALL_INPUTS = r"""
return Array.prototype.slice.call(
  document.querySelectorAll("input[id], select[id], textarea[id]")).map(function(e){
    return {id: e.id, tag: e.tagName.toLowerCase(), type: e.type || "",
            visible: e.offsetParent !== null,
            value: (e.value||"").slice(0,40)}; }).slice(0, 120);
"""

# ช่องความเสียหาย (free-text + checklist) — นับจำนวน + ดู ctl index
JS_DAMAGE = r"""
return Array.prototype.slice.call(document.querySelectorAll(
  '[id*="Dam_Name"], [id*="dgvOtherDamage"], [id*="dgvDamage_List"]'))
  .map(function(e){ return {id: e.id, tag: e.tagName.toLowerCase(),
      type: e.type || "", visible: e.offsetParent !== null}; })
  .slice(0, 200);
"""


def _dump(driver, label):
    """อ่านสภาพหน้าปัจจุบัน (ปลอดภัย อ่านอย่างเดียว)"""
    out = {"label": label, "url": driver.current_url, "title": driver.title}
    try:
        out["file_inputs"] = driver.execute_script(JS_FILE_INPUTS)
    except Exception as e:
        out["file_inputs_err"] = f"{type(e).__name__}: {e}"
    try:
        out["buttons"] = driver.execute_script(JS_BUTTONS)
    except Exception as e:
        out["buttons_err"] = f"{type(e).__name__}: {e}"
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--xml", type=Path, default=None,
                    help="ไฟล์ SURV_REPORT XML ที่จะอัป (default = ตัวแรกใน runs/xml/)")
    ap.add_argument("--do-upload", action="store_true",
                    help="อัปไฟล์จริง (สร้าง draft ใหม่ ลบไม่ได้) — default = inspect เฉยๆ")
    args = ap.parse_args()

    cfg = load_config()
    xml_path = args.xml
    if xml_path is None:
        cands = sorted((cfg.runs_dir / "xml").glob("*.txt"))
        if not cands:
            raise SystemExit("ไม่พบไฟล์ XML ใน runs/xml/ — ระบุ --xml")
        xml_path = cands[0]
    xml_path = xml_path.resolve()
    if not xml_path.exists():
        raise SystemExit(f"ไม่พบไฟล์ XML: {xml_path}")
    log(f"ไฟล์ XML ที่จะใช้: {xml_path}")

    driver = make_driver(detach=False)
    result = {"xml": str(xml_path), "do_upload": args.do_upload, "stages": []}
    try:
        emcs.login(driver, cfg)   # จบที่ frmMainPage
        time.sleep(2)

        # 1) ปุ่มนำเข้า XML + file inputs + ปุ่มบนหน้ารายการ
        btn = driver.execute_script(JS_ELEMENT, "imbFileImport_XML")
        result["import_button"] = btn
        log(f"imbFileImport_XML: {json.dumps(btn, ensure_ascii=False)}")
        stage0 = _dump(driver, "mainpage_before_click")
        result["stages"].append(stage0)
        log(f"   file inputs (ก่อนคลิก): {len(stage0.get('file_inputs', []))}")
        for fi in stage0.get("file_inputs", []):
            log(f"      FILE {fi['id']} visible={fi['visible']} "
                f"disabled={fi['disabled']}")

        onclick = (btn or {}).get("onclick", "")
        btag = (btn or {}).get("tag", "")
        btype = (btn or {}).get("type", "")
        # OS file dialog เกิดเฉพาะตอนคลิก <input type=file> หรือ JS เรียก .click() ใส่มัน
        opens_dialog = (".click()" in onclick) or ("type=file" in onclick)
        # ปลอดภัยต่อการคลิก (postback/submit ฝั่ง server — ไม่เปิด OS dialog):
        #  __doPostBack / <a> / ASP.NET ImageButton (<input type=image|submit|button>
        #  ที่ไม่มี JS เปิด file dialog — onclick ว่าง = submit form ตามปกติ)
        is_postback = (not opens_dialog) and (
            "__doPostBack" in onclick or btag == "a"
            or (btag == "input" and btype in ("image", "submit", "button")))

        # 2) ถ้าเป็น postback/submit (ปลอดภัย ไม่เปิด OS dialog) → คลิกดูหน้า import
        if btn and is_postback:
            log("imbFileImport_XML = postback/submit (ปลอดภัย) → คลิกเพื่อดูหน้า import")
            driver.find_element(By.ID, "imbFileImport_XML").click()
            try:
                accept_alert(driver, timeout=4)   # เผื่อมี JS confirm
            except Exception:
                pass
            time.sleep(3)
            stage1 = _dump(driver, "after_import_click")
            try:
                stage1["fields"] = driver.execute_script(JS_FIELDS)
                stage1["all_inputs"] = driver.execute_script(JS_ALL_INPUTS)
                stage1["interactive"] = driver.execute_script(JS_ALL_INTERACTIVE)
            except Exception as e:
                stage1["dump_err"] = f"{type(e).__name__}: {e}"
            result["stages"].append(stage1)
            log(f"   หลังคลิก: url={stage1['url']}")
            log(f"   file inputs (หลังคลิก): {len(stage1.get('file_inputs', []))}")
            for fi in stage1.get("file_inputs", []):
                log(f"      FILE {fi['id']} visible={fi['visible']} "
                    f"disabled={fi['disabled']} :: {fi['outer'][:120]}")
            log(f"   all inputs/selects: {len(stage1.get('all_inputs', []))}")
            for f in stage1.get("all_inputs", []):
                log(f"      IN  {f['id']} ({f['type']}) vis={f['visible']} "
                    f"= '{f['value']}'")
            log(f"   interactive (clickable/img): {len(stage1.get('interactive', []))}")
            for b in stage1.get("interactive", []):
                if b["tag"] in ("img", "a", "button") or b["type"] in (
                        "image", "button", "submit") or b["onclick"]:
                    log(f"      ACT {b['tag']}#{b['id']} type={b['type']} "
                        f"vis={b['visible']} src={b['src']} alt='{b['alt']}' "
                        f"txt='{b['text']}' onclick='{b['onclick']}'")
        elif btn:
            log(f"⚠️ imbFileImport_XML onclick ไม่ใช่ postback ({onclick[:80]}) — "
                "อาจเปิด OS file dialog ถ้าคลิก → ไม่คลิกอัตโนมัติ ดู outerHTML แทน")
        else:
            log("⚠️ ไม่พบปุ่ม imbFileImport_XML บนหน้านี้ — ดู buttons ใน dump")
            for b in stage0.get("buttons", []):
                if "XML" in b["text"] or "นำเข้า" in b["text"] or "xml" in b["id"].lower():
                    log(f"      CANDIDATE {b['id']} :: {b['text']} :: {b['onclick']}")

        # 3) อัปไฟล์จริง (ต่อเมื่อ --do-upload) — หา file input ที่โผล่ → send_keys →
        #    หาปุ่มนำเข้า/ตกลง → จัดการ alert → dump ฟอร์มหลัง import (ไม่กดส่งงาน)
        if args.do_upload:
            log("=== --do-upload: อัปไฟล์จริง ตามขั้นตอน user (จะสร้าง draft) ===")
            # 1) บริษัทประกัน = 1059 ไอโออิกรุงเทพ (selectpicker: native select ซ่อน → JS)
            log("1) เลือกบริษัทประกัน = 1059 (ไอโออิกรุงเทพ)")
            driver.execute_script(
                "var s=document.getElementById('ddlInsurerNameMajor');"
                "s.value='1059';s.dispatchEvent(new Event('change',{bubbles:true}));"
                "if(window.jQuery&&jQuery.fn.selectpicker)"
                "jQuery('#ddlInsurerNameMajor').selectpicker('refresh');")
            # 2) สาขา (โหลดหลังเลือกบริษัท) — เลือก 'กรุงเทพ' (value ขึ้นต้น 1778) /
            #    ไม่งั้น option แรกที่ value != '0'
            br_val = ""
            for _ in range(20):
                opts = driver.execute_script(
                    "return Array.prototype.map.call("
                    "document.getElementById('ddlInsurerBRList').options,"
                    "function(o){return {v:o.value,t:(o.text||'').trim()};});")
                real = [o for o in opts if o["v"] and o["v"] != "0"]
                if real:
                    br_val = next((o["v"] for o in real
                                   if o["v"].startswith("1778") or "กรุงเทพ" in o["t"]),
                                  real[0]["v"])
                    break
                time.sleep(0.5)
            log(f"2) เลือกสาขา = {br_val or '(ไม่พบ option)'}")
            if br_val:
                driver.execute_script(
                    "var s=document.getElementById('ddlInsurerBRList');"
                    "s.value=arguments[0];"
                    "s.dispatchEvent(new Event('change',{bubbles:true}));"
                    "if(window.jQuery&&jQuery.fn.selectpicker)"
                    "jQuery('#ddlInsurerBRList').selectpicker('refresh');", br_val)
                time.sleep(1)
            # 3) เลือกไฟล์ → send_keys path ตรงเข้า inpImport (file ซ่อน — ไม่เปิด dialog)
            log(f"3) send_keys ไฟล์เข้า inpImport: {xml_path}")
            el = driver.find_element(By.ID, "inpImport")
            el.send_keys(str(xml_path))
            time.sleep(1)
            fn = driver.execute_script(
                "return document.getElementById('txtFileName').value;")
            log(f"   txtFileName = '{fn}'")
            # 4) กดปุ่มนำเข้าข้อมูล btnImport (button ซ่อน display:none → JS click)
            log("4) กดปุ่มนำเข้าข้อมูล (btnImport)")
            driver.execute_script("document.getElementById('btnImport').click();")
            try:
                a = accept_alert(driver, timeout=10)
                log(f"   [alert] {a}")
            except Exception:
                pass
            time.sleep(2)
            try:
                for sb in driver.find_elements(By.CSS_SELECTOR, ".swal-button"):
                    if sb.is_displayed():
                        log(f"   ปิด SweetAlert: '{sb.text}'")
                        sb.click()
                        break
            except Exception:
                pass
            time.sleep(5)
            if True:
                # dump ฟอร์มหลัง import
                post = _dump(driver, "after_import_upload")
                try:
                    post["fields"] = driver.execute_script(JS_FIELDS)
                    post["damage"] = driver.execute_script(JS_DAMAGE)
                except Exception as e:
                    post["fields_err"] = f"{type(e).__name__}: {e}"
                result["stages"].append(post)
                log(f"   หลัง import: url={post['url']}")
                log(f"   ฟิลด์ที่มองเห็น: {len(post.get('fields', []))} / "
                    f"ช่องความเสียหาย: {len(post.get('damage', []))}")
                # โชว์ field สำคัญ
                want = {"txtCar_RegNo", "txtCModel2", "ddlCType", "ddlCMFG",
                        "txtDri_Name01", "txtDri_LastName01", "ddlDri_Title_ID",
                        "txtRef_Claim_No", "txtSurv_JobNo", "rdoGender_0"}
                for f in post.get("fields", []):
                    if f["id"] in want:
                        log(f"      FIELD {f['id']} ({f['type']}) = '{f['value']}'")
                # นับ ctl index ของช่อง free-text ความเสียหาย
                dam_ids = [d["id"] for d in post.get("damage", [])
                           if "txtDam_Name" in d["id"]]
                log(f"   ช่อง txtDam_Name (บน main form): {len(dam_ids)} ช่อง")
                for d in dam_ids[:40]:
                    log(f"      DAM {d}")

                # 5) เปิด popup ความเสียหาย (btnPopUp_DamList) → dump โครงสร้างจริง
                #    (free-text 30 ช่อง? มี checklist ไหม?) — ไม่กรอก ไม่บันทึก
                try:
                    handles_before = set(driver.window_handles)
                    log("5) เปิด popup ความเสียหาย (btnPopUp_DamList)")
                    driver.find_element(By.ID, "btnPopUp_DamList").click()
                    time.sleep(2)
                    new = [h for h in driver.window_handles if h not in handles_before]
                    if new:
                        driver.switch_to.window(new[0])
                        time.sleep(2)
                    dmg = _dump(driver, "damage_popup_after_import")
                    dmg["damage"] = driver.execute_script(JS_DAMAGE)
                    dmg["all_inputs"] = driver.execute_script(JS_ALL_INPUTS)
                    result["stages"].append(dmg)
                    free = [d["id"] for d in dmg["damage"]
                            if "txtDam_Name" in d["id"]]
                    chk = [d["id"] for d in dmg["damage"]
                           if "chbDam_Name" in d["id"]]
                    log(f"   popup url: {dmg['url']}")
                    log(f"   free-text txtDam_Name: {len(free)} | "
                        f"checklist chbDam_Name: {len(chk)}")
                    for fid in free:
                        log(f"      FREE {fid}")
                    for cid in chk[:30]:
                        log(f"      CHK  {cid}")
                except Exception as e:
                    log(f"   ⚠️ เปิด popup ความเสียหายไม่ได้: {type(e).__name__}: {e}")
    finally:
        out = cfg.runs_dir / "emcs_import_xml_dump.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nบันทึก dump → {out}")
        if args.do_upload:
            print("⚠️ อาจมี draft ใหม่ค้างใน EMCS (ลบไม่ได้) — ตรวจหน้ารายการงาน")
        print("browser เปิดค้างให้ตรวจ — ปิดเองเมื่อเสร็จ")
        # ไม่ quit อัตโนมัติ ให้ดูหน้าจอได้ (detach=False = ปิดเมื่อสคริปต์จบ
        # แต่เราเปิด input ค้างเพื่อให้ดูก่อน)
        try:
            input("กด Enter เพื่อปิด browser...")
        except Exception:
            pass
        driver.quit()


if __name__ == "__main__":
    main()
