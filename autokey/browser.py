"""Chrome driver + helper กลางที่ใช้ร่วมกันทั้งฝั่ง ISURVEY และ EMCS"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from rapidfuzz import process, fuzz
from selenium import webdriver
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

# คะแนน fuzzy match ต่ำกว่านี้จะเตือนให้คนตรวจ (0-100)
FUZZY_WARN_SCORE = 60

# โหมดหน้าเว็บ: webui.py ตั้ง env SE_WEBUI=1 ตอนเรียก main.py
# → wait_for_manual_fill จะส่ง marker ออก stdout ให้หน้าเว็บโชว์ปุ่ม "ดำเนินการต่อ"
_WEBUI = os.environ.get("SE_WEBUI") == "1"
MANUAL_MARKER = "@@MANUAL_FILL@@"  # ต้องตรงกับค่าใน webui.py


# ไฟล์ log ของรอบนี้ (ตั้งค่าจาก main ผ่าน set_log_file)
_LOG_FILE = None


def set_log_file(path):
    """ให้ log() เขียนลงไฟล์ด้วย (นอกจากพิมพ์หน้าจอ)"""
    global _LOG_FILE
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _LOG_FILE = path


def _tee(text: str):
    if _LOG_FILE is None:
        return
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def log(msg: str):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line)
    _tee(line)


def log_plain(text: str):
    """พิมพ์ + เขียน log โดยไม่ใส่ timestamp (ใช้กับ banner/สรุปผล)"""
    print(text)
    _tee(text)


def save_debug_snapshot(driver, out_dir, tag: str = "error"):
    """เก็บ screenshot + HTML ของหน้าปัจจุบันไว้ debug ตอนเกิด error"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    try:
        png = out / f"{tag}_{ts}.png"
        driver.save_screenshot(str(png))
        saved.append(str(png))
    except Exception:
        pass
    try:
        html = out / f"{tag}_{ts}.html"
        html.write_text(driver.page_source, encoding="utf-8")
        saved.append(str(html))
    except Exception:
        pass
    if saved:
        log("   📸 เก็บหลักฐาน error: " + " , ".join(saved))
    return saved


# โฟลเดอร์ดาวน์โหลด default ของ Chrome รอบนี้ (ตั้งโดย make_driver)
# images.py ใช้เป็น fallback แทน ~/Downloads กลาง — กันไฟล์ปนกันเมื่อรันหลายงานพร้อมกัน
_DEFAULT_DOWNLOAD_DIR = None


def default_download_dir():
    """โฟลเดอร์ดาวน์โหลด default ของ process นี้ (None = ยังไม่ได้ตั้ง → ใช้ ~/Downloads)"""
    return _DEFAULT_DOWNLOAD_DIR


def make_driver(detach: bool = True, download_dir=None) -> webdriver.Chrome:
    """สร้าง Chrome driver (ตั้งค่าเหมือนใน notebook เดิม)

    detach=True ทำให้ browser ไม่ปิดตัวเองตอนสคริปต์จบ
    เพื่อให้คนตรวจสอบและกดบันทึกขั้นสุดท้ายเองได้

    download_dir: โฟลเดอร์ดาวน์โหลด default เฉพาะรอบนี้ (กันไฟล์ชนกันเมื่อรัน
    หลายงานพร้อมกัน) — แต่ละ subprocess ควรใช้โฟลเดอร์ของตัวเอง
    """
    global _DEFAULT_DOWNLOAD_DIR
    options = Options()
    options.add_experimental_option(
        "excludeSwitches", ["disable-popup-blocking", "enable-automation"]
    )
    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.password_manager_leak_detection": False,
        # ดาวน์โหลดหลายไฟล์ต่อเนื่อง (zip+XML หลายเคลมใน session เดียว)
        # โดยไม่โดน Chrome ถามสิทธิ์/บล็อกเงียบ
        "download.prompt_for_download": False,
        "profile.default_content_setting_values.automatic_downloads": 1,
    }
    if download_dir:
        download_dir = Path(download_dir)
        download_dir.mkdir(parents=True, exist_ok=True)
        prefs["download.default_directory"] = str(download_dir)
        _DEFAULT_DOWNLOAD_DIR = download_dir
    options.add_experimental_option("prefs", prefs)
    options.add_argument("--start-maximized")
    if detach:
        options.add_experimental_option("detach", True)
    return webdriver.Chrome(options=options)


