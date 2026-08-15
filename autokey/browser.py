"""Chrome driver + helper กลางที่ใช้ร่วมกันทั้งฝั่ง ISURVEY และ EMCS"""
import json
import os
import re
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
# ต่ำกว่านี้ = ไม่มีตัวเลือกไหนใกล้จริง → ไม่เลือกให้ หยุดรอคนเลือกเอง
# (กันเคส 'เอ็มจี'/'นิสสัน' ไทย เจอ dropdown อังกฤษ ได้ 0 คะแนน แล้วไปลง '-- ระบุ --')
FUZZY_MIN_SCORE = 40

# ── โหมด "เขียนเฉพาะช่องที่ต่าง" ────────────────────────────────────────────
# ปกติ set_text/fuzzy_select เขียนทับทุกช่องเสมอ ซึ่งถูกต้องตอน import ครั้งแรก
# (หน้าว่าง + dropdown ลูกยังไม่โหลดจนกว่าตัวแม่จะ fire onchange)
# แต่โหมด "เติมส่วนที่ขาด" เปิด draft ที่กรอกไปแล้วมาเติม — พิมพ์ทับทุกช่องซ้ำทั้งหน้า
# ช้ามาก (send_keys ทีละตัวอักษร × ~60 ช่อง) และผิดชื่อโหมดที่บอกว่า "เติมส่วนที่ขาด"
# เปิดธงนี้ = อ่านค่าบนหน้าก่อน ถ้าตรงอยู่แล้วข้าม (user รายงาน 2026-08-10)
# ⚠️ เปิดเฉพาะตอนหน้าโหลดค่าครบแล้วเท่านั้น — เปิดตอน import ใหม่จะทำให้ dropdown
#    ลูก (ยี่ห้อ←ประเภทรถ, อำเภอ←จังหวัด) ไม่ถูก populate เพราะข้ามการ fire onchange
SKIP_UNCHANGED = False


def set_skip_unchanged(on: bool):
    """เปิด/ปิดโหมดเขียนเฉพาะช่องที่ต่าง (ดูหมายเหตุที่ SKIP_UNCHANGED)"""
    global SKIP_UNCHANGED
    SKIP_UNCHANGED = bool(on)


def _same_text(a, b) -> bool:
    """เทียบค่าช่องแบบไม่ถือสาช่องว่างซ้ำ/หัวท้าย (EMCS คืนค่าที่ trim มาแล้วบ้างไม่บ้าง)"""
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


# ข้อความ placeholder ของ dropdown EMCS — ห้ามเลือกเด็ดขาด (= ไม่ได้เลือกอะไรเลย)
_PLACEHOLDER_WORDS = {"ระบุ", "กรุณาเลือก", "เลือก", "โปรดระบุ", "โปรดเลือก", ""}


def _is_placeholder_option(text: str) -> bool:
    """'-- ระบุ --', '--เลือก--', '- กรุณาเลือก -' ฯลฯ = ตัวเลือกหลอก ไม่ใช่ค่าจริง"""
    return " ".join(str(text or "").replace("-", " ").split()) in _PLACEHOLDER_WORDS

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
    driver = webdriver.Chrome(options=options)
    set_active_driver(driver)
    return driver


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


# confirm ที่ "กดตกลงแล้วข้อมูลหาย" — ห้ามกดตกลงอัตโนมัติเด็ดขาด ให้กดยกเลิกแทน
# ของจริงจาก eclaim3 (เจอตอนเปลี่ยน 'ประเภทรถ' บนเรื่องที่บันทึกแล้ว 2026-07-25):
#   "การแก้ไขต่อไปนี้ จะทำให้ข้อมูลที่เคยบันทึกไว้แล้ว ถูกลบออกทั้งหมด
#    คุณต้องการจะแก้ไขข้อมูลหรือไม่?"
_DESTRUCTIVE_ALERT = re.compile(
    r"ถูกลบออกทั้งหมด|ข้อมูล.{0,20}(จะ)?ถูกลบ|ลบข้อมูล.{0,20}ทั้งหมด|จะถูกลบ")


class DestructiveAlert(Exception):
    """เจอ confirm ที่กดตกลงแล้วข้อมูลหาย — กดยกเลิกไปแล้ว ให้คนตัดสินใจเอง"""

    def __init__(self, text):
        super().__init__(text)
        self.text = text


def accept_alert(driver, timeout=30) -> str:
    """รอ alert ขึ้น กดตกลง และคืนข้อความใน alert (พร้อม log)
    — ข้อความนี้สำคัญ: ถ้าเป็นคำเตือน validation จะบอกว่ากรอกอะไรไม่ครบ

    ⛔ ถ้าเป็น confirm แนว "ข้อมูลที่บันทึกไว้จะถูกลบทั้งหมด" จะ **กดยกเลิก**
    แล้วโยน DestructiveAlert — บอทไม่มีสิทธิ์ทำลายงานที่คนกรอกไว้"""
    WebDriverWait(driver, timeout).until(EC.alert_is_present())
    alert = driver.switch_to.alert
    text = (alert.text or "").strip()
    if text:
        log(f"   [alert] {text[:400]}")
        harvest_rule(text)      # คำฟ้อง validation = กฎ 1 ข้อ เก็บไว้เอามาทำช่องบังคับบนเว็บ
    if _DESTRUCTIVE_ALERT.search(text):
        alert.dismiss()
        log("   ⛔ confirm นี้กดตกลงแล้วข้อมูลที่บันทึกไว้จะหาย — กด 'ยกเลิก' แทน "
            "(ให้คนตัดสินใจเองบนหน้าจอ)")
        raise DestructiveAlert(text)
    alert.accept()
    return text


# ---------------------------------------------------------------- อ่าน/กรอกค่า

# ── เครื่องหมาย + หายตอน EMCS บันทึก ────────────────────────────────────────
# ฟอร์มของ EMCS ส่งแบบ application/x-www-form-urlencoded ซึ่ง "+" แปลว่าเว้นวรรค
# (บวกจริงต้องเป็น %2B) ฝั่งเซิร์ฟเวอร์ถอดรหัสค่าที่ถอดมาแล้วซ้ำอีกรอบ → "+" กลายเป็น
# ช่องว่างเงียบ ๆ · ยืนยันจากหน้าจริง 13/08/69: พิมพ์ "ประเภท 2+" ลง txtAcc_Detail
# แล้วกด "แก้ไข" เครื่องหมายบวกหายไป ส่วน : / ( ) . - รอดครบ (จึงไม่ใช่ตัวกรองอักขระ)
#
# แก้ที่ EMCS ไม่ได้ → แปลงเป็นคำก่อนพิมพ์ "เฉพาะช่องที่ระบุไว้" (user เลือกเอง 13/08/69)
# ข้อมูลต้นทางใน se-survey ไม่ถูกแตะ — คนเปิดดูบนเว็บ/แอปยังเห็น "2+" เหมือนเดิม
#
# ⛔ ห้ามใส่ช่องชื่อคน (txtDri_Name / txtOpo_Name / txtAcc_Surv …) — พวกนั้นมี noTyping
#    ของ EMCS กรองอักขระอยู่แล้ว และ set_text ด้านล่าง normalize ให้ตรงกติกานั้นต่างหาก
PLUS_TO_WORD_FIELDS = {
    "txtAcc_Detail",     # รายละเอียดการเกิดเหตุ (หน้าข้อมูลทั่วไป)
    "txtAcc_result",     # ผลการดำเนินงาน (หน้าค่าใช้จ่าย)
    "txtAcc_Comment",    # ความเห็นของผู้ตรวจสอบ (หน้าค่าใช้จ่าย)
    "txtSurv_Comment",   # ความเห็นของเซอร์เวย์ (หน้าค่าใช้จ่าย)
    # ประเภทกรมธรรม์ (หน้าหลัก + บล็อกคู่กรณี) — เพิ่ม 15/08/69 หลังเจอของจริง
    # ⚠️ ช่องนี้ต่างจาก 4 ช่องบน: ที่นั่น + หายแล้วอ่านสะดุด แต่ที่นี่ "ประเภท 2+"
    #    กลายเป็น "ประเภท 2" = **ผิดความคุ้มครอง** ไม่ใช่แค่อักขระหาย
    "txtPolicy_Type",
}
PLUS_WORD = "พลัส"


def _plus_safe(elem_id, value: str) -> str:
    """แปลง + เป็นคำ เฉพาะช่องใน PLUS_TO_WORD_FIELDS (ช่องอื่นคืนค่าเดิม)

    เทียบแบบ "ลงท้ายด้วย _<ชื่อช่อง>" ด้วย เพราะบล็อกคู่กรณีของ EMCS อยู่ใน
    naming container ของ ASP.NET → id จริงเป็น wuOpoCar1_txtPolicy_Type
    (เทียบตรงตัวอย่างเดียวจะพลาดคู่กรณีทั้งหมด ซึ่งเป็นที่ที่ "2+" โผล่บ่อยที่สุด)
    """
    eid = str(elem_id or "")
    if "+" not in value:
        return value
    if not any(eid == f or eid.endswith("_" + f) for f in PLUS_TO_WORD_FIELDS):
        return value
    out = value.replace("+", PLUS_WORD)
    log(f"   ~ {elem_id}: แปลง + เป็น '{PLUS_WORD}' {value.count('+')} จุด "
        f"(EMCS กลืนเครื่องหมายบวกตอนบันทึก)")
    return out


# ── เฟส 2: จำสิ่งที่บอท "ตั้งใจ" กรอก แล้วอ่านกลับมาเทียบหลังบันทึก ──────────
#
# ทำไมต้องมี: EMCS กลืนข้อมูลเงียบ ๆ มาแล้ว 3 ครั้ง (+ หายตอนบันทึก · ยี่ห้อรถว่าง
# เพราะ dropdown โหลดไม่ทัน · "-- ระบุ --" บันทึกเป็นค่าจริง) ทั้ง 3 ครั้งเจอเพราะ
# **มีคนมองจอ** — พอบอทกดส่งเอง (เฟส 3) คนคนนั้นหายไป ตัวนี้ทำหน้าที่แทน
#
# เก็บค่า "หลังผ่านการแปลงที่เราตั้งใจแล้ว" (+ → พลัส · ตัดอักขระที่ EMCS ไม่รับ)
# เพราะนั่นคือสิ่งที่ควรอยู่บนหน้าจริง ไม่ใช่ค่าดิบก่อนแปลง
_FILLED = {}


def reset_filled():
    """เริ่มนับใหม่ต่อ 1 หน้า — ต้องเรียกก่อนกรอกหน้าถัดไป ไม่งั้นจะไปตามหาช่อง
    ของหน้าเก่าที่ไม่มีอยู่แล้ว แล้วรายงาน 'อ่านกลับไม่ได้' เต็มไปหมด"""
    _FILLED.clear()


def _record_filled(elem_id, value):
    _FILLED[str(elem_id)] = str(value)


def _cmp_value(intended: str, actual: str) -> bool:
    """ตรงกันไหม — ผ่อนให้เฉพาะ "รูปแบบ" ที่ EMCS จัดใหม่เอง ไม่ผ่อนให้เนื้อหาที่หายไป"""
    if _same_text(intended, actual):
        return True
    a, b = str(intended).strip(), str(actual).strip()
    if not a or not b:
        return False
    try:                                    # ตัวเลข: 750 → 750.00 / 1,000.00
        if float(a.replace(",", "")) == float(b.replace(",", "")):
            return True
    except ValueError:
        pass
    da, db = re.sub(r"\D", "", a), re.sub(r"\D", "", b)   # วันที่: สลับตัวคั่นเอง
    return len(da) >= 6 and da == db


def verify_filled(driver, label: str = "") -> list:
    """อ่านค่าจริงจากหน้า EMCS กลับมาเทียบกับที่บอทกรอกไป

    คืน list ของ {id, intended, actual, reason}
      reason='ไม่ตรง'     = ค่าบนหน้าไม่เหมือนที่กรอก  ← ข้อมูลเพี้ยน ต้องมีคนดู
      reason='อ่านไม่ได้'  = หาช่องไม่เจอแล้ว (เปลี่ยนหน้า/ถูกซ่อน) ← รายงานไว้เฉย ๆ

    ⛔ ตัวนี้ "ตรวจแล้วรายงาน" ไม่ล้มงานทิ้ง — ตอนถูกเรียก draft เกิดบน EMCS ไปแล้ว
       ล้มตรงนี้ไม่ได้ทำให้ draft หาย มีแต่ทำให้ไม่มีใครรู้ว่าเพี้ยนตรงไหน
       คนที่ต้องใช้ผลนี้คือขั้น "กดส่งงาน" (เฟส 3) — ไม่ตรง = ห้ามกด
    """
    if not _FILLED:
        return []
    got = driver.execute_script(
        "return arguments[0].map(function(id){"
        "var e=document.getElementById(id);"
        "if(!e) return [id, null];"
        "if(e.tagName==='SELECT') return [id, ((e.options[e.selectedIndex]||{}).text)||''];"
        "if(e.type==='checkbox'||e.type==='radio') return [id, e.checked?'1':''];"
        "return [id, e.value];});",
        list(_FILLED.keys())) or []
    bad = []
    for eid, actual in got:
        want = _FILLED.get(eid, "")
        if actual is None:
            bad.append({"id": eid, "intended": want, "actual": None, "reason": "อ่านไม่ได้"})
        elif not _cmp_value(want, actual):
            bad.append({"id": eid, "intended": want, "actual": actual, "reason": "ไม่ตรง"})
    head = f"ตรวจค่าที่กรอก{(' ' + label) if label else ''}: ตรง {len(_FILLED) - len(bad)}/{len(_FILLED)} ช่อง"
    if not bad:
        log(f"   ✓ {head}")
        return []
    log(f"   ⚠️ {head}")
    for b in bad:
        if b["reason"] == "ไม่ตรง":
            log(f"      ✗ {b['id']}: กรอก {b['intended']!r} แต่บนหน้าเป็น {b['actual']!r}")
        else:
            log(f"      ? {b['id']}: อ่านกลับไม่ได้ (หาช่องไม่เจอแล้ว)")
    return bad


# ── ให้ EMCS เป็นคนบอกกฎเอง ─────────────────────────────────────────────────
# อ่านโค้ดตรวจสอบของเขาไปได้แค่ระดับหนึ่ง — มีสาขาแยกตามบริษัท และอ่านธงภายใน
# (hidMemType / hifFeatures / hidLOAD_DAMAGE_STD) ที่เรามองไม่เห็นจากข้างนอก
# ทางที่ครบกว่าคือเก็บ "คำฟ้อง" ของเขาทุกครั้งที่บันทึกไม่ผ่าน แล้วเอามาทำเป็น
# ช่องบังคับบนเว็บ se-survey — ฟ้อง 1 ครั้ง = ได้กฎ 1 ข้อ ไม่ต้องเดา
_RULES_FILE = Path(__file__).resolve().parent.parent / "runs" / "emcs_rules.jsonl"
_RULE_HINT = re.compile(r"กรุณา|ต้องระบุ|ไม่ครบ|ช่องที่ขึ้นสีแดง")
_rules_seen = set()
_rule_context = {}


def set_rule_context(**kw):
    """บอกว่ากำลังทำเคสไหน — กฎที่เก็บได้จะได้ย้อนกลับไปดูเคสต้นเรื่องได้
    (คำฟ้องลอย ๆ ว่า 'กรอกไม่ครบ' โดยไม่รู้ว่าเคสไหน เอาไปทำอะไรต่อไม่ได้)"""
    _rule_context.clear()
    _rule_context.update({k: v for k, v in kw.items() if v})