# ---------------------------------------------------------------- รอ element

def wait_visible(driver, by, value, timeout=10):
    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((by, value))
    )


def wait_present(driver, by, value, timeout=10):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, value))
    )


def wait_clickable(driver, by, value, timeout=10):
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((by, value))
    )


def wait_value_not_empty(driver, elem_id, timeout=60):
    """รอจน input มีค่า (ใช้รอหน้า ISURVEY โหลดข้อมูลเสร็จ — เว็บช้า เผื่อเวลายาว)"""
    WebDriverWait(driver, timeout).until(
        lambda d: d.find_element(By.ID, elem_id).get_attribute("value") != ""
    )


def wait_loading_gone(driver, timeout=30):
    """รอจนข้อความ 'Loading...' ของ ISURVEY หายไป"""
    def _gone(d):
        try:
            return not d.find_element(
                By.XPATH, "//*[contains(text(), 'Loading...')]"
            ).is_displayed()
        except NoSuchElementException:
            return True

    WebDriverWait(driver, timeout).until(_gone)


def accept_alert(driver, timeout=30) -> str:
    """รอ alert ขึ้น กดตกลง และคืนข้อความใน alert (พร้อม log)
    — ข้อความนี้สำคัญ: ถ้าเป็นคำเตือน validation จะบอกว่ากรอกอะไรไม่ครบ"""
    WebDriverWait(driver, timeout).until(EC.alert_is_present())
    alert = driver.switch_to.alert
    text = (alert.text or "").strip()
    if text:
        log(f"   [alert] {text[:400]}")
    alert.accept()
    return text


# ---------------------------------------------------------------- อ่าน/กรอกค่า

def get_value(driver, elem_id) -> str:
    return driver.find_element(By.ID, elem_id).get_attribute("value")


def set_text(driver, elem_id, value):
    """กรอกข้อความลง input (ข้ามถ้าค่าว่าง)

    ทนต่อกรณี element ถูกบัง/ยังไม่พร้อม (เช่น datepicker ของช่องวันที่ก่อนหน้า
    ค้างบังช่องถัดไป): scroll เข้า view → ถ้าพิมพ์ไม่ได้ กด ESC ปิด popup แล้วลองใหม่
    → ทางสุดท้าย set ค่าด้วย JS กัน crash"""
    if value is None or str(value) == "":
        log(f"   - ข้าม {elem_id} (ค่าว่าง)")
        return
    value = str(value)
    el = driver.find_element(By.ID, elem_id)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except Exception:
        pass
    try:
        el.send_keys(value)
        return
    except ElementNotInteractableException:
        pass
    # อาจมี datepicker/popup ของช่องก่อนหน้าบังอยู่ — ปิดด้วย ESC แล้วลองใหม่
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        driver.find_element(By.ID, elem_id).send_keys(value)
        return
    except Exception:
        pass
    # ทางสุดท้าย: เซ็ตค่าด้วย JS + trigger change (เผื่อถูกบัง/แก้ตรงไม่ได้)
    driver.execute_script(
        "arguments[0].value = arguments[1];"
        "arguments[0].dispatchEvent(new Event('change'));",
        driver.find_element(By.ID, elem_id), value)
    log(f"   (ตั้งค่า {elem_id} ด้วย JS — ช่องถูกบัง/พิมพ์ตรงไม่ได้)")