def harvest_rule(text: str, context: dict = None):
    """เก็บคำฟ้อง validation ของ EMCS ลงแฟ้มสะสม (ไม่เก็บ alert ทั่วไป เช่นบันทึกสำเร็จ)"""
    t = " ".join(str(text or "").split())
    if not t or not _RULE_HINT.search(t) or t in _rules_seen:
        return
    _rules_seen.add(t)
    try:
        _RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_RULES_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"text": t, **_rule_context, **(context or {})},
                               ensure_ascii=False) + "\n")
        log(f"   📓 เก็บกฎที่ EMCS ฟ้องไว้แล้ว ({_RULES_FILE.name})")
    except Exception as e:      # เก็บกฎไม่ได้ ห้ามทำให้งานหลักล้ม
        log(f"   (เก็บกฎไม่สำเร็จ: {type(e).__name__})")


def get_value(driver, elem_id) -> str:
    return driver.find_element(By.ID, elem_id).get_attribute("value")


def set_textarea(driver, elem_id, value):
    """กรอกข้อความ "หลายบรรทัด" ลง <textarea> โดยคงขึ้นบรรทัดใหม่ไว้

    ต่างจาก set_text: ใช้ JS assign แทน send_keys เพราะ send_keys แปลง 
 เป็นการกด Enter
    (ช้า + เสี่ยง submit ถ้า element ไม่ใช่ textarea จริง) แล้ว dispatch input/change
    ให้ ASP.NET/jQuery ที่ผูก handler ไว้รับรู้ค่าใหม่
    ใช้กับหน้าค่าใช้จ่ายของ EMCS (txtAcc_result / txtAcc_Comment / txtSurv_Comment)
    ซึ่งงานจริงเขียนเป็น bullet ~20 บรรทัด — ยุบบรรทัดทิ้งคืออ่านยากและผิดรูปแบบสำนวน"""
    if value is None or str(value) == "":
        log(f"   - ข้าม {elem_id} (ค่าว่าง)")
        return
    value = _plus_safe(elem_id, str(value))
    try:
        el = driver.find_element(By.ID, elem_id)
    except Exception:
        log(f"   ⚠️ ไม่พบ {elem_id} — ข้าม")
        return
    if el.tag_name.lower() != "textarea":
        # ไม่ใช่ textarea (เช่นช่องเดียวกันบนหน้า 1 เป็น input) → ยุบบรรทัดแล้วใช้เส้นปกติ
        set_text(driver, elem_id, " ".join(value.split()))
        return
    if SKIP_UNCHANGED and _same_text(el.get_attribute("value"), value):
        log(f"   = {elem_id} ตรงอยู่แล้ว — ข้าม")
        _record_filled(elem_id, value)      # ข้ามเพราะตรงอยู่แล้ว = ยังต้องตรงตอนตรวจกลับ
        return
    _record_filled(elem_id, value)
    driver.execute_script(
        "var e=arguments[0];e.value=arguments[1];"
        "e.dispatchEvent(new Event('input',{bubbles:true}));"
        "e.dispatchEvent(new Event('change',{bubbles:true}));", el, value)
    log(f"   ✓ {elem_id}: {len(value)} ตัวอักษร / {value.count(chr(10)) + 1} บรรทัด")