def click_retry(driver, by, value, timeout=15, attempts=3):
    """คลิกแบบ retry — กัน StaleElementReference ตอนหน้ากำลัง reload
    (เจอ element แล้วแต่หน้า refresh ก่อนคลิกทัน)"""
    last_err = None
    for _ in range(attempts):
        try:
            wait_clickable(driver, by, value, timeout).click()
            return
        except StaleElementReferenceException as e:
            last_err = e
            time.sleep(1)
    raise last_err


def click_first(driver, locators, timeout=10):
    """ลองคลิกตาม locator ทีละตัวจนกว่าจะสำเร็จ (ตัวแรกคือ selector หลัก
    ตัวถัดไปคือ fallback) — ใช้ลดความเสี่ยงจาก selector ที่เปราะ"""
    last_err = None
    for by, value in locators:
        try:
            wait_clickable(driver, by, value, timeout).click()
            return (by, value)
        except Exception as e:  # ลองตัวถัดไป
            last_err = e
    raise last_err


# ---------------------------------------------------------------- วันที่/เวลา

def to_buddhist_date(date_str: str) -> str:
    """แปลง dd/mm/yyyy (ค.ศ.) เป็น dd/mm/yyyy (พ.ศ.)
    ถ้าปีเป็น พ.ศ. อยู่แล้ว (>2400) จะไม่บวกซ้ำ / ค่าว่างคืน ''"""
    if not date_str or not date_str.strip():
        return ""
    d, m, y = date_str.strip().split("/")
    year = int(y)
    if year < 2400:
        year += 543
    return f"{d}/{m}/{year}"


def iso_to_thai_date(date_str: str) -> str:
    """แปลงวันที่จากไฟล์ XML ('YYYY-MM-DD[ HH:MM:SS]') เป็น dd/mm/yyyy (พ.ศ.)
    ปีในไฟล์ปนกันทั้ง ค.ศ. และ พ.ศ. — ถ้า < 2400 ถือเป็น ค.ศ. แล้วบวก 543"""
    date_str = (date_str or "").strip().split(" ")[0]
    if not date_str or "-" not in date_str:
        return ""
    try:
        y, m, d = date_str.split("-")
        year = int(y)
        if year < 2400:
            year += 543
        return f"{d}/{m}/{year}"
    except ValueError:
        return ""


def split_hhmm(time_str: str):
    """แยก 'HH:MM' เป็น (HH, MM) — ค่าว่างคืน ('', '')"""
    if not time_str or ":" not in time_str:
        return "", ""
    h, m = time_str.split(":", 1)
    return h.strip(), m.strip()


def today_buddhist() -> str:
    t = datetime.now()
    return f"{t:%d}/{t:%m}/{t.year + 543}"


# ----------------------------------------------- หยุดรอให้คนกรอกข้อมูลเอง

def wait_for_manual_fill(field_label, reason="", select_id=None):
    """หยุดรอให้ผู้ใช้กรอก/เลือกข้อมูลช่องนี้เองบนหน้า EMCS แล้วค่อยทำงานต่อ

    ใช้เมื่อข้อมูลจาก ISURVEY ไม่ครบ หรือกรอกอัตโนมัติไม่ได้ — ดีกว่าปล่อย
    error จบโปรแกรม คนจะได้กรอกช่องที่ขาดให้ครบแล้วสั่งไปต่อ
    - หน้าเว็บ (webui.py): ส่ง marker ให้เว็บโชว์ปุ่ม "ดำเนินการต่อ"
      เมื่อกดปุ่ม webui จะส่ง newline เข้า stdin มาปลดล็อก
    - console จริง: ผู้ใช้กด Enter ที่หน้าต่างเอง
    - ไม่มี console/stdin ปิด (รันแบบไม่มีคนเฝ้า): readline คืน "" ทันที →
      ไปต่อ ไม่ค้าง (อาศัย EOF ของ stdin ไม่พึ่ง isatty ที่บน Windows เชื่อถือไม่ได้)
    ไม่ขึ้นกับ -y (นี่คือการหยุดเพราะข้อมูลไม่ครบ ไม่ใช่ถามยืนยัน)
    คืน True ถ้าผู้ใช้สั่งต่อจริง, False ถ้าไม่มีใครตอบ (EOF) แล้วไปต่อเอง
    """
    log_plain("")
    log(f"⏸️  ต้องกรอกข้อมูลเอง: {field_label}")
    if reason:
        log(f"     สาเหตุ: {reason}")
    log("     → กรอก/เลือกข้อมูลช่องนี้ในหน้าต่าง EMCS (Chrome) ให้เรียบร้อย แล้ว"
        + ("กดปุ่ม 'ดำเนินการต่อ' บนหน้าเว็บ"
           if _WEBUI else "กลับมากด Enter ที่หน้าต่างนี้") + " เพื่อทำงานต่อ")
    if _WEBUI:
        # marker บรรทัดเดียว ให้ webui จับไปโชว์กล่องแจ้งเตือน + ปุ่มดำเนินการต่อ
        print(MANUAL_MARKER + json.dumps(
            {"label": field_label, "reason": reason}, ensure_ascii=False),
            flush=True)
    try:
        line = sys.stdin.readline()   # block จนได้ Enter (console)/\n (webui); "" ถ้า EOF
    except Exception:
        line = ""
    if line == "":
        # stdin ปิด/EOF = ไม่มีคนเฝ้า → ไปต่อ ไม่ค้าง (ช่องนี้ต้องกรอกเองภายหลัง)
        log("     (ไม่มีการตอบกลับจาก stdin — ไปต่อ ตรวจ/กรอกช่องนี้เองภายหลัง)")
        return False
    log(f"     ▶️ ดำเนินการต่อ ({field_label})")
    return True


SUBMIT_MARKER = "@@READY_SUBMIT@@"  # ต้องตรงกับค่าใน webui.py


def wait_for_submit(claim, reason=""):
    """หลังกรอกครบ (live session) — รอผู้ใช้ตรวจ draft แล้วสั่ง "ส่งงาน + แจ้ง ISURVEY"
    กลไกเดียวกับ wait_for_manual_fill (marker + รอ stdin /continue):
    - หน้าเว็บ: ส่ง SUBMIT_MARKER → เว็บโชว์ปุ่ม "✅ ส่งงาน + แจ้ง ISURVEY"
    - console: กด Enter
    - ไม่มีคนเฝ้า (EOF): คืน False → เก็บเป็น draft ไม่ส่ง (พฤติกรรมเดิม)
    คืน True ถ้าสั่งส่ง / False ถ้าไม่ (เก็บ draft)"""
    log_plain("")
    log(f"⏸️  กรอกครบแล้ว (เคลม {claim}) — ตรวจ draft ให้เรียบร้อย แล้วสั่งส่งงาน")
    log("     → ตรวจความถูกต้องในหน้าต่าง EMCS (Chrome) ก่อน แล้ว"
        + ("กดปุ่ม '✅ ส่งงาน + แจ้ง ISURVEY' บนหน้าเว็บ"
           if _WEBUI else "กด Enter ที่หน้าต่างนี้")
        + " (ระบบจะกด 'ส่งงานใหม่' ให้ + แจ้ง ISURVEY)")
    if _WEBUI:
        print(SUBMIT_MARKER + json.dumps({"claim": claim, "reason": reason},
              ensure_ascii=False), flush=True)
    try:
        line = sys.stdin.readline()
    except Exception:
        line = ""
    if line == "":
        log("     (ไม่มีการตอบกลับ — เก็บเป็น draft ไม่ส่งงาน ตรวจ/กดส่งเองภายหลังได้)")
        return False
    log(f"     ▶️ สั่งส่งงาน (เคลม {claim})")
    return True