def set_text(driver, elem_id, value):
    """กรอกข้อความลง input (ข้ามถ้าค่าว่าง)

    ทนต่อกรณี element ถูกบัง/ยังไม่พร้อม (เช่น datepicker ของช่องวันที่ก่อนหน้า
    ค้างบังช่องถัดไป): scroll เข้า view → ถ้าพิมพ์ไม่ได้ กด ESC ปิด popup แล้วลองใหม่
    → ทางสุดท้าย set ค่าด้วย JS กัน crash"""
    if value is None or str(value) == "":
        log(f"   - ข้าม {elem_id} (ค่าว่าง)")
        return
    value = _plus_safe(elem_id, str(value))
    el = driver.find_element(By.ID, elem_id)
    # ช่องที่มี onkeypress="noTyping" ของ EMCS ยอมเฉพาะ [เว้นวรรค a-zA-Z0-9 ก-์ . -]
    # เส้นพิมพ์ (send_keys) จะโดนตัดอักขระอื่นทิ้ง "เงียบ ๆ" ส่วนเส้น JS fallback ยัดเข้าได้
    # แต่ onblur noTyping_paste จะล้างทั้งช่องทีหลัง → ข้อมูลชุดเดียวกันเก็บไม่เหมือนกัน
    # แล้วแต่ว่าช่องถูกซ่อนอยู่ไหม → normalize ให้เหมือนกันทั้งสองเส้น + log ให้รู้ว่าตัดอะไร
    try:
        if "noTyping" in (el.get_attribute("onkeypress") or ""):
            safe = re.sub(r"[^ a-zA-Z0-9ก-์.\-]", "", value).strip()
            if safe != value.strip():
                log(f"   ⚠️ {elem_id}: EMCS ไม่รับอักขระพิเศษ ตัดออก: {value!r} → {safe!r}")
            value = safe or "-"
    except Exception:
        pass
    # โหมดเติมส่วนที่ขาด: ค่าบนหน้าตรงอยู่แล้ว = ไม่ต้องพิมพ์ทับ (ดู SKIP_UNCHANGED)
    if SKIP_UNCHANGED:
        try:
            if _same_text(el.get_attribute("value"), value):
                log(f"   = {elem_id} ตรงอยู่แล้ว — ข้าม")
                _record_filled(elem_id, value)   # ตรงอยู่แล้ว = ยังต้องตรงตอนตรวจกลับ
                return
        except Exception:
            pass
    # จำค่า "สุดท้ายหลังแปลงแล้ว" ไว้ตรวจกลับ (ผ่าน _plus_safe + noTyping มาแล้ว)
    _record_filled(elem_id, value)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    except Exception:
        pass
    # เคลียร์ก่อนเสมอ — "set" = แทนที่ ไม่ใช่ต่อท้าย (กันค่าซ้ำเมื่อช่องมีค่าเดิม
    # เช่น กรอกซ้ำบน draft หรือ postback re-render ของบล็อกคู่กรณี)
    try:
        el.clear()
    except Exception:
        pass
    try:
        el.send_keys(value)
        return
    except (ElementNotInteractableException, StaleElementReferenceException):
        # stale = postback re-render ช่องก่อนหน้า / interactable = ถูก popup บัง
        # → ไหลไปหา element ใหม่ใน ESC-retry + JS fallback ข้างล่าง
        pass
    # อาจมี datepicker/popup ของช่องก่อนหน้าบังอยู่ — ปิดด้วย ESC แล้วลองใหม่
    try:
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        el2 = driver.find_element(By.ID, elem_id)
        try:
            el2.clear()
        except Exception:
            pass
        el2.send_keys(value)
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
    ถ้าปีเป็น พ.ศ. อยู่แล้ว (>2400) จะไม่บวกซ้ำ / ค่าว่างคืน ''
    - strip |time ที่ติดมา (se-survey เก็บ 'dd/mm/yyyy|HH:mm')
    - zero-pad วัน/เดือน เป็น 2 หลักเสมอ: EMCS date field บังคับ dd/mm/yyyy (เดือน 1 หลัก
      เช่น '23/7/2569' จาก to_char BKK ที่ไม่ pad → EMCS alert 'รูปแบบไม่ถูก')"""
    if not date_str or not date_str.strip():
        return ""
    s = date_str.strip().split("|")[0].strip()   # กัน |time ติดมา
    parts = s.split("/")
    if len(parts) != 3:
        return s                                  # format แปลก → คืนเดิม (ไม่ทำให้พังเพิ่ม)
    d, m, y = parts
    year = int(y)
    if year < 2400:
        year += 543
    return f"{int(d):02d}/{int(m):02d}/{year}"


def iso_to_thai_date(date_str: str) -> str:
    """แปลงวันที่จากไฟล์ XML ('YYYY-MM-DD[ HH:MM:SS]') เป็น dd/mm/yyyy (พ.ศ.)
    ปีในไฟล์ปนกันทั้ง ค.ศ. และ พ.ศ. — ถ้า < 2400 ถือเป็น ค.ศ. แล้วบวก 543"""
    date_str = (date_str or "").strip().split(" ")[0]
    if not date_str:
        return ""
    # se-survey/report ให้วันที่มาเป็น dd/mm/yyyy (พ.ศ.) อยู่แล้ว = ฟอร์แมตที่ EMCS ต้องการ
    # → passthrough (แปลงปี ค.ศ.→พ.ศ. เผื่อกรณีปนมา) ไม่งั้น "/" ที่ไม่มี "-" จะถูกทิ้งเป็นค่าว่าง
    if "/" in date_str and "-" not in date_str:
        try:
            d, m, y = date_str.split("/")
            year = int(y)
            if year < 2400:
                year += 543
            return f"{int(d):02d}/{int(m):02d}/{year}"
        except ValueError:
            return ""
    if "-" not in date_str:
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


# ------------------------------------------- ชี้ช่องที่ต้องแก้บนหน้า EMCS (Chrome)
#
# เวลาบอทหยุดรอ คนต้องไล่หาเองว่า "ช่องไหน" ในฟอร์มยาว ๆ ของ EMCS — ยิง CSS/JS
# เข้าไปตีกรอบแดงกระพริบที่ช่องนั้น + เลื่อนจอไปหา + ขึ้นแถบบอกเหตุผลค้างไว้
# (alert ของ EMCS กดตกลงแล้วหาย อ่านย้อนไม่ได้) + ดึงหน้าต่าง Chrome ขึ้นหน้า
#
# ⚠️ ทุกอย่างหายเมื่อหน้า postback (ASP.NET โหลดหน้าใหม่) — ตั้งใจให้เป็นแบบนั้น
# ไฮไลต์คือ "ป้ายชั่วคราว" ไม่ใช่ state ที่ต้องรักษา ห้ามให้ความล้มเหลวของมัน
# ทำให้การหยุดรอพัง (เรียกผ่าน _safe_js ที่กลืน error ทุกชนิด)

_ACTIVE_DRIVER = None      # Chrome ของ process นี้ (1 process = 1 browser เสมอ)


def set_active_driver(driver):
    """จำ driver ไว้ ให้ wait_for_manual_fill ชี้ช่องบนหน้า EMCS ได้
    โดยไม่ต้องส่ง driver ผ่านทุกชั้นที่เรียกมัน"""
    global _ACTIVE_DRIVER
    _ACTIVE_DRIVER = driver


_HL_CSS = """
@keyframes se-hl-pulse{0%{box-shadow:0 0 0 0 rgba(220,38,38,.7)}
70%{box-shadow:0 0 0 12px rgba(220,38,38,0)}100%{box-shadow:0 0 0 0 rgba(220,38,38,0)}}
.se-hl-target{outline:3px solid #dc2626!important;outline-offset:1px;
background:#fff1f2!important;animation:se-hl-pulse 1.5s infinite}
.se-hl-check{outline:2px dashed #d97706!important;background:#fffbeb!important}
#se-hl-bar{position:fixed;right:18px;bottom:18px;z-index:2147483647;max-width:400px;
background:#dc2626;color:#fff;border-radius:10px;padding:12px 38px 12px 14px;
font:600 14px/1.45 "Segoe UI",Tahoma,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.35)}
#se-hl-bar small{display:block;font-weight:400;font-size:12.5px;opacity:.93;margin-top:4px}
#se-hl-bar .se-hl-tip{opacity:.8;font-style:italic}
#se-hl-x{position:absolute;top:6px;right:8px;background:none;border:0;color:#fff;
font-size:16px;line-height:1;cursor:pointer;opacity:.75}
"""

_HL_SHOW_JS = r"""
var q = arguments[0], css = arguments[1], d = document, w = window;
if (!d.getElementById('se-hl-css')) {
  var s = d.createElement('style'); s.id = 'se-hl-css'; s.textContent = css;
  (d.head || d.documentElement).appendChild(s);
}
Array.prototype.forEach.call(d.querySelectorAll('.se-hl-target'), function (e) {
  e.classList.remove('se-hl-target');
});
var first = null, hit = 0;
(q.ids || []).forEach(function (id) {
  var e = d.getElementById(id);
  if (!e) return;
  e.classList.add('se-hl-target');
  if (!first) first = e;
  hit++;
});
/* ไม่รู้ id (validation ของ EMCS ฟ้องมาเป็น "ชื่อช่อง") — หาจากป้ายในฟอร์ม:
   cell ที่ข้อความ "เกือบเท่ากับ" ชื่อช่องพอดี = ป้าย ไม่ใช่กล่องครอบทั้งหน้า
   แล้วไล่ไปหา input/select ตัวแรกในช่องถัดไป (ฟอร์ม EMCS เป็นตาราง) */