SELECT_IMAGES_MARKER = "@@SELECT_IMAGES@@"  # ต้องตรงกับค่าใน webui.py


def _parse_selected(line, files):
    """แปลงบรรทัด JSON {"selected":[...]} จาก stdin → list ชื่อไฟล์ที่เลือก
    (กรองเฉพาะชื่อที่มีอยู่จริงใน files กันค่าแปลกปลอม)
    คืน None ถ้า parse ไม่ได้/ไม่มีคีย์ selected = ให้ผู้เรียกใช้ทุกรูปตามเดิม"""
    try:
        data = json.loads(line)
    except Exception:
        return None
    sel = data.get("selected") if isinstance(data, dict) else None
    if not isinstance(sel, list):
        return None
    avail = set(files)
    return [s for s in sel if s in avail]


def _image_categories(folder, files):
    """คืน {ชื่อไฟล์: หมวด} ของรูปแต่ละไฟล์ — อ่านจาก manifest ที่เขียนไว้ตอนโหลด
    (`_categories.json` = ชื่อเดิม→หมวด) + ตอนเปลี่ยนชื่อ (`_rename_map.json` =
    ชื่อใหม่→ชื่อเดิม). ไม่มี manifest / ไม่เจอ = หมวด 'OTHERS'
    หมวด: INS=รูปรถประกัน, REPORTS=เอกสาร/ใบรับงาน, OTHERS=อื่นๆ"""
    folder = Path(folder)
    cats, rmap = {}, {}
    try:
        cats = json.loads((folder / "_categories.json").read_text(encoding="utf-8"))
    except Exception:
        cats = {}
    try:
        rmap = json.loads((folder / "_rename_map.json").read_text(encoding="utf-8"))
    except Exception:
        rmap = {}
    out = {}
    for f in files:
        orig = rmap.get(f, f)          # ชื่อใหม่ → ชื่อเดิม (ถ้าเคยเปลี่ยนชื่อ)
        out[f] = cats.get(orig, "OTHERS")
    return out


def wait_for_image_select(folder, files):
    """ให้ผู้ใช้เลือกรูปที่จะอัปโหลดเข้า EMCS — เฉพาะโหมดหน้าเว็บ (SE_WEBUI=1)

    - หน้าเว็บ: ส่ง marker {folder, images:[...]} → เว็บโชว์แกลเลอรีให้ติ๊กเลือก
      แล้วส่ง {"selected":[...]} กลับเข้า stdin
    - console / รันแบบไม่มีคนเฝ้า (EOF) / ไม่ใช่ webui: คืน None = อัปโหลดทุกรูป
      (พฤติกรรมเดิม ไม่เปลี่ยน)
    คืน list ชื่อไฟล์ที่เลือก (อาจเป็น [] = ผู้ใช้ไม่เลือกเลย) หรือ None = ใช้ทุกรูป
    """
    if not _WEBUI:
        return None
    log_plain("")
    log(f"⏸️  เลือกรูปที่จะอัปโหลดเข้า EMCS ({len(files)} รูป) — "
        "ติ๊กเลือกบนหน้าเว็บแล้วกดปุ่มอัปโหลด")
    cat_of = _image_categories(folder, files)
    images = [{"name": f, "cat": cat_of.get(f, "OTHERS")} for f in files]
    print(SELECT_IMAGES_MARKER + json.dumps(
        {"folder": str(folder), "images": images}, ensure_ascii=False),
        flush=True)
    try:
        line = sys.stdin.readline()
    except Exception:
        line = ""
    if line == "":
        log("     (ไม่มีการตอบกลับ — อัปโหลดทุกรูปตามเดิม)")
        return None
    sel = _parse_selected(line, files)
    if sel is None:
        log("     (อ่านรายการที่เลือกไม่ได้ — อัปโหลดทุกรูปตามเดิม)")
        return None
    log(f"     ▶️ เลือก {len(sel)}/{len(files)} รูป")
    return sel


# ---------------------------------------------------------------- dropdown

def _current_select_text(driver, select_id) -> str:
    """อ่านข้อความ option ที่ถูกเลือกอยู่ตอนนี้ (ใช้หลังให้คนเลือกเอง)"""
    try:
        return Select(driver.find_element(By.ID, select_id)).first_selected_option.text
    except Exception:
        return ""


def fuzzy_select(driver, select_id, value, wait_options=True, timeout=10,
                 presleep=0.0, label="", required=False):
    """เลือก option ใน dropdown ด้วย fuzzy matching (rapidfuzz WRatio)

    รวม pattern ที่ซ้ำใน notebook เดิม: รอ dropdown → รอ options โหลด →
    เก็บข้อความ options → หา match ที่ใกล้สุด → เลือก
    มี retry กัน StaleElementReference จาก ASP.NET postback

    required=True (field บังคับของ EMCS): ถ้าค่าว่าง หรือเลือกอัตโนมัติไม่ได้
      จะ "หยุดรอให้คนกรอกเอง" แทนการข้าม/error
    required=False: ค่าว่าง → ข้าม (เหมือนเดิม); เลือกไม่ได้ → หยุดรอให้คนกรอก
      เช่นกัน (ไม่ปล่อย error จบโปรแกรม)

    คืนค่า (ข้อความที่เลือก, คะแนน) หรือ None ถ้าค่าว่างและไม่บังคับ
    """
    name = label or select_id
    if value is None or str(value).strip() == "":
        if required:
            log(f"   ⚠️ ไม่มีข้อมูล {name} จาก ISURVEY (เป็น field บังคับ)")
            wait_for_manual_fill(name, "ISURVEY ไม่มีข้อมูลช่องนี้",
                                 select_id=select_id)
            return _current_select_text(driver, select_id), 0
        log(f"   - ข้าม dropdown {name} (ค่าต้นทางว่าง)")
        return None

    if presleep:
        time.sleep(presleep)  # รอ postback ของหน้าก่อนหน้า render เสร็จ

    last_err = None
    for attempt in range(3):
        try:
            wait_present(driver, By.ID, select_id, timeout)
            if wait_options:
                WebDriverWait(driver, timeout).until(
                    lambda d: len(Select(d.find_element(By.ID, select_id)).options) > 1
                )
            sel = Select(driver.find_element(By.ID, select_id))
            options = [o.text for o in sel.options]

            best = process.extractOne(str(value), options, scorer=fuzz.WRatio)
            text, score = best[0], best[1]

            mark = "⚠️" if score < FUZZY_WARN_SCORE else "✓"
            log(f"   {mark} {name}: '{value}' → '{text}' (score {score:.0f})")
            if score < FUZZY_WARN_SCORE:
                log(f"     ** คะแนนต่ำ ควรตรวจสอบด้วยตาก่อนบันทึก **")

            Select(driver.find_element(By.ID, select_id)).select_by_visible_text(text)
            return text, score
        except StaleElementReferenceException as e:
            last_err = e
            time.sleep(0.5)  # หน้า postback ใหม่ ลองอีกรอบ
        except TimeoutException as e:
            last_err = e
            break  # dropdown ไม่โหลด options — ออกไปหยุดรอคน (ไม่ retry ต่อ)

    # มาถึงตรงนี้ = เลือกอัตโนมัติไม่ได้ (dropdown ไม่พร้อม/element หาย)
    # → หยุดรอให้คนเลือกเอง แทนการ error จบโปรแกรม
    log(f"   ⚠️ เลือก {name} อัตโนมัติไม่ได้ "
        f"({type(last_err).__name__ if last_err else 'unknown'})")
    wait_for_manual_fill(name, f"เลือก '{value}' อัตโนมัติไม่ได้ — dropdown ไม่พร้อม",
                         select_id=select_id)
    return _current_select_text(driver, select_id), 0