(q.labels || []).forEach(function (t) {
  t = (t || '').replace(/\s+/g, ' ').trim();
  if (t.length < 3) return;
  var cells = d.querySelectorAll('td,th,label,span'), i, c, txt, f, sib;
  for (i = 0; i < cells.length; i++) {
    c = cells[i];
    if (c.querySelector('input,select,textarea')) continue;
    txt = (c.textContent || '').replace(/\s+/g, ' ').trim();
    if (txt.indexOf(t) < 0 || txt.length > t.length + 25) continue;
    f = null; sib = c.nextElementSibling;
    while (sib && !f) {
      f = sib.querySelector('input:not([type=hidden]),select,textarea');
      sib = sib.nextElementSibling;
    }
    if (!f && c.closest('tr')) {
      f = c.closest('tr').querySelector('input:not([type=hidden]),select,textarea');
    }
    if (f) { f.classList.add('se-hl-target'); if (!first) first = f; hit++; }
    break;
  }
});
var bar = d.getElementById('se-hl-bar');
if (!bar) { bar = d.createElement('div'); bar.id = 'se-hl-bar'; d.body.appendChild(bar); }
bar.textContent = '';
var x = d.createElement('button');
x.id = 'se-hl-x'; x.textContent = '✕';
x.onclick = function () { bar.remove(); };
bar.appendChild(x);
var h = d.createElement('div');
h.textContent = '⏸️ ' + (q.title || '');   /* ⏸️ */
bar.appendChild(h);
[q.reason, q.tip].forEach(function (t, i) {
  if (!t) return;
  var sm = d.createElement('small');
  if (i) sm.className = 'se-hl-tip';
  sm.textContent = t;
  bar.appendChild(sm);
});
if (first) first.scrollIntoView({block: 'center', behavior: 'smooth'});
if (!w.__seHlTitle) w.__seHlTitle = d.title;
d.title = '⏸️ ' + (q.title || 'se-autokey');
return hit;
"""

_HL_CLEAR_JS = r"""
var d = document, w = window, bar = d.getElementById('se-hl-bar');
if (bar) bar.remove();
Array.prototype.forEach.call(d.querySelectorAll('.se-hl-target'), function (e) {
  e.classList.remove('se-hl-target');
});
if (w.__seHlTitle) { d.title = w.__seHlTitle; w.__seHlTitle = null; }
"""

_HL_MARK_JS = r"""
var d = document, e = d.getElementById(arguments[0]), css = arguments[2];
if (!d.getElementById('se-hl-css')) {
  var s = d.createElement('style'); s.id = 'se-hl-css'; s.textContent = css;
  (d.head || d.documentElement).appendChild(s);
}
if (!e) return false;
e.classList.add('se-hl-check');
if (arguments[1]) e.title = arguments[1];
return true;
"""


def _safe_js(driver, script, *args):
    """ยิง JS ตกแต่งหน้า — ล้มเหลวเงียบเสมอ (browser ปิด/มี alert ค้าง/หน้าเปลี่ยน)
    การชี้ช่องเป็นของแถม ห้ามทำให้งานหลักพัง"""
    if driver is None:
        return None
    try:
        return driver.execute_script(script, *args)
    except Exception:
        return None


def bring_to_front(driver):
    """ดึงหน้าต่าง Chrome ขึ้นมาหน้าสุด — คนอาจกำลังดูหน้าเว็บ webui อยู่"""
    if driver is None:
        return
    try:
        driver.execute_cdp_cmd("Page.bringToFront", {})
    except Exception:
        pass


def highlight_wait(driver, ids, title, reason="", tip="", raise_window=True,
                   labels=None):
    """ตีกรอบแดง + เลื่อนจอไปหาช่องที่บอทรออยู่ พร้อมแถบบอกเหตุผลค้างไว้

    ids    = id ของช่อง (รู้แน่ ๆ ว่าช่องไหน)
    labels = "ชื่อช่อง" ที่ EMCS ฟ้องมาใน validation — หาช่องจากป้ายในฟอร์มให้
    raise_window=False เมื่อคนตอบได้บนหน้า webui อยู่แล้ว (มี dropdown ให้เลือก) —
    ดึง Chrome ขึ้นหน้าตอนนั้นคือแย่งโฟกัสจากหน้าที่เขากำลังจะกด
    คืนจำนวนช่องที่ชี้ได้ (0 = ขึ้นแค่แถบแจ้งเตือน)"""
    ids = [i for i in ([ids] if isinstance(ids, str) else (ids or [])) if i]
    labels = [str(s).strip() for s in ([labels] if isinstance(labels, str)
                                       else (labels or [])) if str(s).strip()]
    hit = _safe_js(driver, _HL_SHOW_JS,
                   {"ids": ids, "labels": labels, "title": title,
                    "reason": reason, "tip": tip},
                   _HL_CSS)
    if raise_window:
        bring_to_front(driver)
    return int(hit or 0)


def highlight_clear(driver):
    """เก็บกรอบแดง + แถบแจ้งเตือน (ย้อมเหลือง se-hl-check ยังอยู่ ให้คนตรวจต่อ)"""
    _safe_js(driver, _HL_CLEAR_JS)


def mark_check(driver, elem_id, note=""):
    """ย้อมเหลืองช่องที่ "บอทกรอกให้แล้วแต่ไม่มั่นใจ" — ค้างไว้จนกว่าหน้าจะ postback
    ให้คนตรวจกวาดตาเห็นได้ทันทีว่าต้องดูช่องไหนก่อนกดส่งงาน"""
    return bool(_safe_js(driver, _HL_MARK_JS, elem_id, note, _HL_CSS))


# ----------------------------------------------- หยุดรอให้คนกรอกข้อมูลเอง

def _parse_choice(line: str, options) -> str:
    """อ่านค่าที่ผู้ใช้เลือกจากหน้าเว็บ — คืน '' ถ้าไม่ได้เลือก/อ่านไม่ได้

    รับได้ทั้ง {"choice": "เก๋งเอเชีย"} (เลือกจาก dropdown) และ newline เปล่า
    (กด 'ดำเนินการต่อ' เฉย ๆ = กรอกเองบนหน้า EMCS แล้ว)
    ตรวจว่าค่าอยู่ในลิสต์จริง — กันค่าแปลกปลอมหลุดไปเข้า fuzzy_select"""
    s = (line or "").strip()
    if not s or not s.startswith("{"):
        return ""
    try:
        val = str((json.loads(s) or {}).get("choice") or "").strip()
    except Exception:
        return ""
    if not val:
        return ""
    return val if (not options or val in options) else ""


def wait_for_manual_fill(field_label, reason="", select_id=None, options=None,
                         focus_ids=None, focus_labels=None, driver=None):
    """หยุดรอให้ผู้ใช้กรอก/เลือกข้อมูลช่องนี้ แล้วค่อยทำงานต่อ

    ใช้เมื่อข้อมูลจาก ISURVEY ไม่ครบ หรือกรอกอัตโนมัติไม่ได้ — ดีกว่าปล่อย
    error จบโปรแกรม คนจะได้เติมช่องที่ขาดให้ครบแล้วสั่งไปต่อ

    - **หน้าเว็บ + ส่ง options มา**: เว็บโชว์ dropdown ให้เลือกได้ในหน้าเว็บเลย
      ไม่ต้องสลับไปหน้าต่าง EMCS (ผู้เรียกเอาค่าที่คืนไปเลือกลงช่องเอง)
    - หน้าเว็บ ไม่มี options: โชว์ปุ่ม 'ดำเนินการต่อ' เฉย ๆ (คนไปกรอกบน EMCS)
    - console จริง: ผู้ใช้กด Enter ที่หน้าต่างเอง

    ทุกกรณี: ตีกรอบแดงกระพริบให้ช่องนั้นบนหน้า EMCS + ขึ้นแถบบอกเหตุผลค้างไว้
    (focus_ids = id ช่องที่จะชี้; ไม่ส่งมาจะใช้ select_id) — จะได้ไม่ต้องไล่หาเอง
    ว่าฟอร์มยาว ๆ นี้ติดตรงไหน
    - ไม่มี console/stdin ปิด (รันแบบไม่มีคนเฝ้า): readline คืน "" ทันที →
      ไปต่อ ไม่ค้าง (อาศัย EOF ของ stdin ไม่พึ่ง isatty ที่บน Windows เชื่อถือไม่ได้)
    ไม่ขึ้นกับ -y (นี่คือการหยุดเพราะข้อมูลไม่ครบ ไม่ใช่ถามยืนยัน)

    คืน:
      str ที่ผู้ใช้เลือก — เมื่อเลือกจาก dropdown บนหน้าเว็บ
      True  — ผู้ใช้สั่งต่อโดยไม่ได้เลือกค่า (ไปกรอกบน EMCS เองแล้ว)
      False — ไม่มีใครตอบ (EOF) แล้วไปต่อเอง
    (str ที่ไม่ว่างเป็น truthy ผู้เรียกเดิมที่เช็ค `if wait_for_manual_fill(...)` ใช้ได้เหมือนเดิม)
    """
    options = [o for o in (options or []) if str(o).strip()]
    on_web = bool(_WEBUI and options)     # ตอบบนหน้าเว็บได้ ไม่ต้องไปแตะ EMCS
    log_plain("")
    log(f"⏸️  ต้องกรอกข้อมูลเอง: {field_label}")
    if reason:
        log(f"     สาเหตุ: {reason}")
    if on_web:
        log(f"     → เลือกค่าบนหน้าเว็บได้เลย ({len(options)} ตัวเลือก) "
            "ไม่ต้องสลับไปหน้าต่าง EMCS")
    else:
        log("     → กรอก/เลือกข้อมูลช่องนี้ในหน้าต่าง EMCS (Chrome) ให้เรียบร้อย แล้ว"
            + ("กดปุ่ม 'ดำเนินการต่อ' บนหน้าเว็บ"
               if _WEBUI else "กลับมากด Enter ที่หน้าต่างนี้") + " เพื่อทำงานต่อ")
    # ชี้ช่องบนหน้า EMCS ให้เห็นด้วยตา (ไม่ขัดจังหวะการทำงาน ถ้าทำไม่ได้ก็ข้าม)
    drv = driver if driver is not None else _ACTIVE_DRIVER
    ids = focus_ids if focus_ids is not None else select_id
    hit = highlight_wait(
        drv, ids, field_label, reason, labels=focus_labels,
        tip=("เลือกค่าบนหน้าเว็บ se-autokey ได้เลย" if on_web
             else "กรอกช่องนี้ แล้วสั่ง 'ดำเนินการต่อ' ที่หน้าเว็บ se-autokey"
             if _WEBUI else "กรอกช่องนี้ แล้วกด Enter ที่หน้าต่างคำสั่ง"),
        raise_window=not on_web)
    if hit:
        log(f"     🔴 ตีกรอบแดงไว้บนหน้า EMCS แล้ว {hit} ช่อง (เลื่อนจอไปให้เห็นด้วย)")
    if _WEBUI:
        # marker บรรทัดเดียว ให้ webui จับไปโชว์กล่องแจ้งเตือน + dropdown (ถ้ามี)
        print(MANUAL_MARKER + json.dumps(
            {"label": field_label, "reason": reason,
             "select_id": select_id or "", "options": options},
            ensure_ascii=False), flush=True)
    try:
        line = sys.stdin.readline()   # block จนได้ Enter (console)/payload (webui); "" ถ้า EOF
    except Exception:
        line = ""
    highlight_clear(drv)              # ตอบแล้ว เก็บกรอบแดง+แถบแจ้งเตือน
    if line == "":
        # stdin ปิด/EOF = ไม่มีคนเฝ้า → ไปต่อ ไม่ค้าง (ช่องนี้ต้องกรอกเองภายหลัง)
        log("     (ไม่มีการตอบกลับจาก stdin — ไปต่อ ตรวจ/กรอกช่องนี้เองภายหลัง)")
        return False
    choice = _parse_choice(line, options)
    if choice:
        log(f"     ▶️ ผู้ใช้เลือก '{choice}' จากหน้าเว็บ — กรอกให้เลย")
        return choice
    log(f"     ▶️ ดำเนินการต่อ ({field_label})")
    return True


SUBMIT_MARKER = "@@READY_SUBMIT@@"  # ต้องตรงกับค่าใน webui.py
SENT_MARKER = "@@JOB_SENT@@"        # ต้องตรงกับค่าใน webui.py (ส่งงานสำเร็จแล้ว)
SEND_FAIL_MARKER = "@@JOB_SEND_FAIL@@"   # ต้องตรงกับค่าใน webui.py (กดส่งแล้วไม่ผ่าน)


def announce_send_failed(claim: str, reason: str = ""):
    """บอกหน้าเว็บว่า "สั่งส่งงานแล้วแต่ไม่สำเร็จ"

    ทำไมต้องมี: process จบด้วย exit code 0 (งานอื่นทำครบ) → การ์ดขึ้น
    "เสร็จแล้ว ✅" ทั้งที่ส่งไม่ผ่าน = รายงานหลอกตา คนเห็นแล้วนึกว่าจบ
    ทั้งที่ยังต้องไปกดส่งเองบน EMCS"""
    if not _WEBUI:
        return
    print(SEND_FAIL_MARKER + json.dumps(
        {"claim": claim, "reason": reason}, ensure_ascii=False), flush=True)


def announce_sent(claim: str, esurvey: str = "", keyer: str = ""):
    """บอกหน้าเว็บว่า "งานนี้ส่งขึ้น EMCS สำเร็จและตรวจสถานะแล้ว"

    ใช้เป็นสัญญาณให้การ์ดปิดตัวเอง — ประกาศ **หลัง** verify สถานะบน EMCS เท่านั้น
    (ไม่ใช่แค่กดปุ่มแล้วเชื่อ) ข้อมูลไม่หายเพราะลงสมุดงานถาวรไว้แล้ว"""
    if not _WEBUI:
        return
    print(SENT_MARKER + json.dumps(
        {"claim": claim, "esurvey": esurvey, "keyer": keyer}, ensure_ascii=False),
        flush=True)


def wait_for_submit(claim, survey_no="", reason=""):
    """หลังกรอกครบ (live session) — รอผู้ใช้ตรวจ draft + เลือกประเภทงาน แล้วสั่งส่ง
    กลไกเดียวกับ wait_for_manual_fill (marker + รอ stdin /continue):
    - หน้าเว็บ: ส่ง SUBMIT_MARKER (พร้อม base_type default) → เว็บโชว์แผงเลือกประเภทงาน
      + ปุ่ม "✅ ส่งงาน + แจ้ง ISURVEY" → ส่ง {base_type, batch, mix} กลับเข้า stdin
    - console: กด Enter = ส่งด้วย default (งานต้น / SESV ถ้าเลขขึ้นต้น SESV)
    - ไม่มีคนเฝ้า (EOF): คืน None → เก็บเป็น draft ไม่ส่ง (พฤติกรรมเดิม)
    คืน dict {base_type, batch, mix} ถ้าสั่งส่ง / None ถ้าไม่ส่ง (เก็บ draft)"""
    default_base = ("SESV" if str(survey_no or "").strip().upper().startswith("SESV")
                    else "งานต้น")
    log_plain("")
    log(f"⏸️  กรอกครบแล้ว (เคลม {claim}) — ตรวจ draft ให้เรียบร้อย แล้วสั่งส่งงาน")
    log("     → ตรวจความถูกต้องในหน้าต่าง EMCS (Chrome) ก่อน แล้ว"
        + ("เลือกประเภทงาน + กดปุ่ม '✅ ส่งงาน + แจ้ง ISURVEY' บนหน้าเว็บ"
           if _WEBUI else "กด Enter ที่หน้าต่างนี้")
        + " (ระบบจะกด 'ส่งงานใหม่' ให้ + แจ้ง ISURVEY + บันทึก se-key)")
    if _WEBUI:
        print(SUBMIT_MARKER + json.dumps(
            {"claim": claim, "survey_no": survey_no, "base_type": default_base,
             "reason": reason}, ensure_ascii=False), flush=True)
    try:
        line = sys.stdin.readline()
    except Exception:
        line = ""
    if line == "":
        log("     (ไม่มีการตอบกลับ — เก็บเป็น draft ไม่ส่งงาน ตรวจ/กดส่งเองภายหลังได้)")
        return None
    sel = {"base_type": default_base, "batch": False, "mix": []}
    try:                                   # console กด Enter เปล่า → ใช้ default
        d = json.loads(line)
        if isinstance(d, dict):
            sel["base_type"] = d.get("base_type") or default_base
            sel["batch"] = bool(d.get("batch"))
            sel["mix"] = [str(m).strip() for m in (d.get("mix") or []) if str(m).strip()]
    except Exception:
        pass
    log(f"     ▶️ สั่งส่งงาน (เคลม {claim}, ประเภทงาน {sel['base_type']}"
        + (" +งานรวม" if sel["batch"] else "") + ")")
    return sel


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
    """คืน {ชื่อไฟล์: หมวด} ของรูปแต่ละไฟล์ — อ่านจาก `_categories.json`
    ไม่มี manifest / ไม่เจอ = หมวด 'OTHERS'
    หมวด: INS=รูปรถประกัน, REPORTS=เอกสาร/ใบรับงาน, OTHERS=อื่นๆ

    **ลองชื่อไฟล์ปัจจุบันก่อนเสมอ** แล้วค่อยถอยไปหาผ่าน `_rename_map.json`
    (ชื่อใหม่→ชื่อเดิม): ตั้งแต่ `images.prepare_images` เขียน manifest กลับด้วย
    sha1 หลัง rename (ดู images.py:_rewrite_manifest) คีย์ใน `_categories.json`
    ก็เป็น "ชื่อปัจจุบัน" อยู่แล้ว การแปลงผ่าน rename map จึงกลายเป็นการแปลง
    ทิ้ง — ชี้ไปที่ชื่อต้นทางฝั่งเซิร์ฟเวอร์ ('DOC_Claimform.jpg') ซึ่งไม่มีใน
    manifest → ตกเป็น OTHERS ทั้งกอง (เจอจริง เคลม 2026013059072 2026-08-05:
    22/22 รูปขึ้น OTHERS ทั้งที่ manifest ถูกต้อง ปุ่ม 'เลือกทั้งหมวด' เลยใช้ไม่ได้)
    เก็บเส้น rename map ไว้เป็น fallback สำหรับโฟลเดอร์เก่าที่ยังไม่ได้ rewrite"""
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
        out[f] = cats.get(f) or cats.get(rmap.get(f, f)) or "OTHERS"
    return out


def wait_for_image_select(folder, files, extra: int = 0):
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
    # extra = รูปบุคคลที่สาม (tp_veh/tp_person/tp_prop) ที่อัปอัตโนมัติ ไม่อยู่ในแกลเลอรี
    # ต้องบอกไปด้วย ไม่งั้นตัวเลขบนจอไม่ตรงกับที่ขึ้น EMCS จริง
    _more = f" + รูปคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินอีก {extra} รูป (อัปให้อัตโนมัติ)" if extra else ""
    log(f"⏸️  เลือกรูปที่จะอัปโหลดเข้า EMCS ({len(files)} รูป{_more}) — "
        "ติ๊กเลือกบนหน้าเว็บแล้วกดปุ่มอัปโหลด")
    cat_of = _image_categories(folder, files)
    images = [{"name": f, "cat": cat_of.get(f, "OTHERS")} for f in files]
    print(SELECT_IMAGES_MARKER + json.dumps(
        {"folder": str(folder), "images": images, "extra": int(extra or 0)},
        ensure_ascii=False), flush=True)
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


INJURY_INPUTS_MARKER = "@@INJURY_INPUTS@@"  # ต้องตรงกับค่าใน webui.py

# ตัวเลือก "ประเภทผู้บาดเจ็บ" (value = code ddlPerson_Type) — fallback ถ้าอ่านจาก
# หน้าจริงไม่ได้ (จริงๆ 02/04 'รถคู่กรณี' จะมีเฉพาะตอนเคลมมีคู่กรณี = dynamic)
INJ_PERSON_TYPE_OPTIONS = [
    {"value": "01", "label": "ผู้ขับขี่ - รถประกัน"},
    {"value": "02", "label": "ผู้ขับขี่ - รถคู่กรณี"},
    {"value": "03", "label": "ผู้โดยสาร - รถประกัน"},
    {"value": "04", "label": "ผู้โดยสาร - รถคู่กรณี"},
    {"value": "05", "label": "บุคคลภายนอกรถ"},
]


def wait_for_injury_inputs(persons, options=None):
    """ให้ผู้ใช้กรอก 'เลขทะเบียน' + เลือก 'ประเภทผู้บาดเจ็บ' ของผู้บาดเจ็บแต่ละคน
    บนหน้าเว็บ — EMCS บังคับเลขทะเบียนผู้บาดเจ็บก่อนเข้าหน้าค่าใช้จ่าย แต่ ISURVEY ว่าง

    persons = [{name, person_type_value(default จาก ISURVEY), car_regno}, ...]
    options = ตัวเลือกประเภทผู้บาดเจ็บ [{value,label}] ที่อ่านจาก ddlPerson_Type
      หน้าจริง (dynamic — มี 02/04 'รถคู่กรณี' เฉพาะตอนเคลมมีคู่กรณี); None →
      ใช้ INJ_PERSON_TYPE_OPTIONS เป็น fallback
    - หน้าเว็บ (webui): marker → ฟอร์มต่อคน (dropdown ประเภท default + ช่องเลขทะเบียน)
      → ส่ง {"persons":[{person_type, car_regno}, ...]} กลับเข้า stdin
    - console / EOF / ไม่ใช่ webui: คืน None = ใช้ค่า ISURVEY เดิม (เลขทะเบียนว่าง →
      billing gate เด้ง ให้กรอกเองบน EMCS ภายหลัง)
    คืน list ต่อคน [{person_type, car_regno}] หรือ None
    """
    if not _WEBUI:
        return None
    opts = options or INJ_PERSON_TYPE_OPTIONS
    log_plain("")
    log(f"⏸️  กรอกเลขทะเบียน + เลือกประเภทผู้บาดเจ็บ {len(persons)} คน บนหน้าเว็บ "
        "(EMCS บังคับก่อนเข้าหน้าค่าใช้จ่าย)")
    print(INJURY_INPUTS_MARKER + json.dumps(
        {"persons": persons, "person_type_options": opts},
        ensure_ascii=False), flush=True)
    try:
        line = sys.stdin.readline()
    except Exception:
        line = ""
    if line == "":
        log("     (ไม่มีการตอบกลับ — ใช้ค่า ISURVEY เดิม; เลขทะเบียนว่างต้องกรอกเองบน EMCS)")
        return None
    try:
        data = json.loads(line)
        result = data.get("persons") if isinstance(data, dict) else data
    except Exception:
        result = None
    if not isinstance(result, list):
        log("     (อ่านค่าไม่ได้ — ใช้ค่า ISURVEY เดิม)")
        return None
    log(f"     ▶️ ได้ข้อมูลผู้บาดเจ็บ {len(result)} คนจากผู้ใช้")
    return result


# ---------------------------------------------------------------- dropdown

def _current_select_text(driver, select_id) -> str:
    """อ่านข้อความ option ที่ถูกเลือกอยู่ตอนนี้ (ใช้หลังให้คนเลือกเอง)"""
    try:
        return Select(driver.find_element(By.ID, select_id)).first_selected_option.text
    except Exception:
        return ""


def fuzzy_select(driver, select_id, value, wait_options=True, timeout=10,
                 presleep=0.0, label="", required=False,
                 min_score=FUZZY_MIN_SCORE):
    """เลือก option ใน dropdown ด้วย fuzzy matching (rapidfuzz WRatio)

    รวม pattern ที่ซ้ำใน notebook เดิม: รอ dropdown → รอ options โหลด →
    เก็บข้อความ options → หา match ที่ใกล้สุด → เลือก
    มี retry กัน StaleElementReference จาก ASP.NET postback

    required=True (field บังคับของ EMCS): ถ้าค่าว่าง หรือเลือกอัตโนมัติไม่ได้
      จะ "หยุดรอให้คนกรอกเอง" แทนการข้าม/error
    required=False: ค่าว่าง → ข้าม (เหมือนเดิม); เลือกไม่ได้ → หยุดรอให้คนกรอก
      เช่นกัน (ไม่ปล่อย error จบโปรแกรม)
    min_score: ต่ำกว่านี้ = ไม่เลือกให้ หยุดรอคน. ดรอปดาวน์ที่ป้ายเป็น "ชุดคำตายตัว"
      (ยี่ห้อรถ — ลิสต์ถูกกรองตามประเภทรถ ยี่ห้อที่ไม่มีในลิสต์จะไปเกาะยี่ห้ออื่นได้)
      ควรตั้งสูง (90) เพราะค่าที่ถูกต้องได้ ≥90 เสมอ ส่วนค่ามั่วเกาะไม่เกิน 80

    คืนค่า (ข้อความที่เลือก, คะแนน) หรือ None ถ้าค่าว่างและไม่บังคับ
    """
    name = label or select_id
    if value is None or str(value).strip() == "":
        if required:
            log(f"   ⚠️ ไม่มีข้อมูล {name} จาก ISURVEY (เป็น field บังคับ)")
            return _manual_pick(driver, select_id, name, None, None,
                                "ISURVEY ไม่มีข้อมูลช่องนี้")
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
            # โหมดเติมส่วนที่ขาด: ตัวที่เลือกอยู่ตรงกับที่จะเลือกแล้ว = ไม่ต้องเลือกซ้ำ
            # (เลือกซ้ำ = ยิง onchange → postback ทั้งหน้า เสียเวลาที่สุดในบรรดาช่องทั้งหมด)
            if SKIP_UNCHANGED:
                try:
                    cur = sel.first_selected_option.text
                    if _same_text(cur, value):
                        log(f"   = {name} ตรงอยู่แล้ว ('{cur}') — ข้าม")
                        return cur, 100
                except Exception:
                    pass
            # ตัวแรกที่ value="0"/"" = placeholder ของ dropdown นี้ (ตรวจทั้งหน้า EMCS
            # 449 select แล้ว ไม่มีตัวเลือกจริงตัวไหนใช้ value 0) — เชื่อถือได้กว่าเดา
            # จากข้อความ เพราะข้อความต่างกันไปตามช่อง: '-- ระบุ --', '-- จังหวัด --',
            # '-- เขต --', '- คำนำหน้า -'
            ph = None
            try:
                if sel.options and sel.options[0].get_attribute("value") in ("", "0"):
                    ph = options[0]
            except Exception:
                pass

            # ตรงเป๊ะมาก่อน (กันเหนียว ไม่ใช่แก้บั๊กที่เกิดแล้ว): ตอนนี้ทุก master ที่ดัมพ์มา
            # exact ชนะ fuzzy อยู่แล้ว 45/45 dropdown — แต่ WRatio ให้ "สตริงสั้นที่เป็น
            # คำนำหน้า" ได้ถึง 100 เช่นกัน ถ้าวันหนึ่งเสมอกัน extractOne จะคืนตัวแรกในลิสต์
            # (= ตัวสั้น) แทนตัวที่ตรงเป๊ะ. ค่าที่ลอก master มาแล้วต้องเลือกได้แน่นอน
            _v = str(value).strip()
            _exact = next((o for o in options if o.strip() == _v), None)
            if _exact is not None:
                log(f"   ✓ {name}: '{value}' → '{_exact}' (ตรงเป๊ะ)")
                Select(driver.find_element(By.ID, select_id)).select_by_visible_text(_exact)
                return _exact, 100

            best = process.extractOne(str(value), options, scorer=fuzz.WRatio)
            text, score = best[0], best[1]

            # ป้ายที่ลงท้ายด้วย "เลขลำดับ" (… คนที่ N / คันที่ N / รายการที่ N / ชิ้นที่ N)
            # = คนละรายการกัน — WRatio ให้ 'คนที่ 12' เกาะ 'คนที่ 1' ได้ถึง 98 → รูปติดผิดคน
            # แบบเงียบ. เลขไม่ตรง = ตัดคะแนนทิ้ง ให้ตกเข้า guard ข้างล่าง (ไม่เลือกให้ หยุดรอคน)
            # ⚠️ เช็คเฉพาะค่าที่มี "คำบอกลำดับ" เท่านั้น — ห้ามเหมาทุกค่าที่ลงท้ายด้วยเลข
            # ไม่งั้นยี่ห้อที่พ่วงรุ่น ('MG 3' → MG 90, 'MAZDA 2' → MAZDA 95) จะถูกตัดทิ้งด้วย
            _want = re.search(r"(?:คันที่|คนที่|ชิ้นที่|รายการที่)\s*(\d+)\s*$", str(value))
            if _want:
                _got = re.search(r"(?:คันที่|คนที่|ชิ้นที่|รายการที่)\s*(\d+)\s*$", text)
                if not _got or _got.group(1) != _want.group(1):
                    score = 0

            # กันเลือก placeholder/มั่ว: extractOne คืน "ตัวที่ใกล้สุด" เสมอ แม้ไม่มี
            # ตัวไหนใกล้จริง — เคส #104 ยี่ห้อไทย 'เอ็มจี' เจอ dropdown อังกฤษ ได้ 0
            # คะแนน แล้วโค้ดเดิม "เลือก '-- ระบุ --'" ให้ = ช่องบังคับว่างเงียบ ๆ
            # ('-' ที่กติกา _dash ใส่ให้ช่องบังคับ ก็ไปเกาะ '-- เขต --' ได้ 60 คะแนน)
            # ตอนนี้ไม่แตะ dropdown แล้วหยุดรอคนเลือกแทน (webui = ปุ่มดำเนินการต่อ,
            # ไม่มีคนเฝ้า = ไปต่อ ปล่อยให้คนตรวจแก้บนหน้าจอ)
            if text == ph or _is_placeholder_option(text) or score < min_score:
                log(f"   ⚠️ {name}: '{value}' ไม่ตรงกับตัวเลือกไหนเลย "
                    f"(ใกล้สุด '{text}' score {score:.0f}) — ไม่เลือกให้")
                # ส่งตัวเลือก "จริงจากหน้านี้ตอนนี้" ไปให้เว็บ (ไม่ใช่จากสเปกที่ดัมป์ไว้)
                # — dropdown ที่ผูกกับช่องก่อนหน้า (ยี่ห้อ←ประเภทรถ, อำเภอ←จังหวัด)
                # ลิสต์ต่างกันไปตามที่เลือกไว้ ต้องอ่านสด ๆ ถึงจะตรง
                return _manual_pick(
                    driver, select_id, name, options, ph,
                    f"ไม่มีตัวเลือกที่ตรงกับ '{value}' ในดรอปดาวน์นี้")

            mark = "⚠️" if score < FUZZY_WARN_SCORE else "✓"
            log(f"   {mark} {name}: '{value}' → '{text}' (score {score:.0f})")
            Select(driver.find_element(By.ID, select_id)).select_by_visible_text(text)
            if score < FUZZY_WARN_SCORE:
                log(f"     ** คะแนนต่ำ ควรตรวจสอบด้วยตาก่อนบันทึก **")
                # ย้อมเหลืองค้างไว้บนหน้า EMCS — คนตรวจจะได้เห็นว่า "ช่องไหน"
                # ที่บอทเดาแบบไม่มั่นใจ ไม่ต้องไล่อ่าน log ย้อนหลัง
                mark_check(driver, select_id,
                           f"se-autokey เดาจาก '{value}' (คะแนน {score:.0f}) — ตรวจก่อนส่งงาน")
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
    return _manual_pick(driver, select_id, name, _live_options(driver, select_id), None,
                        f"เลือก '{value}' อัตโนมัติไม่ได้ — dropdown ไม่พร้อม")


def _live_options(driver, select_id) -> list:
    """ข้อความของตัวเลือกทั้งหมดใน dropdown นี้ "ตอนนี้" — [] ถ้าอ่านไม่ได้"""
    try:
        return [o.text for o in Select(driver.find_element(By.ID, select_id)).options]
    except Exception:
        return []


def _manual_pick(driver, select_id, name, options, placeholder, reason):
    """หยุดรอคนเลือกค่าให้ dropdown นี้ — ถ้าเลือกจากหน้าเว็บมา ก็เลือกลงช่องให้เลย

    คืน (ข้อความที่อยู่ในช่องตอนจบ, score) แบบเดียวกับ fuzzy_select"""
    if options is None:      # ผู้เรียกยังไม่ได้อ่านลิสต์ — รอ dropdown โหลดสักครู่แล้วอ่านเอง
        try:
            WebDriverWait(driver, 5).until(
                lambda d: len(Select(d.find_element(By.ID, select_id)).options) > 1)
        except Exception:
            pass
        options = _live_options(driver, select_id)
    picks = [o for o in (options or [])
             if o and o != placeholder and not _is_placeholder_option(o)]
    ans = wait_for_manual_fill(name, reason, select_id=select_id, options=picks,
                               driver=driver)
    if isinstance(ans, str) and ans:
        try:
            Select(driver.find_element(By.ID, select_id)).select_by_visible_text(ans)
            log(f"   ✓ {name}: เลือก '{ans}' ตามที่ผู้ใช้ระบุจากหน้าเว็บ")
            return ans, 100
        except Exception as e:
            log(f"   ⚠️ เลือก '{ans}' ลงช่องไม่สำเร็จ ({type(e).__name__}) — "
                "เลือกเองบนหน้า EMCS")
    return _current_select_text(driver, select_id), 0
