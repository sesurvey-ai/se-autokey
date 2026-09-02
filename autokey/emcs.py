"""ฝั่งกรอกข้อมูล: login EMCS → สร้างงานใหม่ → กรอกทุกส่วน → อัปโหลดรูป → ค่าใช้จ่าย

โมเดลความปลอดภัย: "บันทึก" ทุกหน้า = draft แก้ไขได้ สคริปต์กดให้ครบ
จุด commit จริงคือปุ่ม 'ส่งงานใหม่' หน้าค่าใช้จ่าย — ไม่กดให้เด็ดขาด
"""
import hashlib
import re
import time
from datetime import datetime
from pathlib import Path

from rapidfuzz import fuzz, process
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    UnexpectedAlertPresentException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from .browser import (
    _current_select_text,
    _is_placeholder_option,
    accept_alert,
    click_retry,
    fuzzy_select,
    iso_to_thai_date,
    log,
    mark_check,
    set_skip_unchanged,
    set_text,
    set_textarea,
    split_hhmm,
    to_buddhist_date,
    today_buddhist,
    wait_clickable,
    wait_for_image_select,
    wait_for_injury_inputs,
    wait_for_manual_fill,
    record_filled,
    reset_filled,
    set_rule_context,
    verify_filled,
    wait_present,
    wait_visible,
)
from .car_brand import BRAND_MIN_SCORE, normalize_brand
from .insurer_map import resolve_insurer_code_by_job_no
# ชื่อ/คำนำหน้า/เพศ อยู่ที่ claim_data (ฝั่งอ่าน ISURVEY ต้องใช้ด้วย) — re-export
# ไว้ที่นี่เพื่อให้ผู้เรียกเดิม (main.py, webui.py, test_smoke.py) ไม่ต้องแก้
from .claim_data import (  # noqa: F401
    CHILD_TITLE_AGE,
    CLAIM_TYPE_NAMES,
    DRY_CLAIM_TYPE,
    THAI_TITLES,
    TITLE_GENDER,
    WEAK_TITLES,
    ClaimData,
    gender_from_title,
    resolve_gender,
    split_thai_name,
    title_from_gender_age,
)
from .images import ZIP_CAT_TO_EMCS, list_images
from .loss_type_map import loss_type_from_acc_type


def _dash(v):
    """ฟิลด์ 'บังคับ' (required) EMCS ชนิด text: คืน '-' เมื่อไม่มีข้อมูลจริง (กติกา user 2026-07-23)
    → ผ่าน required-field gate แล้ว save draft ได้ (set_text ข้ามค่าว่าง จึงต้อง fallback เอง);
    หัวหน้าแก้ค่าจริงตอนตรวจ. '-' = marker 'ไม่มีข้อมูล' ไม่ใช่ค่าปลอม.
    ใช้เฉพาะช่อง text เท่านั้น — dropdown/date/number ใส่ '-' ไม่ได้ (EMCS reject ชนิดข้อมูล)."""
    s = "" if v is None else str(v)
    return s.strip() or "-"

# ผลคดี → id ของ radio button (แก้บั๊กเดิม: 'รถคู่กรณีเป็นฝ่ายผิด' กับ
# 'คู่กรณีคันที่' เป็นคนละ label แต่ต้องชี้ radio ตัวเดียวกัน — โค้ดเดิมเทียบ
# ด้วยข้อความที่ต่อกันจึงไม่มีวันเข้าเงื่อนไข ทำให้ไม่ถูกคลิก)
CAUSE_RADIO = {
    "รถประกันเป็นฝ่ายผิด": "rdoAcc_Cause00",
    "รถคู่กรณีเป็นฝ่ายผิด": "rdoAcc_Cause01",
    "คู่กรณีคันที่": "rdoAcc_Cause01",
    "ประมาทร่วม": "rdoAcc_Cause02",
    "รอสรุปผลคดี": "rdoAcc_Cause03",
    "รถประกันเป็นฝ่ายถูกและผิด": "rdoAcc_Cause04",
    "ยกเลิกการเคลม": "rdoAcc_Cause05",
    "ไปถึงแล้วไม่พบ": "rdoAcc_Cause06",
    # ป้ายฝั่ง ISURVEY ที่สะกดไม่เหมือน EMCS แต่เป็นเรื่องเดียวกัน — user สรุปเอง
    # 2026-08-06 (master masterClaimVerdict ดัมพ์จริง: ISURVEY 8 ตัว / EMCS 7 radio)
    "รอคำตัดสิน": "rdoAcc_Cause03",       # = 'รอสรุปผลคดี'
    "รถประกันเป็นฝ่ายถูก": "rdoAcc_Cause01",  # = 'รถคู่กรณีเป็นฝ่ายผิด คู่กรณีคันที่'
    # ⛔ 'ไม่มีคู่กรณี' (05) จงใจไม่ใส่ — EMCS ไม่มีช่องที่ตรงกันเลย
    #    บอทปล่อยว่าง + ขึ้นเตือนบนการ์ดให้หัวหน้าเลือกเองตอนตรวจ
    # ค่าสั้นที่แอปมือถือเก็บจริง (survey_form_screen.dart _faultDropdown เก็บ key ไม่ใช่ label)
    # ต้อง map ตรงตัว ห้ามพึ่ง fuzzy: 'ฝ่ายผิด' ได้ WRatio 90 เท่ากันทั้ง 'รถประกันเป็นฝ่ายผิด'
    # และ 'รถคู่กรณีเป็นฝ่ายผิด' → extractOne ตัดสินด้วยลำดับ dict = เสี่ยงพลิกฝ่ายทั้งสำนวน
    "ฝ่ายผิด": "rdoAcc_Cause00",
    "รถประกันฝ่ายผิด": "rdoAcc_Cause00",
    "คู่กรณีผิด": "rdoAcc_Cause01",
    "ฝ่ายถูกและผิด": "rdoAcc_Cause04",
}

# radio ที่ "บอทห้ามเลือกเอง" — ต้องให้หัวหน้าตัดสิน (user สรุป 2026-08-06)
# rdoAcc_Cause04 = 'รถประกันเป็นฝ่ายถูกและผิด' (ทั้งถูกทั้งผิดพร้อมกัน = ต้องคนอ่านสำนวน)
NO_AUTO_RADIO = {"rdoAcc_Cause04"}
# radio ที่เลือกให้ได้ แต่ EMCS บังคับข้อมูลต่อท้ายที่บอทกรอกแทนไม่ได้
OPPONENT_FAULT_RADIO = "rdoAcc_Cause01"   # 'รถคู่กรณีเป็นฝ่ายผิด คู่กรณีคันที่ __'


# ── เฟส 2: ผลตรวจ "อ่านกลับ" ล่าสุด ─────────────────────────────────────────
# เฟส 3 (บอทกดปุ่มส่งงานเอง) ต้องอ่านตัวนี้ก่อนกด — ไม่ว่างเมื่อไหร่ = ห้ามกด
# เก็บเป็นตัวแปรระดับโมดูลเพราะขั้นกดส่งอยู่คนละไฟล์ (main.py) และคนละจังหวะกับตอนกรอก
last_verify_mismatches = []


def _verify_after_save(driver, data, page_label: str, reset: bool = True):
    """อ่านค่าจากหน้า EMCS กลับมาเทียบกับที่บอทกรอก แล้วรายงาน (ไม่ล้มงาน)

    เรียกทันทีหลังบันทึกสำเร็จ ขณะยังอยู่หน้าเดิม — ย้ายหน้าไปแล้วอ่านกลับไม่ได้
    ไม่ raise เพราะตอนถึงบรรทัดนี้ draft เกิดบน EMCS ไปแล้ว ล้มไปก็ไม่ได้ทำให้ draft หาย
    มีแต่ทำให้ไม่มีใครรู้ว่าเพี้ยนตรงไหน — หน้าที่ของมันคือ "บอกให้รู้" และกันเฟส 3

    reset=True (หน้าหลัก = หน้าแรกของเคส) → เริ่มนับใหม่
    reset=False (หน้าถัดมาในเคสเดียวกัน เช่นหน้าค่าใช้จ่าย) → **ต่อท้าย**
    ⛔ ห้ามให้หน้าหลังเขียนทับหน้าหน้า ไม่งั้นผลตรวจหน้าหลักหายไป แล้วประตูเฟส 3
       (main.py อ่าน last_verify_mismatches) จะเปิดทั้งที่หน้าหลักเพี้ยนอยู่
    """
    global last_verify_mismatches
    try:
        bad = verify_filled(driver, page_label)
    except Exception as e:
        log(f"   ⚠️ ตรวจค่าที่กรอกไม่สำเร็จ ({type(e).__name__}) — ถือว่ายังไม่ได้ตรวจ")
        bad = [{"id": "-", "intended": "", "actual": None, "reason": "ตรวจไม่สำเร็จ"}]
    found = [b for b in bad if b["reason"] != "อ่านไม่ได้"]
    if reset:
        last_verify_mismatches = list(found)
    else:
        seen = {(b.get("id"), b.get("intended")) for b in last_verify_mismatches}
        last_verify_mismatches = last_verify_mismatches + [
            b for b in found if (b.get("id"), b.get("intended")) not in seen]
    for b in found:
        _review_note(data, f"{page_label}: {b['id']} กรอก {b['intended']!r} "
                           f"แต่บนหน้าเป็น {b['actual']!r} — ตรวจก่อนส่งงาน")
    return found


def _review_note(data, msg: str):
    """สะสมข้อความ 'ให้หัวหน้าตรวจก่อนกดส่ง' → ไปโผล่บนการ์ดตอนรอสั่งส่งงาน
    (เตือนอย่างเดียว ไม่หยุดกลางทาง — กันบอทค้างรอคนตั้งแต่ยังกรอกไม่จบ)"""
    notes = getattr(data, "review_notes", None)
    if notes is None:            # ClaimData เก่า/ออบเจกต์ทดสอบที่ไม่มีฟิลด์นี้
        return
    if msg not in notes:
        notes.append(msg)


# ความเสียหายกรอกได้สูงสุด 8 รายการ (คอลัมน์ A 4 + คอลัมน์ B 4 ตาม layout หน้าเว็บ)
MAX_DAMAGE_ITEMS = 8

# ---------------------------------------------------------------- คู่กรณี
# ฟอร์ม EMCS มีบล็อกรถคู่กรณีเตรียมไว้ 20 คัน: dtlOpo_ctl00..ctl19
OPO_PREFIX = "dtlOpo_ctl{n:02d}_wuOpo_"
MAX_OPPONENTS = 20

# ---------------------------------------------------------------- ผู้บาดเจ็บ/ทรัพย์สิน
# Tab 5/6 (ปลดล็อกหลังบันทึกหน้าหลัก เหมือนคู่กรณี): เลือกจำนวน → กรอกบล็อก → บันทึก
INJ_PREFIX = "dtlInj_ctl{n:02d}_wuInj_"      # imbInjure_Person / ddlInj_Count / btnSave_InjurePerson
ASSET_PREFIX = "dtlAsset_ctl{n:02d}_wuAsset_"  # imbAsset / ddlAsset_Count / btnSave_Asset
# ขนาด repeater จริงของ EMCS (บล็อก render ไว้แบบ static แค่ซ่อนด้วย showInj/showAsset)
MAX_INJURIES = 32   # dtlInj_ctl00..ctl31 (ddlInj_Count มีถึง 32)
MAX_ASSETS = 30     # dtlAsset_ctl00..ctl29 (ddlAsset_Count มีถึง 30)
# ประเภทบุคคล: code XML (PERSON_TYPE) → value ของ ddlPerson_Type
# ผู้ขับขี่ / ผู้โดยสาร / บุคคลภายนอก — รหัสแบบ XML → value ของ ddlPerson_Type
# ⚠️ ผู้โดยสารมี 2 ตัวสะกดในของจริง: 'PV' (ที่เคยเจอ) และ 'PR' (XML ของเคลม
# 2026013058298 ใช้ตัวนี้) — รับทั้งคู่ ไม่งั้นผู้โดยสารจะไม่ถูกเลือกประเภทแบบเงียบ ๆ
PERSON_TYPE_MAP = {"DV": "01", "PV": "03", "PR": "03", "ON": "05"}
# ป้ายไทยจากแอปมือถือ (kPersonTypes) → value ของ ddlPerson_Type โดยตรง
# XML มีรหัสแค่ DV/PV/ON (3 จาก 5) จึงแยก "ฝั่งคู่กรณี" ไม่ได้ — ป้ายจากแอปแยกได้
# งานจริงของพนักงานใช้ '02 ผู้ขับขี่ - รถคู่กรณี' จริง (เคลมไอโออิ 2026013058298)
PERSON_TYPE_LABEL = {
    "ผู้ขับขี่ - รถประกัน": "01", "ผู้ขับขี่รถประกัน": "01",
    "ผู้ขับขี่ - รถคู่กรณี": "02", "ผู้ขับขี่คู่กรณี": "02",
    "ผู้โดยสาร - รถประกัน": "03", "ผู้โดยสารรถประกัน": "03",
    "ผู้โดยสาร - รถคู่กรณี": "04", "ผู้โดยสารคู่กรณี": "04",
    "บุคคลภายนอกรถ": "05", "บุคคลภายนอก": "05",
}

# คำนำหน้าที่คนเขียนได้หลายแบบ → ป้ายจริงของ dropdown EMCS (ddl*_Title_ID มี 6 ตัว:
# นาย/นาง/นางสาว/ด.ช./ด.ญ./คุณ) — เดิมโยนคำเต็มเข้า fuzzy แล้วได้ผิดแบบเงียบ:
# 'เด็กชาย'→'นาย' (72), 'น.ส.'→'ด.ช.' (50) ทั้งคู่ผ่านเกณฑ์ 40 จึงไม่มีคำเตือน
EMCS_TITLE = {"เด็กชาย": "ด.ช.", "เด็กหญิง": "ด.ญ.", "น.ส.": "นางสาว", "นส.": "นางสาว"}
# ลิสต์ปิด 6 ตัว — ค่าที่ถูกต้องได้ 100 เสมอ จึงตัดที่ 90 (ไม่ตรง = หยุดรอคนเลือก)
TITLE_MIN_SCORE = 90

# เกณฑ์ตัดสินว่า "ประเภทรถบนเรื่อง = ประเภทรถในข้อมูล" ทั้งที่สะกดต่าง
# ('เก๋งเอเซีย' ISURVEY vs 'เก๋งเอเชีย' EMCS = 90) — ต้องสูงพอไม่ให้
# 'เก๋งเอเชีย' กับ 'เก๋งยุโรป' (คนละประเภทจริง) หลุดผ่าน
CAR_TYPE_SAME_SCORE = 88


def district_index(district_id: str, province_id: str):
    """รหัสอำเภอของ ISURVEY = <รหัสจังหวัด><ลำดับอำเภอ 2 หลัก>
    เช่น 236 = จังหวัด 2 (กรุงเทพ) เขตลำดับ 36 (ดอนเมือง)
    คืนลำดับอำเภอ (int) หรือ None เมื่อรูปแบบไม่ตรง"""
    district_id = (district_id or "").strip()
    province_id = (province_id or "").strip()
    if not district_id.isdigit() or len(district_id) < 3:
        return None
    if province_id and district_id[:-2] != province_id:
        return None
    return int(district_id[-2:])


def _plate(s: str) -> str:
    """ลบช่องว่างในเลขทะเบียน — EMCS ไม่รับช่องว่าง (server reject เงียบๆ)
    เช่น ISURVEY ให้ '9กฆ 5003' → EMCS ต้องเป็น '9กฆ5003' (verify จริง 2026-06-18)"""
    return "".join((s or "").split())


def resolve_loss_type(data, requested: str) -> str:
    """เลือกค่า 'ลักษณะความเสียหาย' (ddlLoss_ID) เมื่อ requested='auto'

    ISURVEY ไม่มีช่องนี้ตรง ๆ แต่มี 'ลักษณะการเกิดเหตุ' (acc_type_desc) ซึ่ง
    ละเอียดกว่าและบอกทิศอยู่ในตัวคำ ('เฉี่ยว/เบียดคู่กรณี' = เราชน vs
    'คู่กรณีเฉี่ยวชน' = เขาชน) → แปลงได้ 34/58 รายการ (ดู loss_type_map.py)
    ลำดับ:
    - ระบุเอง (--loss-type) → ใช้ตามนั้น
    - **ประเภทเคลมของ ISURVEY = 'เคลมแห้ง'** (claim_MtypeID=2) → 'เคลมแห้ง'
      เชื่อค่าที่ ISURVEY แจ้งมาเอง ไม่ใช่เดาจาก 'ไม่มีคู่กรณี' อย่างเดิม —
      ตัวแทนนั้นพลาดได้ (เคลมสดที่ยังไม่ระบุคู่กรณี เช่น ชนแล้วหนี จะถูกมองเป็นเคลมแห้ง)
    - ที่เหลือ (เคลมสด/ติดตาม/เจรจาสินไหม) → แปลงจาก 'ลักษณะการเกิดเหตุ'
    - แปลงไม่ได้ → '' : fill_accident หยุดรอผู้ใช้เลือกเองบนหน้า EMCS
      (รูปแบบเดียวกับ field บังคับอื่น เช่น ยี่ห้อ/มีประกันภัยที่)"""
    if requested != "auto":
        return requested
    ct = (data.claim_type or "").strip().lstrip("0")
    if ct == DRY_CLAIM_TYPE:
        return "เคลมแห้ง"
    mapped = loss_type_from_acc_type(data.acc_type_desc)
    if mapped:
        return mapped
    # ไม่รู้ประเภทเคลม (ข้อมูลเก่า/อ่านไม่ติด) → ถอยไปใช้เกณฑ์เดิม ไม่ให้แย่กว่าก่อนแก้
    if not ct and not data.third_parties:
        return "เคลมแห้ง"
    return ""


def _is_displayed(driver, elem_id) -> bool:
    """element โผล่/มองเห็นจริงไหม — EMCS สลับ layout ด้วยการซ่อน/โชว์ทั้งแถว
    (บาง layout คู่กรณีซ่อนช่องบางตัว; ฟอร์มทรัพย์สินมีเวอร์ชัน STD/AXA) การเช็คว่า
    'มี element' อย่างเดียวไม่พอ เพราะแถวที่ซ่อนอยู่ก็ยัง find_element เจอ"""
    try:
        return driver.find_element(By.ID, elem_id).is_displayed()
    except Exception:
        return False


def _select_has_options(driver, select_id) -> bool:
    """dropdown มีตัวเลือกจริง (>1 = มีนอกจาก '-- ระบุ --') — ใช้เช็ค dropdown ที่
    ผูกกับตัวอื่น เช่น 'ยี่ห้อ' ที่ว่างจนกว่าจะเลือก 'ประเภทรถ' ก่อน"""
    try:
        return len(Select(driver.find_element(By.ID, select_id)).options) > 1
    except Exception:
        return False


def _select_code_or_label(driver, select_id, wanted: str, label: str = "") -> bool:
    """เลือก dropdown ด้วย **รหัส หรือ ป้ายไทย ก็ได้**

    ⚠️ ต้นทางเดียวกันส่งมาคนละแบบ: XML ของระบบเก่าให้รหัส ('01' / '13') แต่ข้อมูล
    ที่ดึงจาก se-survey ให้ป้ายไทย ('บาดเจ็บ - เล็กน้อย' / 'เจ้าของรถ') — โค้ดเดิม
    เรียก select_by_value() อย่างเดียว จึงเงียบ ๆ ไม่เลือกให้เมื่อได้ป้ายไทยมา
    (เจอจริง 19/08/69: ผู้บาดเจ็บบันทึกผ่าน แต่ 'ระดับการบาดเจ็บ' กับ 'ความสัมพันธ์' ว่าง)
    """
    name = label or select_id
    try:
        sel = Select(driver.find_element(By.ID, select_id))
    except Exception:
        log(f"   ⚠️ ไม่พบ dropdown {name}")
        return False
    for pick, how in ((sel.select_by_value, "รหัส"), (sel.select_by_visible_text, "ป้าย")):
        try:
            pick(wanted)
            log(f"   ✓ {name}: {wanted} ({how})")
            return True
        except Exception:
            continue
    log(f"   ⚠️ เลือก {name} ไม่ได้ — ไม่มีทั้งรหัสและป้าย '{wanted}' ในลิสต์")
    return False


def _select_index(driver, select_id, index: int, label: str = "", timeout=10):
    """เลือก option ตามลำดับ — ใช้กับ dropdown จังหวัด/อำเภอของ EMCS ที่
    เรียงตรงกับรหัสของ ISURVEY (index 0 คือ '-- ระบุ --')
    คืนข้อความที่เลือก หรือ None เมื่อเลือกไม่ได้"""
    name = label or select_id
    if index is None or index <= 0:
        log(f"   - ข้าม {name} (ไม่มีรหัส)")
        return None
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: len(Select(d.find_element(By.ID, select_id)).options) > index
        )
        # scroll เข้า view ก่อนเลือก — บล็อกคู่กรณีอยู่ล่างหน้า ถ้าไม่ scroll
        # จะเจอ ElementNotInteractableException (โดยเฉพาะจังหวัด/อำเภอผู้ขับขี่)
        el = driver.find_element(By.ID, select_id)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        sel = Select(el)
        text = sel.options[index].text
        sel.select_by_index(index)
        log(f"   ✓ {name}: ลำดับ {index} → '{text}' (ตรวจสอบด้วยตาด้วย)")
        return text
    except Exception as e:
        log(f"   ⚠️ เลือก {name} ลำดับ {index} ไม่ได้: {type(e).__name__}")
        return None


#: ตัวเลือกแรกของ ddl*_Count = "ไม่มีแถวเลย" (ค่า "0")
NO_ROWS_OPTION = "-- ไม่ระบุ --"


def _clear_rows_if_any(driver, count_id: str, save_id: str, name: str,
                       open_menu_id: str = "") -> None:
    """ข้อมูลใหม่ไม่มีแถวเลย → ตั้งจำนวนเป็น "-- ไม่ระบุ --" แล้วบันทึก = ลบของเดิมทิ้ง

    ⛔ **เดิม fill_* คืนค่าทันทีเมื่อไม่มีข้อมูล** ซึ่งถูกกับเรื่องใหม่ (ไม่มีอะไรให้ลบ) แต่
    **ผิดกับเรื่องที่เคยกรอกไว้แล้ว**: ผู้ตรวจตีกลับ → ช่างแก้แล้วลบผู้บาดเจ็บออกหมด →
    บอทไม่แตะช่องจำนวนเลย แถวเดิมค้างอยู่บน EMCS = สำนวนของบริษัทประกันมีคนที่ไม่เกี่ยวข้อง
    ติดอยู่ **โดยไม่มีอะไรฟ้อง** (เจอตอนเทสครบวงจร 28/08/69 — user ยกเคสนี้ขึ้นมาเอง)

    EMCS ไม่มีปุ่มลบรายแถว จำนวนแถวคุมด้วย `ddl*_Count` และ **ลดจำนวนแล้วกดบันทึก =
    ลบออกจากฐานข้อมูลจริง** (พิสูจน์สดบน draft ทดสอบ S68426087785 28/08/69:
    เพิ่มผู้บาดเจ็บ 1→2 บันทึก แล้วลด 2→1 บันทึก → โหลดหน้าใหม่ คนที่ 2 หายจริง)

    ⛔ ไม่มีของเดิมอยู่แล้ว = ออกเลย ไม่กดบันทึกให้เปลืองรอบและเปลืองความเสี่ยง
    """
    if open_menu_id:
        click_retry(driver, By.ID, open_menu_id)
    try:
        wait_present(driver, By.ID, count_id, 20)
        # ⛔ "โผล่แล้ว" ยังไม่พอ ต้อง **กดได้จริง** — ddlOpo_Count อยู่บนหน้าหลักและ server
        #    ปลดล็อกให้หลังบันทึกหน้าหลักเท่านั้น · เลือกค่าใส่ช่องที่ยัง disabled = ไม่มีผล
        #    เงียบ ๆ แล้วเราจะรายงานว่า "ลบแล้ว" ทั้งที่แถวยังอยู่ (แย่กว่าไม่ทำเลย)
        WebDriverWait(driver, 30).until(
            lambda d: d.find_element(By.ID, count_id).is_enabled()
        )
    except Exception:   # timeout หรือหา element ไม่เจอระหว่างรอ — ผลเหมือนกันคือทำไม่ได้
        log(f"   ⚠️ ส่วน{name}ยังไม่ปลดล็อก ({count_id} ไม่โผล่/กดไม่ได้) — "
            "ข้าม ลบเองบนหน้า EMCS ถ้าจำเป็น")
        return

    sel = Select(driver.find_element(By.ID, count_id))
    cur = (sel.first_selected_option.text or "").strip()
    if cur in ("", "0", NO_ROWS_OPTION):
        return          # เรื่องนี้ไม่เคยมีแถว — ไม่ต้องทำอะไร

    log(f"EMCS: ข้อมูลใหม่ไม่มี{name}เลย แต่ของเดิมมี {cur} → ตั้งเป็น '{NO_ROWS_OPTION}' เพื่อลบ")
    try:
        sel.select_by_visible_text(NO_ROWS_OPTION)
    except Exception:
        sel.select_by_index(0)
    time.sleep(1.5)     # JS ปิดบล็อก
    _save_section(driver, save_id, name)


def fill_third_parties(driver, data: ClaimData):
    """กรอกข้อมูลรถคู่กรณีทุกคันจากข้อมูล XML ของ ISURVEY แล้วกดบันทึกรถคู่กรณี

    สำคัญ: ส่วนนี้ถูก disable จาก server จนกว่าจะบันทึกหน้าหลักครั้งแรก
    (พิสูจน์จาก probe: toggle ฝั่ง client ใดๆ ไม่ปลด) — ต้องเรียกหลัง
    save_main_form เท่านั้น / บล็อกคู่กรณีโผล่ด้วย JS showOtherVehicle()
    ทันทีที่เลือกจำนวน"""
    tps = data.third_parties
    if not tps:
        # ไม่ใช่ "ไม่มีอะไรให้ทำ" — ถ้าเรื่องเดิมมีคู่กรณีอยู่ ต้องลบออกให้ตรงข้อมูลใหม่
        _clear_rows_if_any(driver, "ddlOpo_Count", "btnSave_Opponent", "รถคู่กรณี")
        return
    main_window = driver.current_window_handle
    # "ประเภทรถคู่กรณี" เป็นช่องของ**หน้าหลัก** ไม่ใช่ของบล็อกคู่กรณี — ปุ่ม 'บันทึกรถคู่กรณี'
    # ไม่ commit ให้ ต้องกด 'แก้ไข' (btnUpdate) ของหน้าหลักอีกที (ดูตอนท้ายฟังก์ชัน)
    _ctype_changed = False

    log(f"EMCS: กรอกรถคู่กรณี {len(tps)} คัน")
    if len(tps) > MAX_OPPONENTS:
        log(f"   ⚠️ คู่กรณี {len(tps)} คัน เกิน {MAX_OPPONENTS} — กรอกเท่าที่ได้")

    # รอ ddlOpo_Count ถูกปลดล็อก (หลังบันทึกหน้าหลัก server จะ enable ให้)
    try:
        WebDriverWait(driver, 30).until(
            lambda d: d.find_element(By.ID, "ddlOpo_Count").is_enabled()
        )
    except Exception as e:
        raise RuntimeError(
            "ส่วนรถคู่กรณียังไม่ถูกปลดล็อก (ddlOpo_Count disabled) — "
            "ต้องบันทึกหน้าหลักก่อน หรือบัญชี/ประเภทเคลมนี้ไม่รองรับ"
        ) from e

    # เลือกจำนวนรถคู่กรณี → JS เปิดบล็อกให้ทันที
    Select(driver.find_element(By.ID, "ddlOpo_Count")).select_by_visible_text(
        str(min(len(tps), MAX_OPPONENTS))
    )
    time.sleep(1.5)

    for n, tp in enumerate(tps[:MAX_OPPONENTS]):
        p = OPO_PREFIX.format(n=n)
        log(f"   --- คันที่ {n + 1}: {tp.get('plate_no', '')} "
            f"{tp.get('car_brand', '')} ---")

        # เจ้าของ (XML มักว่าง — ใช้ชื่อผู้ขับขี่แทน ซึ่งเป็นเคสทั่วไป)
        owner = tp.get("opo_name", "") or tp.get("drv_name", "")
        set_text(driver, p + "txtOpo_Name", _dash(owner))
        set_text(driver, p + "txtOpo_Address",
                 tp.get("opo_address", "") or tp.get("address", ""))
        set_text(driver, p + "txtOpo_Type", tp.get("opo_type", ""))

        # รถ
        set_text(driver, p + "txtCar_RegNo", _plate(tp.get("plate_no", "")))
        # ประเภทรถคู่กรณี (* บังคับ) — จาก Tab 4 (veh_type อ่านได้ เช่น 'เก๋งเอเซีย')
        # ต้องเลือกก่อน "ยี่ห้อ" (ddlCmfg) ถึงจะมีตัวเลือก (dropdown ผูกกัน)
        # ว่างก็ต้องถาม ไม่ใช่ข้าม — เดิมข้ามเงียบ ๆ แล้วไปโดน validation ตอนกดบันทึก
        # ซึ่งตอนนั้นบอทไม่รู้ว่าเป็น dropdown ตัวไหน เลยเสนอตัวเลือกให้เลือกบนหน้าเว็บไม่ได้
        # ต้องไปเลือกเองใน Chrome (เจอจริง เคลม 2026013059072 — ISURVEY ไม่มี vehTID)
        # จำค่าก่อนเลือก — ถ้าเปลี่ยนจริงต้องกด "แก้ไข" หน้าหลักซ้ำตอนท้าย (ดู _needs_main_resave)
        _ctype_before = _current_select_text(driver, p + "ddlCType")
        fuzzy_select(driver, p + "ddlCType", tp.get("veh_type", ""), presleep=0.5,
                     label=f"ประเภทรถคู่กรณี {n + 1}", required=True)
        if _current_select_text(driver, p + "ddlCType") != _ctype_before:
            _ctype_changed = True
        # ⛔ ยี่ห้อคู่กรณีใช้ตัวเดียวกับฝั่งรถประกัน (_select_car_brand) — เดิมเป็น
        #    `sleep(2)` แล้วเช็คครั้งเดียว ถ้าตัวเลือกยังไม่มาก็ "ข้าม" เงียบ ๆ
        #    ปัญหาคือ postback ตัวที่โหลดตัวเลือกยี่ห้อ **เป็นตัวเดียวกับที่บันทึกประเภทรถ**
        #    → ข้ามยี่ห้อ = ประเภทรถไม่ติดด้วย แล้ว EMCS ฟ้อง "ประเภทรถ ขาด" ตอนกดบันทึก
        #    ทั้งที่ log บอกว่าเลือกแล้ว (เจอจริง 28/08/69 ตอนเติมคู่กรณีกลับเข้าบล็อกที่
        #    เพิ่งถูกลบ — บล็อกสร้างใหม่ใช้เวลาโหลดนานกว่า 2 วิ)
        #    ฝั่งรถประกันแก้ไปแล้วตั้งแต่ f81f766 (ยิง __doPostBack ซ้ำ + รอจริง 12 วิ)
        #    แต่ฝั่งคู่กรณีตกหล่น — ตอนนี้ใช้ตัวเดียวกันแล้ว
        _select_car_brand(driver, tp.get("car_brand", ""),
                          label=f"ยี่ห้อรถคู่กรณี {n + 1}",
                          type_id=p + "ddlCType", brand_id=p + "ddlCmfg")
        set_text(driver, p + "txtCModel", tp.get("car_model", ""))
        # สีรถคู่กรณี — เดิมไม่เคยแตะช่องนี้ (มีแต่ฝั่งรถประกัน) ทั้งที่มือถือเก็บให้แล้ว
        if str(tp.get("car_color") or "").strip():
            fuzzy_select(driver, p + "ddlCar_Color", tp["car_color"],
                         label=f"สีรถคู่กรณี {n + 1}", timeout=5)
        set_text(driver, p + "txtChassisNo", tp.get("chassis_no", ""))
        set_text(driver, p + "txtKm_No", tp.get("km_no", ""))
        # ปีจดทะเบียนคู่กรณีเป็น dropdown ค.ศ. (แอปเก็บ พ.ศ. → แปลงก่อน)
        _yr = _year_ad(tp.get("car_reg_year", ""))
        if _yr:
            try:
                Select(driver.find_element(By.ID, p + "ddlCar_RegNo_Year")
                       ).select_by_value(_yr)
                log(f"   ✓ ปีจดทะเบียนคู่กรณี {n + 1} = {_yr}")
            except Exception:
                log(f"   ⚠️ เลือกปีจดทะเบียนคู่กรณี {n + 1} ('{_yr}') ไม่ได้")
        _fill_ev(driver, p, tp.get("ev_type", ""), tp.get("ev_battery_no", ""),
                 tp.get("ev_charger_no", ""), tp.get("ev_battery_start", ""))
        # จังหวัดทะเบียนรถคู่กรณี (* บังคับ) — se-survey ให้ "ชื่อ" (เลือกด้วย fuzzy เหมือนรถประกัน);
        # ISURVEY เดิมให้ index → รองรับทั้งคู่ (มีชื่อใช้ชื่อก่อน ไม่มีค่อย fallback index)
        plate_prov_name = (tp.get("plate_province", "") or "").strip()
        if plate_prov_name:
            fuzzy_select(driver, p + "ddlCar_Province", plate_prov_name,
                         label=f"จังหวัดรถคู่กรณี {n + 1}")
        else:
            _select_index(driver, p + "ddlCar_Province",
                          int(tp["plate_province_id"])
                          if tp.get("plate_province_id", "").strip().isdigit() else None,
                          label=f"จังหวัดรถคู่กรณี {n + 1}")

        # ผู้ขับขี่ — ฟอร์มคู่กรณีใช้ช่อง "ชื่อ" เดี่ยวที่มองเห็น = txtDri_Name
        # (ไม่ใช่ txtDri_Name01 ซึ่งเป็น layout สำรองที่ซ่อนไว้ — เดิมเซ็ตผิดช่อง
        # ทำให้ validation ฟ้อง 'ชื่อผู้ขับขี่รถคู่กรณี')
        drv_full = (tp.get("drv_name", "") or owner).strip()
        set_text(driver, p + "txtDri_Name", _dash(drv_full))
        # ความสัมพันธ์ผู้ขับขี่กับเจ้าของรถ — แต่ละต้นทางให้มาคนละรูปแบบ
        #   XML  → รหัส EMCS ตรง ๆ (<DRI_RELATION> 19 = ญาติ) = เลือกด้วย value
        #   API  → ชื่อ ('ญาติ') = เลือกด้วย fuzzy_select
        #   scrape → ไม่มี (ฟอร์ม tab 4 ของ ISURVEY ไม่ได้ render ช่องนี้)
        _rel_id = str(tp.get("relation_id") or "").strip()
        _rel = str(tp.get("relation") or "").strip()
        if _rel_id:
            try:
                Select(driver.find_element(By.ID, p + "ddlDri_Relation_ID")
                       ).select_by_value(_rel_id)
                log(f"   ✓ ความสัมพันธ์คู่กรณี {n + 1} (code {_rel_id})")
            except Exception:
                log(f"   ⚠️ เลือกความสัมพันธ์คู่กรณี {n + 1} (code {_rel_id}) ไม่ได้")
        elif _rel:
            fuzzy_select(driver, p + "ddlDri_Relation_ID", _rel,
                         label=f"ความสัมพันธ์คู่กรณี {n + 1}", timeout=5)

        # เพศ — ว่างจาก ISURVEY → อนุมานจากคำนำหน้าในชื่อผู้ขับขี่ (fallback)
        gender = resolve_gender(tp.get("gender", ""), drv_full)
        if gender:
            try:
                idx = "0" if gender == "M" else "1"  # 0=ชาย 1=หญิง
                driver.find_element(By.ID, p + f"rdoGender_{idx}").click()
            except Exception:
                log(f"   ⚠️ เลือกเพศคู่กรณีคันที่ {n + 1} ไม่ได้")

        set_text(driver, p + "txtDri_Age", tp.get("age", ""))
        set_text(driver, p + "wuCale_Dri_BirthDay_txtCalendar",
                 iso_to_thai_date(tp.get("birthdate", "")))
        set_text(driver, p + "txtDri_Adrress", _dash(tp.get("address", "")))

        # จังหวัด/อำเภอ ผู้ขับขี่คู่กรณี — บาง layout ซ่อนช่องนี้ (ใช้ "ที่อยู่ปัจจุบัน"
        # เดี่ยวพอ) → เลือกเฉพาะเมื่อช่องโชว์จริง (กัน ElementNotInteractable + หน่วงเวลา)
        prov_id = tp.get("province_id", "").strip()
        prov_idx = int(prov_id) if prov_id.isdigit() else None
        if _is_displayed(driver, p + "ddlDri_ProvinceID"):
            _select_index(driver, p + "ddlDri_ProvinceID", prov_idx,
                          label=f"จังหวัดผู้ขับขี่คู่กรณี {n + 1}")
            dist_idx = district_index(tp.get("district_id", ""), prov_id)
            if prov_idx and dist_idx:
                time.sleep(1)  # รอ postback โหลดรายการอำเภอตามจังหวัด
                _select_index(driver, p + "ddlDri_DistrictID", dist_idx,
                              label=f"อำเภอผู้ขับขี่คู่กรณี {n + 1}")
        else:
            log(f"   - ข้ามจังหวัด/อำเภอผู้ขับขี่คู่กรณี {n + 1} "
                "(layout นี้ใช้ช่องที่อยู่เดี่ยว)")

        set_text(driver, p + "txtDri_TelNo", _dash(tp.get("phone", "")))
        set_text(driver, p + "txtDri_CardID", _dash(tp.get("idcard", "")))
        set_text(driver, p + "txtDri_DrvID", _dash(tp.get("lic_no", "")))
        set_text(driver, p + "wuCale_Dri_DrvDate_Start_txtCalendar",
                 iso_to_thai_date(tp.get("lic_issue_date", "")))
        # ประเภทใบขับขี่คู่กรณี — value ของ dropdown = รหัส LICENSE_TYPE (จาก producer
        # DRI_DRVTYPE); mirror ฝั่ง insured (ddlEmcs_License_Type). เลือกเฉพาะเมื่อ
        # dropdown มีตัวเลือกจริง (กัน ElementNotInteractable ตอน layout ซ่อน/ยัง disabled)
        lic_type = (tp.get("lic_type", "") or "").strip()
        if lic_type and _select_has_options(driver, p + "ddlEmcs_License_Type"):
            try:
                Select(driver.find_element(By.ID, p + "ddlEmcs_License_Type")
                       ).select_by_value(lic_type)
                log(f"   ✓ ประเภทใบขับขี่คู่กรณี (code {lic_type})")
            except Exception:
                log(f"   ⚠️ เลือกประเภทใบขับขี่คู่กรณี {n + 1} (code {lic_type}) ไม่ได้")

        # ประกันของคู่กรณี — ถ้าไม่มีข้อมูลประกันเลย (เช่น มอไซค์ไม่มีประกัน) →
        # เลือก 'ไม่มีบริษัทประกันภัย' (EMCS จะปลด required กรมธรรม์/เลขเคลมคู่กรณี
        # ไม่งั้น validation ฟ้อง 'มีประกันภัยที่/กรมธรรม์/เคลมที่' บันทึกไม่ผ่าน)
        insurer = (tp.get("insurer", "") or "").strip()
        policy_no = (tp.get("policy_no", "") or "").strip()
        claim_no = (tp.get("claim_no", "") or "").strip()
        insure_type = (tp.get("insure_type", "") or "").strip()
        if not (insurer or policy_no or claim_no or insure_type):
            try:
                Select(driver.find_element(By.ID, p + "ddlHave_Insurance")
                       ).select_by_visible_text("ไม่มีบริษัทประกันภัย")
                log(f"   ✓ คู่กรณี {n + 1}: ไม่มีบริษัทประกันภัย (ISURVEY ไม่มีข้อมูลประกัน)")
            except Exception:
                log(f"   ⚠️ เลือก 'ไม่มีบริษัทประกันภัย' คู่กรณี {n + 1} ไม่ได้")
            # ไอโออิบังคับ กรมธรรม์/ประเภทกรมธรรม์/เคลมที่ ของคู่กรณีเสมอ (validForm
            # ไม่ข้ามแม้เลือก 'ไม่มีบริษัทประกันภัย' — case นั้นเป็นของบริษัทอื่น) → ใส่ '-'
            set_text(driver, p + "txtPolicyNo", "-")
            set_text(driver, p + "txtPolicy_Type", "-")
            set_text(driver, p + "txtClaimNo", "-")
        else:
            fuzzy_select(driver, p + "ddlHave_Insurance", insurer,
                         label=f"บริษัทประกันคู่กรณี {n + 1}")
            set_text(driver, p + "txtPolicyNo", _dash(policy_no))
            set_text(driver, p + "txtPolicy_Type", _dash(insure_type))  # ประกันประเภท
            set_text(driver, p + "txtClaimNo", _dash(claim_no))

        # ความเสียหาย + KFK
        cost = tp.get("cost_damage", "").strip()
        if cost and cost != "0":
            set_text(driver, p + "txtCost_Damage", cost)
        if str(tp.get("has_kfk", "")).strip().upper() in ("Y", "YES", "1", "TRUE"):
            try:
                # click() สลับสถานะ — ถ้าติ๊กไว้แล้ว (เติม draft รอบสอง) จะกลายเป็นปลดติ๊ก
                el = driver.find_element(By.ID, p + "chkHas_KFK")
                if el.is_selected():
                    log(f"   – KFK คันที่ {n + 1} ติ๊กไว้แล้ว ไม่กดซ้ำ")
                else:
                    el.click()
                    log(f"   ✓ ติ๊กเข้าสัญญา KFK คันที่ {n + 1}")
            except Exception:
                log(f"   ⚠️ ติ๊ก KFK คันที่ {n + 1} ไม่ได้")

    # บันทึกส่วนรถคู่กรณี — ตรวจ validation จริง (ฟอร์มคู่กรณีมีช่อง * เยอะที่ ISURVEY
    # มักไม่มี เช่น ประเภทรถ/มีประกันภัยที่/อายุ) → ฟ้องช่องขาด = หยุดรอให้คนเติมแล้วลองใหม่
    saved = _save_opponents(driver)

    # ⛔ **"ประเภทรถคู่กรณี" ไม่ได้ถูกบันทึกด้วยปุ่ม 'บันทึกรถคู่กรณี'** — เป็นช่องของหน้าหลัก
    #    EMCS บอกเองตอนจะออกจากหน้า: "ข้อมูลดังต่อไปนี้ ยังไม่ถูกบันทึกข้อมูล, กรุณากดปุ่ม
    #    [แก้ไข] เพื่อบันทึกข้อมูล — ประเภทรถคู่กรณี คันที่ 1" (เจอจริง 28/08/69)
    #    บอทบันทึกหน้าหลัก *ก่อน* กรอกคู่กรณีเสมอ ค่าที่เปลี่ยนทีหลังจึงค้างไม่ commit
    #    → เปลี่ยนจริงเมื่อไหร่ ต้องกด 'แก้ไข' ซ้ำตอนท้าย
    #    เดิมไม่เจอเพราะเคสส่วนใหญ่ประเภทรถตรงอยู่แล้ว (ไม่มีอะไรเปลี่ยน) — โผล่ตอน
    #    บล็อกคู่กรณีถูกสร้างใหม่ (import ครั้งแรก / เติมกลับหลังลบ)
    if _ctype_changed:
        log("EMCS: ประเภทรถคู่กรณีเปลี่ยน → บันทึกหน้าหลักซ้ำเพื่อ commit ค่า")
        save_main_form(driver, data, button_id="btnUpdate", is_new=False)

    # ความเสียหายคู่กรณี — popup เดียวกับรถประกัน (ช่อง free-text dgvOtherDamage_List)
    # ทำหลังบันทึกคู่กรณีสำเร็จ (เหมือน flow รถประกัน: save แล้วค่อยกรอกความเสียหาย)
    if saved:
        for n, tp in enumerate(tps[:MAX_OPPONENTS]):
            if tp.get("damages"):
                try:
                    fill_opponent_damage(driver, OPO_PREFIX.format(n=n),
                                         tp["damages"], main_window)
                except Exception as e:
                    log(f"   ⚠️ กรอกความเสียหายคู่กรณีคันที่ {n + 1} ไม่สำเร็จ "
                        f"({type(e).__name__}) — กรอกเองภายหลัง")


def _save_section(driver, button_id: str, name: str, max_rounds: int = 5) -> bool:
    """กดปุ่มบันทึกของ section (คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน) แล้วตรวจ validation จริง
    - ไม่มี alert / alert ไม่มีคำว่า 'กรุณา' = บันทึกสำเร็จ
    - alert 'กรุณาใส่ข้อมูลให้ครบ...' = validation ไม่ผ่าน → หยุดรอให้คนกรอกช่องที่ฟ้อง
      บนหน้า EMCS แล้วลองใหม่ (unattended/EOF = ข้าม ไม่แจ้งสำเร็จลวง)
    คืน True เมื่อบันทึกสำเร็จ"""
    for attempt in range(1, max_rounds + 1):
        log(f"EMCS: กดบันทึก{name} (รอบ {attempt})")
        wait_clickable(driver, By.ID, button_id).click()
        try:
            alert_text = accept_alert(driver, timeout=15)
        except TimeoutException:
            alert_text = ""        # ไม่มี alert = ผ่าน
        if "กรุณา" not in (alert_text or ""):
            log(f"EMCS: บันทึก{name}สำเร็จ ✓")
            return True
        fields = _missing_field_list(alert_text)
        missing = ", ".join(fields)
        label = f"ข้อมูล{name}ที่ยังขาด" + (f": {missing}" if missing else "")
        if wait_for_manual_fill(label, reason=(alert_text or "").strip(),
                                focus_labels=fields + _quoted_field_names(alert_text),
                                driver=driver):
            log(f"   ↻ ลองบันทึก{name}ใหม่หลังผู้ใช้กรอกข้อมูล")
            continue
        log(f"   ⚠️ {name}ยังไม่ถูกบันทึก (ช่องบังคับขาด — ISURVEY ไม่มีข้อมูล) → "
            f"กรอกช่องที่ฟ้องบน EMCS แล้วกดปุ่มบันทึก{name}เอง")
        return False
    log(f"   ⚠️ บันทึก{name}ไม่ผ่านหลายรอบเกินไป — ตรวจช่องสีแดงบน EMCS แล้วบันทึกเอง")
    return False


def _save_opponents(driver, max_rounds: int = 5) -> bool:
    """กดบันทึกรถคู่กรณี (btnSave_Opponent) + ตรวจ validation (ดู _save_section)"""
    return _save_section(driver, "btnSave_Opponent", "รถคู่กรณี", max_rounds)


def _clear_free_text_slots(driver, slots):
    """ล้างชื่อชิ้นส่วนในช่องอิสระ **ทุกช่อง** ก่อนเขียนชุดใหม่

    ⛔ ต้องล้างทั้งหมด ไม่ใช่เฉพาะช่องที่กำลังจะเขียน — EMCS เก็บของเดิมไว้ และจำนวน/ลำดับ
       ช่องที่อ่านได้เปลี่ยนไปได้ระหว่างรอบ (หลังบันทึกมันแทรกแถวว่างเพิ่ม) พอรันซ้ำ
       ชุดใหม่ไปลงคนละช่องกับรอบก่อน ของเก่าเลยค้าง กลายเป็นรายการซ้ำ
       เจอจริง 20/08/69 บน S68426084815: คู่กรณี 4 ชิ้น รัน 2 รอบ เหลือ 6 แถว
       (ฝากระโปรงหลัง/คิ้วท้าย ซ้ำอย่างละ 2)
    """
    n = 0
    for pp in slots or []:
        try:
            el = driver.find_element(By.ID, pp + "txtDam_Name")
            if (el.get_attribute("value") or "").strip():
                el.clear()
                n += 1
        except Exception:
            continue
    if n:
        log(f"   ↻ ล้างช่องอิสระของเดิม {n} ช่อง ก่อนเขียนชุดใหม่ (กันรายการซ้ำ)")


def fill_opponent_damage(driver, prefix, damages, main_window):
    """กรอกความเสียหายคู่กรณีลง popup (frmDamage.aspx) — ใช้ช่อง free-text
    dgvOtherDamage_List (โครงสร้างเดียวกับความเสียหายรถประกันใน fill_damage_list)
    จาก tp['damages'] = [{part, level, ...}] แล้ว btnSave กลับหน้าหลัก"""
    items = [(d.get("part", ""), d.get("level", ""), d.get("side", ""))
             for d in (damages or []) if d.get("part")]
    if not items:
        return
    log(f"   กรอกความเสียหายคู่กรณี {len(items)} รายการ (popup free-text)")
    handles_before = set(driver.window_handles)
    # หลังบันทึกคู่กรณี (postback หนัก) หน้า re-render — ปุ่ม popup อาจ stale/ช้า
    # → click_retry + timeout ยาว (เดิม wait_clickable 10 วิ timeout บน draft ที่ช้า)
    click_retry(driver, By.ID, prefix + "btnPopUp_DamList", timeout=25)
    try:
        WebDriverWait(driver, 15).until(
            lambda d: len(d.window_handles) > len(handles_before))
        driver.switch_to.window((set(driver.window_handles) - handles_before).pop())
        wait_visible(driver, By.ID, "btnSave", 15)
    except TimeoutException:
        log("   ⚠️ popup ความเสียหายคู่กรณีไม่เปิด — ข้าม (กรอกเองภายหลัง)")
        try:
            driver.switch_to.window(main_window)
        except Exception:
            pass
        return

    # จำนวนช่องอิสระอ่านจาก DOM จริง (cmdNewReport=8 / ฟอร์ม import=20) เหมือนฝั่งรถประกัน
    # — เดิมฮาร์ดโค้ด 8 ทั้งที่ฟอร์มที่ใช้จริงมี 20 ช่อง ทำให้รายการที่ 9+ หายเงียบ
    _slots = _free_text_slots(driver)
    _clear_free_text_slots(driver, _slots)
    _cap = len(_slots) if _slots else MAX_DAMAGE_ITEMS
    if len(items) > _cap:
        log(f"   ⚠️ ความเสียหายคู่กรณี {len(items)} เกิน {_cap} ช่องที่มีจริง — "
            f"ที่เหลือต้องกรอกเองภายหลัง")
    for c, (name, level, side) in enumerate(items[:_cap]):
        if _slots:
            pp = _slots[c]
        else:   # fallback (อ่าน slot ไม่ได้) — สูตรเดิม ctl02-05 × A/B รองรับได้แค่ 8
            pp = (f"dgvOtherDamage_List_ctl0{2 + (c % 4)}_"
                  f"wuOtherDamL{'A' if c < 4 else 'B'}_")
        try:
            el = driver.find_element(By.ID, pp + "txtDam_Name")
            el.clear()
            el.send_keys(name)
        except Exception:
            continue
        side = side or _damage_side(name)   # แอปส่งด้านมาตรง ๆ; ไม่มีค่อยเดาจากชื่อ
        try:
            driver.find_element(By.ID, pp + f"rdoDam_Left_Right_{side}").click()
        except Exception:
            pass
        idx = {"A": "0", "B": "1", "C": "2", "D": "3"}.get((level or "").strip().upper())
        if idx is not None:
            try:
                driver.find_element(By.ID, pp + f"rdoDam_Lavel_{idx}").click()
            except Exception:
                pass
        log(f"   ✓ ความเสียหายคู่กรณี [{c + 1}] {name} | side={side} | level={level}")

    try:
        driver.find_element(By.ID, "btnSave").click()
        accept_alert(driver)
    except Exception:
        pass
    time.sleep(1)
    try:
        driver.switch_to.window(main_window)
    except Exception:
        pass
    log("   ✓ บันทึกความเสียหายคู่กรณีแล้ว")


def _read_person_type_options(driver):
    """อ่านตัวเลือก ddlPerson_Type จากบล็อกแรกที่ render แล้ว (dynamic — 02/04
    'รถคู่กรณี' โผล่เฉพาะตอนเคลมมีคู่กรณี) คืน [{value,label}] หรือ None ถ้าอ่านไม่ได้"""
    try:
        opts = driver.execute_script(
            "var s=document.getElementById(arguments[0]);"
            "if(!s){return null;}"
            "return Array.prototype.map.call(s.options,function(o){"
            "return {value:o.value, label:(o.text||'').trim()};})"
            ".filter(function(o){return o.value && o.value!=='0';});",
            INJ_PREFIX.format(n=0) + "ddlPerson_Type")
        return opts or None
    except Exception:
        return None


def _pick_car_regno_dialog(driver, want: str = "", who: str = "") -> bool:
    """ปิด dialog "กรุณาเลือกทะเบียนรถคู่กรณี" ของ EMCS — คืน True ถ้ามีและปิดแล้ว

    EMCS เด้ง dialog นี้เมื่อเลือกประเภทบุคคลเป็น "รถคู่กรณี" **ขณะที่มีคู่กรณี
    มากกว่า 1 คัน** (มันไม่รู้ว่าจะเติมทะเบียนคันไหนให้) · ตัว dialog มี overlay
    ทับทั้งหน้า → ทุกคลิกหลังจากนั้นโดนกิน ทั้ง radio เพศ และปุ่มบันทึกผู้บาดเจ็บ
    (เจอจริง 22/08/69: คู่กรณี 3 คัน + ผู้บาดเจ็บ 3 คน → ElementClickIntercepted
     ที่ btnSave_InjurePerson ทั้งที่กรอกครบแล้ว)

    เลือก radio ที่ทะเบียนตรงกับที่ผู้บาดเจ็บนั่งมา ไม่ตรงก็ใช้คันแรก แล้วกด 'ตกลง'
    """
    try:
        if not driver.find_element(By.ID, "overlaySelectCarRegNo").is_displayed():
            return False
    except Exception:
        return False
    want, picked = _plate(want or ""), ""
    try:
        radios = driver.find_elements(By.CSS_SELECTOR, "input[name='otherCRN']")
        target = next((r for r in radios
                       if want and _plate(r.get_attribute("value") or "") == want), None)
        target = target or (radios[0] if radios else None)
        if target:
            driver.execute_script("arguments[0].click();", target)
            picked = target.get_attribute("value") or ""
    except Exception:
        pass
    try:
        driver.execute_script(
            "var b=document.getElementById('btnConfirmCarRegNo');if(b){b.click();}")
        WebDriverWait(driver, 10).until(
            lambda d: not d.find_element(By.ID, "overlaySelectCarRegNo").is_displayed())
        log(f"   ✓ เลือกทะเบียนคู่กรณีใน dialog{(' ' + who) if who else ''}: "
            f"{picked or '(คันแรก)'}")
        return True
    except Exception as e:
        log(f"   ⚠️ ปิด dialog เลือกทะเบียนคู่กรณีไม่ได้ ({type(e).__name__}) "
            "— ปุ่มบันทึกจะกดไม่ได้ ต้องกดเองบนหน้าจอ")
        return False


def fill_injuries(driver, data: ClaimData):
    """กรอกผู้บาดเจ็บ (Tab 5) — กดเมนู imbInjure_Person → เลือกจำนวน ddlInj_Count
    → กรอกทีละบล็อก (dtlInj_ctl00_wuInj_*) → บันทึก btnSave_InjurePerson
    (รูปแบบเดียวกับคู่กรณี; ปลดล็อกหลังบันทึกหน้าหลัก) — เรียกหลัง save_main_form"""
    injs = data.injuries
    if not injs:
        _clear_rows_if_any(driver, "ddlInj_Count", "btnSave_InjurePerson", "ผู้บาดเจ็บ",
                           open_menu_id="wuMenuPage1_imbInjure_Person")
        return
    log(f"EMCS: กรอกผู้บาดเจ็บ {len(injs)} คน")

    # ชื่อผู้ขับขี่คู่กรณี — ใช้เดา default 'ผู้ขับขี่รถคู่กรณี' (02) ให้ผู้บาดเจ็บที่ชื่อตรงกัน
    opo_drivers = [
        ((tp.get("drv_name", "") or tp.get("opo_name", "")) or "").strip()
        for tp in (data.third_parties or [])
    ]
    opo_drivers = [nm for nm in opo_drivers if nm]

    # default ประเภทผู้บาดเจ็บต่อคน: ชื่อตรงผู้ขับขี่คู่กรณี (fuzzy ≥85) → 02
    # 'ผู้ขับขี่-รถคู่กรณี', ไม่งั้น map จาก PERSON_TYPE (ISURVEY)
    def _default_type(inj):
        nm = (inj.get("name", "") or "").strip()
        if nm and opo_drivers and max(
                (fuzz.WRatio(nm, o) for o in opo_drivers), default=0) >= 85:
            return "02"
        raw = (inj.get("person_type", "") or "").strip()
        # ป้ายไทยจากแอป (แม่นกว่า — แยกฝั่งคู่กรณีได้) มาก่อนรหัส XML
        return (PERSON_TYPE_LABEL.get(" ".join(raw.split()))
                or PERSON_TYPE_MAP.get(raw.upper(), ""))

    # ปลดล็อก + เลือกจำนวนก่อน เพื่อให้บล็อก render → อ่านตัวเลือก ddlPerson_Type จริง
    # (ต้องมีบล็อกก่อนถึงจะอ่านตัวเลือก dynamic ได้) — แล้วค่อยให้ผู้ใช้ยืนยันบน webui
    click_retry(driver, By.ID, "wuMenuPage1_imbInjure_Person")
    try:
        wait_present(driver, By.ID, "ddlInj_Count", 20)
    except TimeoutException:
        log("   ⚠️ ส่วนผู้บาดเจ็บไม่ปลดล็อก (ddlInj_Count ไม่โผล่) — ข้าม กรอกเอง")
        return
    if len(injs) > MAX_INJURIES:
        log(f"   ⚠️ ผู้บาดเจ็บ {len(injs)} คน เกิน {MAX_INJURIES} — กรอกเท่าที่ได้")

    Select(driver.find_element(By.ID, "ddlInj_Count")).select_by_visible_text(
        str(min(len(injs), MAX_INJURIES)))
    time.sleep(1.5)   # JS เปิดบล็อก

    # อ่านตัวเลือกจริงจากหน้า (dynamic) ส่งให้ webui; ให้ผู้ใช้กรอก 'เลขทะเบียน'
    # (ISURVEY ว่าง — EMCS บังคับก่อนเข้าหน้าค่าใช้จ่าย) + ยืนยัน 'ประเภทผู้บาดเจ็บ'
    options = _read_person_type_options(driver)
    spec = [{"name": inj.get("name", ""),
             "person_type_value": _default_type(inj),
             "car_regno": ""}
            for inj in injs[:MAX_INJURIES]]
    user_inputs = wait_for_injury_inputs(spec, options=options)  # None=console/EOF

    for n, inj in enumerate(injs[:MAX_INJURIES]):
        p = INJ_PREFIX.format(n=n)
        ui = user_inputs[n] if (user_inputs and n < len(user_inputs)) else None
        log(f"   --- คนที่ {n + 1}: {inj.get('name', '')} ---")

        # ประเภทบุคคล (* บังคับ) — ใช้ค่าที่ผู้ใช้เลือกบน webui ถ้ามี ไม่งั้น smart default
        # การเลือกจะ trigger JS ของ EMCS ให้ "เติมเลขทะเบียนอัตโนมัติ":
        #   01/03 (รถประกัน) → ทะเบียนรถประกัน, 02/04 (รถคู่กรณี) → ทะเบียนคู่กรณี
        #   05 (บุคคลภายนอกรถ) → ไม่เติม (ไม่มีรถผูก)
        pt = (ui.get("person_type") if ui else None) or _default_type(inj)
        if pt:
            try:
                Select(driver.find_element(By.ID, p + "ddlPerson_Type")
                       ).select_by_value(pt)
                # ยิง change event ให้ชัวร์ว่า handler auto-fill ทะเบียนทำงาน (กัน
                # กรณี select_by_value ไม่กระตุ้น onchange ของ EMCS)
                driver.execute_script(
                    "var el=document.getElementById(arguments[0]);"
                    "if(el){el.dispatchEvent(new Event('change',{bubbles:true}));}",
                    p + "ddlPerson_Type")
                time.sleep(0.6)   # รอ JS เติมทะเบียน
                # คู่กรณีหลายคัน → EMCS ถามว่าจะเอาทะเบียนคันไหน (overlay ทับทั้งหน้า)
                _pick_car_regno_dialog(driver, inj.get("car_regno", ""),
                                       f"(ผู้บาดเจ็บ {n + 1})")
                log(f"   ✓ ประเภทบุคคล (value {pt})")
            except Exception:
                log(f"   ⚠️ เลือกประเภทบุคคล {n + 1} ไม่ได้")

        # ชื่อ — แยกคำนำหน้า/ชื่อ/สกุล; หน้านี้มี layout 3 แบบที่ server สลับให้
        # (แยกช่อง txtInj_Name01 / ช่องเดียว txtInj_Name / แถว divAXA) → กรอกช่องที่
        # vlidInjPerson เช็คไว้เสมอ แล้วเติมช่องของ layout ที่โผล่จริงเพิ่ม
        # (set_text มี JS fallback เขียนช่องที่ซ่อนอยู่ได้ จึงปลอดภัยที่จะกรอกทั้งคู่)
        full = inj.get("name", "")
        title, first, last = split_thai_name(full)
        set_text(driver, p + "txtInj_Name", _dash(full))
        if _is_displayed(driver, p + "txtInj_Name01"):
            if title and _select_has_options(driver, p + "ddlInj_Title_ID"):
                fuzzy_select(driver, p + "ddlInj_Title_ID", EMCS_TITLE.get(title, title),
                             min_score=TITLE_MIN_SCORE,
                             label=f"คำนำหน้าผู้บาดเจ็บ {n + 1}")
            set_text(driver, p + "txtInj_Name01", _dash(first))
            set_text(driver, p + "txtInj_LastName01", _dash(last))
        if _is_displayed(driver, p + "divAXA"):     # ฟอร์มผู้บาดเจ็บเวอร์ชัน AXA
            if title:
                fuzzy_select(driver, p + "ddlInj_Title_ID", EMCS_TITLE.get(title, title),
                             min_score=TITLE_MIN_SCORE, timeout=5,
                             label=f"คำนำหน้าผู้บาดเจ็บ {n + 1}")
            set_text(driver, p + "txtInj_Name_AXA", _dash(first or full))
            set_text(driver, p + "txtInj_LastName_AXA", last)
            log(f"   ✓ ฟอร์มผู้บาดเจ็บเวอร์ชัน AXA — กรอกชื่อ/นามสกุลแยกช่อง (คนที่ {n + 1})")

        # เพศ (0=ชาย M / 1=หญิง F,W)
        # เพศ — ว่างจาก ISURVEY → อนุมานจากคำนำหน้าในชื่อ (fallback)
        g = resolve_gender(inj.get("gender", ""), inj.get("name", ""))
        if g:
            try:
                driver.find_element(
                    By.ID, p + f"rdoGender_{'0' if g == 'M' else '1'}").click()
            except Exception:
                log(f"   ⚠️ เลือกเพศผู้บาดเจ็บ {n + 1} ไม่ได้")
        else:
            log(f"   ⚠️ ไม่ทราบเพศผู้บาดเจ็บ {n + 1} (ISURVEY ว่าง + ชื่อไม่มีคำนำหน้า)")

        set_text(driver, p + "txtInj_Age", inj.get("age", ""))
        set_text(driver, p + "txtCitizen_ID", _dash(inj.get("citizen_id", "")))
        set_text(driver, p + "txtInj_Job", inj.get("job", ""))
        # เลขทะเบียน — EMCS เติมให้อัตโนมัติจาก ddlPerson_Type แล้ว (รถประกัน/คู่กรณี
        # ตามประเภท) → อ่าน readback: มีค่าแล้ว "ห้ามเขียนทับด้วยค่าว่าง" (บั๊กเดิมที่ทำให้
        # billing gate เด้ง); เติมเองเฉพาะตอนยังว่าง (เช่น บุคคลภายนอกรถ) + มีค่าจากผู้ใช้
        auto = ""
        try:
            auto = (driver.find_element(By.ID, p + "txtCar_RegNo")
                    .get_attribute("value") or "").strip()
        except Exception:
            pass
        manual = (ui.get("car_regno") if ui else None) or inj.get("car_regno", "")
        manual = _plate(manual)
        if manual and manual != auto:
            # ผู้ใช้กรอก/override (เช่น บุคคลภายนอกที่นั่งรถคันที่ 3 มีทะเบียนจริง)
            set_text(driver, p + "txtCar_RegNo", manual)
            log(f"   ✓ เลขทะเบียน (กรอก/override): {manual}")
        elif auto:
            log(f"   ✓ เลขทะเบียน auto-fill จากประเภทบุคคล: {auto}")
        elif pt == "05":
            # บุคคลภายนอกรถ — ไม่มีรถผูก ไม่ auto-fill → ใส่ 'บุคคลภายนอก' ให้ผ่าน gate
            set_text(driver, p + "txtCar_RegNo", "บุคคลภายนอก")
            log("   ✓ เลขทะเบียน = 'บุคคลภายนอก' (บุคคลภายนอกรถ ไม่มีรถผูก)")
        else:
            log(f"   ⚠️ เลขทะเบียนผู้บาดเจ็บ {n + 1} ว่าง (ไม่ auto-fill + ไม่มีค่ากรอก) "
                "— อาจติด gate หน้าค่าใช้จ่าย ต้องกรอกเองบน EMCS")
        set_text(driver, p + "txtInj_Address", inj.get("address", ""))
        set_text(driver, p + "txtInj_Tel_No", inj.get("tel_no", ""))
        # โรงพยาบาล = ฟิลด์บังคับ EMCS; ไม่มีข้อมูลจริง → _dash คืน "-" ให้ผ่าน gate + เซฟบล็อกได้
        set_text(driver, p + "txtInj_Hos_Name", _dash(inj.get("hospital", "")))
        set_text(driver, p + "txtInj_Cost", inj.get("cost", ""))

        # ประเภทบาดเจ็บ — ddlWounded_Type รับได้ทั้ง code XML (01-06) และป้ายไทย
        wt = (inj.get("wounded_type", "") or "").strip()
        if wt:
            _select_code_or_label(driver, p + "ddlWounded_Type", wt,
                                  f"ประเภทบาดเจ็บ {n + 1}")
        set_text(driver, p + "txtInj_Injure", _dash(inj.get("injure", "")))

        # ── ฟิลด์เสริม form-carried (id ยืนยันจาก ผู้บาดเจ็บ.html; EMCS ไม่บังคับ,
        #    set_text ข้ามค่าว่างเอง + มี JS fallback สำหรับ calendar readonly) ──
        set_text(driver, p + "txtInj_Work_Place", inj.get("work_place", ""))
        set_text(driver, p + "txtInj_Position", inj.get("position", ""))
        set_text(driver, p + "txtInj_Income", inj.get("income", ""))
        # ช่วงวันรักษา — XML เป็น ISO ค.ศ. (toXmlCE) → แปลงเป็นไทยเหมือน birthdate คู่กรณี
        set_text(driver, p + "wuCale_From_Date_txtCalendar",
                 iso_to_thai_date(inj.get("treat_from", "")))
        set_text(driver, p + "wuCale_To_Date_txtCalendar",
                 iso_to_thai_date(inj.get("treat_to", "")))
        # ความสัมพันธ์ผู้บาดเจ็บ — รับได้ทั้งรหัส RELATION และป้ายไทย
        rel = (inj.get("relation", "") or "").strip()
        if rel:
            _select_code_or_label(driver, p + "ddlDri_Relation_ID", rel,
                                  f"ความสัมพันธ์ผู้บาดเจ็บ {n + 1}")

    _pick_car_regno_dialog(driver, "", "(ก่อนบันทึก)")   # กันค้างจากคนสุดท้าย
    _save_section(driver, "btnSave_InjurePerson", "ผู้บาดเจ็บ")


def fill_assets(driver, data: ClaimData):
    """กรอกทรัพย์สิน (Tab 6) — กดเมนู imbAsset → เลือกจำนวน ddlAsset_Count →
    กรอกทีละบล็อก (dtlAsset_ctl00_wuAsset_*) → บันทึก btnSave_Asset
    (รูปแบบเดียวกับคู่กรณี) — เรียกหลัง save_main_form"""
    assets = data.assets
    if not assets:
        _clear_rows_if_any(driver, "ddlAsset_Count", "btnSave_Asset", "ทรัพย์สิน",
                           open_menu_id="wuMenuPage1_imbAsset")
        return
    log(f"EMCS: กรอกทรัพย์สิน {len(assets)} รายการ")
    click_retry(driver, By.ID, "wuMenuPage1_imbAsset")
    try:
        wait_present(driver, By.ID, "ddlAsset_Count", 20)
    except TimeoutException:
        log("   ⚠️ ส่วนทรัพย์สินไม่ปลดล็อก (ddlAsset_Count ไม่โผล่) — ข้าม กรอกเอง")
        return
    if len(assets) > MAX_ASSETS:
        log(f"   ⚠️ ทรัพย์สิน {len(assets)} รายการ เกิน {MAX_ASSETS} — กรอกเท่าที่ได้")

    Select(driver.find_element(By.ID, "ddlAsset_Count")).select_by_visible_text(
        str(min(len(assets), MAX_ASSETS)))
    time.sleep(1.5)

    for n, a in enumerate(assets[:MAX_ASSETS]):
        p = ASSET_PREFIX.format(n=n)
        log(f"   --- ชิ้นที่ {n + 1}: {a.get('name', '')} ---")
        set_text(driver, p + "txtAsset_Desc", _dash(a.get("name", "")))
        set_text(driver, p + "txtAsset_Damage", _dash(a.get("damage_detail", "")))
        set_text(driver, p + "txtAsset_Damage_Cause", _dash(a.get("damage_cause", "")))
        set_text(driver, p + "txtCost_Damage", a.get("damage_cost", ""))

        # เจ้าของ — EMCS มีฟอร์ม 2 เวอร์ชันที่ server สลับให้ตามบริษัทประกัน:
        #   ปกติ  = แถว divSTD: ช่องเดียว txtOwner
        #   AXA   = แถว divAXA: คำนำหน้า (ddlAsset_Title_ID) + ชื่อ + นามสกุล แยกช่อง
        # เดิมเช็คแค่ "dropdown มี options ไหม" ซึ่งเป็นจริงแม้แถว AXA ถูกซ่อน → เคส AXA
        # ที่มีทรัพย์สินเสียหาย กดบันทึกแล้ว EMCS ฟ้อง 'กรุณาใส่ชื่อเจ้าของทรัพย์สิน' ค้าง
        owner = a.get("owner_name", "")
        title, first, last = split_thai_name(owner)
        set_text(driver, p + "txtOwner", _dash(owner))
        if _is_displayed(driver, p + "divAXA"):
            if title:
                fuzzy_select(driver, p + "ddlAsset_Title_ID", EMCS_TITLE.get(title, title),
                             min_score=TITLE_MIN_SCORE,
                             label=f"คำนำหน้าเจ้าของ {n + 1}", timeout=5)
            set_text(driver, p + "txtAsset_Name_AXA", _dash(first or owner))
            set_text(driver, p + "txtAsset_LastName_AXA", last)   # ไม่ใช่ช่องบังคับ — ว่างได้
            log(f"   ✓ ฟอร์มทรัพย์สินเวอร์ชัน AXA — กรอกชื่อ/นามสกุลแยกช่อง (ชิ้นที่ {n + 1})")
        set_text(driver, p + "txtAddress", a.get("owner_address", ""))
        set_text(driver, p + "txtTel_No", a.get("owner_phone", ""))

    _save_section(driver, "btnSave_Asset", "ทรัพย์สิน")


def login(driver, cfg):
    """เปิดหน้า login แล้วเข้าสู่ระบบ — timeout ยาว (160s) ตามเดิม
    เผื่อหน้าโหลดช้าหรือมีขั้นตอนที่ต้องให้คนช่วยกดบนหน้าจอ"""
    log("EMCS: เปิดหน้า login")
    driver.get(cfg.emcs_login_url)

    # ปิด popup ประชาสัมพันธ์ (ถ้ามี)
    try:
        driver.find_element(By.XPATH, '//*[@id="divPR"]/div[1]/a').click()
    except Exception:
        pass

    wait_visible(driver, By.ID, "txtUserName", 160)
    wait_visible(driver, By.ID, "txtPassWord", 160)
    wait_visible(driver, By.ID, "imbLogin", 160)

    driver.find_element(By.ID, "txtUserName").send_keys(cfg.emcs_username)
    driver.find_element(By.ID, "txtPassWord").send_keys(cfg.emcs_password)
    driver.find_element(By.ID, "imbLogin").click()

    # หลัง login จะเข้าหน้า frmBill_News — เปลี่ยน path เป็น frmMainPage
    # โดยคง query string (session token) เดิมไว้
    wait_clickable(driver, By.ID, "btnEnter", 160)
    link = driver.current_url.replace("frmBill_News.aspx", "frmMainPage.aspx")
    driver.get(link)
    log("EMCS: login แล้ว เข้าหน้า MainPage")


# ดึงเลข e-Survey จากแถวผลค้นหาที่มีเลขเคลมตรงกัน (กันแถวอื่นปน)
_JS_FIND_ESURVEY_ROWS = r"""
const claim = arguments[0];
const out = [];
document.querySelectorAll("a").forEach(a => {
  const t = (a.innerText || "").trim();
  if (!/^S\d{9,13}$/.test(t)) return;
  const row = a.closest("tr");
  const rowText = row
    ? row.innerText.replace(/\s+/g, " ").trim().slice(0, 130) : "";
  if (claim && rowText && !rowText.includes(claim)) return;
  out.push({esurvey: t, row: rowText});
});
return out;
"""


def find_existing_reports(driver, claim_no: str) -> list:
    """ค้นหาว่าเลขเคลมนี้เคยเปิดเรื่องใน EMCS แล้วหรือยัง (หน้า MainPage)
    คืน [{'esurvey': 'S68...', 'row': 'ข้อความแถว'}, ...]"""
    if not (claim_no or "").strip():
        return []
    wait_visible(driver, By.ID, "txtRef_Claim_No", 20)
    box = driver.find_element(By.ID, "txtRef_Claim_No")
    box.clear()
    box.send_keys(claim_no.strip())
    driver.find_element(By.ID, "btnSearch").click()
    time.sleep(3)  # รอผลค้นหา (postback)
    return driver.execute_script(_JS_FIND_ESURVEY_ROWS, claim_no.strip())


def guard_duplicate_report(driver, data: ClaimData, force_new: bool, existing=None):
    """ด่านกันเปิดเรื่องซ้ำ: ถ้าเคลมนี้มีเรื่องใน EMCS แล้ว → หยุดทันที
    (ข้ามด่านได้ด้วย --force-new เมื่อตั้งใจสร้างซ้ำจริงๆ)

    existing: ส่งผลค้นหาที่ดึงมาแล้วเข้ามาได้ (กันค้นซ้ำ) — None = ค้นเอง
    หมายเหตุ: กรณี "มีเรื่องเดิม + invoice ใหม่ = งานต่อเนื่อง" ถูกแยกไปจัดการก่อน
    ใน fill_one แล้ว — ด่านนี้จะ raise เฉพาะเรื่องซ้ำจริง (invoice เดิม/ไม่ระบุ)"""
    if existing is None:
        try:
            existing = find_existing_reports(driver, data.claim_value)
        except Exception as e:
            log(f"   ⚠️ ตรวจเรื่องซ้ำไม่สำเร็จ ({type(e).__name__}) — ดำเนินการต่อ "
                "โปรดเช็คเรื่องซ้ำเองด้วย")
            return

    if not existing:
        log("EMCS: ไม่พบเรื่องเดิมของเคลมนี้ — สร้างงานใหม่ได้")
        return

    lines = "\n".join(f"   - {r['esurvey']}  {r['row'][:90]}" for r in existing)
    if not force_new:
        raise RuntimeError(
            f"เคลม {data.claim_value} มีเรื่องใน EMCS อยู่แล้ว "
            f"{len(existing)} เรื่อง:\n{lines}\n"
            "→ หยุดเพื่อกันเปิดเรื่องซ้ำ — ถ้าตั้งใจสร้างใหม่จริงๆ "
            "ให้รันด้วย --force-new"
        )
    log(f"   ⚠️ พบเรื่องเดิม {len(existing)} เรื่อง แต่ได้รับคำสั่ง "
        f"--force-new — สร้างเรื่องใหม่ต่อ\n{lines}")


def continuation_esurvey(existing, invoice: str):
    """ตัดสินว่าเป็น "งานต่อเนื่อง" ไหม → คืนเลข e-Survey ที่จะทำต่อ (None = ไม่ใช่)

    เกณฑ์: เคลมมีเรื่องเดิมใน EMCS แล้ว + เลข invoice (เซอร์เวย์) ใหม่นี้
    "ยังไม่ปรากฏ" ในเรื่องเดิมใดเลย = เป็นครั้งถัดไป → ทำงานต่อเนื่องกับเรื่องเดิม
    (ถ้า invoice มีในเรื่องเดิมแล้ว = ซ้ำของจริง → คืน None ให้ด่านบล็อก)"""
    invoice = (invoice or "").strip()
    if not existing or not invoice:
        return None
    if any(invoice in (r.get("row") or "") for r in existing):
        return None  # invoice นี้อยู่ในเรื่องเดิมแล้ว = ซ้ำ ไม่ใช่งานต่อเนื่อง
    if len(existing) > 1:
        log(f"   ⚠️ เจอเรื่องเดิม {len(existing)} เรื่อง — ทำงานต่อเนื่องกับเรื่องแรก "
            f"({existing[0]['esurvey']}) โปรดตรวจให้แน่ใจว่าถูกเรื่อง")
    return existing[0]["esurvey"]


# ----------------------------------------------------------------- สถานะเรื่อง
# คอลัมน์ "สถานะ" ในหน้าค้นหา EMCS แยก draft กับ ส่งงานแล้ว:
#   'รายงานสร้างใหม่'        = draft (ยังไม่กดส่งงานใหม่)
#   'ประกันตรวจสอบรายงาน'   = ส่งงานแล้ว (รอประกันตรวจ)
DRAFT_STATUSES = {"รายงานสร้างใหม่"}
# สถานะที่ "แปลว่าส่งงานแล้วแน่นอน" — whitelist เท่านั้น
# เดิมใช้กติกากลับด้าน ("ไม่ใช่ draft = ส่งแล้ว") ซึ่งกว้างเกินจริง: EMCS มีสถานะอื่นอีก
# ที่ไม่ได้แปลว่าส่ง (เช่นถูกตีกลับ/ยกเลิก) แล้วเราจะรายงานผิดว่าส่งเรียบร้อย
SUBMITTED_STATUSES = {"ประกันตรวจสอบรายงาน"}

_JS_REPORT_STATUS = r"""
const claim = arguments[0];
const rows = [];
document.querySelectorAll("a").forEach(a => {
  const t = (a.innerText || "").trim();
  if (!/^S\d{9,13}$/.test(t)) return;
  const row = a.closest("tr");
  if (!row) return;
  const cells = [...row.querySelectorAll("td")].map(td => (td.innerText||"").trim());
  if (claim && !cells.join(" ").includes(claim)) return;
  let statusIdx = -1;
  const table = row.closest("table");
  if (table) {
    const hr = table.querySelector("tr");
    if (hr) statusIdx = [...hr.querySelectorAll("td,th")]
      .map(c => (c.innerText||"").trim()).indexOf("สถานะ");
  }
  // เลขใบแจ้งหนี้/เลขเซอร์เวย์ — ISURVEY ใช้ SEABI-… ส่วนงานจาก se-survey ใช้ SETP-…
  // (เดิมจับแค่ SEABI → งานของเราคืน survey_no ว่างเสมอ แยกเรื่องไม่ได้)
  const surv = cells.find(c => /\b(SEABI|SETP)[-\w]/i.test(c)) || "";
  rows.push({esurvey: t, status: statusIdx >= 0 ? (cells[statusIdx] || "") : "",
             survey_no: surv});
});
return rows;
"""


def report_status(driver, claim: str, wait: float = 20, survey_no: str = ""):
    """ค้นเรื่องของเคลมในหน้า EMCS → คืน {esurvey, status, survey_no} (None ถ้าไม่เจอ)

    survey_no (เลขเซอร์เวย์/ใบแจ้งหนี้ SETP-… หรือ SEABI-…) = **ตัวแยกเรื่อง**
    1 เลขเคลมมีได้หลายเรื่อง (งานครั้งที่ 1, 2, …) เดิมกรองด้วยเลขเคลมอย่างเดียวแล้ว
    เก็บ "แถวสุดท้ายที่เจอ" → อ่านสถานะของเรื่องผิดใบได้ (user ยืนยัน 2026-08-11 ว่า
    ตัวแยกคือเลขเซอร์เวย์). ไม่ส่ง survey_no มา + เจอหลายแถว = คืน None (ไม่เดา)

    ⚠️ ห้ามกลับไปใช้ sleep คงที่แล้วอ่านทีเดียว — เจอจริง 2026-08-06 (เคลม
    2026013160796): หลังกดส่งงาน session หลุด ต้อง login ใหม่ ตารางผลค้นเลยมาช้า
    กว่า 3 วิ อ่านได้ค่าว่าง → บอทสรุปว่า "ส่งงานไม่สำเร็จ" ทั้งที่ส่งสำเร็จแล้ว
    """
    if not (claim or "").strip():
        return None
    claim = claim.strip()
    want = (survey_no or "").strip().upper()
    wait_visible(driver, By.ID, "txtRef_Claim_No", 20)
    box = driver.find_element(By.ID, "txtRef_Claim_No")
    box.clear()
    box.send_keys(claim)
    driver.find_element(By.ID, "btnSearch").click()

    def pick(rows):
        """เลือกแถวที่ตรงเรื่อง — คืน None ถ้าแยกไม่ออก (ดีกว่าเดาผิด)"""
        if not rows:
            return None
        if want:
            hit = [r for r in rows if want in (r.get("survey_no") or "").upper()]
            if len(hit) == 1:
                return hit[0]
            if not hit:
                log(f"   ⚠️ ไม่พบเรื่องที่เลขเซอร์เวย์ {want} ในเคลม {claim} "
                    f"(เจอ {len(rows)} เรื่อง) — ไม่เดา")
                return None
            log(f"   ⚠️ เลขเซอร์เวย์ {want} ตรงหลายเรื่อง ({len(hit)}) — ไม่เดา")
            return None
        if len(rows) == 1:
            return rows[0]
        log(f"   ⚠️ เคลม {claim} มี {len(rows)} เรื่องใน EMCS แต่ไม่ได้บอกเลขเซอร์เวย์ "
            "— แยกไม่ออก ไม่เดา")
        return None

    # รอจนแถวโผล่จริง (postback ช้ากว่าปกติได้ตอนเพิ่ง login ใหม่)
    deadline = time.time() + max(3.0, float(wait))
    info = None
    while time.time() < deadline:
        time.sleep(1)
        try:
            rows = driver.execute_script(_JS_REPORT_STATUS, claim) or []
        except Exception:
            rows = []
        info = pick(rows)
        if info and (info.get("status") or "").strip():
            return info
    return info


def is_report_submitted(driver, claim: str, survey_no: str = ""):
    """ตรวจว่าเรื่องนี้ "กดส่งงานใหม่แล้วจริงไหม" — gate ก่อนแจ้ง ISURVEY

    คืน (submitted: bool | None, reason: str) — **None = ไม่ทราบ** (อ่านไม่ได้/แยกเรื่อง
    ไม่ออก) ผู้เรียกต้องไม่ตีความว่า "ยังไม่ส่ง" แล้วไปกดส่งซ้ำ
    ใช้ whitelist SUBMITTED_STATUSES ไม่ใช่กติกากลับด้าน "ไม่ใช่ draft = ส่งแล้ว"
    """
    info = report_status(driver, claim, survey_no=survey_no)
    if not info:
        return None, "ไม่พบเรื่องของเคลมนี้ใน EMCS (หรือแยกเรื่องไม่ออก)"
    st = (info.get("status") or "").strip()
    if not st:
        return None, "อ่านสถานะเรื่องไม่ได้"
    if st in SUBMITTED_STATUSES:
        return True, f"ส่งงานแล้ว (สถานะ: {st})"
    if st in DRAFT_STATUSES:
        return False, f"ยังไม่ได้กดส่งงานใหม่ (สถานะ: {st})"
    return None, f"สถานะอื่น ไม่ยืนยันว่าส่งแล้ว: {st}"


def goto_mainpage(driver, cfg, mainpage_url: str = "") -> str:
    """กลับหน้ารายการงาน (ใช้ตอนทำหลายเคลมต่อกัน) — login ใหม่ถ้า session หาย
    คืน URL หน้ารายการ (มี session token) ไว้ใช้รอบถัดไป"""
    if mainpage_url:
        driver.get(mainpage_url)
        try:
            wait_visible(driver, By.ID, "cmdNewReport", 10)
            return mainpage_url
        except TimeoutException:
            log("EMCS: session หาย — login ใหม่")
    login(driver, cfg)
    return driver.current_url


def new_report(driver):
    log("EMCS: กดสร้างงานใหม่")
    wait_clickable(driver, By.ID, "cmdNewReport").click()


# ------------------------------------------------------ นำเข้าข้อมูลแบบ XML
# ค่าสำรองของ "หน้านำเข้า XML" เท่านั้น (ผู้เรียกส่ง insurer_code มาเสมอในทางปฏิบัติ)
# ⛔ ห้ามเอาไปใช้เป็น default ของเส้นทางอื่น — ตอนนี้รับงาน 2 บริษัท (ไอโออิ 1059 ·
#    ไทยไพบูลย์ 2429) การ default เป็นบริษัทใดบริษัทหนึ่งคือยื่นสำนวนผิดเจ้าของงาน
INSURER_MAJOR_ID = "1059"   # ไอโออิกรุงเทพประกันภัย


def _set_selectpicker(driver, select_id: str, value: str):
    """ตั้งค่า bootstrap-selectpicker (native <select> ซ่อน tabindex=-98) ผ่าน JS:
    set value + ยิง change + refresh ตัว selectpicker ให้ UI ตรงกับค่าจริง"""
    driver.execute_script(
        "var s=document.getElementById(arguments[0]);"
        "if(!s)return;s.value=arguments[1];"
        "s.dispatchEvent(new Event('change',{bubbles:true}));"
        "if(window.jQuery&&jQuery.fn.selectpicker)"
        "jQuery('#'+arguments[0]).selectpicker('refresh');",
        select_id, value)


def _import_branch_value(driver, timeout: int = 12) -> str:
    """รอ option สาขา (ddlInsurerBRList) โหลด lazy หลังเลือกบริษัท → คืน value
    (เลือก 'กรุงเทพ' ถ้ามี ไม่งั้น option แรกที่ไม่ใช่ '0')"""
    for _ in range(timeout * 2):
        opts = driver.execute_script(
            "var s=document.getElementById('ddlInsurerBRList');"
            "return s?Array.prototype.map.call(s.options,function(o){"
            "return [o.value,(o.text||'').trim()];}):[];")
        real = [(v, t) for v, t in opts if v and v != "0"]
        if real:
            return next((v for v, t in real if "กรุงเทพ" in t), real[0][0])
        time.sleep(0.5)
    raise RuntimeError("สาขาประกันไม่โหลด (ddlInsurerBRList ว่าง) — ตรวจหน้านำเข้า XML")


def _close_sweetalert(driver, timeout: int = 10) -> str:
    """ปิด SweetAlert (.swal-button) ถ้ามี — คืนข้อความ (.swal-text) ก่อนปิด"""
    end = time.time() + timeout
    text = ""
    while time.time() < end:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, ".swal-text")
            if els and els[0].is_displayed() and els[0].text.strip():
                text = els[0].text.strip()
            for sb in driver.find_elements(By.CSS_SELECTOR, ".swal-button"):
                if sb.is_displayed():
                    sb.click()
                    return text
        except Exception:
            pass
        time.sleep(0.4)
    return text


def import_xml_report(driver, cfg, data: ClaimData, insurer_code: str = None) -> str:
    """นำเข้า SURV_REPORT XML เข้า EMCS แทนการกรอกฟอร์มหลักเอง (ปุ่ม imbFileImport_XML)

    flow (verify หน้าจริง 2026-06-24): frmMainPage → imbFileImport_XML →
    frmFileImportXML.aspx → เลือกบริษัท (1059) + สาขา (selectpicker JS) →
    send_keys ไฟล์เข้า inpImport (file ซ่อน — ไม่เปิด OS dialog) → JS click btnImport →
    ปิด SweetAlert → frmSurvey.aspx (draft สร้างแล้ว เข้าโหมดแก้ btnUpdate)
    คืนเลข e-Survey ถ้าอ่านได้จากข้อความ import (ไม่งั้น '')

    import เติมฟอร์มหลัก ~90% แต่ทิ้งช่องว่าง/ทำพลาด: คำนำหน้า, แยกชื่อ-สกุล,
    อำเภอผู้ขับขี่/เกิดเหตุ, ลักษณะความเสียหาย, รถเสียหายหนัก/เบา, ประเภทรถ (code-based),
    คู่กรณีทุกฟิลด์ (สร้าง row เปล่า) → ผู้เรียก (fill_imported) อุด/แก้ด้วย fill_* เดิม"""
    xml_path = Path(data.xml_file or "")
    if not data.xml_file or not xml_path.exists():
        raise RuntimeError(
            f"โหมดนำเข้า XML ต้องมีไฟล์ SURV_REPORT XML แต่ไม่พบ: {data.xml_file!r} — "
            "อ่านเคลมแบบมี XML ก่อน (อย่าใช้ --no-xml; ฝั่งอ่านต้องดาวน์โหลด XML ไว้)")
    xml_path = xml_path.resolve()
    log(f"EMCS: นำเข้าข้อมูลแบบ XML — {xml_path.name}")

    wait_clickable(driver, By.ID, "imbFileImport_XML").click()
    wait_present(driver, By.ID, "inpImport", 30)        # frmFileImportXML โหลดแล้ว
    # รหัสบริษัท: ผู้เรียกส่ง insurer_code มา (resolve จากชื่อบริษัทของเคส) — ไม่งั้น fallback
    # ไอโออิ (บริษัทเดียวของโปรเจกต์เดิม). ผิดบริษัท = เข้าเรื่องผิดบัญชี → ต้อง resolve ให้ได้ก่อน
    ins_id = str(insurer_code or INSURER_MAJOR_ID)
    log(f"   เลือกบริษัทประกัน (รหัส {ins_id}) + สาขา")
    _set_selectpicker(driver, "ddlInsurerNameMajor", ins_id)
    branch = _import_branch_value(driver)
    _set_selectpicker(driver, "ddlInsurerBRList", branch)
    time.sleep(1.5)   # ให้ postback ของบริษัท/สาขา (บางบริษัทมี ajax) settle ก่อนแนบไฟล์

    # patch INSURERBRID ในไฟล์ให้เป็น "รหัสสาขา" (ตัวเลข) ที่ตรงกับสาขาที่เลือกจริง —
    # se-survey ใส่เป็นข้อความ (เช่น "กรุงเทพ") แต่ EMCS validate ว่าต้องเป็นรหัสสาขา
    # ไม่งั้น server ตอบ "ไม่พบข้อมูลนำเข้าที่ระบบต้องการ". branch = composite "brId|xxx" → ใช้ส่วนหน้า
    br_id = str(branch).split("|")[0].strip()
    if br_id.isdigit():
        try:
            _t = xml_path.read_text(encoding="utf-8", errors="replace")
            _new = re.sub(r"<INSURERBRID>.*?</INSURERBRID>",
                          f"<INSURERBRID>{br_id}</INSURERBRID>", _t, count=1, flags=re.S)
            if _new != _t:
                xml_path.write_text(_new, encoding="utf-8")
                log(f"   patch INSURERBRID → {br_id} (ให้ตรงสาขาที่เลือก)")
        except Exception as _e:
            log(f"   ⚠️ patch INSURERBRID ไม่สำเร็จ: {_e}")

    # แนบไฟล์ แล้ว "ยืนยันว่าติดจริง" ก่อนกดนำเข้า (กัน import ทั้งที่ไฟล์ไม่ติด → EMCS สร้างเรื่องเปล่า)
    # หมายเหตุสำคัญ: EMCS มี change handler validate นามสกุล — รับเฉพาะ .txt เท่านั้น
    # ไฟล์นามสกุลอื่น (เช่น .xml) จะโดน $("#inpImport").val("") ล้างทิ้งทันที + swal เตือน
    # → ต้องส่งไฟล์ .txt เข้ามา (ผู้เรียกตั้งชื่อ data.xml_file เป็น .txt); un-hide เผื่อ chromedriver
    #   ไม่ยอม send_keys ให้ input ที่ display:none; verify files.length กันทุกกรณีที่ไฟล์ไม่ติด
    log("   ส่งไฟล์ (un-hide input + ยืนยันว่าติดก่อนกดนำเข้า)")
    attached = ""
    for attempt in range(6):
        driver.execute_script(
            "var f=document.getElementById('inpImport');"
            "if(f){f.style.display='block';f.style.visibility='visible';f.style.opacity='1';"
            "f.style.width='1px';f.style.height='1px';f.removeAttribute('hidden');}")
        driver.find_element(By.ID, "inpImport").send_keys(str(xml_path))
        time.sleep(1.2)
        attached = driver.execute_script(
            "var f=document.getElementById('inpImport');"
            "return (f && f.files && f.files.length) ? f.files[0].name : '';")
        if attached:
            break
        log(f"   (แนบไฟล์รอบ {attempt + 1} ยังไม่ติด — รอแล้วลองใหม่)")
        time.sleep(1.5)
    if not attached:
        raise RuntimeError(
            "แนบไฟล์ XML เข้า inpImport ไม่ติดหลังลอง 6 รอบ — ตรวจหน้า Import File XML")
    log(f"   ✓ ไฟล์ติดแล้ว: {attached} → กดนำเข้าข้อมูล")
    driver.execute_script("document.getElementById('btnImport').click();")
    try:
        accept_alert(driver, timeout=10)               # เผื่อมี JS confirm
    except Exception:
        pass
    # จับ swal เต็มก่อนปิด — server-side validation ส่งตารางรายละเอียด (field ที่ผิด) มาใน content span
    # ซึ่ง _close_sweetalert จับแค่หัวเรื่อง ไม่รวม span → อ่าน innerText ของ modal ทั้งก้อน
    time.sleep(2.5)
    swal_full = ""
    try:
        swal_full = driver.execute_script(
            "var m=document.querySelector('.swal-modal,.sweet-alert,.swal-overlay');"
            "return m ? m.innerText : '';") or ""
    except Exception:
        pass
    swal = _close_sweetalert(driver, timeout=12) or ""
    if swal_full:
        log(f"   [import swal] {swal_full[:600]}")
    elif swal:
        log(f"   [import] {swal[:200]}")

    # ต้องเข้าหน้าฟอร์ม (frmSurvey) จริง — ไม่งั้น import ล้มเหลว
    try:
        WebDriverWait(driver, 30).until(
            lambda d: "frmSurvey.aspx" in d.current_url)
        wait_visible(driver, By.ID, "btnUpdate", 20)
    except TimeoutException as e:
        raise RuntimeError(
            "นำเข้า XML แล้วไม่เข้าหน้าฟอร์ม (frmSurvey) — "
            f"ข้อความระบบ: {(swal_full or swal)[:400]!r}") from e
    m = re.search(r"S\d{9,13}", swal or "")
    log("EMCS: นำเข้า XML สำเร็จ → ฟอร์มแก้ (frmSurvey)"
        + (f" e-Survey {m.group(0)}" if m else ""))
    return m.group(0) if m else ""


# ------------------------------------------------------------------ ส่วนกรอก

# ประเภทเคลม: **สองระบบไม่ตรงกัน** (ดัมป์ master จริง 2026-08-05)
#   ISURVEY masterClaimMType : 01 เคลมสด · 02 เคลมแห้ง · 03 ติดตาม · 04 เจรจาสินไหม
#   EMCS rdoSurv_Claim_Type  : [0] เคลมสด · [1] เคลมแห้ง · [2] งานนัดหมาย · [3] งานติดตาม
# เดิมแปลงด้วย index ตรง ๆ (idx = ประเภท-1) ซึ่งถูกเฉพาะ 01/02 — ถ้าเจอ 03 จะไปติ๊ก
# "งานนัดหมาย" และ 04 จะเป็น "งานติดตาม" ผิดทั้งคู่แบบเงียบ ๆ (ยังไม่เคยเจอเพราะ
# งานที่ทำมาเป็น 01/02 หมด) → แปลงตามความหมาย ไม่ใช่ตามลำดับ
# ⚠️ "งานนัดหมาย" ของ EMCS **ไม่มีคู่ใน ISURVEY** — เลือกได้เฉพาะคนบนหน้าจอ
CLAIM_TYPE_TO_EMCS = {
    "1": 0,       # เคลมสด      → เคลมสด
    "2": 1,       # เคลมแห้ง    → เคลมแห้ง
    "3": 3,       # ติดตาม      → งานติดตาม (ไม่ใช่ [2] งานนัดหมาย)
    "4": None,    # เจรจาสินไหม → EMCS ไม่มีตัวเลือกนี้ ต้องให้คนเลือกเอง
}
EMCS_CLAIM_TYPE_LABEL = ["เคลมสด", "เคลมแห้ง", "งานนัดหมาย", "งานติดตาม"]


def fill_claim_type(driver, claim_type: str, warn: str = ""):
    """เลือกประเภทเคลมบน EMCS ตามค่าที่ ISURVEY ให้มา (แปลงตามความหมาย ดู
    CLAIM_TYPE_TO_EMCS) — หา radio จาก container id ก่อน ถ้าไม่ได้ค่อย fallback
    ไป absolute XPath เดิมที่พิสูจน์แล้วว่าใช้ได้

    warn: ข้อความเตือนให้คนตรวจประเภทเคลมเองบนหน้าจอ (เช่น น่าจะเป็นงานนัดหมาย)
      — บอท **ไม่เปลี่ยนให้เอง** แค่ติ๊กตามต้นทางแล้วย้อมเหลืองไว้ให้เห็น
    """
    code = str(claim_type or "").strip().lstrip("0") or ""
    idx = CLAIM_TYPE_TO_EMCS.get(code, "?")
    container = wait_visible(driver, By.ID, "rdoSurv_Claim_Type")

    if idx == "?":
        raise ValueError(f"ประเภทเคลมไม่ถูกต้อง: {claim_type!r} (ต้องเป็น 1-4)")
    if idx is None:
        log(f"   ⚠️ ประเภทเคลม '{CLAIM_TYPE_NAMES.get(code, code)}' ของ ISURVEY "
            "ไม่มีตัวเลือกตรงกันใน EMCS — เลือกเองบนหน้าจอ")
        _warn_claim_type(driver, f"ISURVEY = '{CLAIM_TYPE_NAMES.get(code, code)}' "
                                 "ซึ่ง EMCS ไม่มีตัวเลือกนี้ — เลือกให้ตรงงานเอง")
        return

    log(f"EMCS: เลือกประเภทเคลม = {claim_type} ({EMCS_CLAIM_TYPE_LABEL[idx]})")
    try:
        radios = container.find_elements(By.TAG_NAME, "input")
        radios[idx].click()
    except Exception:
        driver.find_element(
            By.XPATH,
            "/html/body/form/table[3]/tbody/tr[1]/td[3]/table/tbody/tr/td[2]"
            f"/table/tbody/tr/td[{idx + 1}]/input",
        ).click()
    if warn:
        log(f"   ⚠️ {warn}")
        _warn_claim_type(driver, warn)


def _days_apart(d1: str, d2: str):
    """ห่างกันกี่วัน ระหว่างวันที่รูปแบบ dd/mm/yyyy (พ.ศ. หรือ ค.ศ. ก็ได้)
    คืน None ถ้าอ่านไม่ได้ — ไม่เดา"""
    def _parse(s):
        parts = str(s or "").strip().split("|")[0].strip().split("/")
        if len(parts) != 3:
            return None
        try:
            d, m, y = (int(x) for x in parts)
        except ValueError:
            return None
        y = y - 543 if y > 2400 else y
        try:
            return datetime(y, m, d)
        except ValueError:
            return None
    a, b = _parse(d1), _parse(d2)
    return None if (a is None or b is None) else abs((b - a).days)


# EMCS บังคับว่า "เคลมสด" ต้องมี 5 วันที่อยู่ในกรอบ 24 ชม. (validForm → AlertSummary)
# งานที่เกิดเหตุไปนานแล้วแต่เพิ่งออกตรวจตามนัด = **งานนัดหมาย** ซึ่ง ISURVEY
# ไม่มีตัวเลือกให้เลือก (master มีแค่ เคลมสด/เคลมแห้ง/ติดตาม/เจรจาสินไหม)
# → ต้นทางจึงติดเป็น 'เคลมสด' มาเรื่อย ๆ แล้วไปเด้งตอนกดบันทึก
APPOINTMENT_GAP_DAYS = 1


def appointment_hint(data) -> str:
    """คืนข้อความเตือนถ้าเคลมนี้ "น่าจะเป็นงานนัดหมาย" แต่ต้นทางตั้งเป็นเคลมสด
    ('' = ไม่เข้าข่าย) — แจ้งเตือนอย่างเดียว ไม่เปลี่ยนค่าให้"""
    if str(getattr(data, "claim_type", "") or "").strip().lstrip("0") != "1":
        return ""
    acc = getattr(data, "acc_date", "")
    for field, label in (("arrive_date", "วันที่สำรวจภัย"),
                         ("finish_date", "วันที่สำรวจภัยเสร็จ")):
        gap = _days_apart(acc, getattr(data, field, ""))
        if gap is not None and gap > APPOINTMENT_GAP_DAYS:
            return (f"เกิดเหตุ {acc} แต่{label} {getattr(data, field)} — ห่างกัน {gap} วัน "
                    "เกินกรอบ 24 ชม. ของ 'เคลมสด' ที่ EMCS บังคับ "
                    "(น่าจะเป็น 'งานนัดหมาย' ซึ่ง ISURVEY ไม่มีให้เลือก) — เลือกเองบนหน้าจอ")
    return ""


def _warn_claim_type(driver, note: str):
    """ย้อมเหลือง radio ประเภทเคลมทั้งชุด + ใส่ tooltip — ให้คนเห็นว่าต้องตรวจช่องนี้
    (บอทไม่เดาแทน เพราะ ISURVEY แสดงเจตนา 'นัดหมาย' ไม่ได้เลย ต้องคนตัดสิน)"""
    for i in range(4):
        mark_check(driver, f"rdoSurv_Claim_Type_{i}", f"ตรวจประเภทเคลม: {note}")


def fill_severity(driver, severity: str):
    """เลือก 'รถเสียหาย : หนัก/เบา' (field บังคับของ EMCS)
    rdoHev_Car_0 = หนัก, rdoHev_Car_1 = เบา"""
    sev = (severity or "").strip()
    idx = {"หนัก": "0", "เบา": "1"}.get(sev)
    if idx is None:
        log(f"   ⚠️ ค่ารถเสียหาย '{severity}' ไม่รู้จัก (ต้องเป็น หนัก/เบา) — "
            "ข้าม ต้องเลือกเองบนหน้าจอ")
        return
    wait_visible(driver, By.ID, f"rdoHev_Car_{idx}").click()
    log(f"EMCS: รถเสียหาย = {sev}")


def _derive_insured_title(data: ClaimData) -> tuple:
    """หาคำนำหน้าผู้ขับขี่รถประกัน (EMCS บังคับ แต่ ISURVEY ไม่มีช่องนี้ให้ตรง ๆ —
    ช่อง drv_title มีในโครงสร้างแต่ว่างทุกเคส ส่วน drv_gender มีค่าจริงเสมอ)

    ไล่จากแหล่งที่แม่นสุดลงมา:
      1. se-survey กรอกคำนำหน้ามาตรง ๆ (มือถือ/สแกนบัตร)
      2. คำนำหน้าติดมากับชื่อผู้ขับขี่เอง — ISURVEY เก็บรวมในช่องชื่อบ่อย
         (เจอจริง: 'น.ส. อุมาพร' เคลม 058298, 'คุณ พัลลภ ธาดากิจวณิช' เคลม 158841)
      3. ชื่อผู้เอาประกันมีคำนำหน้า และเป็นคนเดียวกับผู้ขับขี่
      4. เพศ + อายุ — ชาย = 'นาย' แน่นอน, หญิงผู้ใหญ่ตกเป็น 'คุณ' (ดู title_from_gender_age)

    คืน ('', '') เฉพาะตอนไม่รู้เพศด้วย → fill_driver หยุดรอคนเลือก
    คืน (title, แหล่งที่มา)"""
    weak, weak_src = "", ""     # 'คุณ' ที่ต้นทางกรอกมา — เก็บไว้เป็นทางสำรองท้ายสุด

    def _take(t, src):
        """คืน (title, src) ถ้าเป็นคำนำหน้าจริง; ถ้าเป็นคำกลางเก็บไว้ก่อนแล้วไปหาต่อ"""
        nonlocal weak, weak_src
        if t in WEAK_TITLES:
            if not weak:
                weak, weak_src = t, src
            return None
        return (t, src)

    # 1) มีคำนำหน้าจากต้นทางตรง ๆ (se-survey = ช่องบนมือถือ/สแกนบัตร;
    #    ISURVEY = ที่แยกออกจากช่องชื่อตอนอ่าน)
    t = getattr(data, "driver_title", "").strip()
    if t and t not in ("0", "-"):
        got = _take(t, "จากคำนำหน้าที่ต้นทางให้มา")
        if got:
            return got
    # 2) คำนำหน้าติดมากับชื่อผู้ขับขี่ (เช่น 'น.ส.ปฐมาวดี') — ตัดออกก่อนเทียบชื่อ
    driver_full = f"{data.driver_name} {data.driver_surname}".strip()
    dt, d_first, d_last = split_thai_name(driver_full)
    if dt:
        got = _take(dt, "จากชื่อผู้ขับขี่")
        if got:
            return got
    # 3) ผู้ขับขี่เป็นคนเดียวกับผู้เอาประกัน → ยกคำนำหน้าของผู้เอาประกันมา
    driver_clean = f"{d_first} {d_last}".strip()
    title, first, last = split_thai_name(data.insure_name)
    if title and f"{first} {last}".strip() == driver_clean:
        got = _take(title, "จากชื่อผู้เอาประกัน")
        if got:
            return got
    # 4) เพศ (+อายุ) — ชายได้ 'นาย' แน่นอน, หญิงผู้ใหญ่ตกเป็นค่ากลาง 'คุณ'
    guess = title_from_gender_age(data.driver_gender, data.driver_age)
    if guess and guess not in WEAK_TITLES:
        g = resolve_gender(data.driver_gender)
        return guess, "อนุมานจากเพศ" + ("ชาย" if g == "M" else "หญิง")
    if guess:
        return guess, "เพศหญิง แต่แยก นาง/นางสาว ไม่ได้ — ใช้ค่ากลาง"
    return weak or "", weak_src


def _real_options(driver, select_id: str):
    """[(value, text)] ของ option ที่ไม่ใช่ placeholder ('0'/ว่าง) — อ่านสด ไม่ค้าง stale"""
    opts = driver.execute_script(
        "var s=document.getElementById(arguments[0]);"
        "return s?Array.prototype.map.call(s.options,function(o){"
        "return [o.value,(o.text||'').trim()];}):[];", select_id) or []
    return [(v, t) for v, t in opts if v and v != "0"]


def _wait_selected(driver, select_id: str, value: str, timeout: int = 10) -> bool:
    """รอให้ค่าที่ถูกเลือก "จริง" ตรงกับที่สั่ง — dropdown ของ EMCS ยิง postback หลังเลือก
    อ่านทันทีหลังคลิกจะได้ค่าก่อน postback"""
    for _ in range(timeout * 2):
        cur = driver.execute_script(
            "var s=document.getElementById(arguments[0]);return s?s.value:null;", select_id)
        if str(cur) == str(value):
            return True
        time.sleep(0.5)
    return False


def fill_insurer_and_refs(driver, data: ClaimData):
    """เลือกบริษัทประกัน **ตามเจ้าของงานจริง** + เลขเซอร์เวย์/เลขเคลม

    ⛔ เดิมคลิก option[2] ตายตัว = ไอโออิเสมอ ไม่ว่างานเป็นของบริษัทไหน
       ใช้ได้ตราบที่เส้นนี้รับแต่งานไอโออิ · พองานไทยไพบูลย์ (SETP) เข้ามา สำนวนจะไปอยู่
       ใต้ไอโออิ และ EMCS ก็ตรวจรูปแบบเลขเคลมด้วยกติกาผิดบริษัท
       (เจอจริง 15/08/69 เคลม 21BR10AVD-6905-001860 → บันทึกหน้าหลักไม่ผ่าน)

    ⛔ resolve ไม่ได้ = หยุด ห้าม fallback เป็นบริษัทใดบริษัทหนึ่ง — เดาผิดคือยื่นสำนวน
       เข้าบริษัทที่ไม่ใช่เจ้าของงาน ซึ่งลบไม่ได้
    """
    log("EMCS: เลือกบริษัทประกัน + เลขอ้างอิง")
    prefix = str(data.invoice_value or "").split("-")[0].strip().upper()
    code = resolve_insurer_code_by_job_no(data.invoice_value)
    if not code:
        raise RuntimeError(
            f"ไม่รู้ว่าเลขเซอร์เวย์ {data.invoice_value!r} เป็นงานของบริษัทไหน — หยุดก่อน ไม่เดา "
            f"(เติม prefix {prefix!r} ใน autokey/insurer_map.py แล้วรันใหม่)")

    wait_clickable(driver, By.XPATH,
                   f'//*[@id="ddlInsurerNameMajor"]/option[@value="{code}"]', 30).click()
    if not _wait_selected(driver, "ddlInsurerNameMajor", code):
        raise RuntimeError(f"เลือกบริษัทประกันรหัส {code} ไม่ติด — หยุดก่อน กันกรอกเข้าบริษัทผิด")
    log(f"   ✓ บริษัทประกัน: รหัส {code} (จาก prefix {prefix} ของเลขเซอร์เวย์)")

    # ชื่อบริษัทย่อย/สาขา โหลด lazy หลังเลือกบริษัทใหญ่ (postback) — เดิมคลิก option[2]
    # ทันทีโดยไม่รอ ใช้ได้เพราะไอโออิมีตัวเลือกเดียวเสมอ บริษัทอื่นไม่รับประกันว่าเหมือนกัน
    wait_clickable(driver, By.ID, "ddlInsurer_Name", 30)
    sub = None
    for _ in range(24):
        real = _real_options(driver, "ddlInsurer_Name")
        if real:
            sub = next((v for v, t in real if "กรุงเทพ" in t), real[0][0])
            break
        time.sleep(0.5)
    if not sub:
        raise RuntimeError("รายชื่อบริษัทย่อย (ddlInsurer_Name) ไม่โหลด — หยุดก่อน")
    driver.find_element(By.XPATH, f'//*[@id="ddlInsurer_Name"]/option[@value="{sub}"]').click()

    wait_clickable(driver, By.ID, "txtSurv_JobNo")
    set_text(driver, "txtSurv_JobNo", data.invoice_value)
    set_text(driver, "txtRef_Claim_No", data.claim_value)
    _warn_format(driver, "txtRef_Claim_No", data.claim_value, "เลขที่เคลม")


def fill_policy(driver, data: ClaimData):
    log("EMCS: กรอกข้อมูลกรมธรรม์")
    wait_visible(driver, By.ID, "txtAcc_Policy_No")
    # กรมธรรม์ (พ.ร.บ.) — บังคับ **เฉพาะเมื่อติ๊ก chkHas_Prb** เท่านั้น
    #   if (document.getElementById("chkHas_Prb").checked == true) { CheckInputBoxValid('txtPrb_Number'...
    # (ตรวจด้วย tools/emcs_spec.py 2026-08-02 — ก่อนหน้านี้เข้าใจผิดว่าบังคับทุกกรณี)
    # ตรงกับดีไซน์แอปมือถือพอดี: เคสไม่มี พ.ร.บ. ไม่ต้องกรอก → ไม่ต้องยัด '-' ให้เป็นเลขปลอม
    # ยัด '-' เฉพาะตอนติ๊กไว้แต่ไม่มีเลข ไม่งั้นบันทึกไม่ผ่าน (กติกา required-field-empty)
    if _checkbox_checked(driver, "chkHas_Prb"):
        set_text(driver, "txtPrb_Number", _dash(data.prb_number))
    elif str(data.prb_number or "").strip():
        log("   – มีเลข พ.ร.บ. แต่ EMCS ไม่ได้ติ๊ก 'มี พ.ร.บ.' → ข้ามช่องนี้ หัวหน้าติ๊กเองได้")
    set_text(driver, "txtAcc_Policy_No", data.policy_value)
    _warn_format(driver, "txtAcc_Policy_No", data.policy_value, "กรมธรรม์เลขที่")
    set_text(driver, "wuCale_Policy_Start_txtCalendar", to_buddhist_date(data.effective_date))
    set_text(driver, "wuCale_Policy_End_txtCalendar", to_buddhist_date(data.expiry_date))
    set_text(driver, "txtAssured_Name", data.insure_name)
    set_text(driver, "txtPolicy_Type", data.insure_type)
    # แอปเก็บแต่เดิมไม่มีอะไรพาไป (EMCS มีช่องจริงทั้งคู่ ไม่บังคับ)
    set_text(driver, "txtAssured_Email", data.assured_email)
    set_text(driver, "txtDeductible", data.deductible)
    set_text(driver, "txtDri_Order", data.driver_ticket)
    if getattr(data, "car_lost", False):
        try:
            el = driver.find_element(By.ID, "chkLost_Car")
            if not el.is_selected():      # click() สลับสถานะ — เติม draft ซ้ำจะปลดติ๊ก
                el.click()
                log("   ✓ ติ๊ก 'รถหาย'")
        except Exception:
            log("   ⚠️ ติ๊ก 'รถหาย' ไม่ได้ — ติ๊กเองบนหน้าจอ")


def _select_car_type(driver, car_type):
    """เลือก 'ประเภทรถ' (ddlCType) — แต่ห้ามเปลี่ยนทับค่าที่บันทึกไว้แล้ว

    ⛔ ของจริงจาก eclaim3: เปลี่ยนประเภทรถบนเรื่องที่บันทึกแล้ว จะเด้ง confirm
    "การแก้ไขต่อไปนี้ จะทำให้ข้อมูลที่เคยบันทึกไว้แล้ว ถูกลบออกทั้งหมด" — กดตกลง =
    งานที่หัวหน้ากรอกไว้หายหมด. บอทจึงเลือกได้เฉพาะตอนช่องยัง "ว่าง/placeholder"
    เท่านั้น; ถ้ามีค่าอยู่แล้วและไม่ตรง = แจ้งให้คนแก้เอง (เป็นช่องบังคับที่คนตรวจอยู่แล้ว)
    ที่ผ่านมารอดเพราะค่ามักตรงกันพอดี (เลือกค่าเดิม = ไม่ยิง onchange = ไม่มี confirm)"""
    cur = _current_select_text(driver, "ddlCType").strip()
    want = str(car_type or "").strip()
    if cur and not _is_placeholder_option(cur):
        if not want:
            log(f"   - ประเภทรถ: มีค่าอยู่แล้ว ('{cur}') และต้นทางว่าง — ไม่แตะ")
            return
        # เทียบแบบ fuzzy ด้วยเกณฑ์เดียวกับตอนเลือก — ISURVEY กับ EMCS สะกดต่างกันได้
        # ('เก๋งเอเซีย' vs 'เก๋งเอเชีย' ซ/ช) ถ้าเทียบสตริงตรง ๆ จะขึ้น ⛔ เตือนผิด
        # ทั้งที่ค่าบนเรื่องถูกอยู่แล้ว (เจอจริง เคลม 2026013059072)
        _score = fuzz.WRatio(cur, want)
        if cur == want or _score >= CAR_TYPE_SAME_SCORE:
            _note = "" if cur == want else f" (สะกดต่าง: ข้อมูลว่า '{want}')"
            log(f"   ✓ ประเภทรถ: '{cur}' ตรงกับข้อมูลอยู่แล้ว{_note} — ไม่ต้องเปลี่ยน")
            return
        log(f"   ⛔ ประเภทรถบนเรื่องเป็น '{cur}' แต่ข้อมูลว่า '{want}' (ต่างกันจริง "
            f"score {_score:.0f}) — ไม่เปลี่ยนให้ (EMCS จะลบข้อมูลที่บันทึกไว้ทั้งหมด) "
            "แก้เองบนหน้าจอถ้าจำเป็น")
        return
    if not _set_ctype_via_postback(driver, want):
        fuzzy_select(driver, "ddlCType", want, presleep=1,
                     label="ประเภทรถ", required=True)


def _ctype_value(driver, label: str) -> str:
    """หา option value ของประเภทรถจากชื่อไทย (A/E/M/O/T/V/W) — '' ถ้าไม่เจอ"""
    want = str(label or "").strip()
    try:
        for o in Select(driver.find_element(By.ID, "ddlCType")).options:
            if o.text.strip() == want:
                return (o.get_attribute("value") or "").strip()
    except Exception:
        pass
    return ""


def _set_ctype_via_postback(driver, label: str) -> bool:
    """ตั้งประเภทรถแล้วยิง __doPostBack('ddlCType','') ตรง ๆ — **ไม่เรียก checkChangeCType**

    ยืนยันบน draft จริง 2026-07-26 (S68426076666, ทำซ้ำ 2 รอบ): วิธีนี้ทำให้ cascade
    โหลดลิสต์ยี่ห้อครบ 71 ตัว โดย **ไม่มี popup 'ข้อมูลที่เคยบันทึกไว้จะถูกลบทั้งหมด'**
    และตรวจ 11 ช่องแล้วไม่มีข้อมูลไหนหาย (ต่างจาก select_by_visible_text ที่ยิง onchange
    เต็ม รวม checkChangeCType ซึ่งเป็นตัวเด้ง popup แล้วทำให้บอทค้าง)

    ⚠️ ผู้เรียกต้องกันไว้แล้วว่า "ช่องยังว่าง/placeholder" เท่านั้น — เคสเปลี่ยนทับค่าเดิม
    ห้ามใช้ทางนี้ เพราะเท่ากับข้ามคำเตือนของระบบ (ดู _select_car_type)
    คืน True ถ้าตั้งค่าได้จริง"""
    val = _ctype_value(driver, label)
    if not val:
        return False
    try:
        driver.execute_script(
            "var e=document.getElementById('ddlCType');"
            "if(!e)return;e.value=arguments[0];"
            "setTimeout(function(){__doPostBack('ddlCType','');},50);", val)
    except Exception as e:
        log(f"   ⚠️ ยิง postback ประเภทรถไม่ได้ ({type(e).__name__}) — ใช้วิธีเลือกปกติแทน")
        return False
    try:    # รอ postback จบ (ยี่ห้อโหลด = สัญญาณว่า cascade มาแล้ว)
        WebDriverWait(driver, 15).until(
            lambda d: _select_has_options(d, "ddlCMFG")
            or _current_select_text(d, "ddlCType").strip() == label)
    except TimeoutException:
        pass
    got = _current_select_text(driver, "ddlCType").strip()
    if got == label:
        log(f"   ✓ ประเภทรถ: '{label}' (postback ตรง — ไม่ผ่าน checkChangeCType)")
        return True
    log(f"   ⚠️ ตั้งประเภทรถด้วย postback แล้วได้ '{got}' — ลองวิธีเลือกปกติ")
    return False


def _select_car_brand(driver, car_brand, label="ยี่ห้อรถ",
                      type_id="ddlCType", brand_id="ddlCMFG"):
    """เลือก 'ยี่ห้อรถ' (ddlCMFG) ให้ทน race ของ cascade ประเภทรถ→ยี่ห้อ:
    ตัวเลือกยี่ห้อถูกโหลดจาก onchange postback ของ ddlCType ซึ่งบางครั้ง commit ไม่ทัน
    presleep เดิม → ddlCMFG ว่าง (มีแต่ '-- ระบุ --'). แก้แบบเดียวกับที่คนต้องกดประเภทรถ
    ซ้ำเองบนหน้าเว็บ: รอ list โหลด → ถ้ายังว่างให้ยิง onchange ของ ddlCType ซ้ำแล้วรอ
    → ค่อย fuzzy_select. list ไม่ขึ้นจริง = หยุดรอคน (required)

    ยี่ห้อใช้เกณฑ์เข้ม (min_score=90): ลิสต์ยี่ห้อถูกกรองตามประเภทรถ ยี่ห้อที่ไม่มีใน
    ลิสต์นั้นจะไป "เกาะ" ยี่ห้ออื่นแบบเงียบ ๆ ได้ (TRIUMPH→TRUMPCHI 80) — ค่าที่ถูกต้อง
    ได้ ≥90 เสมอ (TOYOTA 100 / 'MG 3'→MG 90) จึงตัดที่ 90 แล้วให้คนเลือกเองถ้าไม่ถึง"""
    car_brand = normalize_brand(car_brand)   # ไทย→อังกฤษ: ตัวเลือก EMCS เป็นอังกฤษล้วน
    time.sleep(2)   # รอ postback ประเภทรถ โหลดตัวเลือกยี่ห้อ
    if not _select_has_options(driver, brand_id):
        # repopulate ยี่ห้อ: ยิง __doPostBack ของ ddlCType ตรง ๆ ด้วยค่าที่เลือกอยู่
        # (เดิม dispatchEvent('change') → วิ่งผ่าน checkChangeCType = เด้ง popup ทำลายข้อมูล
        #  แล้วบอทค้าง; postback ตรงให้ผลเดียวกันแต่ไม่มี popup — ยืนยันบน draft จริง)
        try:
            driver.execute_script(
                "setTimeout(function(){__doPostBack(arguments[0],'');},50);", type_id)
        except Exception:
            pass
        try:
            WebDriverWait(driver, 12).until(
                lambda d: _select_has_options(d, brand_id))
        except TimeoutException:
            pass
    if _select_has_options(driver, brand_id):
        fuzzy_select(driver, brand_id, car_brand, label=label,
                     required=True, timeout=5, min_score=BRAND_MIN_SCORE)
    else:
        wait_for_manual_fill(
            label, "ตัวเลือกยี่ห้อยังไม่โหลด (postback ประเภทรถ ไม่สมบูรณ์)",
            select_id=brand_id, driver=driver)


def fill_car(driver, data: ClaimData):
    log("EMCS: กรอกรายละเอียดรถยนต์")
    wait_visible(driver, By.ID, "txtCar_RegNo")
    set_text(driver, "txtCar_RegNo", _dash(_plate(data.insure_plate)))
    set_text(driver, "txtCModel2", data.insure_model)
    set_text(driver, "txtChassisNo", data.insure_chassis)
    set_text(driver, "txtEngineNo", data.insure_engine)

    # dropdown แต่ละตัวมี postback — ประเภทรถ→ยี่ห้อ เป็น cascade (ผูกกัน) ฝั่ง server:
    # onchange ของ ddlCType โหลด "ตัวเลือกยี่ห้อ" (ddlCMFG) → ต้องเลือกยี่ห้อ "ทันทีหลัง"
    # ประเภทรถ + รอ list โหลดจริง ก่อนแตะจังหวัด (เลียนแบบฝั่งคู่กรณีที่ทำงานถูก emcs.py:260-287);
    # ลำดับเดิม (จังหวัดคั่นกลาง + presleep=1) ทำ postback ยี่ห้อ commit ไม่ทัน → ยี่ห้อว่าง
    # ประเภทรถ/จังหวัดรถ/ยี่ห้อรถ = field บังคับ (required) → ว่าง/เลือกไม่ได้ หยุดรอคน
    _select_car_type(driver, data.prb_car_type)
    _select_car_brand(driver, data.car_brand)   # รอ+guard+ยิง onchange ซ้ำถ้า list ยังว่าง
    fuzzy_select(driver, "ddlCar_Province", data.plate_province, presleep=1,
                 label="จังหวัดรถ", required=True)
    # verify-stuck: เผื่อ postback จังหวัดรีเซ็ตยี่ห้อกลับเป็น placeholder → เลือกยี่ห้อซ้ำ
    # (เงื่อนไข "ช่องยังว่าง" กันอยู่แล้ว — อย่าไปผูกกับผลรอบแรก เพราะรอบแรกที่ล้มจาก
    # race ของ cascade คือเคสที่ต้องใช้รอบสองกู้พอดี เช่น 'HONDA' ก็เคยได้ 0 คะแนน)
    if str(data.car_brand or "").strip() and \
            _current_select_text(driver, "ddlCMFG").strip() in ("", "-- ระบุ --"):
        _select_car_brand(driver, data.car_brand, label="ยี่ห้อรถ (เลือกซ้ำ)")
    fuzzy_select(driver, "ddlCar_Color", data.car_color, presleep=1, label="สีรถ")
    set_text(driver, "txtCar_RegNo_Year", _year_ad(data.car_reg_year))
    # เดิมกรอกให้เฉพาะคู่กรณี (emcs.py:298) ของรถประกันตกหล่น — แอปเก็บมาแล้วต้องพาไป
    set_text(driver, "txtKm_No", data.mileage)
    set_text(driver, "txtModelNo", data.model_no)
    _fill_ev(driver, "", data.ev_type, data.ev_battery_no, data.ev_charger_no,
             data.ev_battery_start)


def _year_ad(year) -> str:
    """ปีจดทะเบียน: แอปเก็บ พ.ศ. ('2567') แต่ EMCS รับเฉพาะ ค.ศ. (dropdown 1900-2026)
    → ลบ 543 ให้; ถ้าเป็น ค.ศ. อยู่แล้วหรืออ่านไม่ออก คืนตามเดิม/ว่าง"""
    s = str(year or "").strip()
    if not s.isdigit() or len(s) != 4:
        return "" if not s else s
    y = int(s)
    return str(y - 543) if y >= 2400 else s


def _report_date(v) -> str:
    """วันที่จาก se-survey → dd/mm/พ.ศ. ที่ช่องปฏิทิน EMCS ใช้
    รับได้ทั้ง '2026-07-01', '2026-07-01T00:00:00.000Z' (API คืน timestamp) และ
    '01/07/2569' ที่เป็น พ.ศ. อยู่แล้ว (iso_to_thai_date เพี้ยนถ้ามีส่วนเวลาติดมา)"""
    s = str(v or "").strip()
    if not s:
        return ""
    if len(s) > 10 and s[4] == "-":
        s = s[:10]
    return iso_to_thai_date(s)


def _fill_ev(driver, prefix, ev_type, batt_no, charger_no, batt_start):
    """รถยนต์ไฟฟ้า — value ของ ddlEvType คือ code ตรง ๆ ('BEV'/'HEV'/…) ซึ่งตรงกับที่
    se-survey เก็บ จึง select_by_value ไม่ต้อง fuzzy (ป้ายเต็มของ EMCS มีวงเล็บ/เว้นวรรค
    ไม่ตรงกับป้ายในแอป). ไม่ใช่ EV = ไม่แตะอะไรเลย"""
    code = str(ev_type or "").strip().upper()
    if not code:
        return
    sel_id = prefix + ("ddlEv_Type" if prefix else "ddlEvType")   # คู่กรณีใช้คนละชื่อ
    try:
        Select(driver.find_element(By.ID, sel_id)).select_by_value(code)
        log(f"   ✓ ประเภทรถไฟฟ้า (EV) = {code}")
    except Exception:
        log(f"   ⚠️ เลือกประเภท EV '{code}' ไม่ได้ — เลือกเองบนหน้าจอ")
        return
    set_text(driver, prefix + "txtBatt_Number", batt_no)
    set_text(driver, prefix + "txtWallcharge_number", charger_no)
    if str(batt_start or "").strip():
        set_text(driver, prefix + "wuCale_batt_effdate_txtCalendar",
                 _report_date(batt_start))


def fill_driver(driver, data: ClaimData):
    log("EMCS: กรอกข้อมูลผู้ขับขี่")
    wait_visible(driver, By.ID, "txtDri_Name01")

    # คำนำหน้าผู้ขับขี่ (บังคับ) — ไล่จากคำนำหน้าจริงลงมาถึงอนุมานจากเพศ+อายุ
    title, source = _derive_insured_title(data)

    # เพศผู้ขับขี่ (บังคับ) — rdoGender_0=ชาย(M), rdoGender_1=หญิง(F)
    # ISURVEY มี drv_gender เสมอ (M/F); ว่างเมื่อไหร่ค่อยอนุมานจากคำนำหน้า
    # (title→เพศ ชัดเจน 100% ยกเว้น 'คุณ' ที่เป็นคำกลาง — TITLE_GENDER จึงไม่มี 'คุณ')
    g = resolve_gender(data.driver_gender)
    src = "จากข้อมูล ISURVEY"
    if not g:
        g = TITLE_GENDER.get(title, "")
        src = f"อนุมานจากคำนำหน้า '{title}'"
    if g in ("M", "W"):
        idx = "0" if g == "M" else "1"
        driver.find_element(By.ID, f"rdoGender_{idx}").click()
        log(f"   ✓ เพศผู้ขับขี่ = {'ชาย' if g == 'M' else 'หญิง'} ({src})")
    else:
        log("   ⚠️ ไม่ทราบเพศผู้ขับขี่ (ISURVEY ว่าง + ชื่อไม่มีคำนำหน้า)")
        wait_for_manual_fill("เพศผู้ขับขี่ (ชาย/หญิง)",
                             "ISURVEY ไม่มีเพศ + แยกจากคำนำหน้าไม่ได้ — ต้องเลือกเอง",
                             focus_ids=["rdoGender_0", "rdoGender_1"], driver=driver)

    # คำนำหน้าผู้ขับขี่ (บังคับ) — 'คุณ' คือค่ากลางตอนรู้แค่ว่าเป็นผู้หญิง
    # (แยก นาง/นางสาว ไม่ได้) ปล่อยผ่านเป็น draft ให้หัวหน้าแก้ตอนตรวจ
    if title:
        fuzzy_select(driver, "ddlDri_Title_ID", EMCS_TITLE.get(title, title),
                     min_score=TITLE_MIN_SCORE,
                     label=f"คำนำหน้าผู้ขับขี่ ({source})")
        if title == "คุณ":
            log("   ℹ️ ใช้ 'คุณ' เป็นค่ากลาง — ถ้ารู้ว่าเป็น นาง/นางสาว ให้แก้ตอนตรวจ")
    else:
        log("   ⚠️ หาคำนำหน้าผู้ขับขี่ไม่ได้ (ไม่มีคำนำหน้าในชื่อ + ไม่รู้เพศ)")
        wait_for_manual_fill(
            "คำนำหน้าผู้ขับขี่",
            "ต้นทางไม่มีคำนำหน้า และไม่มีเพศให้อนุมาน — เลือกเอง",
            select_id="ddlDri_Title_ID", driver=driver)

    # ตัดคำนำหน้าที่ติดมากับชื่อ (เช่น 'น.ส.ปฐมาวดี'→'ปฐมาวดี') — ไม่งั้นชื่อจะมีคำนำหน้าซ้ำ
    _t, dri_first, dri_last = split_thai_name(
        f"{data.driver_name} {data.driver_surname}".strip())
    set_text(driver, "txtDri_Name01", _dash(dri_first or data.driver_name))
    set_text(driver, "txtDri_LastName01", _dash(dri_last or data.driver_surname))
    set_text(driver, "txtDri_Age", data.driver_age)
    set_text(driver, "txtDri_Address", data.driver_address)
    set_text(driver, "txtDri_TelNo", _dash(data.driver_phone))
    set_text(driver, "txtDri_CardID", _dash(data.driver_idcard))
    set_text(driver, "txtDri_DrvID", _dash(data.driver_license_no))
    set_text(driver, "txtDri_DrvPlace", data.driver_license_place)
    set_text(driver, "txtCost_Damage", data.damage_estimate)
    set_text(driver, "wuCale_Dri_BirthDay_txtCalendar", to_buddhist_date(data.driver_birthdate))
    set_text(driver, "wuCale_Dri_DrvDate_Start_txtCalendar", to_buddhist_date(data.license_issue_date))
    set_text(driver, "wuCale_Dri_DrvDate_End_txtCalendar", to_buddhist_date(data.license_expiry_date))

    # dropdown มี postback — ต้องเว้นจังหวะกันค่าโดน postback ก่อนหน้าทับ
    fuzzy_select(driver, "ddlDri_Relation_ID", data.driver_relation,
                 presleep=1, label="ความสัมพันธ์")
    fuzzy_select(driver, "ddlDri_ProvinceID", data.driver_province,
                 presleep=1, label="จังหวัดผู้ขับขี่")
    fuzzy_select(driver, "ddlDri_DistrictID", data.driver_amphur,
                 presleep=1, label="อำเภอผู้ขับขี่")
    fuzzy_select(driver, "ddlEmcs_License_Type", data.driver_license_type,
                 presleep=1, label="ประเภทใบขับขี่")


def _dt_time(v) -> str:
    """แยกส่วนเวลาออกจากค่ารูปแบบ 'dd/mm/yyyy|HH:MM' ที่ se-survey ใช้เก็บวัน-เวลาคู่กัน
    (to_buddhist_date ตัด '|time' ทิ้งให้อยู่แล้ว — ตัวนี้เอาอีกครึ่งที่เหลือ)"""
    s = str(v or "")
    return s.split("|", 1)[1].strip() if "|" in s else ""


def _fill_police_and_alcohol(driver, data: ClaimData):
    """บล็อกตำรวจ + ผลตรวจแอลกอฮอล์ — แอปเก็บครบแต่เดิมบอทไม่เคยกรอกเลย
    (grep 'Police'/'Alc' ในโค้ดเก่า = 0 hit) จึงพึ่ง XML importer ทางเดียว →
    หายทั้งบล็อกในโหมดเติม draft (--sesurvey-fill-existing) ที่ข้ามขั้น import
    งานจริงของพนักงานกรอกบล็อกนี้จริง (ไอโออิ: ชื่อ/สถานี/วันที่/ความเห็น ครบ)
    ทุกช่องไม่บังคับ → ว่างก็ข้าม (set_text ข้ามค่าว่างอยู่แล้ว) ไม่ใส่ '-'"""
    set_text(driver, "txtPolice_Name", data.police_name)
    set_text(driver, "txtPolice_Station", data.police_station)
    set_text(driver, "txtPolice_Comment", data.police_comment)
    set_text(driver, "txtBook_Number", data.police_book_no)
    if str(data.police_date or "").strip():
        set_text(driver, "wuCale_Police_Date_txtCalendar",
                 to_buddhist_date(data.police_date))
        ph, pm = split_hhmm(_dt_time(data.police_date))
        set_text(driver, "txtPolice_Date_Hour", ph)
        set_text(driver, "txtPolice_Date_Minute", pm)

    # ผลตรวจแอลกอฮอล์: EMCS แยกเป็น radio "มี/ไม่มีการตรวจ" + ช่องผลตรวจ
    # แอปมือถือใช้ dropdown ป้ายตรง EMCS แล้ว (ไม่ใช่กล่องข้อความเดียวอย่างที่เคยเขียนไว้ตรงนี้)
    # → เทียบป้ายตรง ๆ ก่อน ส่วนการตีความจากข้อความเป็น fallback ของเคสเก่า/ISURVEY
    alc = " ".join(str(data.alcohol_test or "").split())
    res = " ".join(str(data.alcohol_result or "").split())
    if alc or res:
        # แอปส่งป้ายเต็ม 2 ค่า ('ไม่มีการตรวจแอลกอฮอล์' / 'มีการตรวจแอลกอฮอล์') → เทียบตรง
        # ส่วนข้อความอิสระของเคสเก่า/ISURVEY ยังใช้การตีความคำเดิมเป็น fallback
        no_test = (alc == "ไม่มีการตรวจแอลกอฮอล์") or (
            alc != "มีการตรวจแอลกอฮอล์"
            and any(k in alc for k in ("ไม่ได้ตรวจ", "ไม่ตรวจ", "ไม่มีการตรวจ", "ไม่มี")))
        # ⚠️ ลำดับ radio: rdoAlc_Chk_0 = "ไม่มีการตรวจ" / rdoAlc_Chk_1 = "มีการตรวจ"
        # (ยืนยันจาก label ในหน้าจริง — เคยเขียนกลับด้านมาแล้ว ห้ามเดาจากเลข index)
        try:
            driver.find_element(By.ID, f"rdoAlc_Chk_{'0' if no_test else '1'}").click()
            log(f"   ✓ ผลตรวจแอลกอฮอล์: {'ไม่มีการตรวจ' if no_test else 'มีการตรวจ'}")
        except Exception:
            log("   ⚠️ เลือก radio ผลตรวจแอลกอฮอล์ไม่ได้ — กรอกเอง")
        if not no_test:
            set_text(driver, "txtAlc_Result", res or alc)



# ────────────────────────────────────────────────────────────────────
# เวลาสำรวจภัย: ISURVEY ไม่มีข้อมูลได้จริง → EMCS ปัดตกแบบ **เงียบสนิท**
#
# 4 ช่องนี้ EMCS บังคับ และเป็นชนิดวันที่/ตัวเลข จึงใส่ "-" แทนไม่ได้
# (ต่างจากช่องข้อความที่ _dash เติมให้ได้) — มีทางเดียวคือคนกรอกเอง
#
# เจอจริง 01/09/69 เคลม 2026013168056: ISURVEY ว่างทั้ง 4 ช่อง บอทรู้ตั้งแต่
# ตอนอ่านข้อมูล (validate() ขึ้น ❌ ครบ 4) แต่เดินหน้าต่อ → กรอกทั้งหน้า →
# กดบันทึก → validForm() ปัดตกโดยไม่มี alert → ไล่หาสาเหตุอยู่ 3 รอบ →
# สุดท้ายคนไปกรอกเองบน EMCS ทั้งเรื่อง
#
# ⛔ ห้ามเดาค่าให้ (เช่นใส่เวลาปัจจุบัน) — EMCS ตรวจลำดับเวลากับวันเกิดเหตุ
#    และเลขนี้เป็นหลักฐานว่าผู้สำรวจไปถึงเมื่อไหร่ เดาผิด = เอกสารผิด
SURVEY_TIME_FIELDS = (
    ("arrive_date", "wuCale_Acc_Reach_txtCalendar", "วันที่สำรวจภัย (ถึงที่เกิดเหตุ)"),
    ("arrive_time", "txtAcc_Reach_Hour", "เวลาถึงที่เกิดเหตุ (ชั่วโมง:นาที)"),
    ("finish_date", "wuCale_Acc_Finish_txtCalendar", "วันที่สำรวจภัยเสร็จ"),
    ("finish_time", "txtAcc_Finish_Hour", "เวลาสำรวจภัยเสร็จ (ชั่วโมง:นาที)"),
)


def missing_survey_times(data) -> list:
    """ชื่อช่องเวลาสำรวจที่ต้นทางไม่มีข้อมูล — [] = ครบ (ใช้ตรวจก่อนเปิด EMCS ได้ด้วย)"""
    return [label for field, _id, label in SURVEY_TIME_FIELDS
            if not str(getattr(data, field, "") or "").strip()]


def _pause_for_survey_times(driver, data) -> bool:
    """ช่องเวลาสำรวจว่าง → ตีกรอบแดงช่องนั้นบน EMCS แล้วหยุดรอให้คนกรอก

    คืน True เมื่อมีการหยุดถาม (ไม่ว่าคนจะตอบหรือไม่) · False = ครบอยู่แล้ว
    ไม่มีคนตอบ (รันแบบไม่มีคนเฝ้า) = ไปต่อเหมือนเดิม — จะไปโดนปัดตกตอนบันทึก
    ซึ่งยังดีกว่าเงียบ เพราะ log มีบรรทัดบอกชัดแล้วว่าขาดอะไร
    """
    missing = missing_survey_times(data)
    if not missing:
        return False
    ids = [fid for field, fid, _label in SURVEY_TIME_FIELDS
           if not str(getattr(data, field, "") or "").strip()]
    wait_for_manual_fill(
        "เวลาสำรวจภัย: " + ", ".join(missing),
        reason=("ISURVEY ไม่มีข้อมูลช่องเหล่านี้ — EMCS บังคับ และเป็นช่องวันที่/เวลา "
                'จึงใส่ "-" แทนไม่ได้ ถ้าไม่กรอก EMCS จะปัดตกตอนบันทึกโดยไม่ขึ้นข้อความ'),
        focus_ids=ids,
        focus_labels=missing,
        driver=driver,
    )
    return True

def fill_accident(driver, data: ClaimData, loss_type: str = "เคลมแห้ง"):
    log("EMCS: กรอกรายละเอียดอุบัติเหตุ")
    wait_visible(driver, By.ID, "wuCale_Acc_Date_txtCalendar")

    # วัน-เวลาเกิดเหตุ
    set_text(driver, "wuCale_Acc_Date_txtCalendar", to_buddhist_date(data.acc_date))
    h, m = split_hhmm(data.acc_time)
    set_text(driver, "txtAcc_Date_Hour", h)
    set_text(driver, "txtAcc_Date_Minute", m)

    set_text(driver, "txtAcc_Place", _dash(data.acc_place))
    set_text(driver, "txtAcc_Detail", _dash(data.acc_detail))
    _fill_police_and_alcohol(driver, data)
    # ผลการดำเนินงาน + ความเห็นผู้ตรวจสอบ (se-survey มีข้อความ; EMCS มาร์ค 'not used' แต่ช่องแก้ได้)
    # หน้า 1: 2 ช่องนี้เป็น input บรรทัดเดียว (EMCS มาร์ค 'not used') → ยุบบรรทัดก่อนพิมพ์
    # ต่างจากหน้าค่าใช้จ่ายที่เป็น textarea และคงบรรทัดไว้
    set_text(driver, "txtAcc_result", " ".join(str(data.accident_summary or "").split()))
    set_text(driver, "txtAcc_Comment", " ".join(str(data.review_comment or "").split()))
    set_text(driver, "txtAcc_Surv", data.surveyor_name)
    set_text(driver, "txtAcc_Tel", data.surveyor_phone)   # ช่องติดกับผู้สำรวจภัย เดิมว่างทุกเคส

    # EMCS แยก 2 จังหวะ: "ลูกค้าแจ้ง บ.ประกัน" (Acc_Call) → "บ.ประกันแจ้งพนักงานสำรวจ"
    # (Ins_Calling_Surv) — ใช้วัดเวลาตอบสนอง จึงห้ามยัดค่าเดียวกันทั้งคู่
    # se-survey เก็บแยก (acc_customer_report_date / acc_insurance_notify_date) และ XML ส่งแยกถูกแล้ว
    # ISURVEY ไม่มีค่าแรก → ว่าง แล้ว fallback มาใช้ noti_* เหมือนพฤติกรรมเดิม
    noti_date = to_buddhist_date(data.noti_date)
    nh, nm = split_hhmm(data.noti_time)
    call_date = to_buddhist_date(data.call_date) or noti_date
    ch, cm = split_hhmm(data.call_time)
    if not (ch or cm):
        ch, cm = nh, nm
    set_text(driver, "wuCale_Acc_Call_Date_txtCalendar", call_date)
    set_text(driver, "txtAcc_Call_Date_Hour", ch)
    set_text(driver, "txtAcc_Call_Date_Minute", cm)
    set_text(driver, "wuCale_Ins_Calling_Surv_Date_txtCalendar", noti_date)
    set_text(driver, "txtIns_Calling_Surv_Date_Hour", nh)
    set_text(driver, "txtIns_Calling_Surv_Date_Minute", nm)

    # วัน-เวลาถึงที่เกิดเหตุ
    set_text(driver, "wuCale_Acc_Reach_txtCalendar", to_buddhist_date(data.arrive_date))
    ah, am = split_hhmm(data.arrive_time)
    set_text(driver, "txtAcc_Reach_Hour", ah)
    set_text(driver, "txtAcc_Reach_Minute", am)

    # วัน-เวลาเสร็จงาน
    set_text(driver, "wuCale_Acc_Finish_txtCalendar", to_buddhist_date(data.finish_date))
    fh, fm = split_hhmm(data.finish_time)
    set_text(driver, "txtAcc_Finish_Hour", fh)
    set_text(driver, "txtAcc_Finish_Minute", fm)

    # ── เวลาสำรวจว่าง = หยุดถามตรงนี้เลย ไม่รอไปตายตอนกดบันทึก ──
    _pause_for_survey_times(driver, data)

    # ลักษณะการเกิดเหตุ + จังหวัด/อำเภอเกิดเหตุ (ทุกตัวมี postback —
    # เว้นจังหวะกัน select ถัดไปทับค่าเดิมระหว่าง postback ยังไม่จบ)
    # ลักษณะการเกิดเหตุ/จังหวัด/อำเภอเกิดเหตุ = field บังคับ → ว่าง/เลือกไม่ได้ หยุดรอคน
    fuzzy_select(driver, "ddlClm_Cause", data.acc_type_desc,
                 presleep=1, label="ลักษณะการเกิดเหตุ", required=True)
    fuzzy_select(driver, "ddlAcc_ProvinceID", data.acc_province,
                 presleep=1, label="จังหวัดเกิดเหตุ", required=True)
    fuzzy_select(driver, "ddlAcc_DistrictID", data.acc_amphur,
                 presleep=1, label="อำเภอเกิดเหตุ", required=True)

    # ลักษณะความเสียหาย (ddlLoss_ID) — ISURVEY ไม่มีข้อมูลนี้ (มีแต่ลักษณะการเกิดเหตุ)
    # เคลมแห้ง → loss_type='เคลมแห้ง' เลือกอัตโนมัติ / เคลมสด → loss_type='' →
    # required=True หยุดรอให้ผู้ใช้เลือกเองบนหน้า EMCS (รูปแบบเดียวกับ field บังคับอื่น)
    fuzzy_select(driver, "ddlLoss_ID", loss_type, presleep=1,
                 label="ลักษณะความเสียหาย", required=True)


def fill_verdict(driver, data: ClaimData):
    """เลือกผลคดี (radio) จากผลคดีของ ISURVEY — เทียบตรงตัวเท่านั้น ห้าม fuzzy

    ⚠️ ห้ามเอา fuzzy กลับมาที่ช่องนี้เด็ดขาด ติ๊กผิด = สลับฝ่ายผิดทั้งสำนวน
    ของจริงที่เจอ 2026-08-06 (user ทัก): ลิสต์สองระบบไม่ตรงกัน ISURVEY มี 8 ตัว
    EMCS มี 7 ตัว และ fuzzy เดาผิดแบบ "มั่นใจ" จนตัวกันคะแนนเท่าไม่ทำงาน —
      'รถประกันเป็นฝ่ายถูก' → ติ๊ก 'รถประกันเป็นฝ่ายถูกและผิด' (score 86) กลับข้าง
      'ไม่มีคู่กรณี'        → ติ๊ก 'รถคู่กรณีเป็นฝ่ายผิด'      (score 64) ทั้งที่ไม่มีคู่กรณี
    ค่าที่ไม่มีในตาราง = หยุดถามคน ไม่เดา (ตาราง CAUSE_RADIO คือข้อตกลงเดียว)
    """
    log("EMCS: เลือกผลคดี")
    wait_visible(driver, By.ID, "rdoAcc_Cause00")

    if not data.acc_result.strip():
        log("   ⚠️ ไม่มีข้อมูลผลคดีจาก ISURVEY — ข้าม (เลือกเองบนหน้าเว็บ)")
        return

    _res = " ".join(str(data.acc_result).split())
    rid = CAUSE_RADIO.get(_res)
    _RADIOS = sorted(set(CAUSE_RADIO.values()))
    if rid is None:
        # เช่น 'ไม่มีคู่กรณี' หรือค่าที่ไม่รู้จัก — EMCS ไม่มีช่องที่ตรงกัน
        log(f"   ⚠️ ผลคดี '{_res}' ไม่มีคู่ที่ตรงกันใน EMCS — หยุดรอให้หัวหน้าเลือกเอง")
        wait_for_manual_fill("ผลคดี (ฝ่ายประมาท)",
                             f"ผลคดีของ ISURVEY คือ '{_res}' "
                             "ซึ่งไม่มีตัวเลือกตรงกันบน EMCS — เลือกช่องที่ตรงสำนวนเอง",
                             focus_ids=_RADIOS, driver=driver)
    elif rid in NO_AUTO_RADIO:
        # user 2026-08-06: 'รถประกันเป็นฝ่ายถูกและผิด' บอทห้ามเลือกเอง
        log(f"   ⏸️ ผลคดี '{_res}' — บอทไม่เลือกให้ หยุดรอหัวหน้าตัดสิน")
        wait_for_manual_fill("ผลคดี (ฝ่ายประมาท)",
                             f"ผลคดี '{_res}' ต้องให้หัวหน้าตัดสินเอง "
                             "บอทไม่เลือกช่องนี้ให้ — เลือกบนหน้า EMCS แล้วกดดำเนินการต่อ",
                             focus_ids=_RADIOS, driver=driver)
    else:
        log(f"   ✓ ผลคดี: '{_res}'")
        driver.find_element(By.ID, rid).click()
    # "การเรียกร้องค่าเสียหายจากคู่กรณี" (chkOpo_Result + ยอดเงิน 2 ช่อง) ไม่ได้ผูกกับผลคดี
    # ยืนยันจากงานจริงที่พนักงานกรอก (เคลมไอโออิ 2026013058298): ผลคดี = rdoAcc_Cause03
    # 'รอสรุปผลคดี' แต่ยังติ๊ก chkOpo_Result_0 ไว้ → เดิมบอทกรอกเฉพาะตอน rdoAcc_Cause01
    # ทำให้เซอร์เวย์ติ๊ก+พิมพ์ยอดเงินไปฟรีทุกเคสที่ผลคดีเป็นอย่างอื่น
    _opp = (rid == OPPONENT_FAULT_RADIO)
    missing = _fill_opponent_fault(driver, data, is_opponent_fault=_opp)
    # ผลคดี = คู่กรณีผิด เป็นช่องเดียวที่ EMCS บังคับ 'คู่กรณีคันที่' + ติ๊กการเรียกร้อง
    # และเป็นข้อมูลที่บอทติ๊กแทนเซอร์เวย์ไม่ได้ → หยุดรอตรงนี้เลย (user 2026-08-06)
    if _opp and missing:
        log("   ⏸️ รอหัวหน้าเลือก 'การเรียกร้องค่าเสียหายจากคู่กรณี'"
            + (" + กรอก 'คู่กรณีคันที่'" if "คู่กรณีคันที่" in " ".join(missing) else ""))
        wait_for_manual_fill(
            " + ".join(missing),
            "ผลคดี = 'รถคู่กรณีเป็นฝ่ายผิด' → EMCS บังคับให้ระบุคู่กรณีคันที่ และติ๊ก "
            "'การเรียกร้องค่าเสียหายจากคู่กรณี' อย่างน้อย 1 ข้อ "
            "(คัดประจำวัน / รับหลักฐานจากคู่กรณี / บันทึกยอมรับผิด / บัตรติดต่อ / รับเงินจำนวน)",
            focus_ids=(["txtAcc_Cause_No"] if "คู่กรณีคันที่" in " ".join(missing) else [])
                      + [f"chkOpo_Result_{i}" for i in range(5)],
            driver=driver)
    _fill_followup(driver, data)


# การติดตามงาน (rdoFlu_Type) — value ตรงกับป้ายที่ se-survey เก็บ 1:1
FLU_TYPE_RADIO = {
    "ไม่มีการนัดหมาย": "rdoFlu_Type_0",
    "รอการนัดหมาย": "rdoFlu_Type_1",
    "มีการนัดหมาย": "rdoFlu_Type_2",
}


def _fill_followup(driver, data: ClaimData):
    """บล็อกติดตามงาน/นัดหมาย — se-survey บังคับให้ผู้สำรวจกรอก แต่เดิมไม่มีอะไรพาเข้า
    EMCS เลย (backend ก็ hardcode FLU_* ว่างใน XML) → ข้อมูลหายทั้งบล็อกทุกเคส"""
    t = str(data.followup_type or "").strip()
    rid = FLU_TYPE_RADIO.get(t)
    if not rid:
        if t:
            log(f"   ⚠️ ไม่รู้จักสถานะติดตามงาน '{t}' — เลือกเองบนหน้าจอ")
        return
    try:
        driver.find_element(By.ID, rid).click()
        log(f"   ✓ การติดตามงาน: {t}")
    except Exception:
        log(f"   ⚠️ เลือกการติดตามงาน '{t}' ไม่ได้ — เลือกเองบนหน้าจอ")
        return
    # "ครั้งที่นัดหมาย" บนหน้าจอเป็น dropdown (ddlFlu_No) ส่วน txtFlu_No เป็นช่องซ่อน
    # → เลือก dropdown ก่อน ถ้าไม่มีค่อยตกไปช่องข้อความเดิม
    _cnt = str(data.followup_count or "").strip()
    if _cnt:
        try:
            Select(driver.find_element(By.ID, "ddlFlu_No")).select_by_value(_cnt)
        except Exception:
            set_text(driver, "txtFlu_No", _cnt)
    set_text(driver, "txtFlu_Detail", data.followup_detail)
    if str(data.followup_date or "").strip():
        set_text(driver, "wuCale_Flu_Date_txtCalendar",
                 _report_date(data.followup_date))
        # EMCS มีช่องชั่วโมง/นาทีคู่กับวันที่ (ปลดล็อกหลังเลือก 'มีการนัดหมาย' แล้วเท่านั้น)
        fh, fm = split_hhmm(_dt_time(data.followup_date))
        set_text(driver, "txtFlu_Date_Hour", fh)
        set_text(driver, "txtFlu_Date_Minute", fm)


# การเรียกร้องค่าเสียหายจากคู่กรณี (chkOpo_Result_0..4) — ติ๊กด้วย index จึงไม่ต้องแคร์
# ว่าป้ายฝั่งแอปกับ EMCS สะกดต่างกัน (รับได้ทั้งคำเก่าของแอปและป้าย EMCS)
OPO_RESULT_IDX = {
    "คัดประจำวัน": 0,
    "รับหลักฐานจากคู่กรณีผิด": 1, "รับหลักฐานจากคู่กรณี": 1,
    "บันทึกยอมรับผิด": 2,
    "บัตรติดต่อ": 3,
    "รับเงิน": 4, "รับเงินจำนวน": 4,
}


def _fill_opponent_fault(driver, data: ClaimData, is_opponent_fault: bool = False):
    """ผลคดี = 'รถคู่กรณีเป็นฝ่ายผิด' → EMCS บังคับ (vlidSurvey) 2 อย่างพร้อมกัน:
    'คู่กรณีคันที่' (txtAcc_Cause_No) + ติ๊ก 'การเรียกร้องค่าเสียหายจากคู่กรณี'
    อย่างน้อย 1 ใน 5 (chkOpo_Result_0..4) — ไม่ครบ = กดบันทึกไม่ผ่าน คนต้องมาเติมเอง

    is_opponent_fault=False (ผลคดีอื่น) → ไม่แตะ 'คู่กรณีคันที่' เลย (user 2026-08-06:
    ช่องนั้นเป็นของผลคดีนี้ช่องเดียว) แต่ยังติ๊กการเรียกร้องตามข้อมูลที่มีได้

    คืน list ชื่อช่องที่ EMCS บังคับแต่ยังว่างอยู่ (ผู้เรียกเอาไปหยุดรอคนกรอก)

    ⚠️ ต้อง .click() จริง (ห้าม set .checked ผ่าน JS) เพราะ onclick ของ EMCS เป็นตัว
    ปลดล็อกช่อง txtOpo_Pay/txtOpo_Recovery_Amount ถ้าไม่ยิง set_text จะไม่เข้า"""
    missing = []
    # 1) คู่กรณีคันที่ — เฉพาะผลคดี 'รถคู่กรณีเป็นฝ่ายผิด' เท่านั้น
    if is_opponent_fault:
        # ใช้ค่าจากรายงานถ้ามี; ไม่มีแต่มีคู่กรณีคันเดียว = คันที่ 1 แน่นอน
        no = str(getattr(data, "acc_fault_opponent_no", "") or "").strip()
        if not no and len(data.third_parties or []) == 1:
            no = "1"
        if no:
            set_text(driver, "txtAcc_Cause_No", no)
            log(f"   ✓ คู่กรณีคันที่ = {no}")
        else:
            log(f"   ⚠️ ไม่รู้ว่าคู่กรณีคันไหนผิด (มี {len(data.third_parties or [])} คัน) — "
                "กรอก 'คู่กรณีคันที่' เองบนหน้าจอ (EMCS บังคับ)")
            missing.append("คู่กรณีคันที่")

    # 2) การเรียกร้องค่าเสียหายจากคู่กรณี
    picked = [s.strip() for s in str(data.opo_results or "").split(",") if s.strip()]
    if not picked:
        log("   ⚠️ ไม่มีข้อมูล 'การเรียกร้องค่าเสียหายจากคู่กรณี' — ติ๊กเองบนหน้าจอ "
            "(EMCS บังคับอย่างน้อย 1 ข้อ; บอทไม่ติ๊กมั่วแทนเซอร์เวย์)")
        missing.append("การเรียกร้องค่าเสียหายจากคู่กรณี")
        return missing
    ticked = 0
    for t in picked:
        idx = OPO_RESULT_IDX.get(t)
        if idx is None:
            log(f"   ⚠️ ไม่รู้จักตัวเลือก '{t}' — ข้าม (ติ๊กเองถ้าจำเป็น)")
            continue
        if idx == 4 and not _opo_amounts_ok(data):
            continue
        try:
            cb = driver.find_element(By.ID, f"chkOpo_Result_{idx}")
            if not cb.is_selected():
                cb.click()
            log(f"   ☑ {t}")
            ticked += 1
        except Exception as e:
            log(f"   ⚠️ ติ๊ก '{t}' ไม่ได้ ({type(e).__name__}) — ติ๊กเองบนหน้าจอ")
            continue
        if idx == 4:      # ติ๊ก 'รับเงินจำนวน' แล้ว EMCS บังคับ 2 ช่องเงินนี้
            set_text(driver, "txtOpo_Pay", data.opo_pay)
            set_text(driver, "txtOpo_Recovery_Amount", data.opo_recovery)
            log(f"   ✓ รับเงิน {data.opo_pay} จากเรียกร้องทั้งหมด {data.opo_recovery}")
    # มีข้อมูลมาแต่ติ๊กไม่เข้าสักข้อ = ยังไม่ผ่านด่าน EMCS อยู่ดี
    if not ticked:
        missing.append("การเรียกร้องค่าเสียหายจากคู่กรณี")
    return missing


def _opo_amounts_ok(data: ClaimData) -> bool:
    """ติ๊ก 'รับเงินจำนวน' ได้ต่อเมื่อมีเงินครบทั้ง 2 ช่องและ รับ ≤ เรียกร้องทั้งหมด
    (EMCS validate ข้อนี้ — ติ๊กแล้วเงินไม่ผ่าน = บันทึก draft ไม่ได้เลย)"""
    pay, total = str(data.opo_pay or "").strip(), str(data.opo_recovery or "").strip()
    if not pay or not total:
        log("   ⚠️ ข้าม 'รับเงินจำนวน' — ไม่มีตัวเลขรับเงิน/เรียกร้องทั้งหมด "
            "(ติ๊กแล้ว EMCS จะบล็อกการบันทึก)")
        return False
    try:
        if float(pay) > float(total):
            log(f"   ⚠️ ข้าม 'รับเงินจำนวน' — รับเงิน {pay} มากกว่าเรียกร้องทั้งหมด {total} "
                "(EMCS ไม่ยอม) ตรวจตัวเลขแล้วติ๊กเอง")
            return False
    except ValueError:
        log(f"   ⚠️ ข้าม 'รับเงินจำนวน' — ตัวเลขอ่านไม่ออก ({pay!r}/{total!r})")
        return False
    return True


def _refill_missing_fields(driver, data: ClaimData, alert_text: str) -> bool:
    """ค่า dropdown อาจหลุดจาก postback race — อ่านชื่อ field จากข้อความ
    validation แล้วกรอกซ้ำเฉพาะตัวที่ระบบฟ้อง คืน True เมื่อซ่อมได้บ้าง"""
    fixers = {
        "ลักษณะการเกิดเหตุ": lambda: fuzzy_select(
            driver, "ddlClm_Cause", data.acc_type_desc,
            presleep=1, label="ลักษณะการเกิดเหตุ (ซ่อม)"),
        "จังหวัด ที่เกิดเหตุ": lambda: fuzzy_select(
            driver, "ddlAcc_ProvinceID", data.acc_province,
            presleep=1, label="จังหวัดเกิดเหตุ (ซ่อม)"),
        "เขต/อำเภอ ที่เกิดเหตุ": lambda: fuzzy_select(
            driver, "ddlAcc_DistrictID", data.acc_amphur,
            presleep=1, label="อำเภอเกิดเหตุ (ซ่อม)"),
        # ผ่าน _select_car_type เสมอ — ห้ามเปลี่ยนทับค่าที่บันทึกแล้ว (EMCS จะลบข้อมูลทิ้ง)
        "ประเภทรถ": lambda: _select_car_type(driver, data.prb_car_type),
        "คำนำหน้าผู้ขับขี่": lambda: fuzzy_select(
            driver, "ddlDri_Title_ID",
            EMCS_TITLE.get(_derive_insured_title(data)[0], _derive_insured_title(data)[0]),
            min_score=TITLE_MIN_SCORE,
            presleep=1, label="คำนำหน้า (ซ่อม)"),
        # คู่กรณีคันที่ + การเรียกร้องค่าเสียหาย — EMCS ฟ้องคู่กันเมื่อผลคดี = คู่กรณีผิด
        "คู่กรณีคันที่": lambda: _fill_opponent_fault(driver, data),
        "การเรียกร้องค่าเสียหายจากคู่กรณี": lambda: _fill_opponent_fault(driver, data),
    }
    fixed = False
    for keyword, fixer in fixers.items():
        if keyword in alert_text:
            try:
                fixer()
                fixed = True
            except Exception as e:
                log(f"   ⚠️ ซ่อม '{keyword}' ไม่สำเร็จ: {type(e).__name__}")
    return fixed


def _missing_field_list(alert_text: str) -> list:
    """ชื่อช่องที่ระบบฟ้อง (บรรทัดแบบ '1. สถานที่เกิดเหตุ') — ใช้ทั้งขึ้น log
    และส่งให้ browser ไปตีกรอบแดงช่องนั้นบนหน้า EMCS"""
    if not alert_text:
        return []
    return [s.strip() for s in re.findall(r"\d+\.\s*(.+)", alert_text) if s.strip()]


# ชื่อช่องในเครื่องหมายคำพูด: EMCS ฝั่ง "ตรวจรูปแบบ" (AlertSummary 'F2') ไม่ได้ฟ้อง
# เป็นรายการชื่อช่องล้วน แต่เป็นประโยคที่ **อ้างชื่อช่องไว้ในเครื่องหมายคำพูด** เช่น
#   "1. กรณี [เคลมสด] ต้องระบุ 'วันที่เกิดเหตุและเวลา' 'วันที่ลูกค้าแจ้งบ.ประกัน' …"
# ทั้งประโยคเอาไปหาช่องบนฟอร์มไม่เจอ แต่ชื่อในคำพูดเจอ → ดึงออกมาตีกรอบแดงได้
_QUOTED = re.compile(r"['‘’\"“”]([^'‘’\"“”]{3,40})['‘’\"“”]")


def _quoted_field_names(alert_text: str) -> list:
    """ชื่อช่องที่ EMCS อ้างในเครื่องหมายคำพูด — คืนตามลำดับ ไม่ซ้ำ"""
    out = []
    for s in _QUOTED.findall(alert_text or ""):
        s = " ".join(s.split())
        if s and s not in out:
            out.append(s)
    return out


def _parse_missing_fields(alert_text: str) -> str:
    """ดึงรายชื่อช่องที่ระบบฟ้องจากข้อความ validation (บรรทัดแบบ '1. สถานที่เกิดเหตุ')"""
    return ", ".join(_missing_field_list(alert_text))


def verify_car_saved(driver, data: ClaimData, save_fn=None) -> bool:
    """อ่านค่ากลับมาตรวจว่า 'ประเภทรถ + ยี่ห้อ' ติดจริงหลังบันทึก

    ทำไมต้องมี: บน draft จริง (S68426076666) บอทเคย log '✓ ประเภทรถ 90' และ
    '✓ ยี่ห้อ' แต่พอเปิดเรื่องใหม่วันถัดมา ทั้งสองช่องเป็น '-- ระบุ --' — คือ
    เลือกได้บนหน้าจอแต่ค่าไม่ commit ตอนกดบันทึก (postback ของ cascade ไม่ทัน)
    รายงานว่าสำเร็จทั้งที่ข้อมูลไม่ติด = อันตรายกว่าล้มเห็น ๆ

    ไม่ติด → กรอกซ้ำ 1 รอบ (+ save_fn ถ้าส่งมา) แล้วตรวจอีกครั้ง; ยังไม่ติด = ฟ้อง
    คืน True เมื่อครบทั้งสองช่อง"""
    want_type = str(data.prb_car_type or "").strip()
    want_brand = normalize_brand(data.car_brand)
    for rnd in (1, 2):
        ctype = _current_select_text(driver, "ddlCType").strip()
        brand = _current_select_text(driver, "ddlCMFG").strip()
        ok_t = bool(ctype) and not _is_placeholder_option(ctype)
        ok_b = bool(brand) and not _is_placeholder_option(brand)
        if (ok_t or not want_type) and (ok_b or not want_brand):
            log(f"   ✓ ตรวจหลังบันทึก: ประเภทรถ='{ctype}' ยี่ห้อ='{brand}' ติดครบ")
            return True
        miss = ", ".join(n for n, ok in (("ประเภทรถ", ok_t), ("ยี่ห้อ", ok_b)) if not ok)
        if rnd == 2:
            log(f"   ⚠️ หลังบันทึกแล้ว {miss} ยังว่างอยู่ — เลือก/บันทึกเองบนหน้าจอ "
                "(ค่าไม่ commit ผ่าน postback)")
            return False
        log(f"   ↻ หลังบันทึก {miss} ยังว่าง — กรอกซ้ำอีกรอบ")
        if want_type and not ok_t:
            _select_car_type(driver, want_type)
        if want_brand and not ok_b:
            _select_car_brand(driver, data.car_brand, label="ยี่ห้อรถ (ซ่อมหลังบันทึก)")
        if save_fn is not None:
            try:
                save_fn()
            except Exception as e:
                log(f"   ⚠️ บันทึกซ้ำไม่สำเร็จ ({type(e).__name__})")
                return False
    return False


# อ่านผล validForm() **พร้อมของที่มันไม่ยอมบอก**
#
# วงจรจริงในหน้า EMCS (ดัมป์จาก error_emcs_2026013059072_20260804_225233.html):
#   validForm()  = if (vlidSurvey() == true) { เช็ค format → AlertSummary ถ้าพัง }
#                  else { return false }        ← ทางนี้ **เงียบสนิท**
#   vlidSurvey() = ไล่เช็คช่องบังคับ เก็บชื่อช่องที่ขาดต่อกันใน global `strJoinText`
#                  และ id ช่องแรกที่ผิดใน `objControlName` แล้วบรรทัดสุดท้าย…
#       //AlertSummary(strJoinText, objControlName, 'R');   ← **ถูกคอมเมนต์ทิ้ง**
#
# = ช่องบังคับขาดเมื่อไหร่ EMCS ปัดปุ่มตกโดยไม่บอกอะไรเลยสักตัวอักษร แต่ข้อมูลยังค้าง
# อยู่ในตัวแปร global → อ่านออกมาเองได้ ทั้งรายชื่อช่องและ id ช่องที่จะตีกรอบแดง
_JS_VALIDFORM = """
var out = {ok: null, err: '', missing: '', control: ''};
try { out.ok = (validForm() !== false); }
catch (e) { out.err = String((e && e.message) || e); }
try { if (typeof strJoinText !== 'undefined') out.missing = String(strJoinText || ''); }
catch (e) {}
try { if (typeof objControlName !== 'undefined') out.control = String(objControlName || ''); }
catch (e) {}
return out;
"""


def _read_validform(driver) -> dict:
    """เรียก validForm() แล้วอ่าน global ที่ EMCS ใช้เก็บ "ช่องที่ขาด" ออกมาด้วย

    คืน {ok, err, missing[list], control, alert}
      ok=None = เรียกไม่ได้/พังกลางทาง · alert = ข้อความที่ validForm เด้งออกมา

    ⚠️ **validForm() ไม่ใช่ฟังก์ชันอ่านอย่างเดียว** — ถ้า format ไม่ผ่านมันเรียก
    `AlertSummary()` ซึ่งเป็น `alert()` ของจริง (เจอสด เคลม 2026013058422: เตือน
    'กรณี [เคลมสด] ต้องระบุ 5 วันที่ ให้อยู่ภายใน 24 ชั่วโมง') alert ที่ค้างอยู่จะ
    ทำให้ **คำสั่ง Selenium ถัดไปทุกคำสั่งโยน UnexpectedAlertPresentException**
    แล้วโปรแกรมตายทั้งที่ควรแค่ "หยุดรอคนแก้" → ที่นี่จึงเก็บ alert ทิ้งให้เสมอ
    และส่งข้อความกลับไปให้ผู้เรียกใช้ต่อ (มันคือ validation message ที่อยากได้อยู่แล้ว)
    """
    out = {"ok": None, "err": "", "missing": [], "control": "", "alert": ""}
    res = None
    try:
        res = driver.execute_script(_JS_VALIDFORM)
    except UnexpectedAlertPresentException:
        pass                       # validForm เด้ง alert ระหว่างรัน — เก็บข้างล่าง
    except Exception as e:
        out["err"] = type(e).__name__
        return out
    finally:
        try:                       # กวาด alert ที่ validForm อาจเพิ่งเด้งทิ้งเสมอ
            out["alert"] = (accept_alert(driver, timeout=2) or "").strip()
        except Exception:
            pass
    if not isinstance(res, dict):
        return out
    out["ok"] = res.get("ok")
    out["err"] = str(res.get("err") or "")
    # strJoinText = "ชื่อช่อง1,ชื่อช่อง2," (ต่อท้ายด้วย , ทุกตัว)
    out["missing"] = [s.strip() for s in str(res.get("missing") or "").split(",")
                      if s.strip()]
    out["control"] = str(res.get("control") or "").strip()
    return out


# ---- ตัวดักคลิก: ครั้งหน้าที่ "กดแล้วเงียบ" ต้องบอกได้ว่าเงียบตรงไหน ----
#
# บั๊กนี้เกิดไม่สม่ำเสมอ บังคับให้เกิดตอนรันจริงไม่ได้ (เคลม 2026013059072: บอทกด
# ไม่ติด 2 รอบ แต่คนกดปุ่มเดียวกันบนหน้าเดียวกันผ่านทันที) — เดาสาเหตุจาก log เดิม
# ไม่ได้เลย เพราะมันบอกได้แค่ "ปุ่มไม่เปลี่ยนสถานะ" ซึ่งเกิดได้จากหลายเหตุ
#
# ก่อนคลิกจึงติดตัวดัก 2 ชั้นไว้บนปุ่ม แล้วอ่านทีหลังว่าเดินไปถึงไหน:
#   got=false                → event คลิกไม่ถึงปุ่มเลย (มีอะไรบัง/คลิกผิดจุด)
#   got=true, ran=false      → event ถึง แต่ onclick ไม่ทำงาน (handler หลุด)
#   ran=true, ret=false      → onclick ทำงานแล้ว "ปัดตกเอง" (validForm คืน false)
#   ran=true, err=…          → onclick พังกลางทาง (JS error)
#   ran=true, ret≠false      → onclick ผ่านหมด แต่ postback ไม่ออก
# ตัวห่อคืนค่าเดิมของ onclick เสมอ → พฤติกรรมปุ่มไม่เปลี่ยน (return false ยังยกเลิก submit ได้)
_JS_ARM_CLICK = """
var id = arguments[0], b = document.getElementById(id);
if (!b) return false;
window.__seClick = {got: false, ran: false, ret: null, err: '', over: ''};
if (!b.__seArmed) {
  b.__seArmed = true;
  b.addEventListener('click', function () { window.__seClick.got = true; }, true);
  var orig = b.onclick;
  b.onclick = function (e) {
    window.__seClick.ran = true;
    try { window.__seClick.ret = orig ? orig.call(this, e) : null; }
    catch (err) { window.__seClick.err = String((err && err.message) || err); throw err; }
    return window.__seClick.ret;
  };
}
/* ใครนั่งทับจุดที่ selenium จะคลิก (กึ่งกลางปุ่ม) — เลื่อนเข้าจอก่อนวัด เพราะ
   selenium ก็เลื่อนก่อนคลิกเหมือนกัน ไม่งั้น elementFromPoint คืน null ทุกครั้ง
   ที่ปุ่มอยู่ใต้ fold แล้วรายงานเป็น '(นอกจอ)' หลอก */
try { b.scrollIntoView({block: 'center'}); } catch (e) {}
var r = b.getBoundingClientRect();
var top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
window.__seClick.over = (top === b || (top && b.contains(top))) ? ''
  : (top ? (top.tagName + (top.id ? '#' + top.id : '') +
            (top.className ? '.' + String(top.className).split(' ')[0] : '')) : '(นอกจอ)');
return true;
"""


def _arm_click_probe(driver, button_id: str):
    """ติดตัวดักบนปุ่มก่อนคลิก (ล้มเหลวเงียบ — เป็นแค่เครื่องมือวินิจฉัย)"""
    try:
        driver.execute_script(_JS_ARM_CLICK, button_id)
    except Exception:
        pass


def _read_click_probe(driver) -> str:
    """อ่านผลตัวดัก → ข้อความอธิบายว่าคลิกไปตายตรงไหน ('' = อ่านไม่ได้/ไม่มีข้อมูล)"""
    try:
        c = driver.execute_script("return window.__seClick || null;")
    except Exception:
        return ""
    if not isinstance(c, dict):
        return ""
    over = c.get("over") or ""
    if not c.get("got"):
        return ("event คลิกไม่ถึงปุ่มเลย"
                + (f" — มี {over} ทับจุดที่คลิกอยู่" if over else " (ปุ่มอาจเลื่อน/ถูกวาดใหม่ระหว่างคลิก)"))
    if not c.get("ran"):
        return "event คลิกถึงปุ่มแล้ว แต่ onclick ของ EMCS ไม่ทำงาน (handler หลุดจาก DOM)"
    if c.get("err"):
        return f"onclick ของ EMCS พังกลางทาง: {str(c['err'])[:160]}"
    if c.get("ret") is False:
        return "onclick ทำงานแล้วปัดตกเอง (validForm() คืน false) — ข้อมูลยังไม่ครบ/ไม่ถูกรูปแบบ"
    return "onclick ผ่านหมดแล้วแต่ postback ไม่ออก (ฝั่ง ASP.NET ไม่ยิงฟอร์ม)"


def _wait_page_quiet(driver, quiet: float = 2.0, timeout: float = 10.0) -> bool:
    """รอจนหน้า "ไม่ถูก render ใหม่" ติดกัน `quiet` วินาที — คืน False ถ้าไม่นิ่งจนหมดเวลา

    ทำไมต้องมีทั้งที่เช็ค readyState + isInAsyncPostBack ไปแล้ว: postback แบบเต็มหน้า
    ของ ASP.NET (ที่ ddl จังหวัด/อำเภอ/ประเภทรถ ยิงตอน onchange) **ยังไม่เริ่มเดินทาง**
    ตอนที่เราถาม readyState ได้ — ถามตอนนั้นได้ 'complete' ของหน้าเดิม แล้วอีกแป๊บ
    response ค่อยมาถึงแล้วเปลี่ยนหน้าทั้งใบ

    ผลคือ: กดบันทึกไปแล้ว (ปุ่มขึ้น 'Please wait...' ยิง __doPostBack เรียบร้อย) แต่
    response ของ postback ก่อนหน้ามาถึงทีหลัง render หน้าใหม่ทับ → **คำสั่งบันทึกหายไป
    เฉย ๆ ไม่มี alert ไม่มี error** บอทรอ alert เก้อ 30 วิแล้วรายงานว่า 'EMCS เงียบ'
    (พิสูจน์แล้วกับเคส #126 10 ส.ค. 69: เลือกจังหวัด 14:28:50 → อำเภอ 14:28:51 →
    กดบันทึก 14:28:52 → ไม่มี alert; 30 วิให้หลังปุ่มกลับมาเป็น 'แก้ไข' กดได้ = หน้าถูก
    render ใหม่ทับจริง)

    วิธีตรวจ: ปัก marker ไว้บน window แล้วรอ — full postback โหลด document ใหม่
    marker จะหายไปเอง (ต่างจาก async postback ที่ window อยู่ยง)"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            driver.execute_script("window.__akQuiet = 1;")
        except Exception:
            return False                     # หน้ากำลังเปลี่ยนอยู่พอดี — วนรอใหม่ไม่ได้
        time.sleep(quiet)
        try:
            if driver.execute_script("return window.__akQuiet === 1;"):
                return True
        except Exception:
            pass
        log("   ⏳ หน้าเพิ่งถูกโหลดใหม่ (postback ของช่องก่อนหน้าเพิ่งมาถึง) — รอให้นิ่งก่อนกด")
    return False


def _click_save_button(driver, button_id: str, tries: int = 3) -> bool:
    """กดปุ่มบันทึก แล้ว **ยืนยันว่าคลิกติดจริง** — คืน True เมื่อติด

    onclick ของ EMCS ตั้ง `this.value='Please wait...'; this.disabled=true;` ก่อนยิง
    postback → ใช้เป็นสัญญาณว่าคลิกเข้าถึง handler แล้ว

    ทำไมต้องมี: ดรอปดาวน์ก่อนหน้า (เช่น ddlLoss_ID) มี postback ถ้ากดบันทึกตอน
    postback ยังไม่จบ คลิกจะหลุดเงียบ ๆ — ปุ่มยังเป็น 'บันทึก' เหมือนเดิม ไม่มีทั้ง
    alert และ postback แล้วบอทไปรอ alert เก้อ 30 วิ
    ⚠️ verify ของจริง เคลม 2026013059072: บอทกดแล้วเงียบ แต่คนกดเองบนหน้าเดียวกัน
    ด้วยข้อมูลชุดเดิมผ่านทันที (ได้ S68426080794) — ไม่ใช่ปัญหาข้อมูล แต่เป็นจังหวะกด
    """
    for i in range(1, tries + 1):
        # รอหน้าให้นิ่งก่อน: โหลดจบ + ไม่มี postback ของ ASP.NET ค้างอยู่
        try:
            WebDriverWait(driver, 20).until(lambda d: d.execute_script(
                "return document.readyState === 'complete'"
                " && !(window.Sys && Sys.WebForms"
                "      && Sys.WebForms.PageRequestManager.getInstance()"
                "         .get_isInAsyncPostBack());"))
        except Exception:
            pass
        _wait_page_quiet(driver)
        try:
            btn = wait_clickable(driver, By.ID, button_id)
        except UnexpectedAlertPresentException:
            # alert โผล่ระหว่างรอปุ่ม = คำสั่งก่อนหน้าถึง handler แล้ว (มักเป็นข้อความ
            # "บันทึก...เรียบร้อยแล้ว" ที่มาช้า) — ผู้เรียกอ่าน alert ต่อเอง
            # เดิมหลุดออกไปเป็น UnexpectedAlertPresentException ดิบ ล้มทั้งงาน
            log("   ↳ มี alert เด้งระหว่างรอปุ่ม — ถือว่าคลิกก่อนหน้าถึง EMCS แล้ว")
            return True
        except TimeoutException:
            # ปุ่มไม่ขึ้น/ยังกดไม่ได้ — เดิมโยน TimeoutException ดิบออกไปล้มทั้งงาน
            # ทั้งที่ผู้เรียก (save_main_form) มีทางวินิจฉัย + หยุดรอคนอยู่แล้ว
            # (เจอจริง 20/08/69 เคลม 2026013164636 บนเครื่องพนักงาน: ค่าครบ 41/41
            #  ช่อง แต่ btnSave กดไม่ได้ → traceback ยาว งานค้าง เรื่องล็อกไว้ใน EMCS)
            log(f"   ⚠️ {button_id} ยังกดไม่ได้ (รอบ {i}/{tries}) — ปุ่มไม่ขึ้นหรือถูกปิดอยู่")
            if i < tries:
                continue
            return False
        _arm_click_probe(driver, button_id)     # ไว้บอกทีหลังว่าคลิกไปตายตรงไหน
        btn.click()
        try:    # onclick ทำงาน → ปุ่มถูกปิด/เปลี่ยนข้อความทันที
            WebDriverWait(driver, 5).until(
                lambda d: (lambda e: (not e.is_enabled())
                           or "wait" in (e.get_attribute("value") or "").lower())(
                    d.find_element(By.ID, button_id)))
            return True
        except UnexpectedAlertPresentException:
            return True          # เด้ง alert = คลิกติดแน่นอน (ผู้เรียกอ่านต่อเอง)
        except Exception:
            pass
        # คลิกไม่ติด = ได้ 2 อย่าง แยกให้ออกก่อนจะกดซ้ำ:
        #  ก) validForm() ปัดตก (ช่องบังคับขาด) → onclick return false ก่อนถึง disable
        #     กดซ้ำอีกกี่ครั้งก็ผลเดิม — ออกไปให้ผู้เรียกอ่านรายชื่อช่องที่ขาดเลย
        #  ข) คลิกไม่ถึง handler จริง ๆ (หน้า postback ค้าง) → ค่อยกดซ้ำ
        _probe = _read_click_probe(driver)
        if _probe:
            log(f"   🔎 {button_id} รอบ {i}: {_probe}")
        _vf = _read_validform(driver)
        if _vf.get("alert"):
            # EMCS เตือนรูปแบบข้อมูล (AlertSummary) — เป็นเรื่องข้อมูล ไม่ใช่จังหวะกด
            # กดซ้ำก็เจอ alert เดิม → ออกไปให้ผู้เรียกไปหยุดรอคนแก้
            log(f"   ⚠️ {button_id} ไม่ผ่านด่านข้อมูลของ EMCS: {_vf['alert'][:200]}")
            return False
        if _vf.get("ok") is False:
            log(f"   ⚠️ {button_id} ไม่ตอบสนองเพราะ validForm() ปัดตก (ช่องบังคับขาด) "
                "— ไม่กดซ้ำ")
            return False
        if i < tries:
            log(f"   ↻ คลิก {button_id} ไม่ติด (หน้าน่าจะยัง postback ค้าง) — ลองใหม่ {i}/{tries}")
            time.sleep(2)

    # ทางสุดท้าย: ยิง postback เองแบบเดียวกับที่ onclick ของ EMCS ทำทุกบรรทัด
    # (validForm ผ่านแล้ว = ข้อมูลครบ เหลือแค่คลิกส่งไม่ถึง handler) — เส้นนี้ตัดปัญหา
    # "คลิกหลุด" ทิ้งทั้งกอง โดยไม่ข้ามด่านตรวจของ EMCS สักด่าน
    log(f"   ↳ กด {button_id} ไม่ติดครบ {tries} ครั้ง แต่ข้อมูลผ่าน validForm() — "
        "ยิง postback ตรงแบบเดียวกับที่ปุ่มทำ")
    try:
        ok = driver.execute_script(
            "var b = document.getElementById(arguments[0]);"
            "if (!b) return false;"
            "if (typeof validForm === 'function' && validForm() === false) return false;"
            "b.value = 'Please wait...'; b.disabled = true;"
            "__doPostBack(arguments[0], '');"
            "return true;", button_id)
        if ok:
            return True
        log("   ⚠️ ยิง postback ตรงไม่สำเร็จ (ไม่พบปุ่ม/validForm ปัดตก)")
    except UnexpectedAlertPresentException:
        return True          # เด้ง alert = ถึง handler แล้ว ผู้เรียกอ่านต่อเอง
    except Exception as e:
        log(f"   ⚠️ ยิง postback ตรงไม่ได้ ({type(e).__name__})")
    log(f"   ⚠️ กด {button_id} ครบ {tries} ครั้งแล้วปุ่มยังไม่ตอบสนอง")
    return False


_JS_VISIBLE_BUTTONS = """
return Array.prototype.slice.call(document.querySelectorAll(
    'input[type=submit],input[type=button],button,input[type=image]'))
  .filter(function(e){ return e.offsetParent !== null; })
  .map(function(e){ return {
      id: e.id || '(ไม่มี id)',
      label: ((e.value || e.textContent || e.alt || '') + '').trim().slice(0, 40),
      disabled: !!e.disabled,
      onclick: !!(e.getAttribute('onclick'))
  }; });
"""


def _report_visible_buttons(driver, button_id: str = "") -> str:
    """ลิสต์ปุ่มที่ 'มองเห็นจริง' บนหน้า EMCS ตอนนี้ + สถานะของปุ่มที่บอทกด

    EMCS เปลี่ยนชุดปุ่มตามสถานะเรื่อง (สร้างใหม่ = บันทึก/บันทึกฉบับร่าง,
    บันทึกแล้ว = ยกเลิก/แก้ไข) ถ้าบอทกด id ที่ยังอยู่ใน DOM แต่ไม่ใช่ปุ่มที่ใช้งาน
    จริงในสถานะนั้น จะเงียบสนิทแบบหาสาเหตุไม่เจอ — ลิสต์นี้ทำให้เห็นทันที"""
    try:
        btns = driver.execute_script(_JS_VISIBLE_BUTTONS) or []
    except Exception as e:
        return f"อ่านรายการปุ่มบนหน้าไม่ได้ ({type(e).__name__})"
    if not btns:
        return ""
    log("   ปุ่มที่มองเห็นบนหน้า EMCS ตอนนี้:")
    for b in btns[:25]:
        mark = " ← ปุ่มที่บอทกด" if b.get("id") == button_id else ""
        state = "ปิดอยู่" if b.get("disabled") else "กดได้"
        log(f"     · {b.get('id')} = '{b.get('label')}' ({state})"
            f"{'' if b.get('onclick') else ' [ไม่มี onclick]'}{mark}")
    ids = [b.get("id") for b in btns]
    if button_id and button_id not in ids:
        return (f"ปุ่ม {button_id} ไม่ได้อยู่ในชุดปุ่มที่ใช้งานได้ของสถานะนี้ "
                f"(หน้าโชว์: {', '.join(repr(b.get('label')) for b in btns[:6])}) "
                "— บอทกดผิดปุ่ม")
    return ""


def _diagnose_save_click(driver, button_id: str = "", vf=None) -> str:
    """หาเหตุผลที่กดปุ่มบันทึกแล้ว EMCS เงียบสนิท (ไม่มีทั้ง alert และ postback)

    onclick ของ btnSave คือ
        if (validForm() == false) { return false; } ... __doPostBack('btnSave','')
    ดังนั้นเงียบได้ 2 แบบ:
      1. `validForm()` คืน false — ปกติมันจะ alert เอง แต่บาง path คืน false เฉย ๆ
      2. `validForm()` **โยน JS error** — EMCS render ฟิลด์ไม่เท่ากันทุกบริษัท/ทุกสถานะ
         `getElementById(...)` ที่หาไม่เจอจะได้ null แล้ว `.value` ระเบิด → onclick
         ตายกลางทาง ไม่มีอะไรโผล่ให้เห็นเลย

    เรียก validForm() เองแล้วเก็บผล/ข้อความ error มาบอก — ถ้ามันเด้ง alert ตอนเรียก
    ก็ยิ่งดี เพราะนั่นคือข้อความ validation ที่เราอยากได้ตั้งแต่แรก
    คืนข้อความอธิบาย ('' = บอกไม่ได้)"""
    # 1) กดผิดปุ่มหรือเปล่า — เช็คก่อนอย่างอื่น เพราะถ้าใช่ ที่เหลือไม่ต้องเดาแล้ว
    wrong = _report_visible_buttons(driver, button_id)
    if wrong:
        return wrong

    # 2) validForm() ว่ายังไง (+ ขุด global ที่มันเก็บชื่อช่องที่ขาดไว้แต่ไม่ยอมโชว์)
    # ⛔ รับผลที่อ่านมาแล้วได้ (vf) — เรียก _read_validform ซ้ำ = validForm เด้ง alert อีกรอบ
    #    ทำให้ log มี alert เดียวกันซ้ำ ๆ และผลรอบท้ายเชื่อไม่ได้ (เจอจริง 01/09/69)
    res = vf if vf is not None else _read_validform(driver)
    if res.get("alert"):
        return f"EMCS แจ้ง: {res['alert'][:300]}"
    if res.get("err"):
        return (f"validForm() ของ EMCS พังกลางทาง ({res['err'][:160]}) — "
                "ฟอร์มนี้ไม่มีช่องที่สคริปต์ EMCS อ้างถึง กรอก/บันทึกเองบนหน้าจอ")
    if res.get("ok") is False:
        # EMCS คอมเมนต์ AlertSummary ของ vlidSurvey ทิ้ง → หน้าจอเงียบ แต่รายชื่อช่อง
        # ยังอยู่ใน strJoinText — เอามาบอกให้ตรงว่าขาดอะไร แทนคำว่า "ไม่บอกเหตุผล"
        miss = res.get("missing") or []
        if miss:
            # ขึ้นบรรทัดต่อข้อ = รูปแบบเดียวกับ alert จริงของ EMCS → _missing_field_list
            # (ตัวเดียวกับที่ใช้กับ alert ปกติ) แยกชื่อช่องไปตีกรอบแดงได้เลย
            return ("EMCS ไม่ยอมบันทึกเพราะช่องบังคับยังขาด (มันไม่ขึ้นข้อความให้เอง)\n"
                    + "\n".join(f"{i + 1}. {m}" for i, m in enumerate(miss)))
        return "validForm() คืน false โดยไม่บอกเหตุผล — ตรวจช่องดอกจันแดงบนหน้าจอ"
    if res.get("ok") is True:
        # validForm ผ่านตอนเราเรียกเอง แต่กดปุ่มแล้วไม่เกิดอะไร = ปัญหาอยู่ที่การกด
        # ไม่ใช่ข้อมูล (verify: คนกดปุ่มเดียวกันบนหน้าเดียวกันแล้วผ่าน — 2026013059072)
        return ("validForm() ผ่านตอนเรียกเอง แต่กดปุ่มแล้วไม่มีอะไรเกิดขึ้น — "
                "ปัญหาอยู่ที่การกดปุ่ม ไม่ใช่ข้อมูลไม่ครบ (ลองกดปุ่มเองบนหน้าจอ)")
    return ""


def _main_form_saved(driver, button_id: str) -> bool:
    """EMCS บันทึกหน้าหลักไปแล้วหรือยัง — **ถามจาก DOM ไม่พึ่ง alert**

    บันทึกสำเร็จแล้ว EMCS จะสลับปุ่ม 'บันทึก' (btnSave) เป็น 'แก้ไข' (btnUpdate)
    และปลดล็อกปุ่มความเสียหาย · alert ยืนยันมาช้าหรือหายไปเลยก็ได้

    เจอจริง 20-21/08/69 บนเครื่องพนักงาน (เคลม 2026013164636 · 2026013164456 ·
    2026013163746): บันทึกผ่านตั้งแต่รอบแรก แต่ alert มาถึงช้ากว่า 90 วิ ระหว่างนั้น
    บอทเห็นว่า "เงียบ" เลยกดซ้ำ — ซึ่งกดไม่ได้เพราะ btnSave หายไปแล้ว — วนจนตาย
    และทิ้งเรื่องล็อกไว้ใน EMCS ทั้งที่งานเข้าเรียบร้อยแล้ว

    ⚠️ ใช้กับเส้น btnSave (สร้างเรื่องใหม่) เท่านั้น — โหมด btnUpdate ไม่มี btnSave
    อยู่แล้วตั้งแต่ต้น เช็คแบบนี้จะได้ True ตลอด
    """
    if button_id != "btnSave":
        return False
    try:
        return bool(driver.execute_script(
            "var s=document.getElementById('btnSave');"
            "var u=document.getElementById('btnUpdate');"
            "var d=document.getElementById('btnPopUp_DamList');"
            "var gone=(!s)||s.offsetParent===null||s.disabled;"
            "return gone && ((!!u && u.offsetParent!==null) || (!!d && !d.disabled));"))
    except Exception:
        return False


def save_main_form(driver, data: ClaimData, button_id: str = "btnSave",
                   is_new: bool = True):
    """กดบันทึกหน้าหลัก แล้ว "ตรวจว่าบันทึกสำเร็จจริง"

    - is_new (btnSave สร้างใหม่): สำเร็จ → ปุ่มความเสียหาย (btnPopUp_DamList) ปลดล็อก
    - is_new=False (btnUpdate โหมดแก้ เช่นหลังนำเข้า XML): btnPopUp_DamList ปลดล็อก
      อยู่แล้ว → ใช้สัญญาณ "alert ไม่ใช่ validation ('กรุณา')" = สำเร็จแทน
    - validation ไม่ผ่าน → alert บอกรายการที่ขาด:
        1) ลองซ่อม dropdown ที่ค่าหลุดจาก postback race อัตโนมัติก่อน (สูงสุด 2 รอบ)
        2) ถ้าซ่อมอัตโนมัติไม่ได้ (เช่น text field ว่างอย่าง 'สถานที่เกิดเหตุ')
           → หยุดรอให้คนกรอกช่องที่ฟ้องเองบนหน้า EMCS แล้วลองบันทึกใหม่
      (มี cap กันลูปไม่รู้จบ — ถ้าไม่มีคนตอบ/แก้ไม่ได้จะ raise)
    - **กดแล้วเงียบ ไม่มี alert เลย** ก็เกิดได้ (ดู _diagnose_save_click) — ต้องไม่ตีความ
      ว่าสำเร็จ และต้องไม่ปล่อย TimeoutException ดิบ ๆ ออกไป"""
    auto_heal_left = 2    # จำนวนรอบที่ยอมให้ซ่อม dropdown อัตโนมัติ
    # จำนวนรอบที่ยอมลองกดใหม่เมื่อคลิกไม่ติด — 3 เพราะการทดสอบจริงครั้งแรก (เคส #126
    # 10 ส.ค. 69) ผ่านที่รอบ 3 พอดี = ใช้โควตาเดิม (2) จนหมดเกลี้ยง เหลือ 0 ที่กันพลาด
    click_fail_left = 3
    bad_id = ""           # id ช่องแรกที่ EMCS ตีตก (objControlName) — ไว้ตีกรอบแดง
    for attempt in range(1, 8):
        log(f"EMCS: กดบันทึกหน้าหลัก ({button_id}, รอบ {attempt})")
        bad_id = ""
        clicked = _click_save_button(driver, button_id)
        try:
            alert_text, silent = accept_alert(driver), False
        except TimeoutException:
            alert_text, silent = "", True
            # ⚠️ "ไม่มี alert" ไม่ได้แปลว่าไม่ได้บันทึก — ถาม DOM ก่อนเสมอ
            if _main_form_saved(driver, button_id):
                log("   ↳ หน้าเว็บบอกว่าบันทึกไปแล้ว (ปุ่ม 'บันทึก' กลายเป็น 'แก้ไข') "
                    "— ถือว่าสำเร็จ ไม่กดซ้ำ")
                silent = False
            elif not clicked:
                # คลิกไม่ถึง handler เลย — คนละเรื่องกับ "ข้อมูลไม่ครบ" ต้องบอกให้ตรง
                log("   ⚠️ ปุ่มบันทึกไม่ตอบสนอง (คลิกไม่ถึง handler ของ EMCS)")
                _vf = _read_validform(driver)
                bad_id = _vf.get("control") or ""
                if _vf.get("ok") is False:
                    # ช่องบังคับขาดจริง (EMCS ปัดปุ่มตกเงียบ ๆ) — กดซ้ำอีกกี่ครั้งก็ผลเดิม
                    # ไปหยุดรอคนเลย พร้อมบอกว่าขาดช่องไหนบ้าง
                    alert_text = _diagnose_save_click(driver, button_id, vf=_vf) or alert_text
                    log(f"   ↳ {alert_text}")
                elif click_fail_left > 0:
                    click_fail_left -= 1
                    continue
                else:
                    alert_text = ("กดปุ่มบันทึกไม่ติด — หน้าอาจค้าง postback หรือปุ่มถูกปิดอยู่ "
                                  "ลองกดบันทึกเองบนหน้าจอ")
            else:
                # คลิกติดแล้วแต่ EMCS เงียบ → validForm() ปัดตกฝั่ง JS
                # เดิมปล่อย TimeoutException ทะลุออกไป = โปรแกรมตาย ทั้งที่ทางที่ถูกคือ
                # ตกลงมาที่ "หยุดรอคนกรอก" เหมือน validation ปกติ (เคลม 2026013059072)
                log("   ⚠️ กดบันทึกแล้ว EMCS เงียบ ไม่มี alert ตอบกลับ — ปุ่มถูก "
                    "validForm() ฝั่ง JS ปัดตก (ไม่ยิง postback)")
                # ⛔ อ่าน validForm ครั้งเดียวแล้วใช้ต่อ — **ทุกครั้งที่เรียกมันเด้ง alert ใหม่**
                #    ของเดิมเรียก 3 ครั้งซ้อน (ในตัววินิจฉัย + อีก 2 ที่นี่) alert เดียวกัน
                #    จึงโผล่ 3 รอบใน log แล้วผลรอบท้ายเชื่อไม่ได้
                _vf = _read_validform(driver)
                _why = _diagnose_save_click(driver, button_id, vf=_vf)
                bad_id = _vf.get("control") or ""
                if _vf.get("ok") is True and click_fail_left > 0:
                    # validForm ผ่านจริง แต่หน้าเงียบ = คำสั่งบันทึกหายกลางทาง (postback ของ
                    # ช่องก่อนหน้ามาถึงทีหลังแล้ว render ทับ) — กดใหม่บนหน้าที่นิ่งแล้วมักผ่าน
                    # ⛔ ต้องเป็น True เท่านั้น — ok=None แปลว่า "อ่านไม่ได้" ไม่ใช่ "ข้อมูลครบ"
                    #    ของเดิมใช้ `is not False` จึงกดเดาทั้งที่ยังไม่รู้ว่าขาดอะไร
                    click_fail_left -= 1
                    log("   ↻ ข้อมูลผ่าน validForm() — น่าจะเป็นคำสั่งบันทึกหายกลางทาง กดใหม่")
                    continue
                if _why:
                    # EMCS บอกมาแล้วว่าขาดช่องไหน → **ห้ามกดซ้ำ** ตกไปเส้น "หยุดรอคนกรอก"
                    # (เจอจริง 01/09/69 เคลม 2026013168056: ได้รายชื่อช่องมาครบตั้งแต่รอบแรก
                    #  แต่โยนทิ้งไปกดซ้ำ 3 รอบ เสีย 90 วิ ทั้งที่คนนั่งรอเติมให้อยู่)
                    log(f"   ↳ {_why}")
                    alert_text = _why

        if not is_new:
            # โหมดแก้: สำเร็จเมื่อ "มี alert" และไม่ใช่ validation ('กรุณา...')
            # (เงียบ = ไม่ได้บันทึก ห้ามนับว่าสำเร็จ)
            if not silent and "กรุณา" not in (alert_text or ""):
                log("EMCS: บันทึกแก้ไขหน้าหลักสำเร็จ ✓")
                m = re.search(r"S\d{9,13}", alert_text or "")
                return m.group(0) if m else ""
        else:
            try:
                WebDriverWait(driver, 25).until(
                    lambda d: d.find_element(By.ID, "btnPopUp_DamList").is_enabled()
                )
                log("EMCS: บันทึกหน้าหลักสำเร็จ ✓")
                # ดึงเลข e-Survey จากข้อความยืนยัน (ใช้รายงานสรุปท้ายชุด)
                m = re.search(r"S\d{9,13}", alert_text or "")
                return m.group(0) if m else ""
            except TimeoutException:
                pass
            except UnexpectedAlertPresentException:
                # มี alert ค้างอยู่ (EMCS เพิ่งเตือน validation ช้ากว่ารอบแรก /
                # validForm เด้งตอนวินิจฉัย) — เก็บข้อความไปใช้เป็นเหตุผลหยุดรอคน
                # ⛔ ห้ามปล่อยหลุด: เดิมเป็น error จบโปรแกรมทั้งที่ควรแค่หยุดรอคนแก้
                # (เจอสด เคลม 2026013058422 — 5 วันที่ของเคลมสดต้องอยู่ใน 24 ชม.)
                try:
                    alert_text = (accept_alert(driver, timeout=5) or "").strip() or alert_text
                except Exception:
                    pass
                silent = False
                log(f"   ⚠️ EMCS เตือนหลังกดบันทึก: {(alert_text or '')[:200]}")

        # ── ข้อความยืนยันความสำเร็จที่มาช้า ────────────────────────────────
        # EMCS ตอบ "บันทึกรายละเอียด เซอร์เวย์ เรียบร้อยแล้ว หมายเลข e-Survey คือ S684..."
        # ซึ่งอาจโผล่หลังจากที่ตัวตรวจสำเร็จข้างบนหมดเวลารอไปแล้ว · ห้ามปล่อยให้ไหลลงไป
        # เข้าทาง "validation ไม่ผ่าน" เพราะจะไปขึ้นบนหน้าเว็บว่า "ข้อมูลหน้าหลักที่ยังขาด
        # — สาเหตุ: บันทึก...เรียบร้อยแล้ว" (เจอจริง 20/08/69 เคลม 2026013163746)
        # แล้วบอทยังกดบันทึกซ้ำอีกรอบทั้งที่เรื่องเกิดไปแล้ว
        _m_ok = re.search(r"S\d{9,13}", alert_text or "")
        if _m_ok and "กรุณา" not in (alert_text or ""):
            log(f"EMCS: บันทึกหน้าหลักสำเร็จ ✓ (e-Survey {_m_ok.group(0)} — ข้อความยืนยันมาช้า)")
            return _m_ok.group(0)

        # validation ไม่ผ่าน — ลองซ่อม dropdown ที่หลุดจาก postback ก่อน (อัตโนมัติ)
        if auto_heal_left > 0 and "กรุณา" in (alert_text or "") \
                and _refill_missing_fields(driver, data, alert_text):
            auto_heal_left -= 1
            log("   ↻ กรอก field (dropdown) ที่หลุดซ้ำแล้ว — ลองบันทึกใหม่")
            continue

        # ซ่อมอัตโนมัติไม่ได้ (เช่น text field ว่าง) → หยุดรอให้คนกรอกช่องที่ฟ้องเอง
        fields = _missing_field_list(alert_text)
        missing = ", ".join(fields)
        label = "ข้อมูลหน้าหลักที่ยังขาด" + (f": {missing}" if missing else "")
        # ชี้ช่องได้ 2 ทาง: ชื่อช่องที่ฟ้องเป็นรายการ + ชื่อที่อ้างในเครื่องหมายคำพูด
        # (ข้อความตรวจรูปแบบเป็นประโยค เอาไปหาช่องตรง ๆ ไม่เจอ แต่ชื่อในคำพูดเจอ)
        if wait_for_manual_fill(label, reason=(alert_text or "").strip(),
                                focus_ids=[bad_id] if bad_id else None,
                                focus_labels=fields + _quoted_field_names(alert_text),
                                driver=driver):
            log("   ↻ ลองบันทึกหน้าหลักใหม่หลังผู้ใช้กรอกข้อมูล")
            continue

        # ไม่มีคนตอบ (รันแบบไม่มีคนเฝ้า) → ยอมแพ้
        raise RuntimeError(
            "บันทึกหน้าหลักไม่ผ่าน validation ของ EMCS — ข้อความที่ระบบแจ้ง: "
            f"\"{alert_text or '(ไม่มีข้อความ)'}\""
        )

    raise RuntimeError("บันทึกหน้าหลักไม่ผ่านหลายรอบเกินไป — หยุดกันลูปไม่รู้จบ "
                       "(ตรวจช่องที่ขึ้นสีแดงบนหน้า EMCS แล้วลองใหม่)")


# ------------------------------------------------------------------ ความเสียหาย

# ความเสียหายรถประกัน: ฟอร์มใหม่ (ปี 2569+) เพิ่ม "checklist ชิ้นส่วนสำเร็จรูป"
# (checkbox dgvDamage_List_ctl{NN}_WuDamL{A|B}_chbDam_Name_0 — ไม่มี postback)
# ทับช่องอิสระเดิม (dgvOtherDamage_List_..._txtDam_Name); ฟอร์มเก่าไม่มี checklist
# → verify DOM สด 2026-06-23
DAMAGE_CHECKLIST_THRESHOLD = 88   # fuzz.ratio ต่ำสุดที่ถือว่าตรง checkbox ชิ้นส่วน

# อ่าน checklist จาก popup (ฟอร์มเก่าคืน []); กรอง se-check-mix ('งานรวม') ด้วยเงื่อนไข
# id ต้องมี 'dgvDamage_List'
JS_READ_DAMAGE_CHECKLIST = r"""
return Array.prototype.slice.call(
  document.querySelectorAll('input[type=checkbox][id$="chbDam_Name_0"]'))
  .filter(function(cb){ return cb.id.indexOf('dgvDamage_List') >= 0; })
  .map(function(cb){
    var prefix = cb.id.replace('chbDam_Name_0','');
    var td = cb.closest('td');
    var part = (td ? (td.innerText || '') : '').replace(/\s+/g,' ').trim();
    return {cb: cb.id, prefix: prefix, part: part,
            has_pos: !!document.getElementById(prefix + 'rdoDam_Left_Right_0')};
  });
"""

# decoration ที่ตัดทิ้งก่อน match (วงเล็บ/ด้าน/ตัวบน-ล่าง/ซ้าย-ขวา/ช่องว่าง) — เรียงยาวก่อน
# ห้ามตัด 'หน้า'/'หลัง' (เป็นชิ้นคนละชิ้น เช่น กันชนหน้า≠กันชนหลัง)
_DAMAGE_DECOR_RE = re.compile(
    r"\([^)]*\)|ด้านบน|ด้านล่าง|ด้านซ้าย|ด้านขวา|ด้าน|ตัวบน|ตัวล่าง|ซ้าย|ขวา|\s+")


def _norm_damage_part(name: str) -> str:
    """ตัด decoration เหลือชื่อชิ้นส่วนหลัก เพื่อ match checklist
    เช่น 'กันชนหน้า(ใหญ่)'→'กันชนหน้า', 'บังโคลนหน้าขวา'→'บังโคลนหน้า'"""
    return _DAMAGE_DECOR_RE.sub("", name or "")


def _damage_side(name: str) -> str:
    """ซ้าย/ขวา/ทั้งคู่ จากชื่อชิ้นส่วน → index radio rdoDam_Left_Right ('0'/'1'/'2')"""
    name = name or ""
    if "ซ้าย" in name and "ขวา" in name:
        return "2"
    if "ขวา" in name:
        return "1"
    if "ซ้าย" in name:
        return "0"
    return "2"


def _damage_rank_idx(rank: str):
    """ระดับความเสียหาย A-D → index radio rdoDam_Lavel ('0'-'3') หรือ None"""
    return {"A": "0", "B": "1", "C": "2", "D": "3"}.get((rank or "").strip().upper())


def _match_damage_checklist(name, parts, used, threshold=DAMAGE_CHECKLIST_THRESHOLD):
    """หา index ของ checklist ที่ตรงชื่อชิ้นส่วน — ยังไม่ถูกติ๊ก:
    1) **prefix** (หลัก) — ชื่อความเสียหาย (normalize) ขึ้นต้นด้วยชื่อชิ้นส่วน checklist
       (เลือกชิ้นที่ยาวสุด) เพราะชื่อจริง ISURVEY = 'ชิ้นส่วน+คำเสริม+อาการ'
       เช่น 'ฝากระโปรงหน้า+คิ้ว บุบ'→ติ๊ก 'ฝากระโปรงหน้า'; แต่ 'คิ้วครอบไฟหน้า' ไม่ขึ้นต้น
       ด้วย 'ไฟหน้า' → ไม่ติ๊ก (กัน substring/ชิ้นคนละชิ้น)
    2) **fallback** — fuzz.ratio ≥ threshold (กันพิมพ์ผิดเล็กน้อยเมื่อชื่อ≈ชิ้นส่วน)
    คืน (index, score) หรือ (None, 0) = ไม่ตรง → ใช้ช่องอิสระแทน"""
    if not parts:
        return None, 0
    q = _norm_damage_part(name)
    if not q:
        return None, 0
    best_idx, best_len = None, 0
    for idx, part in enumerate(parts):
        if idx in used:
            continue
        p = _norm_damage_part(part)
        if p and q.startswith(p) and len(p) > best_len:
            best_idx, best_len = idx, len(p)
    if best_idx is not None:
        return best_idx, 100
    for _choice, score, idx in process.extract(
            q, parts, scorer=fuzz.ratio, limit=5):
        if score < threshold:
            break
        if idx not in used:
            return idx, score
    return None, 0


# ช่องอิสระความเสียหาย: pattern dgvOtherDamage_List_ctl{NN}_wuOtherDamL{A|B}_txtDam_Name
# cmdNewReport มี 8 (ctl02-05 × A/B) / ฟอร์ม import มี 20 (ctl02-11 × A/B) →
# อ่าน slot จริงจาก DOM แทน hardcode (ctl0{row} เดิมพังเมื่อ row>9: ctl010)
JS_FREE_TEXT_SLOTS = r"""
return Array.prototype.slice.call(document.querySelectorAll(
  'input[id^="dgvOtherDamage_List_ctl"][id$="_txtDam_Name"]'))
  .map(function(e){ return e.id.replace("txtDam_Name",""); });
"""


def _free_text_slots(driver) -> list:
    """คืน prefix ช่องอิสระความเสียหายที่มีจริงบนฟอร์ม popup — เรียงคอลัมน์ A ก่อน B
    (บน→ล่าง) เพื่อกรอกซ้ายเต็มก่อนค่อยขวา (คงพฤติกรรมเดิม)"""
    try:
        slots = driver.execute_script(JS_FREE_TEXT_SLOTS) or []
    except Exception:
        slots = []

    def _key(p):   # p = 'dgvOtherDamage_List_ctlNN_wuOtherDamLX_'
        m = re.search(r"ctl(\d+)_wuOtherDamL([AB])_", p)
        return (m.group(2), int(m.group(1))) if m else ("Z", 999)

    return sorted(slots, key=_key)


def fill_damage_list(driver, data: ClaimData, main_window: str):
    """เปิด popup ความเสียหาย กรอกทุกรายการ บันทึก แล้วสลับกลับหน้าหลัก

    ฟอร์มใหม่ (2569+): ติ๊ก "checklist ชิ้นส่วนสำเร็จรูป" (chbDam_Name_0) ที่ชื่อตรง +
    L/R/A + ระดับ; ชิ้นที่ไม่มีใน checklist → ช่องอิสระเดิม
    (dgvOtherDamage_List_ctl0{2-5}_wuOtherDamL{A|B}_, สูงสุด 8). ฟอร์มเก่า (checklist
    ว่าง) → ลงช่องอิสระทั้งหมดเหมือนเดิม
    """
    if not data.damage:
        log("EMCS: ไม่มีรายการความเสียหาย — ข้าม")
        return

    log(f"EMCS: กรอกความเสียหาย {len(data.damage)} รายการ")
    handles_before = set(driver.window_handles)
    wait_clickable(driver, By.ID, "btnPopUp_DamList").click()

    # รอ window ใหม่เปิดแล้วสลับไป
    WebDriverWait(driver, 15).until(
        lambda d: len(d.window_handles) > len(handles_before)
    )
    new_handle = (set(driver.window_handles) - handles_before).pop()
    driver.switch_to.window(new_handle)
    wait_visible(driver, By.ID, "btnSave", 15)

    # side ที่แอปส่งมาตรง ๆ (EMCS แยก radio ด้านออกจากชื่อชิ้นส่วน) — ไม่มีก็เดาจากชื่อ
    # เหมือนเดิม (flow ISURVEY ที่ชื่อชิ้นส่วนมีคำว่าซ้าย/ขวาอยู่ในตัว)
    _sides = list(getattr(data, "side_damage", []) or [])
    _sides += [""] * (len(data.damage) - len(_sides))
    items = list(zip(data.damage, data.type_damage, data.rank_damage, _sides))

    # ฟอร์มใหม่ (2569+) มี checklist ชิ้นส่วนสำเร็จรูป — อ่านจาก DOM (ฟอร์มเก่าคืน [])
    try:
        checklist = driver.execute_script(JS_READ_DAMAGE_CHECKLIST) or []
    except Exception as e:
        log(f"   ⚠️ อ่าน checklist ไม่ได้ ({type(e).__name__}) — ลงช่องอิสระทั้งหมด")
        checklist = []
    parts = [c.get("part", "") for c in checklist]
    if checklist:
        log(f"   พบ checklist ชิ้นส่วนสำเร็จรูป {len(checklist)} รายการ (ฟอร์มใหม่)")

    # match ชิ้นส่วนเข้า checklist (ติ๊ก checkbox) — ไม่ตรง → คิวช่องอิสระ
    used, free_items = set(), []
    for (name, _dtype, rank, side) in items:
        idx, score = _match_damage_checklist(name, parts, used)
        if idx is None:
            free_items.append((name, rank, side))
            continue
        c = checklist[idx]
        used.add(idx)
        try:
            driver.execute_script(
                "arguments[0].click();", driver.find_element(By.ID, c["cb"]))
            if c.get("has_pos"):
                driver.execute_script("arguments[0].click();", driver.find_element(
                    By.ID, c["prefix"] + "rdoDam_Left_Right_" + (side or _damage_side(name))))
            ri = _damage_rank_idx(rank)
            if ri is not None:
                driver.execute_script("arguments[0].click();", driver.find_element(
                    By.ID, c["prefix"] + "rdoDam_Lavel_" + ri))
            log(f"   ☑ checklist: {name} → {c['part']} (score {score:.0f}) rank={rank}")
        except Exception as e:
            log(f"   ⚠️ ติ๊ก checklist '{c['part']}' ไม่ได้ ({type(e).__name__}) — ช่องอิสระแทน")
            used.discard(idx)
            free_items.append((name, rank, side))

    # ที่เหลือ (ไม่ match checklist) → ช่องอิสระ dgvOtherDamage_List
    # อ่าน slot จริงจาก DOM (cmdNewReport=8 / ฟอร์ม import=20) แทน hardcode
    slots = _free_text_slots(driver)
    _clear_free_text_slots(driver, slots)
    cap = len(slots) if slots else MAX_DAMAGE_ITEMS
    if len(free_items) > cap:
        log(f"   ⚠️ ช่องอิสระมี {len(free_items)} เกิน {cap} ช่อง — "
            f"ที่เหลือต้องกรอกเองภายหลัง")
    for c, (name, rank, side) in enumerate(free_items[:cap]):
        if slots:
            prefix = slots[c]
        else:   # fallback (อ่าน slot ไม่ได้) — สูตรเดิม ctl02-05 × A/B (≤8)
            prefix = (f"dgvOtherDamage_List_ctl0{2 + (c % 4)}_"
                      f"wuOtherDamL{'A' if c < 4 else 'B'}_")

        # ⛔ ต้อง clear() ก่อนพิมพ์ — ช่องอิสระของ popup เก็บค่าเดิมไว้ พอรันซ้ำ
        #    (โหมดซ่อมรันกี่รอบก็ได้) send_keys จะ "ต่อท้าย" ชื่อเดิมเรื่อย ๆ
        #    เจอจริง 20/08/69 บน S68426084815: รัน 7 รอบ ได้ 'คิ้วกันชน' ซ้ำ 7 ครั้งติดกัน
        #    (ฝั่งคู่กรณี fill_opponent_damage clear() อยู่แล้ว — ตกหล่นเฉพาะฝั่งรถประกัน)
        el = driver.find_element(By.ID, prefix + "txtDam_Name")
        el.clear()
        el.send_keys(name)
        driver.find_element(
            By.ID, prefix + f"rdoDam_Left_Right_{side or _damage_side(name)}").click()
        ri = _damage_rank_idx(rank)
        if ri is not None:
            driver.find_element(By.ID, prefix + f"rdoDam_Lavel_{ri}").click()
        else:
            log(f"   ⚠️ ระดับความเสียหาย '{rank}' ไม่รู้จัก (รายการ: {name}) — ข้าม")
        log(f"   ✎ ช่องอิสระ [{c + 1}] {name} | side={side} | rank={rank}")

    # บันทึกหน้า popup แล้วกลับหน้าหลัก
    driver.find_element(By.ID, "btnSave").click()
    accept_alert(driver)
    time.sleep(1)
    driver.switch_to.window(main_window)

    # กดอัปเดตหน้าหลักอีกครั้งตาม workflow เดิม
    wait_clickable(driver, By.ID, "btnUpdate").click()
    accept_alert(driver)
    log("EMCS: บันทึกความเสียหายแล้ว")


# ------------------------------------------------------------------ รูปภาพ

def _dedup_images(paths):
    """กรองรูปซ้ำตามเนื้อหา (กันไฟล์ _2/_3 ที่เกิดจากการโหลดทับรอบก่อน)
    เก็บไฟล์แรกที่เจอของแต่ละเนื้อหา (list_images เรียง natural → ตัวชื่อสั้นมาก่อน)"""
    seen, out = set(), []
    for p in paths:
        try:
            h = hashlib.md5(p.read_bytes()).hexdigest()
        except OSError:
            continue
        if h in seen:
            continue
        seen.add(h)
        out.append(p)
    return out


_RENAME_SEQ = 0          # ตัวคั่นชื่อไฟล์ชั่วคราว กันชนเมื่อเรียกหลายรอบในโฟลเดอร์เดียว


def _rename_clean_files(paths, name_tmpl: str, idx: int):
    """เปลี่ยนชื่อ paths (list[Path] โฟลเดอร์เดียวกัน เรียงแล้ว) เป็น
    name_tmpl.format(i=idx, seq=ลำดับ) + นามสกุลเดิม — แพทเทิร์นเดียวกับรูปรถประกัน
    (คอลัมน์รายการใน EMCS = ชื่อไฟล์นี้). two-phase กันชนชื่อ + idempotent
    name_tmpl เช่น 'รูปรถคู่กรณีคันที่{i}_{seq}' / 'รูปผู้บาดเจ็บคนที่{i}_{seq}'"""
    if not paths:
        return []
    folder = paths[0].parent

    def _targets(ps):
        return [f"{name_tmpl.format(i=idx, seq=s)}{p.suffix.lower()}"
                for s, p in enumerate(ps, start=1)]

    targets = _targets(paths)
    # กู้ซากจากรอบก่อนที่ล้มกลางการเปลี่ยนชื่อ: เหลือแต่ __bak_X ส่วน X หายไป
    # (เจอจริง 16/08/69 เคลม -000348: 9 ไฟล์เป็น __bak_ หมด รอบถัดไปอัปรูปล้มทั้งงาน
    #  ด้วย error ของ Selenium ว่า "File not found" ซึ่งอ่านแล้วไม่รู้เลยว่าเกิดอะไรขึ้น)
    for t in targets:
        dst, bak = folder / t, folder / f"__bak_{t}"
        if bak.exists() and not dst.exists():
            bak.rename(dst)
            log(f"   ↻ กู้ไฟล์รูปที่ค้างจากรอบก่อน: {t}")

    # ไฟล์ที่หายไปจริง ๆ — ข้ามไป ไม่ล้มทั้งงานเพราะรูปเดียว
    gone = [p for p in paths if not p.exists()]
    if gone:
        log(f"   ⚠️ ไม่พบไฟล์รูป {len(gone)} ไฟล์ (ค้างจากรอบก่อน?) — ข้าม: "
            + ", ".join(p.name for p in gone[:3]))
        paths = [p for p in paths if p.exists()]
        if not paths:
            return []
        targets = _targets(paths)

    if all(p.name == t for p, t in zip(paths, targets)):
        return list(paths)                       # ชื่อถูกหมดแล้ว — ไม่แตะ
    # phase 1: ทุกไฟล์ → ชื่อชั่วคราว (กันชนกับชื่อเป้าที่ไฟล์อื่นถืออยู่)
    # ใส่ตัวคั่นเฉพาะรอบ — ถ้าฟังก์ชันนี้ถูกเรียกหลายครั้งในโฟลเดอร์เดียวกัน
    # (รูปหลายหมวดในเคสเดียว) ชื่อชั่วคราวต้องไม่ชนกันเอง
    global _RENAME_SEQ
    _RENAME_SEQ += 1
    tag = _RENAME_SEQ
    temps = []
    for j, p in enumerate(paths):
        tmp = folder / f"__tpren_{tag}_{j}{p.suffix.lower()}"
        p.rename(tmp)
        temps.append(tmp)
    # phase 2: ชั่วคราว → ชื่อเป้า (สำรองไฟล์เก่าที่บังเอิญชื่อชนไว้ก่อน)
    out = []
    for tmp, t in zip(temps, targets):
        dst = folder / t
        if dst.exists():
            dst.rename(folder / f"__bak_{t}")
        tmp.rename(dst)
        out.append(dst)
    # ต้องมีครบทุกไฟล์ก่อนส่งต่อให้ตัวอัป — ไม่งั้นไปพังตอน send_keys ด้วย error
    # ของ Selenium ที่อ่านไม่ออก และ draft ค้างครึ่ง ๆ กลาง ๆ
    missing = [p.name for p in out if not p.exists()]
    if missing:
        raise RuntimeError(
            f"เปลี่ยนชื่อรูปแล้วหาไฟล์ไม่เจอ {len(missing)} ไฟล์ ({missing[0]}) — "
            f"โฟลเดอร์ {folder.name} เพี้ยน ลบโฟลเดอร์นี้แล้วให้บอทโหลดรูปใหม่")
    return out


def _rename_opponent_files(paths, car: int):
    """(คงไว้เพื่อ backward-compat) รูปคู่กรณี → 'รูปรถคู่กรณีคันที่N_ลำดับ.jpg'"""
    return _rename_clean_files(paths, "รูปรถคู่กรณีคันที่{i}_{seq}", car)


def _tp_image_batches(folder, subdir: str, count: int, type_tmpl: str,
                      name_tmpl: str, rename: bool = True):
    """สร้างชุดอัปรูป "บุคคลที่สาม" จากโฟลเดอร์ subdir (tp_veh/tp_person/tp_prop)
    คืน list ของ (ประเภทรูป, [Path,...]) — dedup + ย้ายซ้ำเข้า _dup + เปลี่ยนชื่อสะอาด

    - count = จำนวนรายการ (คัน/คน/ชิ้น): 1 (หรือนับไม่ได้) → รวมเป็นรายการที่1;
      >1 → แยกตามโฟลเดอร์ย่อย (prefix ก่อน '_' = id ต่อราย) ถ้าได้กลุ่ม=count;
      ไม่งั้นรวมเป็นที่1 + เตือน
    - type_tmpl: ส่งให้ fuzzy_select เลือก option dynamic (โผล่หลังบันทึก section นั้น)
      เช่น 'รูปรถคู่กรณี คันที่{i}' / 'รูปผู้บาดเจ็บ คนที่{i}' / 'รูปทรัพย์สิน รายการที่{i}'
    - name_tmpl: ชื่อไฟล์สะอาดบนดิสก์ เช่น 'รูปผู้บาดเจ็บคนที่{i}_{seq}'"""
    tp = Path(folder) / subdir
    if not tp.is_dir():
        return []
    all_names = list_images(tp)
    files = _dedup_images([tp / name for name in all_names])
    if not files:
        return []

    # ย้ายรูปซ้ำ (ที่ dedup คัดออก) ไป _dup/ กันรกในโฟลเดอร์ (ไม่ลบทิ้ง)
    if rename:
        keep = {p.name for p in files}
        dropped = [tp / name for name in all_names if name not in keep]
        if dropped:
            dup_dir = tp / "_dup"
            dup_dir.mkdir(exist_ok=True)
            for d in dropped:
                dst = dup_dir / d.name
                k = 2
                while dst.exists():
                    dst = dup_dir / f"{d.stem}_{k}{d.suffix}"
                    k += 1
                d.rename(dst)
            log(f"   ย้ายรูปซ้ำ {len(dropped)} ไฟล์ → {subdir}/_dup/")

    n = max(1, int(count or 0))
    if n == 1:
        groups = {1: files}
    else:
        # หลายราย — แยกตามชื่อโฟลเดอร์ย่อย (ส่วนหน้าก่อน '_' แรก) เรียงคงที่
        raw = {}
        for p in files:
            raw.setdefault(p.name.split("_", 1)[0], []).append(p)
        if len(raw) == n:
            groups = {i: raw[k] for i, k in enumerate(sorted(raw), start=1)}
        else:
            log(f"   ⚠️ {subdir}: มี {n} รายการ แต่แยกรูปตามรายการไม่ชัด "
                f"({len(raw)} กลุ่มจากชื่อไฟล์) → รวมเป็น 'ที่1' ทั้งหมด "
                "ตรวจ/ย้ายเองบนหน้าเว็บ")
            groups = {1: files}

    batches = []
    for idx in sorted(groups):
        paths = groups[idx]               # เรียง natural อยู่แล้วจาก list_images
        if rename:
            paths = _rename_clean_files(paths, name_tmpl, idx)
        batches.append((type_tmpl.format(i=idx), paths))
    return batches


def _opponent_image_batches(folder, n_opponents: int, rename: bool = True):
    """(คงไว้เพื่อ backward-compat/tests) รูปคู่กรณี tp_veh/ → 'รูปรถคู่กรณี คันที่N'"""
    return _tp_image_batches(folder, "tp_veh", n_opponents,
                             "รูปรถคู่กรณี คันที่{i}", "รูปรถคู่กรณีคันที่{i}_{seq}",
                             rename)


def _resolve_image_type(driver, category: str) -> str:
    """เลือก "ป้ายประเภทรูป" ที่มีอยู่จริงบน ddlImage_Type_Html5 ตอนนี้

    EMCS เพิ่ม option dynamic ต่อรายการ **หลังบันทึก section นั้น** โดยใช้ป้ายฐานคนละคำ
    กับหมวดในแอป: 'รูปผู้บาดเจ็บ คนที่ N' / 'รูปทรัพย์สิน รายการที่ N' / 'รูปรถคู่กรณี คันที่ N'
    ลำดับที่ลอง: ป้าย dynamic (ผูกกับรายการที่ N) → ป้ายเต็มของแอป → ป้ายฐาน
    ไม่มีตัวไหนตรงเลย = คืนป้ายฐาน (เป็น option จริงแน่นอน) แทนที่จะปล่อยให้ fuzzy
    ไปเกาะ 'คนที่ 1' หรือข้ามฝั่งรถประกัน/คู่กรณีแบบเงียบ ๆ"""
    cat = (category or "").strip()
    try:
        opts = [o.text.strip()
                for o in Select(driver.find_element(By.ID, "ddlImage_Type_Html5")).options]
    except Exception:
        return cat
    m = re.search(r"\s*(คันที่|คนที่|ชิ้นที่|รายการที่)\s*(\d+)\s*$", cat)
    base = re.sub(r"\s*(คันที่|คนที่|ชิ้นที่|รายการที่)\s*\d+\s*$", "", cat).strip()
    cands = []
    if m:
        n = m.group(2)
        if base.startswith("รูปผู้บาดเจ็บ"):
            cands.append(f"รูปผู้บาดเจ็บ คนที่ {n}")
        elif base.startswith("รูปทรัพย์สิน"):
            cands.append(f"รูปทรัพย์สิน รายการที่ {n}")
        elif base.startswith("รูปรถคู่กรณี"):
            cands.append(f"รูปรถคู่กรณี คันที่ {n}")
    cands += [cat, base]
    for c in cands:
        if c and c in opts:
            if c != cat:
                log(f"   ↪ ประเภทรูป '{cat}' → ใช้ '{c}' (ตัวเลือกที่มีจริงบนหน้า)")
            return c
    if m and base in opts:
        return base
    if m:
        log(f"   ⚠️ ไม่มีตัวเลือก '{cat}' บนหน้า (ยังไม่บันทึก{base[3:] or 'รายการ'}?) — "
            f"ใช้ '{base}' แทน รูปจะไม่ผูกกับรายการที่ {m.group(2)}")
        return base
    return cat


def image_quota_left(driver) -> int:
    """จำนวนรูปที่ยังอัปได้ของเคลมนี้ อ่านจากป้าย lblCurr_Image บนหน้า Upload รูป
    ("คงเหลือ 66 รูป : upload ไปแล้ว 14 รูป : จากทั้งหมด 80 รูป")

    ⚠️ EMCS จำกัดจำนวนรูปต่อเคลม (เห็นจริง 80 ใบ) และโควตานี้**แชร์กับรูปที่คนอื่น
    อัปไว้ก่อนแล้ว** ถ้าส่งเกิน หน้าเว็บเด้ง JS alert
    'ไม่สามารถอัพโหลดรูปภาพได้ :: เนื่องจาก รูปที่กำลังอัพโหลดมีจำนวนมากกว่า จำนวนรูปคงเหลือ.'
    แล้ว alert ตัวนั้นจะบล็อกทุกอย่างต่อ — เดิมบอทไม่รู้จัก ไปค้างรอปุ่มปิดกล่อง 600 วิ

    คืน -1 ถ้าอ่านไม่ได้ (ให้ผู้เรียกทำต่อตามปกติ ไม่ block งาน)"""
    try:
        txt = driver.find_element(By.ID, "lblCurr_Image").text or ""
    except Exception:
        return -1
    m = re.search(r"คงเหลือ\s*(\d+)", txt)
    return int(m.group(1)) if m else -1


def _upload_one_batch(driver, paths, image_type: str, html5_ui: bool):
    """[อยู่หน้ารูปแล้ว] เลือกประเภท image_type → ส่ง paths → อัปโหลด → ปิดกล่องผล
    **ไม่ navigate** — ฟอร์มอัปโหลด (ddlImage_Type_Html5) คงอยู่บนหน้ารูปหลังอัป
    แต่ละชุด ส่วนเมนู wuMenuPage1_imbImage จะ disabled เพราะอยู่หน้านี้แล้ว
    (ห้ามกดซ้ำ — เคยทำให้ TimeoutException) → เรียกซ้ำได้หลายชุดบนหน้าเดียว

    HTML5: เลือกประเภทก่อน (input file disable จนเลือก) → ส่งทุกไฟล์เข้า input
    ตัวเดียว (multiple) รวดเดียว — UI เก่า fallback (ทีละไฟล์ + ประเภทต่อแถว)"""
    if not paths:
        return
    # โควตารูปต่อเคลมของ EMCS — ส่งเกินแล้วเว็บเด้ง alert บล็อกทุกอย่างต่อ
    # ตัดให้พอดีโควตาแทนที่จะปล่อยพัง แล้ว log ว่าตกไปกี่ใบ (ห้ามเงียบ)
    left = image_quota_left(driver)
    if left == 0:
        log(f"   ⛔ โควตารูปของเคลมนี้เต็มแล้ว — ข้ามรูป {len(paths)} ใบ (ประเภท '{image_type}')")
        return
    if 0 < left < len(paths):
        log(f"   ⚠️ โควตารูปเหลือ {left} ใบ แต่จะอัป {len(paths)} ใบ "
            f"→ อัปแค่ {left} ใบ **ตกไป {len(paths) - left} ใบ** (ประเภท '{image_type}')")
        paths = list(paths)[:left]
    log(f"EMCS: อัปโหลดรูป {len(paths)} ไฟล์ (ประเภท '{image_type}')"
        + (f" [โควตาเหลือ {left}]" if left >= 0 else ""))
    if html5_ui:
        # หน้าอาจเพิ่ง refresh จากชุดก่อน — รอ dropdown พร้อมก่อน (กัน stale)
        wait_present(driver, By.ID, "ddlImage_Type_Html5", 15)
        # 1) เลือกประเภทรูป → ระบบ enable ช่องเลือกไฟล์ให้
        fuzzy_select(driver, "ddlImage_Type_Html5",
                     _resolve_image_type(driver, image_type), label="ประเภทรูป")
        WebDriverWait(driver, 10).until(
            lambda d: d.find_element(By.ID, "selectedFile").is_enabled()
        )
        # 2) ส่งทุกไฟล์ในครั้งเดียว (input รับ multiple, คั่นด้วย \n)
        driver.find_element(By.ID, "selectedFile").send_keys(
            "\n".join(str(p) for p in paths))
        WebDriverWait(driver, 30).until(
            lambda d: "0 Files" not in d.find_element(
                By.ID, "lblFiles_Upload_Html5").get_attribute("value")
        )
        count_label = driver.find_element(
            By.ID, "lblFiles_Upload_Html5").get_attribute("value")
        log(f"   ✓ เพิ่มไฟล์แล้ว: {count_label}")
        # 3) อัปโหลด
        driver.find_element(By.ID, "btnUpload").click()
    else:
        # ----- UI เก่า: ส่งทีละไฟล์ + เลือกประเภทรูปต่อแถว -----
        wait_present(driver, By.XPATH, "//input[@type='file']", 15)
        for p in paths:
            time.sleep(0.5)
            driver.find_element(By.XPATH, "//input[@type='file']").send_keys(str(p))
            log(f"   + {p.name}")
        rows = driver.find_element(By.ID, "fileList").find_elements(
            By.XPATH, ".//table/tbody/tr"
        )
        for c in range(1, len(rows)):
            try:
                Select(driver.find_element(By.ID, f"ddlImageType{c}")
                       ).select_by_visible_text(image_type)
            except Exception:
                fuzzy_select(driver, f"ddlImageType{c}", image_type,
                             label=f"ประเภทรูปแถว {c}")
        log(f"   ✓ ตั้งประเภทรูป '{image_type}' ครบ {len(rows) - 1} แถว")
        driver.find_element(By.ID, "btnUpload").click()

    # เผื่อ EMCS เด้ง alert (เช่นเกินโควตา) — ต้องเคลียร์ก่อน ไม่งั้น wait ข้างล่างค้างยาว
    try:
        alert_txt = accept_alert(driver, timeout=3)
        if alert_txt:
            log(f"   ⚠️ EMCS แจ้ง: {alert_txt.strip()[:120]} — รูปชุดนี้อาจไม่ถูกอัป")
            return
    except Exception:
        pass
    # รออัปโหลดเสร็จ (ปุ่มปิดกล่องแจ้งผลโผล่) — เผื่อเวลาสำหรับรูปจำนวนมาก
    try:
        wait_clickable(driver, By.CLASS_NAME, "close", 600).click()
    except TimeoutException:
        log("   ⚠️ ไม่เห็นกล่องแจ้งผลอัปโหลด — ตรวจผลบนหน้าจอด้วย")
    time.sleep(2)  # ปิดกล่องแล้วหน้า refresh — พักให้นิ่งก่อนไปหน้าถัดไป


# ── 13 ประเภทรูปหลักของ EMCS (ddlImage_Type_Html5) — se-survey เก็บ category ต่อรูปตรงชุดนี้ 1:1 ──
_EMCS_IMAGE_TYPES = {
    "รูปประกอบ", "รูปแผนที่เกิดเหตุ", "รูปรถประกัน", "รูปรถคู่กรณี",
    "ใบรายงานความเสียหาย", "ใบแจ้งความเสียหาย", "ใบรับเงินจากคู่กรณี",
    "ใบขับขี่รถประกัน", "ใบขับขี่รถคู่กรณี", "ใบรายการแจ้งความ",
    "รูปผู้บาดเจ็บรถประกัน", "รูปผู้บาดเจ็บรถคู่กรณี", "รูปทรัพย์สินอื่นๆของคู่กรณี",
}
_EMCS_DEFAULT_IMAGE_TYPE = "รูปประกอบ"   # ถังรวม (v1) — รูปไม่มีหมวด/หมวดแปลก ลงที่นี่ กัน misfile


def _se_cat_to_emcs(category):
    """map หมวดรูป se-survey → ตัวเลือก 'ประเภทรูป' EMCS. se-survey เก็บป้ายไทยชุดเดียวกับ EMCS
    (1:1) → คืนตรง ๆ ให้ fuzzy_select เลือก; ว่าง/ไม่รู้จัก → 'รูปประกอบ'. รองรับหมวดคู่กรณี/
    ผู้บาดเจ็บ/ทรัพย์สินที่ต่อท้าย 'คันที่N/คนที่N/ชิ้นที่N/รายการที่N' (EMCS มีตัวเลือก dynamic)"""
    cat = (category or "").strip()
    if not cat:
        return _EMCS_DEFAULT_IMAGE_TYPE
    if cat in _EMCS_IMAGE_TYPES:
        return cat
    # หมวดของ ISURVEY เป็นรหัสอังกฤษ (INS/REPORTS/OTHERS/ACC_MAP) ไม่ใช่ป้ายไทยแบบ
    # se-survey → แปลงก่อน ไม่งั้นตกถัง 'รูปประกอบ' ทั้งกอง (เจอจริง: งานครั้งที่ 2
    # เคลม 2026013147939 รูปรถประกัน 10 ใบขึ้น EMCS ผิดหมวด ต้องลบแล้วอัปใหม่)
    if cat in ZIP_CAT_TO_EMCS:
        return ZIP_CAT_TO_EMCS[cat]
    m = re.search(r"\s*(คันที่|คนที่|ชิ้นที่|รายการที่)\s*(\d+)\s*$", cat)
    base = re.sub(r"\s*(คันที่|คนที่|ชิ้นที่|รายการที่)\s*\d+\s*$", "", cat).strip()
    # ป้าย canonical ของ EMCS เอง ('รูปผู้บาดเจ็บ คนที่ 2' / 'รูปทรัพย์สิน รายการที่ 2')
    # — มาจากเส้น zip export — ต้องผ่านได้ ไม่ใช่ถูกตีตกเป็น 'รูปประกอบ'
    if m and base in ("รูปผู้บาดเจ็บ", "รูปทรัพย์สิน"):
        return cat
    if base in _EMCS_IMAGE_TYPES:
        # ⚠️ คงป้ายเต็มของแอปไว้เสมอ — ห้ามเขียนทับเป็นป้าย dynamic ที่นี่ เพราะฟังก์ชันนี้
        # ไม่เห็น dropdown จริง ถ้า option dynamic ยังไม่โผล่ (section ยังไม่บันทึก) การทิ้ง
        # คำแยก 'รถประกัน/รถคู่กรณี' จะทำให้ fallback ตกผิดถัง — การเลือกป้าย dynamic ทำที่
        # _resolve_image_type() ตอนอัปจริง ซึ่งอ่าน option ที่มีอยู่บนหน้าได้
        return cat
    log(f"   ⚠️ หมวดรูป '{cat}' ไม่ตรงประเภท EMCS — ใช้ '{_EMCS_DEFAULT_IMAGE_TYPE}'")
    return _EMCS_DEFAULT_IMAGE_TYPE


def _group_flat_by_category(folder, file_names, fallback_type):
    """จัดกลุ่มรูปในโฟลเดอร์แบนตามหมวด (จาก _categories.json ของ se-survey) →
    [(ประเภทรูป EMCS, [Path,...]), ...] เรียงตามลำดับที่พบหมวด
    ไม่มี manifest (flow ISURVEY) → คืน None ให้ผู้เรียกใช้ batch เดี่ยวแบบเดิม (fallback_type)"""
    import json
    manifest = folder / "_categories.json"
    if not manifest.exists():
        return None
    try:
        cat_map = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"   ⚠️ อ่าน _categories.json ไม่ได้ ({e}) — อัปเป็นประเภทเดียว")
        return None
    groups = {}   # ประเภทรูป → [Path]  (dict รักษาลำดับ py3.7+)
    for name in file_names:
        etype = _se_cat_to_emcs(cat_map.get(name))
        groups.setdefault(etype, []).append(folder / name)
    # rename ชื่อไฟล์ = 'หมวด_ลำดับ' ก่อนอัป → EMCS คอลัมน์ "รายการ" โชว์หมวดจากชื่อไฟล์
    # (เหมือน flow มือถือที่ rename ฝั่ง client; เดิมอัปชื่อเดิม rn_image_picker_* = รายการเพี้ยน)
    out = []
    new_map = {}
    for etype, paths in groups.items():
        ordered = sorted(paths, key=lambda p: p.name)
        renamed = _rename_clean_files(ordered, etype + "_{seq}", 1)
        # จำหมวด "เดิม" ของแต่ละรูปไว้กับชื่อใหม่ (ดูเหตุผลด้านล่าง)
        for old, new in zip(ordered, renamed):
            cat = cat_map.get(old.name)
            if cat:
                new_map[Path(new).name] = cat
        out.append((etype, renamed))
    # ⚠️ ต้องเขียน _categories.json ใหม่ตามชื่อที่เพิ่ง rename — ไม่งั้นรอบถัดไป
    # (อัปซ้ำ/แก้หมวดแล้วอัปใหม่) จะจับคู่ชื่อไม่ได้สักรูป แล้วเทลงถัง 'รูปประกอบ'
    # ทั้งกองแบบเงียบ ๆ (เจอจริงกับงานครั้งที่ 2 เคลม 2026013147939 — ต้องลบ+อัปใหม่ 2 รอบ)
    if new_map:
        try:
            manifest.write_text(json.dumps(new_map, ensure_ascii=False),
                                encoding="utf-8")
        except Exception as e:
            log(f"   ⚠️ อัปเดต _categories.json หลัง rename ไม่ได้ ({e})")
    return out


def _replace_current_round_images(driver) -> bool:
    """ลบรูป **เฉพาะครั้งล่าสุด** ออกก่อนอัปชุดใหม่ — คืน True เมื่อครั้งนั้นว่างจริง

    ทำไมต้องมี: dedupe เทียบ **ชื่อไฟล์** อย่างเดียว และบอท rename เป็น 'หมวด_ลำดับ'
    ทุกรอบ → ชื่อชนกันโดยธรรมชาติ ผลคือ
      - ช่างถ่ายรูปใหม่แทนใบเดิม (จำนวนเท่าเดิม) → ข้ามหมด **EMCS ยังเป็นรูปเก่า**
      - ช่างเพิ่มรูป 3→4 ใบ → ลำดับขยับ ใบ 1-3 ถูกข้ามทั้งที่เนื้อเปลี่ยน → ชุดปนเก่า/ใหม่
    ทั้งสองแบบไม่มีอะไรฟ้องเลย (user ตั้งข้อสังเกตเอง 28/08/69)

    ⛔ **ลบเฉพาะ "ครั้งที่" ล่าสุดเท่านั้น** — งานครั้งที่ 2 (ติดตาม) เก็บรูปของครั้งก่อน
    ไว้ในเรื่องเดียวกัน นั่นคือหลักฐานของงานที่จบและเบิกเงินไปแล้ว ห้ามแตะข้ามครั้ง
    ⛔ ห้ามใช้กับ flow "งานต่อเนื่อง" ที่กำลังจะอัปเข้า**ครั้งใหม่** — ครั้งใหม่ยังไม่มีในตาราง
       max(ครั้งที่) จะกลายเป็นของครั้งก่อน แล้วลบผิดครั้ง (flow นั้นใช้ dedupe=False อยู่แล้ว)
    """
    try:
        rows = list_report_images(driver)
    except Exception as e:
        log(f"   ⚠️ อ่านรายการรูปไม่ได้ ({type(e).__name__}) — ข้ามการแทนที่ อัปแบบกันชื่อซ้ำแทน")
        return False
    if not rows:
        return True          # ยังไม่มีรูปเลย = ครั้งนี้ว่างอยู่แล้ว อัปได้ตรง ๆ

    def _num(v):
        try:
            return int(str(v).strip() or 0)
        except ValueError:
            return 0

    cur = max((_num(r["round"]) for r in rows), default=0)
    targets = [r for r in rows if _num(r["round"]) == cur]
    others = len(rows) - len(targets)
    log(f"EMCS: แทนที่รูปของครั้งที่ {cur} — จะลบ {len(targets)} ใบ"
        + (f" (เก็บของครั้งก่อนไว้ {others} ใบ)" if others else ""))
    try:
        _delete_image_rows(driver, targets, rows)
    except Exception as e:
        log(f"   ⚠️ ลบรูปครั้งที่ {cur} ไม่สำเร็จ ({e}) — อัปแบบกันชื่อซ้ำแทน")
        return False
    # ยืนยันว่าครั้งนี้ว่างจริง ถึงจะยอมปิด dedupe (ไม่งั้นเสี่ยงอัปซ้ำทับของเดิม)
    try:
        left = [r for r in list_report_images(driver) if _num(r["round"]) == cur]
    except Exception:
        return False
    if left:
        log(f"   ⚠️ ครั้งที่ {cur} ยังเหลือ {len(left)} ใบ — อัปแบบกันชื่อซ้ำแทน")
        return False
    return True


def upload_images(driver, folder, image_type: str = "รูปรถประกัน", only=None,
                  n_opponents: int = 0, n_injuries: int = 0, n_assets: int = 0,
                  single_type: str = "", dedupe: bool = True,
                  replace_round: bool = False):
    """อัปโหลดรูปทั้งหมด: รูปรถประกัน (หลัก) + บุคคลที่สาม (tp_veh/tp_person/tp_prop)

    - รูปรถประกัน: เลือกประเภท image_type ('รูปรถประกัน') — only คุมว่าจะอัปรูปไหน
      (None = ให้ผู้ใช้เลือกบนหน้าเว็บ / console = ทุกไฟล์; list ว่าง = ไม่อัป)
    - รูปคู่กรณี (tp_veh/) → 'รูปรถคู่กรณี คันที่N' / ผู้บาดเจ็บ (tp_person/) →
      'รูปผู้บาดเจ็บ คนที่N' / ทรัพย์สิน (tp_prop/) → 'รูปทรัพย์สิน รายการที่N'
      (option dynamic — โผล่หลังบันทึก section นั้นแล้ว ซึ่ง upload รันหลัง fill_*)
      แยกตามรายการด้วยจำนวน n_opponents/n_injuries/n_assets
    - single_type: ยัดทุกรูปลงหมวดเดียวที่ระบุ ไม่แยกตามหมวดเดิม — ใช้กับ **งานต่อเนื่อง**
      ซึ่ง EMCS หลังกด 'งานต่อเนื่อง' ทำได้แค่ใส่รูป + หน้าค่าใช้จ่าย (ตัวเลือกหมวดที่ผูกกับ
      คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินไม่มีให้เลือกในโหมดนั้น) — user เคาะ 19/08/69"""
    folder = Path(folder)
    files = list_images(folder)
    opp_batches = _opponent_image_batches(folder, n_opponents)
    inj_batches = _tp_image_batches(folder, "tp_person", n_injuries,
                                    "รูปผู้บาดเจ็บ คนที่{i}", "รูปผู้บาดเจ็บคนที่{i}_{seq}")
    asset_batches = _tp_image_batches(folder, "tp_prop", n_assets,
                                      "รูปทรัพย์สิน รายการที่{i}",
                                      "รูปทรัพย์สินรายการที่{i}_{seq}")

    if not files and not (opp_batches or inj_batches or asset_batches):
        log("EMCS: ไม่มีรูปให้อัปโหลด — ข้าม")
        return

    # รวมทุกชุดที่จะอัป (รูปหลัก + บุคคลที่สามแต่ละราย) แล้วค่อยนำทางครั้งเดียว
    batches = []   # [(ประเภทรูป, [Path,...]), ...]
    if files:
        # ให้ผู้ใช้เลือกรูปที่จะอัปโหลด (หน้าเว็บ); console/ไม่ตอบ = ทุกรูปตามเดิม
        # บอกจำนวนชุด "บุคคลที่สาม" ที่จะอัปตามไปด้วย — ชุดพวกนี้ไม่ได้อยู่ในแกลเลอรี
        # (อัปอัตโนมัติตามโฟลเดอร์ tp_veh/tp_person/tp_prop) ถ้าไม่บอก ตัวเลขบนจอ
        # จะไม่ตรงกับที่ขึ้น EMCS จริง — user ทักเอง: มี 33 ใบ แต่จอบอก 26 (2026013063304)
        if only is None:
            extra = sum(len(b[1]) for b in (opp_batches + inj_batches + asset_batches))
            only = wait_for_image_select(folder, files, extra=extra)
        if only is not None:
            chosen = set(only)
            files = [f for f in files if f in chosen]
        if files:
            grouped = _group_flat_by_category(folder, files, image_type)
            if grouped is None:
                # ไม่มี _categories.json = ไม่รู้หมวดจริง (ISURVEY / รูป se-survey ที่ไม่ถูก
                # tag หมวด เช่นรูปที่โหลด/รับมาจากภายนอก LINE/อีเมล/ระบบอื่น) → อย่าเดาว่าเป็น "รูปรถประกัน"
                # ทั้งกอง (โกหกว่าเป็นรูปรถประกัน). ใช้ถังกลาง "รูปประกอบ" + rename ชื่อสะอาด
                # (EMCS คอลัมน์ "รายการ" = ชื่อไฟล์ → กันชื่อดิบ rn_image_picker_*/S__*;
                # หัวหน้าจัดหมวดจริงบน EMCS ทีหลัง)
                flat = _rename_clean_files(
                    sorted((folder / name for name in files), key=lambda p: p.name),
                    _EMCS_DEFAULT_IMAGE_TYPE + "_{seq}", 1)
                batches.append((_EMCS_DEFAULT_IMAGE_TYPE, flat))
                log(f"EMCS: รูป {len(flat)} ไฟล์ไม่มีหมวด → อัปเป็น "
                    f"'{_EMCS_DEFAULT_IMAGE_TYPE}' + ตั้งชื่อใหม่ (หัวหน้าจัดหมวดบน EMCS)")
            else:
                # se-survey: แยกตามหมวดที่ติดมากับรูป → หลายชุด หลายประเภท
                batches.extend(grouped)
                log("EMCS: จัดกลุ่มรูปตามประเภท — " +
                    ", ".join(f"{t}×{len(p)}" for t, p in grouped))
        elif only is not None:
            log("EMCS: ผู้ใช้ไม่ได้เลือกรูปรถประกัน — ข้ามส่วนรูปรถประกัน")
    batches.extend(opp_batches)     # รูปคู่กรณี (tp_veh/)
    batches.extend(inj_batches)     # รูปผู้บาดเจ็บ (tp_person/)
    batches.extend(asset_batches)   # รูปทรัพย์สิน (tp_prop/)

    if single_type:
        merged = [p for _lbl, paths in batches for p in paths]
        batches = [(single_type, merged)] if merged else []
        if merged:
            log(f"EMCS: งานต่อเนื่อง — รวมรูป {len(merged)} ไฟล์ลงหมวดเดียว '{single_type}' "
                "(EMCS ติดป้าย 'ครั้งที่' ให้เอง)")

    if not batches:
        log("EMCS: ไม่มีรูปให้อัปโหลด — ข้าม")
        return

    # นำทางเข้าหน้ารูป "ครั้งเดียว" — หลังอัปชุดแรกเมนู imbImage จะ disabled (อยู่
    # หน้านี้แล้ว กดซ้ำ = TimeoutException) แต่ฟอร์มอัปโหลดยังอยู่ → อัปชุดถัดไป
    # บนหน้าเดิมได้เลย
    click_retry(driver, By.ID, "wuMenuPage1_imbImage")
    try:
        wait_present(driver, By.ID, "ddlImage_Type_Html5", 15)
        html5_ui = True
    except TimeoutException:
        html5_ui = False

    # ⛔ EMCS **ไม่ dedupe ชื่อไฟล์** — อัปชุดเดิมซ้ำ = รูปซ้ำทั้งกอง กินโควตา 80 ใบ
    #    และบริษัทประกันเห็นรูปซ้ำ. โหมดซ่อม (--sesurvey-fill-existing) รันกี่รอบก็ได้
    #    ตามธรรมชาติ (btnUpdate ล้มบ่อย) → เดิมรัน 3 รอบ รูป 5 ใบกลายเป็น 20 ใบ
    #    (เจอจริง 19/08/69 บน S68426084815) → ตัดใบที่แนบไว้แล้วออกก่อนเสมอ
    #    dedupe=False เฉพาะ "งานต่อเนื่อง" ซึ่งตั้งใจอัปไฟล์ชื่อเดิมซ้ำเข้าครั้งใหม่
    # แทนที่รูปของครั้งนี้: ลบให้ว่างก่อน แล้วอัปทั้งชุด (ไม่ต้อง dedupe เพราะเพิ่งล้าง
    # — และ **ต้องไม่ dedupe** ด้วย ไม่งั้นชื่อที่ชนกับ "ครั้งก่อน" จะทำให้ข้ามรูปครั้งนี้ทิ้ง)
    if replace_round and _replace_current_round_images(driver):
        dedupe = False

    if dedupe:
        # ⚠️ EMCS เก็บชื่อไฟล์แบบ "ตัดช่องว่างทิ้ง" — ไฟล์ 'รูปรถคู่กรณี คันที่ 1_1.jpg'
        #    ขึ้นในตารางเป็น 'รูปรถคู่กรณีคันที่1_1.jpg' → เทียบชื่อดิบจะไม่มีวันตรง
        #    (รอบแรกของ dedupe จับได้แค่ชื่อที่ไม่มีช่องว่าง แล้วอัปรูปคู่กรณีซ้ำอีก)
        _key = lambda n: "".join(str(n).split())      # noqa: E731
        try:
            attached = {_key(r["name"]) for r in list_report_images(driver)}
        except Exception as e:
            log(f"   ⚠️ อ่านรายการรูปที่แนบไว้ไม่ได้ ({type(e).__name__}) — อัปตามปกติ")
            attached = set()
        if attached:
            kept, skipped = [], 0
            for label, paths in batches:
                keep = [p for p in paths if _key(p.name) not in attached]
                skipped += len(paths) - len(keep)
                if keep:
                    kept.append((label, keep))
            if skipped:
                log(f"   ↩ ข้ามรูปที่แนบไว้แล้ว {skipped} ใบ (กันรูปซ้ำ)")
            batches = kept
            if not batches:
                log("EMCS: รูปทุกใบแนบไว้แล้ว — ไม่ต้องอัปซ้ำ")
                return

    for label, paths in batches:
        _upload_one_batch(driver, paths, label, html5_ui)

    log("EMCS: อัปโหลดรูปเสร็จ")


def list_report_images(driver) -> list:
    """อ่านตารางรูปที่แนบไว้แล้ว (#dgvImageList) — ต้องอยู่หน้ารูป (frmImageUpload) แล้ว

    1 แถว = 10 คอลัมน์: ลำดับ | checkbox | รายการ(ชื่อไฟล์) | ดูรูป | ครั้งที่ |
    สถานะ(ประเภทรูป) | IMAGEID | MEM_TYPE | วันที่แนบรูป | ผู้แนบรูป
    IMAGEID/MEM_TYPE ซ่อนด้วย .txtHide → ต้องอ่าน textContent ไม่ใช่ .text"""
    def _txt(el):
        return " ".join((el.get_attribute("textContent") or "").split())

    out = []
    for tr in driver.find_elements(By.CSS_SELECTOR, "#dgvImageList tr"):
        tds = tr.find_elements(By.TAG_NAME, "td")
        if len(tds) < 6:
            continue                      # แถวหัวตาราง (th) / แถว pager
        chk = tds[1].find_elements(By.CSS_SELECTOR, "input[type=checkbox]")
        if not chk:
            continue                      # แถวหัวตารางที่ใช้ td (chkAll) ถูกกรองด้วย id ข้างล่าง
        cid = chk[0].get_attribute("id") or ""
        if cid.endswith("_chkAll"):
            continue                      # ⛔ ห้ามแตะ "ติ๊กทั้งหมด"
        out.append({
            "seq": _txt(tds[0]),
            "chk": chk[0],
            "chk_id": cid,
            "name": _txt(tds[2]),                       # คอลัมน์ "รายการ" = ชื่อไฟล์
            "round": _txt(tds[4]) if len(tds) > 4 else "",
            "type": _txt(tds[5]) if len(tds) > 5 else "",   # คอลัมน์ "สถานะ" = ประเภทรูป
            "image_id": _txt(tds[6]) if len(tds) > 6 else "",
            "added": _txt(tds[8]) if len(tds) > 8 else "",
        })
    return out


def delete_report_images(driver, names) -> list:
    """ลบรูปที่แนบไว้แล้ว ตาม "ชื่อไฟล์เป๊ะ ๆ" (คอลัมน์ รายการ) — ต้องอยู่หน้ารูปแล้ว

    ใช้ตอนมีรูปหลุดขึ้น EMCS ไปแล้ว (เช่นรูปยืนยันถึงที่เกิดเหตุก่อน fix 727411f)
    EMCS ไม่มีปุ่มลบรายแถว มีแต่ "ติ๊กแล้วกดลบ" → ต้องระวังเป็นพิเศษ

    guard (ล้มทั้งชุดถ้าไม่ผ่าน — ยอมไม่ลบ ดีกว่าลบผิดใบ):
    - ทุกชื่อต้องเจอ "พอดี 1 แถว" (0 หรือ >1 = หยุด)
    - ติ๊กเฉพาะแถวเป้าหมาย + ตรวจซ้ำว่าที่ติ๊กจริงตรงเป๊ะ ก่อนกดลบ
    - ห้ามแตะ chkAll (list_report_images กรองออกแล้ว)
    - ลบเสร็จอ่านตารางใหม่ ยืนยันว่าหายเฉพาะเป้าหมาย ใบอื่นครบเท่าเดิม
    คืน list ของแถวที่ลบไป"""
    want = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not want:
        raise RuntimeError("delete_report_images: ไม่ได้ระบุชื่อไฟล์ที่จะลบ")

    before = list_report_images(driver)
    if not before:
        raise RuntimeError("ไม่พบตารางรูป (#dgvImageList) หรือเรื่องนี้ยังไม่มีรูปแนบ")

    targets = []
    for n in want:
        hit = [r for r in before if r["name"] == n]
        if len(hit) != 1:
            raise RuntimeError(
                f"หยุด: ชื่อไฟล์ '{n}' เจอ {len(hit)} แถว (ต้องเจอพอดี 1) — "
                f"รูปในเรื่องนี้: {[r['name'] for r in before]}")
        targets.append(hit[0])
    return _delete_image_rows(driver, targets, before)


def _delete_image_rows(driver, targets, before=None) -> list:
    """แกนของการลบรูป — รับ "แถว" ที่เลือกมาแล้ว (จาก list_report_images) ไม่ใช่ชื่อไฟล์

    แยกออกมาเพื่อให้โหมด "แทนที่รูปของครั้งนี้" ใช้ร่วมได้ — โหมดนั้นเลือกแถวด้วย
    **ครั้งที่** ซึ่งชื่อไฟล์ซ้ำข้ามครั้งได้ (บอท rename เป็น 'หมวด_ลำดับ' ทุกรอบ)
    จึงระบุตัวด้วยชื่อไม่ได้

    ⛔ ตรวจผลด้วย **IMAGEID** (ไม่ซ้ำ) ไม่ใช่ชื่อไฟล์ — เทียบด้วยชื่อจะสับสนเมื่อ
    ครั้งก่อนมีไฟล์ชื่อเดียวกันอยู่ แล้วสรุปผิดว่า "ลบไม่ตรงที่สั่ง"
    """
    if not targets:
        return []
    if before is None:
        before = list_report_images(driver)

    def _id(r):
        return r.get("image_id") or r.get("name")

    for r in targets:
        log(f"   จะลบ: [{r['seq']}] {r['name']}  (ประเภท '{r['type']}', "
            f"IMAGEID {r['image_id']}, แนบ {r['added']})")
        if not r["chk"].is_selected():
            r["chk"].click()

    # ตรวจซ้ำหน้างาน: ที่ติ๊กอยู่จริง ต้องเท่ากับเป้าหมายเป๊ะ ๆ (เทียบด้วย IMAGEID)
    ticked = [_id(r) for r in list_report_images(driver) if r["chk"].is_selected()]
    if sorted(ticked) != sorted(_id(r) for r in targets):
        raise RuntimeError(
            f"หยุด: ติ๊กไม่ตรงเป้า — ติ๊กอยู่ {ticked} แต่ตั้งใจลบ "
            f"{[_id(r) for r in targets]}")

    log(f"EMCS: ลบรูป {len(targets)} ใบ (จากทั้งหมด {len(before)} ใบ)")
    # ปุ่มลบตัวจริง (submit id=btnDelete_Image) อยู่ในตาราง #oldBtn ที่ display:none →
    # Selenium คลิกไม่ได้. ปุ่มที่เห็นคือ btnDelete_Image2 ซึ่ง jQuery ผูกไว้ให้ยิง
    # $("#btnDelete_Image").click() ต่อ (= ผ่าน handler confirm แล้ว submit จริง)
    btn = next((b for b in (driver.find_elements(By.ID, "btnDelete_Image2")
                            + driver.find_elements(By.ID, "btnDelete_Image"))
                if b.is_displayed()), None)
    if btn is None:
        raise RuntimeError(
            "ไม่พบปุ่มลบรูปที่กดได้ (btnDelete_Image2/btnDelete_Image ถูกซ่อนทั้งคู่) — "
            "มักแปลว่าเรื่องนี้ส่งงานแล้ว/หน้าเป็นอ่านอย่างเดียว ลบไม่ได้")
    btn.click()
    accept_alert(driver)                            # confirm("คุณต้องการลบรูปภาพที่เลือกไว้ใช่หรือไม่?")
    time.sleep(2)
    # ⚠️ EMCS เด้ง alert **ใบที่สอง** ตามมาหลัง postback ("บันทึกการลบรายการภาพเรียบร้อยแล้ว")
    #    ถ้าไม่ปิด คำสั่ง Selenium ถัดไปโยน UnexpectedAlertPresentException ทั้งที่ลบสำเร็จแล้ว
    #    → เดิมสคริปต์ล้มหลังลบเสร็จ ทำให้ดูเหมือนลบไม่สำเร็จ (เจอจริง 19/08/69)
    try:
        log("   [alert] " + accept_alert(driver, timeout=10))
    except Exception:
        pass

    # หลัง postback ตารางถูก render ใหม่ — อ่านเร็วไปจะเจอ StaleElementReference/แถวยังไม่ครบ
    # ⚠️ "ตารางว่าง" มี 2 ความหมาย: อ่านไม่ทัน (ผิด) กับ ลบหมดจริง ๆ (ถูก) —
    #    โหมดแทนที่รูปของครั้งนี้ลบ *ทุกแถว* จึงจบที่ตารางว่างเป็นปกติ ต้องแยกให้ออก
    #    ไม่งั้นรายงานว่า "ลบไม่สำเร็จ" ทั้งที่ลบถูกครบ (เจอจริง 28/08/69 บน S68426084815)
    deleting_all = len(targets) == len(before)
    after = None
    for attempt in range(5):
        try:
            after = list_report_images(driver)
            # ลบหมด = รอจนตารางว่างจริง / ลบบางส่วน = รอจนอ่านได้อย่างน้อย 1 แถว
            if (not after) if deleting_all else bool(after):
                break
        except (StaleElementReferenceException, UnexpectedAlertPresentException):
            try:
                accept_alert(driver, timeout=5)
            except Exception:
                pass
        time.sleep(1)
    # ตารางว่างจะเชื่อได้ ต่อเมื่อยังอยู่หน้ารูปจริง (ปุ่มลบยังอยู่) — กันกรณีหลุดไปหน้าอื่น
    # แล้วอ่านได้ [] ซึ่งจะแปลว่า "ลบหมดแล้ว" ทั้งที่รูปยังอยู่ครบ
    still_on_page = bool(driver.find_elements(By.ID, "btnDelete_Image2")
                         or driver.find_elements(By.ID, "btnDelete_Image"))
    if after is None or (not after and not (deleting_all and still_on_page)):
        raise RuntimeError(
            "ลบไปแล้วแต่อ่านตารางรูปหลังลบไม่ได้ — เปิด EMCS ตรวจด้วยตาว่าลบถูกใบไหม")
    gone = {_id(r) for r in before} - {_id(r) for r in after}
    want_set = {_id(r) for r in targets}
    if gone != want_set:
        raise RuntimeError(
            f"⚠️ ผลลบไม่ตรงที่สั่ง: หายไป {sorted(gone)} แต่สั่งลบ {sorted(want_set)} "
            f"(ก่อน {len(before)} → หลัง {len(after)} ใบ) — ตรวจ EMCS ด้วยมือทันที")
    log(f"✓ ลบเรียบร้อย — เหลือรูป {len(after)} ใบ")
    return targets


def _pick_draft_report(reports, esurvey: str = "") -> str:
    """เลือกเรื่อง (เลข e-Survey) ที่จะเติมรูป จากผลค้น find_existing_reports
    - ระบุ esurvey มา → ใช้ตามนั้น (เตือนถ้าไม่อยู่ในผลค้น แต่ยังลองตามที่ระบุ)
    - ไม่ระบุ → เลือกเรื่องที่เป็น draft ('รายงานสร้างใหม่' ในข้อความแถว):
      draft เดียว = ใช้เลย / หลาย draft = ตัวแรก + เตือน /
      ไม่มี draft ชัดเจน = เรื่องเดียวใช้เลย, หลายเรื่อง = ต้องระบุ --esurvey"""
    esurvey = (esurvey or "").strip()
    if esurvey:
        if not any(r.get("esurvey") == esurvey for r in reports):
            log(f"   ⚠️ ระบุ {esurvey} แต่ไม่พบในผลค้น — ลองใช้ตามที่ระบุ")
        return esurvey

    def _is_draft(r):
        return any(s in (r.get("row") or "") for s in DRAFT_STATUSES)

    drafts = [r for r in reports if _is_draft(r)]
    if len(drafts) == 1:
        return drafts[0]["esurvey"]
    lines = "\n".join(f"   - {r['esurvey']}  {r['row'][:90]}"
                      for r in (drafts or reports))
    if len(drafts) > 1:
        log(f"   ⚠️ มี draft {len(drafts)} เรื่อง — เลือกเรื่องแรก "
            f"({drafts[0]['esurvey']}); ระบุ --esurvey ถ้าต้องการเจาะจง\n{lines}")
        return drafts[0]["esurvey"]
    if len(reports) == 1:
        return reports[0]["esurvey"]
    raise RuntimeError(
        "เลือกเรื่อง draft ที่จะเติมรูปไม่ได้ (สถานะไม่ชี้ชัด/หลายเรื่อง) — "
        f"ระบุเลขด้วย --esurvey จากรายการนี้:\n{lines}")


def open_report_images(driver, claim: str, esurvey: str):
    """ค้นเลขเคลม (ให้ลิงก์ e-Survey โผล่บนหน้า MainPage) → คลิกลิงก์เปิดเรื่อง →
    รอเมนู 'รูปประกอบ' (wuMenuPage1_imbImage) พร้อม (upload_images จะกดเมนูเอง)"""
    find_existing_reports(driver, claim)
    link = wait_present(driver, By.XPATH, f"//a[normalize-space(text())='{esurvey}']", 20)
    # EMCS ล็อกเรื่องที่กำลังถูกเปิด/แก้ไข → render ลิงก์ e-Survey เป็น disabled (คลิกไม่ทำงาน)
    # เช็คก่อนคลิก เพื่อขึ้น error ชัดเจน แทนที่จะ timeout ลึก ๆ ที่ wuMenuPage1_imbImage
    if link.get_attribute("disabled") is not None or "not-allowed" in (link.get_attribute("style") or ""):
        raise RuntimeError(
            f"เรื่อง {esurvey} ถูกล็อกใน EMCS (ลิงก์ e-Survey ถูก disable = มี session เปิด/แก้ไขค้างอยู่). "
            "รอ lock ปลด (มัก timeout นาน/อาจต้องให้แอดมิน EMCS ปลด) หรือปิด session ที่เปิดเรื่องนี้ค้าง "
            "ให้เรียบร้อยก่อน แล้วลองใหม่ — ห้ามเปิดเรื่องนี้ค้างในเบราว์เซอร์อื่นระหว่างรันบอท")
    wait_clickable(driver, By.XPATH, f"//a[normalize-space(text())='{esurvey}']", 20).click()
    wait_present(driver, By.ID, "wuMenuPage1_imbImage", 20)


def add_images_only(driver, cfg, data: ClaimData, images_folder,
                    image_type: str = "รูปรถประกัน", include_main: bool = False,
                    esurvey: str = "") -> str:
    """เติมรูปเข้า 'เรื่องเดิม' (draft) ที่มีอยู่แล้ว โดยไม่สร้างเรื่องใหม่/ไม่แตะ
    ข้อมูลทั่วไป/คู่กรณี/ความเสียหาย/ค่าใช้จ่าย — ใช้ตอนกรอกเรื่อง+อัปรูปรถประกัน
    ไปแล้ว เหลือเติมรูปรถคู่กรณี

    - login EMCS → ค้นเรื่องเดิมของเคลม → เลือก draft → เปิด → หน้ารูป → อัปโหลด
    - include_main=False (ปกติ): อัปเฉพาะรูปรถคู่กรณี (tp_veh/) ส่ง only=[] ข้าม
      รูปรถประกัน (กันอัปซ้ำที่อัปไปแล้ว) / True: อัปรูปรถประกันด้วย (มีให้เลือกตามปกติ)
    คืนเลข e-Survey ของเรื่องที่เติมรูป"""
    login(driver, cfg)
    reports = find_existing_reports(driver, data.claim_value)
    if not reports:
        raise RuntimeError(
            f"ไม่พบเรื่องเดิมของเคลม {data.claim_value} ใน EMCS — ยังไม่มี draft "
            "ให้เติมรูป (สร้างเรื่องก่อนด้วย flow ปกติ)")
    target = _pick_draft_report(reports, esurvey)
    log(f"EMCS: เปิดเรื่องเดิม {target} เพื่อเติมรูป "
        f"({'รูปรถประกัน+บุคคลที่สาม' if include_main else 'เฉพาะรูปบุคคลที่สาม'})")
    open_report_images(driver, data.claim_value, target)
    # ซ่อมรูปของเรื่องเดิม = ครั้งเดิม → แทนที่ทั้งครั้ง (ดู _replace_current_round_images)
    upload_images(driver, images_folder, image_type=image_type,
                  only=(None if include_main else []),
                  n_opponents=len(data.third_parties or []),
                  n_injuries=len(data.injuries or []),
                  n_assets=len(data.assets or []),
                  replace_round=True)
    return target


# ------------------------------------------------------------------ ค่าใช้จ่าย

def _money(value) -> float:
    """แปลงข้อความจำนวนเงินจาก XML เป็นตัวเลข ('300.00' → 300.0, ว่าง → 0)"""
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _type_fee(driver, elem_id: str, value, label: str):
    """พิมพ์ค่าลงช่องราคา แล้วกด Tab ให้ JS ของหน้าคำนวณยอดรวม

    ใช้ send_keys ตรง ๆ (ไม่ผ่าน set_text) เพราะต้องกด Tab ให้หน้าคำนวณ →
    ต้อง record_filled เองด้วย ไม่งั้น **ตัวเลขเงินจะเป็นส่วนเดียวของหน้านี้ที่ไม่มีใครตรวจ**
    (_cmp_value ผ่อนรูปแบบตัวเลขให้อยู่แล้ว: 500 = 500.00 = 1,000.00)"""
    el = driver.find_element(By.ID, elem_id)
    el.clear()
    el.send_keys(str(value), Keys.TAB)
    record_filled(elem_id, value)
    log(f"   ✓ {label} = {value}")


def fill_fee_table(driver, bill: dict):
    """กรอกตารางราคาค่าสำรวจ "เฉพาะช่องเสนอ" จากข้อมูล XML ของ ISURVEY
    (ช่องอนุมัติ txtIns_* ถูก disable ไว้สำหรับฝั่งบริษัทประกัน — ไม่แตะ)
    กรอกเฉพาะรายการที่มีค่า > 0 และกด Tab ให้ระบบคำนวณยอดรวมเอง"""
    if not bill:
        log("   ไม่มีข้อมูลค่าสำรวจจาก XML — กรอกตารางราคาเองบนหน้าจอ")
        return

    log("EMCS: กรอกตารางราคา (ช่องเสนอ)")
    filled = 0

    # ค่าบริการ: จำนวน × ราคาต่อหน่วย (จำนวน default 1 ถ้า XML ไม่ระบุ)
    invest = _money(bill.get("invest"))
    if invest > 0:
        n = int(_money(bill.get("invest_num"))) or 1
        _type_fee(driver, "txtNum_Investigate", n, "ค่าบริการ (จำนวน)")
        _type_fee(driver, "txtInvestigate_UnitPrice", f"{invest:g}",
                  "ค่าบริการ (เสนอ)")
        filled += 1

    # ค่าเดินทาง/ค่าพาหนะ
    trans = _money(bill.get("trans"))
    if trans > 0:
        n = int(_money(bill.get("trans_num"))) or 1
        _type_fee(driver, "txtNum_Transport", n, "ค่าเดินทาง (จำนวน)")
        _type_fee(driver, "txtTransport_UnitPrice", f"{trans:g}",
                  "ค่าเดินทาง (เสนอ)")
        filled += 1

    # ค่ารูปถ่าย: XML ให้ยอดรวม+จำนวนรูป → หน้าเว็บต้องการราคาต่อรูป
    photo_total = _money(bill.get("photo"))
    photo_num = int(_money(bill.get("photo_num")))
    if photo_total > 0:
        n = photo_num or 1
        unit = round(photo_total / n, 2)
        # ⚠️ ยอดรวมคอลัมน์ "จำนวนเงินเสนอ" ของ EMCS คิดแถวค่ารูปถ่ายเป็น *ราคา/หน่วย*
        #    ไม่ใช่ยอดของแถว (5×50 → รวมขึ้น 750 แทน 950) — ทดสอบสลับลำดับกรอก
        #    (ราคาก่อน/จำนวนก่อน) แล้ว **ได้ 750 เท่ากันทั้งสองแบบ = ไม่ใช่ปัญหาลำดับ**
        #    ตัวแถวเอง (txtPhoto_Price) · คอลัมน์ "อนุมัติ" · ยอดสะสม ถูกต้องหมด
        #    → เป็นอาการฝั่ง EMCS แก้จากเราไม่ได้ อย่าเสียเวลาสลับลำดับซ้ำ (19/08/69)
        _type_fee(driver, "txtNum_Photo", n, "ค่ารูปถ่าย (จำนวนรูป)")
        _type_fee(driver, "txtPhoto_UnitPrice", f"{unit:g}",
                  f"ค่ารูปถ่าย (เสนอ/รูป จากยอดรวม {photo_total:g})")
        filled += 1

    # รายการเดี่ยว
    singles = [
        ("tel", "txtSur_Tel", "ค่าโทรศัพท์ (เสนอ)"),
        ("insure", "txtSur_Insure", "ค่าประกัน (เสนอ)"),
        ("claim", "txtSur_Claim", "ค่าเคลม (เสนอ)"),
        ("claim_percent", "txtSur_Percent_Claim", "%% ค่าเคลม"),
        ("daily", "txtSur_Daily", "ค่าคัดประจำวัน (เสนอ)"),
    ]
    for key, elem_id, label in singles:
        val = _money(bill.get(key))
        if val > 0:
            _type_fee(driver, elem_id, f"{val:g}", label)
            filled += 1

    # ค่าใช้จ่ายอื่นๆ (มีช่องคำอธิบายคู่กัน)
    other = _money(bill.get("other"))
    if other > 0:
        desc = bill.get("other_desc", "").strip()
        if desc:
            _type_fee(driver, "txtOther_Desc", desc, "อื่นๆ (รายละเอียด)")
        _type_fee(driver, "txtOther_UnitPrice", f"{other:g}", "อื่นๆ (เสนอ)")
        filled += 1

    # รายการที่หน้า Debit Note ยังไม่มีช่อง map ตรง — เตือนให้กรอกเอง
    for key, name in (("dist", "ค่าระยะทาง"), ("cartow", "ค่ายกลาก")):
        val = _money(bill.get(key))
        if val > 0:
            log(f"   ⚠️ {name} = {val:g} ยังไม่รองรับกรอกอัตโนมัติ — เติมเองด้วย")

    if filled == 0:
        log("   ค่าสำรวจทุกรายการเป็น 0 — ไม่มีอะไรต้องกรอก")
    time.sleep(1)  # ให้ JS คำนวณยอดรวมจบก่อนไปกดบันทึก


# หาปุ่ม "บันทึก" บนหน้าโดยกันคำว่า "ส่งงาน" เด็ดขาด (ส่งงาน = commit จริง
# ที่ต้องเป็นคนกดเองเสมอ)
_JS_FIND_SAVE_BUTTON = r"""
const out = [];
document.querySelectorAll(
  "input[type=button], input[type=submit], input[type=image], button, a"
).forEach(e => {
  const txt = (e.value || e.innerText || e.title || "").trim();
  if (!txt || txt.length > 30) return;
  if (txt.includes("ส่งงาน")) return;          // ห้ามแตะปุ่มส่งงานเด็ดขาด
  if (e.offsetParent === null) return;          // เอาเฉพาะที่มองเห็น
  if (txt === "บันทึก" || txt === "บันทึกข้อมูล" || e.id === "btnSave") {
    out.push(e.id || "");
  }
});
return out;
"""


# ปุ่ม "บันทึกราคา" ของหน้าค่าใช้จ่าย — **มี 2 id หน้าหนึ่ง render ตัวเดียว**
#   `btnSurveySave`   title='Survey บันทึก'  = ใบแจ้งหนี้ครั้งนี้ยังไม่เคยถูกบันทึก
#   `btnSurvey_Update` title='Survey แก้ไข'  = เคยบันทึกแล้ว (เปิดมาแก้)
# ตัวชี้ขาด = "บิลครั้งนี้เคยบันทึกหรือยัง" — **ไม่ใช่ `hifPostStatus`**
# พิสูจน์จากเรื่องเดียวกัน (S68426080392) 2026-08-03:
#   21:31 ก่อนบันทึกบิล → hifPostStatus=1 + btnSurveySave
#   22:36 หลังบันทึกบิล → hifPostStatus=1 + btnSurvey_Update   ← status เท่าเดิม ปุ่มเปลี่ยน
# (ตัวอย่างที่ 3: เคส 058298 ส่งงานแล้ว = status 2 + btnSurvey_Update)
# ยังไม่ได้พิสูจน์เงื่อนไขเป๊ะทุกกรณี (3 ตัวอย่าง) — วิธีที่ปลอดภัยคือ "ลองทั้งคู่" เสมอ
# ⚠️ กับดักที่พลาดมา 2 รอบในเรื่องเดียวกัน: (1) ตรวจหน้าเดียวแล้วสรุปว่า "ไม่มี btnSurveySave
# อยู่จริง" → บอทหาปุ่มไม่เจอทุก draft ใหม่ (2) เจอ 2 หน้าที่ต่างกัน 2 ตัวแปรพร้อมกัน
# แล้วชี้ผิดตัวว่า hifPostStatus เป็นเหตุ — ทั้งคู่คือ "สรุปจากตัวอย่างน้อยเกินไป"
_PRICE_SAVE_BUTTONS = ("btnSurveySave", "btnSurvey_Update")


def _find_price_save_button(driver):
    """หาปุ่ม 'บันทึกราคา' ที่ถูก render จริงบนหน้า — คืน (element, id) หรือ (None, '')
    ปุ่มที่มีคำว่า 'ส่งงาน' ถือว่าไม่ใช่ (กันกดส่งงานพลาด) — ข้ามไปตัวถัดไป"""
    for eid in _PRICE_SAVE_BUTTONS:
        try:
            el = driver.find_element(By.ID, eid)
            if not el.is_displayed():
                continue
            txt = (el.get_attribute("value") or el.text or "").strip()
            if "ส่งงาน" in txt:
                log(f"   ⛔ {eid} มีข้อความ 'ส่งงาน' ({txt!r}) — ข้าม (กันกดส่งงานพลาด)")
                continue
            return el, eid
        except Exception:
            pass
    return None, ""


def leave_report(driver) -> bool:
    """ออกจากเรื่อง (กลับหน้า Inbox/Outbox) = ปลดล็อกให้คนอื่นเปิดต่อได้

    ⚠️ **จังหวะสำคัญ** — EMCS ล็อกเรื่องไว้ระหว่างที่มีคนเปิดค้าง ต้องออกถึงจะปลด
    แต่ปุ่ม 'ส่งงานใหม่' อยู่ในเรื่อง **ออกก่อน = หาปุ่มไม่เจอ**
    (เจอจริง 2026013058422 + 2026013159949: บอทออกทันทีหลังบันทึกราคา แล้วค่อยหยุด
     รอคนสั่งส่ง → พอสั่ง ปุ่มไม่อยู่บนหน้า) → เรียกตัวนี้ **หลังรู้ผลแล้วว่าไม่ส่ง**
    (ส่งสำเร็จ EMCS พาออกเอง) คืน True เมื่อออกสำเร็จ"""
    try:
        click_retry(driver, By.ID, "wuMenuPage1_imbReturn_In_Out")
        try:
            accept_alert(driver, timeout=10)   # เผื่อมี confirm/alert ตอนออกจากเรื่อง
        except Exception:
            pass
        log("EMCS: กลับหน้า Inbox/Outbox แล้ว — ออกจากเรื่อง ปลดล็อก (คนอื่นเปิดต่อได้)")
        return True
    except Exception as e:
        log(f"   ⚠️ กดปุ่มกลับ Inbox/Outbox (wuMenuPage1_imbReturn_In_Out) ไม่ได้ "
            f"({type(e).__name__}) — กดกลับ/ออกจากเรื่องเองเพื่อปลดล็อก")
        return False


def _save_and_exit_billing(driver, leave: bool = True, data=None):
    """โหมด draft-park (se-survey ⚡ นำเข้า / เติม draft เดิม): บันทึกหัวบิล
    (เลขที่ใบแจ้งหนี้ + วันที่วางบิล) ด้วยปุ่ม 'บันทึกราคา' — id ต่างกันตามสถานะงาน
    (`btnSurveySave` draft ใหม่ / `btnSurvey_Update` เปิดมาแก้, ดู _PRICE_SAVE_BUTTONS)
    เป็นปุ่มบันทึกเดียวของหน้า จึงเลี่ยงไม่ได้ถ้าอยากให้หัวบิลติด
    (user อนุญาตให้กดแล้ว 2026-07-27)
    leave=True → ออกจากเรื่องต่อเลย (ปลดล็อก) · leave=False → **ค้างอยู่ในเรื่อง**
    เพราะยังจะต้องใช้ปุ่ม 'ส่งงานใหม่' ที่อยู่ในหน้านี้ (ดู leave_report)
    ⛔ ไม่แตะ 'ส่งงานใหม่' (wuFlow1_cmdSendNew) เด็ดขาด
    บันทึกไม่สำเร็จ = ไม่กดกลับ (กันข้อมูลหาย + ปล่อยให้คนตรวจบนหน้าจอ)."""
    # (1) บันทึกหัวบิล (เลขที่ใบแจ้งหนี้ + วันที่วางบิล) — verify ปุ่มไม่ใช่ 'ส่งงาน' ก่อนกด
    #     (กันกดส่งงานพลาด ตามวินัยเดียวกับตัวหาปุ่ม 'บันทึก' ที่กันคำว่า 'ส่งงาน')
    try:
        # รอให้หน้าโหลดปุ่มก่อน (postback ก่อนหน้าอาจยังไม่จบ) แล้วค่อยเลือก id ที่มีจริง
        try:
            wait_clickable(driver, By.CSS_SELECTOR,
                           "#btnSurveySave, #btnSurvey_Update", 15)
        except Exception:
            pass
        btn, eid = _find_price_save_button(driver)
        if btn is None:
            ids = "/".join(_PRICE_SAVE_BUTTONS)
            log(f"   ⚠️ ไม่พบปุ่มบันทึกราคา ({ids}) บนหน้า — "
                "บันทึก + ออกจากเรื่องเองบนหน้าจอ (ยังไม่กดกลับ Inbox กันข้อมูลหาย)")
            return
        status = _field_value(driver, "hifPostStatus") or "?"
        btn.click()
        try:
            # ยืนยันจริงจาก eclaim3: ขึ้น alert 'บันทึกการแก้ไขเรียบร้อยแล้ว' → accept_alert กด 'ตกลง'
            accept_alert(driver, timeout=10)
        except Exception:
            pass
        log(f"EMCS: บันทึกหน้าค่าใช้จ่าย ({eid}, hifPostStatus={status}) ✅")
    except Exception as e:
        log(f"   ⚠️ กดปุ่มบันทึกราคาไม่ได้ ({type(e).__name__}) — "
            "บันทึก + ออกจากเรื่องเองบนหน้าจอ (ยังไม่กดกลับ Inbox กันข้อมูลหาย)")
        return
    # (1.5) อ่านค่ากลับมาเทียบ **ขณะยังอยู่หน้านี้** — ออกจากเรื่องไปแล้วอ่านไม่ได้
    #    เดิมหน้านี้ไม่เคยถูกตรวจเลย (ตรวจแต่หน้าหลัก) ทั้งที่ 3 ช่องความเห็นอยู่ในชุด
    #    ที่ EMCS กลืนอักขระ + ตารางราคาคือตัวเลขเงิน · ที่ log '✓ ผลการดำเนินงาน = ...'
    #    ไว้ก่อนหน้าเป็นการอ่าน **ก่อนกดบันทึก** จับของที่หายตอนบันทึกไม่ได้
    if data is not None:
        _verify_after_save(driver, data, "หน้าค่าใช้จ่าย", reset=False)
    # (2) กลับหน้า Inbox/Outbox = ออกจากเรื่องที่ทำเสร็จ → ปลดล็อกให้คนอื่นเข้าต่อได้
    #     แต่ถ้ายังจะเสนอให้คนสั่งส่ง ต้อง **ค้างอยู่ในเรื่อง** ไม่งั้นหาปุ่มส่งไม่เจอ
    if leave:
        leave_report(driver)
    else:
        log("EMCS: ค้างอยู่ในเรื่องไว้ก่อน (ปุ่ม 'ส่งงานใหม่' อยู่ในหน้านี้) — "
            "ออกจากเรื่องหลังรู้ผลว่าจะส่งหรือไม่ส่ง")


def fill_billing(driver, data: ClaimData, full_billing: bool = True,
                 navigate: bool = True, leave: bool = True):
    """หน้าค่าใช้จ่าย — **กรอกมาก/น้อยขึ้นกับต้นทางข้อมูล** (กติกา user 2026-08-03)
    แล้วกด "บันทึกราคา" (เป็น draft แก้ได้ — จุดส่งงานจริงคือปุ่ม 'ส่งงานใหม่'
    ซึ่งสคริปต์ไม่กดให้เด็ดขาด ต้องตรวจแล้วกดเอง)

    | ต้นทาง | full_billing | กรอกอะไร |
    |---|---|---|
    | **ISURVEY** (มีข้อมูลหัวหน้าครบแล้ว) | True | เต็มหน้า |
    | **se-survey** (ระบบเราเอง) | True | เต็มหน้า (ตั้งแต่ 2026-08-15) |

    เหตุผล: ทั้งสองต้นทางมีข้อมูลหัวหน้าครบก่อนถึงมือบอทแล้ว จึงยกมาได้เลย
    ⚠️ เดิม se-survey เป็น False เพราะ "หัวหน้ายังไม่ได้กรอก จะไปกรอกใน EMCS เอง
       บอทเติมให้จะกลายเป็นขยะ" (user 2026-08-03) — เงื่อนไขนั้นหมดไปเมื่อเว็บ se-survey
       กลายเป็นศูนย์กลาง: หัวหน้ากรอกยอด+ความเห็นบนเว็บก่อนอนุมัติ และกดอนุมัติไม่ได้
       ถ้ายังไม่กรอกยอด

    เต็มหน้า = หัวบิล + 3 ช่องสรุปความเห็น (ผลการดำเนินงาน / ความเห็นผู้ตรวจสอบ /
    ความเห็นเซอร์เวย์) + ตารางราคาคอลัมน์ "เสนอ" — ทุกช่องข้ามให้เองถ้าต้นทางไม่มีข้อมูล
    (ไม่ทับของเดิมที่คนกรอกไว้ ไม่เขียนเลขมั่ว)
    คอลัมน์อนุมัติ txtIns_* เป็นของบริษัทประกัน — disabled อยู่แล้ว ไม่แตะโดยโครงสร้าง

    ⚠️ ข้อเท็จจริงของหน้านี้: ปุ่มบันทึกบนจอมีปุ่มเดียว value='บันทึกราคา' แต่ **id มี 2 แบบ**
    (ยังไม่เคยบันทึกบิล = `btnSurveySave` / เคยบันทึกแล้ว = `btnSurvey_Update`)
    — ดู _PRICE_SAVE_BUTTONS, บอทลองทั้งคู่.
    เดิมโค้ด/ล็อกเขียนว่า "ไม่กดบันทึกราคา" ทั้งที่กดปุ่มนั้นอยู่
    (guard เช็คแค่คำว่า 'ส่งงาน' จึงผ่าน) = ล็อกหลอก.
    user ปรับกติกา 2026-07-27: **กด 'บันทึกราคา' ได้** เพราะเลขที่ใบแจ้งหนี้ + วันที่วางบิล
    ต้องถูกบันทึกด้วยปุ่มนี้ปุ่มเดียว. ที่ยังห้ามเด็ดขาดคือ 'ส่งงานใหม่' (wuFlow1_cmdSendNew)

    full_billing=False (โหมด draft-park: se-survey ⚡ นำเข้า / เติม draft เดิม):
    กรอกแค่หัวบิล 2 ช่อง — ไม่แตะตารางราคา/ความเห็น (หัวหน้ากรอกเองใน EMCS)
    แต่ยัง **บันทึกด้วยปุ่ม 'บันทึกราคา'** (ไม่งั้นหัวบิลไม่ติด) แล้ว **กดกลับหน้า Inbox/Outbox
    (wuMenuPage1_imbReturn_In_Out) = ออกจากเรื่อง เพื่อปลดล็อก** (ไม่งั้นเรื่องค้างถูกล็อก
    คนอื่นเปิดต่อไม่ได้ ต้องรอ). ราคา + ส่งงาน คนทำเองภายหลัง
    navigate=False: อยู่หน้าค่าใช้จ่ายแล้ว (เช่นหลังกด 'งานต่อเนื่อง') — ไม่ต้องกดเมนูเข้าใหม่"""
    log("EMCS: กรอกหน้าค่าใช้จ่าย")
    if navigate:
        click_retry(driver, By.ID, "wuMenuPage1_imbSpend")

    # EMCS อาจ gate ก่อนเข้าหน้าค่าใช้จ่าย (alert "ไม่สามารถไปหน้า [ค่าใช้จ่าย] ได้
    # กรุณาตรวจสอบ ... เลขทะเบียนผู้บาดเจ็บ" ฯลฯ) — รันผ่าน webui ผู้ใช้กรอกเลขทะเบียน
    # แล้ว ไม่ติด; รัน console/ไม่มีคนเฝ้า = อ่าน alert → หยุดรอคนกรอกแล้วกดเมนูใหม่ (cap 5)
    for _ in range(5):
        try:
            wait_visible(driver, By.ID, "txtBill_No", 15)
            break
        except UnexpectedAlertPresentException:
            alert_text = (accept_alert(driver, timeout=3) or "").strip()
            log(f"   ⚠️ เข้าหน้าค่าใช้จ่ายไม่ได้ (EMCS gate): {alert_text[:140]}")
            if wait_for_manual_fill(
                    "ข้อมูลที่ EMCS บังคับก่อนเข้าหน้าค่าใช้จ่าย (เช่น เลขทะเบียนผู้บาดเจ็บ)",
                    reason=alert_text, focus_labels=_missing_field_list(alert_text),
                    driver=driver):
                click_retry(driver, By.ID, "wuMenuPage1_imbSpend")
            else:
                log("   → ข้ามหน้าค่าใช้จ่าย — เข้า/กรอกเองภายหลัง")
                return
        except TimeoutException:
            log("   ⚠️ หน้าค่าใช้จ่ายไม่โหลด (txtBill_No ไม่โผล่) — ข้าม กรอกเอง")
            return
    # เริ่มจำค่าที่กรอกของ "หน้าค่าใช้จ่าย" (เฟส 2 — อ่านกลับมาเทียบหลังบันทึก)
    # วางตรงนี้เพราะหน้าโหลดแล้วแน่ ๆ และครอบหัวบิลด้วย (เลขที่ใบแจ้งหนี้ = เลขอ้างอิงเบิกเงิน)
    # ของหน้าหลักถูกตรวจไปแล้วตั้งแต่บันทึกหน้าหลัก — ผลเก็บไว้ใน last_verify_mismatches
    reset_filled()
    # เคลียร์ก่อนกรอก — งานต่อเนื่องช่องอาจมีค่าครั้งก่อนค้าง (set_text ต่อท้ายไม่ทับ)
    for fid in ("txtBill_No", "wuCale_Bill_Date_txtCalendar"):
        try:
            driver.find_element(By.ID, fid).clear()
        except Exception:
            pass
    set_text(driver, "txtBill_No", data.invoice_value)
    set_text(driver, "wuCale_Bill_Date_txtCalendar", today_buddhist())

    # readback ยืนยันค่าที่กรอก (set_text เงียบตอนสำเร็จ — log ไว้ให้ตรวจ/audit)
    try:
        _bn = driver.find_element(By.ID, "txtBill_No").get_attribute("value")
        _bd = driver.find_element(
            By.ID, "wuCale_Bill_Date_txtCalendar").get_attribute("value")
        log(f"   ✓ เลขที่ใบแจ้งหนี้ = {_bn!r} | วันที่วางบิล = {_bd!r}")
    except Exception:
        pass

    if not full_billing:
        # ต้นทาง se-survey: หัวหน้ายังไม่ได้กรอกความเห็น/เรทราคา จะไปกรอกใน EMCS เอง
        # บอทเติมให้ = ขยะที่หัวหน้าต้องมาลบ → กรอกแค่หัวบิล 2 ช่อง
        # แต่ยังกด 'บันทึกราคา' (ไม่งั้นหัวบิลไม่ติด) แล้วออกจากเรื่องเพื่อปลดล็อก
        log("EMCS: หน้าค่าใช้จ่าย — กรอกแค่เลขที่ใบแจ้งหนี้ + วันที่วางบิล "
            "(งานจาก se-survey: ความเห็น/เรทราคา หัวหน้ากรอกเองใน EMCS); "
            "บันทึกด้วยปุ่ม 'บันทึกราคา' ไม่กด 'ส่งงานใหม่'")
        _save_and_exit_billing(driver, leave=leave, data=data)
        return

    # ---- ต้นทาง ISURVEY: หัวหน้ากรอกความเห็น+เรทราคาไว้ในระบบเดิมแล้ว → ยกมาทั้งหน้า ----
    # 3 ช่องสรุปความเห็น (textarea ทั้งหมด — ยืนยัน id จากหน้าจริง 21/07/69)
    # try รายช่อง: บางบริษัท/บางเลย์เอาต์อาจไม่มีช่องเหล่านี้ — ขาดช่องต้องไม่ล้มทั้งหน้า
    # ค่าว่าง = ไม่มีข้อมูลจากต้นทาง → set_textarea ข้ามให้เอง (ไม่ลบของเดิมที่คนกรอกไว้)
    for _fid, _val in (("txtAcc_result", data.accident_summary),      # ผลการดำเนินงาน
                       ("txtAcc_Comment", data.review_comment),       # ความเห็นของผู้ตรวจสอบ
                       ("txtSurv_Comment", data.surveyor_comment)):   # ความเห็นของเซอร์เวย์
        try:
            set_textarea(driver, _fid, _val)   # คงบรรทัดใหม่ (3 ช่องนี้เป็น textarea)
        except Exception as e:
            log(f"   ⚠️ กรอก {_fid} ไม่ได้ ({type(e).__name__}) — ข้าม กรอกเอง")
    for _fid, _lbl in (("txtAcc_result", "ผลการดำเนินงาน"),
                       ("txtAcc_Comment", "ความเห็นของผู้ตรวจสอบ"),
                       ("txtSurv_Comment", "ความเห็นของเซอร์เวย์")):
        try:
            _v = driver.find_element(By.ID, _fid).get_attribute("value") or ""
            log(f"   ✓ {_lbl} = {_v[:60]!r}{'…' if len(_v) > 60 else ''}")
        except Exception:
            pass

    # ตารางราคา — กรอกเฉพาะคอลัมน์ "เสนอ" จากข้อมูล ISURVEY (data.bill)
    # คอลัมน์อนุมัติ txtIns_* ของบริษัทประกัน disabled อยู่แล้ว — ไม่แตะโดยโครงสร้าง
    # ยอดรวม/VAT (txtTotalPrice, txtVatPrice, txtGrandTotalPrice) JS คำนวณเองตอนกด Tab
    fill_fee_table(driver, data.bill)

    # ปุ่มบันทึกบนจอมีปุ่มเดียว value='บันทึกราคา' แต่ id มี 2 แบบ (ยังไม่เคยบันทึกบิล =
    # btnSurveySave / เคยบันทึกแล้ว = btnSurvey_Update) — _save_and_exit_billing ลองทั้งคู่
    # ⛔ 'ส่งงานใหม่' (wuFlow1_cmdSendNew) ยังห้ามแตะเด็ดขาดเหมือนเดิม
    _save_and_exit_billing(driver, leave=leave, data=data)
    log("EMCS: บันทึกหน้าค่าใช้จ่ายแล้ว — ตรวจ/แก้ราคา แล้วกด 'ส่งงานใหม่' เอง "
        "(สคริปต์ไม่กดส่งให้)")


# --------------------------------------------------------------- ส่งงาน (commit)
# ปุ่มส่งงาน (commit) ที่อาจอยู่บนหน้าค่าใช้จ่าย — ลองหาตามลำดับ
#   ส่งงานใหม่ = งานครั้งแรก (cmdSendNew) / ส่งผลงานต่อเนื่อง = ครั้งที่ 2,3,… (cmdSendFollow)
_SUBMIT_BUTTONS = (
    ("wuFlow1_cmdSendNew", "ส่งงานใหม่"),
    ("wuFlow1_cmdSendFollow", "ส่งผลงานต่อเนื่อง"),
)


def _find_submit_button(driver):
    """หาปุ่มส่งงาน (commit) ที่มีบนหน้า — รองรับทั้ง 'ส่งงานใหม่' (cmdSendNew) และ
    'ส่งผลงานต่อเนื่อง' (cmdSendFollow). ปุ่มมีเฉพาะ draft โหมดแก้ = เป็น gate ในตัว
    คืน (element, ชื่อปุ่ม) หรือ (None, '') ถ้าไม่เจอ"""
    for eid, label in _SUBMIT_BUTTONS:
        try:
            el = driver.find_element(By.ID, eid)
            if el.is_displayed():
                return el, label
        except Exception:
            pass
    # fallback: หาโดยข้อความปุ่ม
    labels = {lab for _, lab in _SUBMIT_BUTTONS}
    try:
        for el in driver.find_elements(
                By.CSS_SELECTOR, "input[type=submit],input[type=button],button"):
            txt = (el.get_attribute("value") or el.text or "").strip()
            if txt in labels and el.is_displayed():
                return el, txt
    except Exception:
        pass
    return None, ""


# ข้อความที่ EMCS ตอบหลังกดส่งงาน — ใช้เป็นหลักฐานแทนการกลับไปค้นหน้ารายการ
# ⚠️ เช็คคำ "ไม่สำเร็จ" ก่อน "สำเร็จ" เสมอ (คำหลังเป็นสตริงย่อยของคำแรก)
_SUBMIT_FAIL_WORDS = ("ไม่สำเร็จ", "ไม่สามารถ", "ผิดพลาด", "error", "กรุณา", "ต้องระบุ")
_SUBMIT_OK_WORDS = ("สำเร็จ", "เรียบร้อย", "success")

_JS_RESULT_MODAL = r"""
const sels = [".swal-modal", ".swal2-popup", ".sweet-alert", ".swal-text",
              "#swal2-html-container", ".modal.show", ".modal[style*='block']"];
for (const s of sels) {
  for (const el of document.querySelectorAll(s)) {
    const vis = el.offsetParent !== null || getComputedStyle(el).display !== "none";
    const t = ((el.innerText || "").trim());
    if (vis && t) return t.replace(/\s+/g, " ").slice(0, 300);
  }
}
return "";
"""


def _read_submit_result(driver, timeout: float = 15) -> tuple:
    """อ่านคำตอบของ EMCS ตรงหน้าหลังกดส่งงาน → (ok: bool|None, text: str)

    ok=True/False = EMCS บอกมาตรง ๆ / None = ไม่เจอข้อความ (ผู้เรียกค่อยไปตรวจทางอื่น)
    ทำแบบนี้แทนการกลับไปค้นหน้ารายการ เพราะหลังกดส่ง session มักหลุดต้อง login ใหม่
    แล้วหน้ารายการมาช้า → เคยฟันธงผิดว่า "ส่งไม่สำเร็จ" (เคลม 2026013160796)
    """
    text = ""
    deadline = time.time() + timeout
    while time.time() < deadline and not text:
        try:                                   # JS alert (บางจังหวะ EMCS ใช้ alert)
            text = accept_alert(driver, timeout=1) or ""
        except Exception:
            pass
        if not text:
            try:
                text = driver.execute_script(_JS_RESULT_MODAL) or ""
            except Exception:
                text = ""
        if not text:
            time.sleep(0.5)
    if not text:
        return None, ""
    low = text.lower()
    if any(w in low for w in _SUBMIT_FAIL_WORDS):
        return False, text
    if any(w in low for w in _SUBMIT_OK_WORDS):
        return True, text
    return None, text


def submit_report(driver, cfg, claim, esurvey: str = ""):
    """commit งาน: กดปุ่มส่งงานที่มีบนหน้าค่าใช้จ่าย (โหมดแก้ของ draft — live session
    ที่เพิ่งกรอกเสร็จ) — รองรับทั้ง 'ส่งงานใหม่' (งานใหม่) และ 'ส่งผลงานต่อเนื่อง'
    (งานต่อเนื่อง ครั้งที่ 2,3,…) — แล้ว verify ว่าสถานะเปลี่ยนเป็น 'ส่งงานแล้ว' จริง

    คืน (ok: bool, msg: str). จะกดเฉพาะเมื่อ "เจอปุ่ม" (= เป็น draft) เท่านั้น —
    เป็น gate ในตัว (สถานะอื่นไม่มีปุ่มนี้)

    ⚠️ **ห้ามสมมติว่ายังอยู่หน้าค่าใช้จ่าย** — หลังบันทึกราคาเสร็จ บอทกลับไปหน้า
    Inbox/Outbox เพื่อปลดล็อกเรื่อง (คนอื่นจะได้เปิดต่อได้) แล้วค่อยหยุดรอให้คนสั่งส่ง
    ระหว่างนั้นคนอาจไม่ได้แตะ Chrome เลย → พอสั่งส่ง ปุ่มไม่อยู่บนหน้า = "ไม่เจอปุ่ม"
    (เจอจริง S68426080893 2026-08-05: เช้าผ่านเพราะคนบังเอิญเปิดเรื่องกลับมาดูเอง
    บ่ายไม่ได้เปิด เลยล้ม — snapshot ตอนจบงานเป็นหน้า Inbox ทั้ง 2 รอบ ยืนยันแล้ว)
    → ไม่เจอปุ่ม = เปิดเรื่องกลับเข้าหน้าค่าใช้จ่ายเองก่อน แล้วค่อยหาใหม่"""
    btn, label = _find_submit_button(driver)
    if btn is None:
        # ไม่รู้เลข e-Survey ก็หาเอาจาก EMCS ได้ — อย่าให้ "ความไม่รู้" มาปิดทางกู้
        # (เกิดจริง 2026013159949: บันทึกหน้าหลักสำเร็จแต่บอทไม่ทันจับ alert ที่มีเลข
        #  → esurvey='' → เส้นกู้ถูกข้าม → ส่งงานไม่ได้ทั้งที่เรื่องมีอยู่จริง)
        if not esurvey:
            try:
                goto_mainpage(driver, cfg, "")
                esurvey = (report_status(driver, claim) or {}).get("esurvey", "")
                if esurvey:
                    log(f"   ℹ️ ไม่รู้เลข e-Survey มาก่อน — ค้นจาก EMCS ได้ {esurvey}")
            except Exception:
                esurvey = ""
        if not esurvey:
            return False, ("ไม่เจอปุ่มส่งงาน และหาเลข e-Survey ของเคลมนี้ใน EMCS ไม่เจอ "
                           "— เปิดเรื่องเองบนหน้าจอแล้วกดส่ง")
        log("   ↻ ไม่เจอปุ่มส่งงานบนหน้าปัจจุบัน — เปิดเรื่องกลับเข้าหน้าค่าใช้จ่ายก่อน")
        try:
            _open_report_billing(driver, claim, esurvey)
            btn, label = _find_submit_button(driver)
        except Exception as e:
            return False, (f"เปิดเรื่อง {esurvey} เข้าหน้าค่าใช้จ่ายไม่ได้ "
                           f"({type(e).__name__}) — เปิดเองบนหน้าจอแล้วกดส่ง")
    if btn is None:
        return False, ("ไม่เจอปุ่มส่งงาน (ส่งงานใหม่/ส่งผลงานต่อเนื่อง) — งานนี้อาจไม่ใช่ "
                       "draft หรือไม่ได้อยู่หน้าค่าใช้จ่ายโหมดแก้")
    try:
        if not btn.is_enabled():
            return False, f"ปุ่ม '{label}' ยัง disabled (ข้อมูล/ราคายังไม่ครบ?)"
    except Exception:
        pass

    log(f"EMCS: กดปุ่ม '{label}' (commit งาน)")
    try:
        btn.click()
    except Exception as e:
        return False, f"กดปุ่มส่งงานไม่ได้: {type(e).__name__}"
    # ---- อ่านคำตอบของ EMCS ตรงหน้านั้นเลย (ไม่กลับไปค้นหน้ารายการซ้ำ) ----
    # หลังกดส่งสำเร็จ EMCS ขึ้น SweetAlert "สำเร็จ! ส่งงานใหม่...เรียบร้อยแล้ว"
    # = หลักฐานตรงจาก EMCS เอง เร็วกว่าและไม่พังตอน session หลุด (user 2026-08-06)
    ok_now, said = _read_submit_result(driver)
    if said:
        log(f"   [EMCS ตอบ] {said[:200]}")
    # ปิด modal ให้เรียบร้อยก่อน (กด OK)
    for sel in (".swal-button--confirm", ".swal-button", ".swal2-confirm", ".confirm"):
        try:
            for e in driver.find_elements(By.CSS_SELECTOR, sel):
                if e.is_displayed():
                    e.click()
                    time.sleep(1)
                    break
        except Exception:
            pass
    # เผื่อมี HTML dialog 'สร้างเรื่องต่อเนื่อง?' โผล่ → ไม่สร้างเพิ่ม (ยกเลิก)
    for bid in ("btnCancelCreateMore", "btnNoCancel"):
        try:
            d = driver.find_element(By.ID, bid)
            if d.is_displayed() and d.is_enabled():
                d.click()
                time.sleep(1)
        except Exception:
            pass
    time.sleep(1)

    # EMCS ตอบมาชัดแล้ว = จบตรงนี้ ไม่ต้องเปิด EMCS ตรวจซ้ำ (user 2026-08-06)
    if ok_now is True:
        return True, f"ส่งงานสำเร็จ — EMCS ตอบ: {said[:120]}"
    if ok_now is False:
        return False, f"EMCS ตอบว่าส่งไม่สำเร็จ: {said[:160]}"

    # ---- ทางสำรอง: EMCS ไม่ขึ้นข้อความอะไรเลย (ไม่ควรเกิด) ----
    # ถึงค่อยกลับไปดูสถานะในหน้ารายการ ลองได้ 3 รอบ — หลังกดส่ง session มักหลุด
    # (ต้อง login ใหม่) แล้วหน้ารายการมาช้า อ่านรอบเดียวได้ค่าว่างแล้วสรุปว่า
    # "ส่งไม่สำเร็จ" ทั้งที่ส่งไปแล้ว (เจอจริงเคลม 2026013160796)
    log("   ℹ️ ไม่เห็นข้อความตอบกลับจาก EMCS — ใช้ทางสำรอง: ไปดูสถานะในหน้ารายการ")
    st, err = "", ""
    for attempt in (1, 2, 3):
        try:
            goto_mainpage(driver, cfg, "")
            # esurvey ของเรื่องที่เพิ่งกดส่ง — ใช้แยกเรื่องเมื่อเคลมนี้มีหลายเรื่อง
            info = report_status(driver, claim)
            if esurvey and info and (info.get("esurvey") or "") != esurvey:
                log(f"   ⚠️ สถานะที่อ่านได้เป็นของเรื่อง {info.get('esurvey')} "
                    f"ไม่ใช่ {esurvey} — ไม่นับ")
                info = None
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            log(f"   ⚠️ ตรวจสถานะรอบ {attempt} ไม่สำเร็จ ({err})")
            time.sleep(2)
            continue
        st = ((info or {}).get("status") or "").strip()
        if st:
            break
        log(f"   ⚠️ ตรวจสถานะรอบ {attempt}: ยังอ่านไม่ได้ (หน้ารายการอาจยังไม่ขึ้น)")
        time.sleep(3)
    # whitelist เท่านั้น — สถานะแปลก ๆ ไม่ถือว่าส่งสำเร็จ (เดิมใช้ "ไม่ใช่ draft = สำเร็จ")
    if st in SUBMITTED_STATUSES:
        return True, f"ส่งงานสำเร็จ (สถานะ → {st})"
    if st:
        return False, (f"กดส่งแล้วแต่สถานะเป็น '{st}' — ยังไม่ยืนยันว่าสำเร็จ ตรวจเอง")
    # อ่านสถานะไม่ได้ ≠ ส่งไม่สำเร็จ — บอกตรง ๆ ว่าตรวจไม่ได้ ห้ามแจ้ง ISURVEY เอง
    return False, ("กดส่งงานไปแล้ว แต่ตรวจสถานะบน EMCS ไม่ได้ (ลอง 3 รอบ"
                   + (f", ล่าสุด {err}" if err else "") + ") — "
                   "งานอาจส่งสำเร็จแล้ว เปิด EMCS ดูเอง ถ้าส่งแล้วให้แจ้ง ISURVEY ด้วย")


# --------------------------------------------------------------- งานต่อเนื่อง
def _addno_count(driver) -> int:
    """จำนวน 'ครั้งที่' (options ของ ddlAdd_No) — ใช้เช็คว่ากด 'งานต่อเนื่อง' สำเร็จ
    (ครั้งที่เพิ่มขึ้น) — 0 ถ้าไม่เจอ dropdown"""
    try:
        return len(Select(driver.find_element(By.ID, "ddlAdd_No")).options)
    except Exception:
        return 0


def _open_report_billing(driver, claim: str, esurvey: str):
    """ค้นเลขเคลม (ให้ลิงก์โผล่บนหน้า MainPage) → คลิกลิงก์ e-Survey เปิดเรื่อง →
    เข้าหน้าค่าใช้จ่าย (frmBilling.aspx) → รอช่องเลขที่ใบแจ้งหนี้โผล่"""
    find_existing_reports(driver, claim)          # ค้นเพื่อให้ลิงก์ e-Survey โผล่
    wait_clickable(
        driver, By.XPATH, f"//a[normalize-space(text())='{esurvey}']", 20
    ).click()
    click_retry(driver, By.ID, "wuMenuPage1_imbSpend")
    wait_visible(driver, By.ID, "txtBill_No", 20)


def start_continuation(driver, claim: str, esurvey: str):
    """เปิดเรื่องเดิม → หน้าค่าใช้จ่าย → ทำให้ "ครั้งงานต่อเนื่อง (draft)" พร้อมกรอก

    พฤติกรรม EMCS (พิสูจน์จาก probe): กด 'งานต่อเนื่อง' (cmdFollow) จะ "สร้างครั้งใหม่
    แล้วเด้งกลับหน้ารายการ" → ต้องเปิดเรื่องซ้ำ ครั้งใหม่จะถูกเลือกอัตโนมัติ + ช่องปลดล็อก
    ตัวชี้วัด: txtBill_No แก้ไขได้ = อยู่ครั้ง draft (กรอกได้เลย) / ถูกล็อก = ครั้งล่าสุด
    ส่งแล้ว (ต้องกด 'งานต่อเนื่อง' สร้างครั้งใหม่)
    - แก้ไขได้อยู่แล้ว → กรอกต่อเลย (ไม่กด 'งานต่อเนื่อง' ซ้ำ กันสร้างครั้งเกิน)
    - ถูกล็อก → กด 'งานต่อเนื่อง' + ยืนยัน → เปิดเรื่องซ้ำ → ครั้งใหม่พร้อมกรอก"""
    log(f"EMCS: เปิดเรื่องเดิม {esurvey} เพื่อทำงานต่อเนื่อง")
    _open_report_billing(driver, claim, esurvey)

    if driver.find_element(By.ID, "txtBill_No").is_enabled():
        log(f"EMCS: มีครั้งงานต่อเนื่อง (draft) ค้างอยู่ → ครั้งที่ "
            f"{_addno_count(driver)} แก้ไขได้ กรอกต่อได้เลย (ไม่กด 'งานต่อเนื่อง' ซ้ำ)")
        return

    # ครั้งล่าสุดถูกล็อก (ส่งแล้ว) → สร้างครั้งใหม่ด้วยปุ่ม 'งานต่อเนื่อง'
    try:
        follow = wait_clickable(driver, By.ID, "wuFlow1_cmdFollow", 15)
    except TimeoutException as e:
        raise RuntimeError(
            "ช่องค่าใช้จ่ายถูกล็อก และไม่เจอปุ่ม 'งานต่อเนื่อง' — "
            "ตรวจสถานะเรื่องบน EMCS"
        ) from e
    before = _addno_count(driver)
    log(f"EMCS: กด 'งานต่อเนื่อง' (ครั้งที่ปัจจุบัน = {before})")
    follow.click()
    time.sleep(1)
    try:
        accept_alert(driver, timeout=10)   # 'คุณยืนยันที่จะเพิ่มงานต่อเนื่อง...'
    except TimeoutException:
        for sel in (".swal-button--confirm", ".swal-button", ".swal2-confirm",
                    "#btnConfirm", ".confirm"):
            try:
                for e in driver.find_elements(By.CSS_SELECTOR, sel):
                    if e.is_displayed():
                        e.click()
                        time.sleep(1)
                        break
            except Exception:
                pass

    # EMCS เด้งกลับหน้ารายการ → เปิดเรื่องซ้ำ ครั้งใหม่จะถูกเลือก + ช่องปลดล็อก
    time.sleep(2)
    _open_report_billing(driver, claim, esurvey)
    try:
        WebDriverWait(driver, 20).until(
            lambda d: _addno_count(d) > before
            and d.find_element(By.ID, "txtBill_No").is_enabled()
        )
    except TimeoutException as e:
        raise RuntimeError(
            "สร้างงานต่อเนื่องแล้วแต่เปิดครั้งใหม่ไม่เจอ/ช่องไม่ปลดล็อก — ตรวจบน EMCS"
        ) from e
    log(f"EMCS: เพิ่มงานต่อเนื่องแล้ว → ครั้งที่ {_addno_count(driver)} (พร้อมกรอก)")


def fill_continuation(driver, cfg, data: ClaimData, esurvey: str,
                      full_billing: bool = True, images_folder=None,
                      image_type: str = "รูปรถประกัน") -> str:
    """งานต่อเนื่อง (ครั้งถัดไปของเคลมเดิม): เปิดเรื่องเดิม → 'งานต่อเนื่อง' →
    **อัปรูปของครั้งนี้** → กรอกหน้าค่าใช้จ่าย (invoice ใหม่ + ตารางราคา)
    ไม่แตะหน้าหลัก/คู่กรณี (ข้อมูลพวกนั้นอยู่ครั้งที่ 1 แล้ว)

    รูป (user เคาะ 19/08/69): **ใส่ทุกใบที่มี ไม่ต้องพิสูจน์ว่าซ้ำกับครั้งที่ 1 ไหม**
    และ **ลงหมวด "รูปประกอบ" หมด** — หลังกด 'งานต่อเนื่อง' EMCS ให้ทำได้แค่ใส่รูปกับ
    กรอกหน้าค่าใช้จ่าย ตัวเลือกหมวดที่ผูกกับคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินจึงไม่มีให้เลือก
    EMCS ติดป้าย "ครั้งที่ N" ให้เองตามครั้งที่กำลังเปิดอยู่
    🟡 โควตา 80 ใบ: **ยังไม่รู้ว่านับรวมทั้งเคลมหรือแยกรายครั้ง** (รูปติดป้ายแยกครั้ง)
       ตอนนี้ upload_images ยังตัดตาม image_quota_left ที่อ่านจากหน้าจอ ซึ่งปลอดภัยกว่า
       เพราะเป็นตัวเลขที่ EMCS บอกเอง ไม่ใช่เราเดา

    full_billing=False: ไม่กด 'บันทึกราคา'. ปุ่มส่งจริงคือ 'ส่งผลงานต่อเนื่อง'
    (wuFlow1_cmdSendFollow) — สคริปต์ไม่กดให้เด็ดขาด (เหมือนปุ่ม 'ส่งงานใหม่')
    คืนเลข e-Survey เดิม (งานต่อเนื่องใช้เรื่อง/เลขเดิม ไม่สร้างใหม่)"""
    start_continuation(driver, data.claim_value, esurvey)
    # start_continuation ทิ้งเราไว้ที่หน้าค่าใช้จ่ายอยู่แล้ว (fill_billing จึง navigate=False)
    # แต่ upload_images กด wuMenuPage1_imbImage แล้วค้างอยู่หน้ารูป → ถ้าอัปรูป
    # ต้องให้ fill_billing กดกลับหน้าค่าใช้จ่ายเอง ไม่งั้นหา txtBill_No ไม่เจอแล้วล้ม
    moved = images_folder is not None
    if moved:
        upload_images(driver, images_folder, image_type=image_type,
                      n_opponents=len(data.third_parties or []),
                      n_injuries=len(data.injuries or []),
                      n_assets=len(data.assets or []),
                      single_type=_EMCS_DEFAULT_IMAGE_TYPE, dedupe=False)
    fill_billing(driver, data, full_billing=full_billing, navigate=moved,
                 leave=False)   # ค้างในเรื่องไว้ ปุ่มส่งงานอยู่หน้านี้
    return esurvey


# ------------------------------------------------------------------ flow รวม

def fill_one(driver, cfg, data: ClaimData, images_folder=None,
             loss_type: str = "auto", image_type: str = "รูปรถประกัน",
             severity: str = "เบา", force_new: bool = False,
             full_billing: bool = True) -> str:
    """กรอกเคลมเดียวจนจบ (driver ต้องอยู่หน้ารายการงาน EMCS แล้ว)
    คืนเลข e-Survey ของเรื่องที่สร้าง

    การ "บันทึก" ทุกหน้าเป็นแค่ draft แก้ไขได้ — สคริปต์กดบันทึกให้ครบ
    จุดส่งงานจริงคือปุ่ม 'ส่งงานใหม่' หน้าค่าใช้จ่าย ซึ่งสคริปต์
    **ไม่กดให้เด็ดขาด** / มีด่านกันเปิดเรื่องซ้ำ (ข้ามด้วย force_new)

    ถ้าเคลมมีเรื่องเดิมใน EMCS แล้ว + invoice ใหม่ (ยังไม่อยู่ในเรื่องเดิม) →
    เข้าโหมด "งานต่อเนื่อง" อัตโนมัติ (เปิดเรื่องเดิม กรอกครั้งถัดไปหน้าค่าใช้จ่าย)"""
    # งานต่อเนื่อง: มีเรื่องเดิม + invoice ใหม่ → ทำครั้งถัดไป (ไม่สร้างเรื่องใหม่)
    if not force_new:
        try:
            existing = find_existing_reports(driver, data.claim_value)
        except Exception as e:
            log(f"   ⚠️ ตรวจเรื่องเดิมไม่สำเร็จ ({type(e).__name__}) — ทำต่อแบบสร้างใหม่")
            existing = []
        cont = continuation_esurvey(existing, data.invoice_value)
        if cont:
            log(f"EMCS: เคลมนี้มีเรื่องเดิม + invoice ใหม่ → โหมดงานต่อเนื่อง (ต่อจาก {cont})")
            return fill_continuation(driver, cfg, data, cont, full_billing=full_billing,
                                     images_folder=images_folder, image_type=image_type)
        guard_duplicate_report(driver, data, force_new, existing=existing)
    else:
        guard_duplicate_report(driver, data, force_new)
    new_report(driver)

    main_window = driver.current_window_handle
    resolved_loss = resolve_loss_type(data, loss_type)

    set_rule_context(claim=data.claim_value, survey_no=data.invoice_value, page="หน้าหลัก")
    reset_filled()          # เริ่มจำค่าที่กรอกของ "หน้าหลัก" (เฟส 2 — อ่านกลับมาเทียบ)
    fill_claim_type(driver, data.claim_type, warn=appointment_hint(data))
    fill_severity(driver, severity)
    fill_insurer_and_refs(driver, data)
    fill_policy(driver, data)
    fill_car(driver, data)
    fill_driver(driver, data)
    fill_accident(driver, data, loss_type=resolved_loss)
    fill_verdict(driver, data)
    # เลขที่รับแจ้ง — เส้นนี้ไม่เคยกรอกเลย และไม่มีใครรู้ เพราะไอโออิ (1059) ยอมให้ว่างได้
    # ถ้า txtRef_Claim_No มีค่า · พอทำงานไทยไพบูลย์ (2429) เป็นครั้งแรก EMCS ฟ้องทันที
    # ว่า "1. เลขที่รับแจ้ง" แล้วบันทึกหน้าหลักไม่ผ่าน (เจอจริง 15/08/69 เคลม -001860)
    # ตัวมันเองรู้กติกาแยกรายบริษัทอยู่แล้ว — เรียกได้เลย ไม่ต้องเช็คซ้ำตรงนี้
    _set_or_clear_claim_ref(driver, data.notify_value)

    try:
        esurvey = save_main_form(driver, data)
    except Exception:
        # บันทึกไม่ผ่านคือกรณีที่ "อยากรู้ที่สุด" ว่าบนหน้ามีอะไรอยู่จริง — ค่ายังอยู่ให้อ่านได้
        # เดิมตรวจเฉพาะตอนสำเร็จ = พลาดทุกครั้งที่พลาด ซึ่งเป็นครั้งที่ต้องการข้อมูลมากที่สุด
        _verify_after_save(driver, data, "หน้าหลัก (บันทึกไม่ผ่าน)")
        raise
    _verify_after_save(driver, data, "หน้าหลัก")
    verify_car_saved(driver, data,
                     lambda: save_main_form(driver, data, button_id="btnUpdate",
                                            is_new=False))
    # เคลมสด: ส่วนคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน ปลดล็อกหลังบันทึกหน้าหลักเท่านั้น
    # ลำดับสำคัญ: คู่กรณี + ความเสียหาย ทำบนแท็บ "ข้อมูลทั่วไป" ให้จบก่อน แล้วค่อย
    # ผู้บาดเจ็บ/ทรัพย์สิน (กดเมนู imbInjure_Person/imbAsset นำทางไปแท็บอื่น —
    # ถ้าทำก่อน fill_damage_list จะหา btnPopUp_DamList บนแท็บหลักไม่เจอ → timeout)
    fill_third_parties(driver, data)
    fill_damage_list(driver, data, main_window)
    fill_injuries(driver, data)
    fill_assets(driver, data)

    if images_folder is not None:
        upload_images(driver, images_folder, image_type=image_type,
                      n_opponents=len(data.third_parties or []),
                      n_injuries=len(data.injuries or []),
                      n_assets=len(data.assets or []))

    fill_billing(driver, data, full_billing=full_billing, leave=False)
    return esurvey


def run_fill(driver, cfg, data: ClaimData, images_folder=None,
             loss_type: str = "auto", image_type: str = "รูปรถประกัน",
             severity: str = "เบา", force_new: bool = False,
             full_billing: bool = True) -> str:
    """login แล้วกรอกเคลมเดียว (flow เดิมสำหรับรันทีละเคลม)"""
    login(driver, cfg)
    return fill_one(driver, cfg, data, images_folder=images_folder,
                    loss_type=loss_type, image_type=image_type,
                    severity=severity, force_new=force_new,
                    full_billing=full_billing)


def _recascade_province(driver, province_id: str, timeout: int = 10):
    """import เซ็ต 'จังหวัด' ไว้แต่ไม่ trigger postback ให้ dropdown 'อำเภอ' (dependent) โหลด
    → fill_* เลือกจังหวัดเดิมซ้ำจะไม่ fire onchange (Selenium ไม่คลิก option ที่เลือกอยู่)
    → อำเภอไม่โหลด → fuzzy_select(อำเภอ) timeout

    แก้: บังคับจังหวัด → ช่องว่าง (option แรก) ผ่าน postback จริง (server เคลียร์ค่า)
    → fill_* เลือกจังหวัดเป็น 'การเปลี่ยนจริง' (ว่าง→จังหวัด) → onchange → อำเภอโหลด
    (เหมือน flow cmdNewReport ที่จังหวัดเริ่มจากว่าง)"""
    try:
        el = driver.find_element(By.ID, province_id)
        cur = Select(el).first_selected_option.get_attribute("value")
    except Exception:
        return
    if not cur or cur in ("0",):
        return   # ว่างอยู่แล้ว — fill_* จะเลือกเองได้ cascade ปกติ
    try:
        Select(el).select_by_index(0)   # คลิก option ว่าง → change → postback
        WebDriverWait(driver, timeout).until(EC.staleness_of(el))
    except Exception:
        pass
    time.sleep(0.8)


# กติการูปแบบข้อความรายบริษัท — สกัดอัตโนมัติจาก validForm() ใน JS ของ EMCS
# ด้วย tools/emcs_spec.py (2026-08-02) **อย่าแก้ด้วยมือ** ให้รันเครื่องมือแล้วคัดลอกใหม่
#
# validForm() ถูกผูกไว้กับปุ่มบันทึก/แก้ไขของหน้าหลัก → ถ้ารูปแบบไม่ตรง EMCS เด้ง
# AlertSummary แล้วบันทึกไม่ผ่าน แม้ข้อมูลจะถูกต้องก็ตาม
# เทียบด้วย re.search ไม่ใช่ re.match เพราะ JS ใช้ .test() และหลายแพตเทิร์นมี ^
# เฉพาะตัวเลือกแรกของ | เท่านั้น (เช่น ของ 1059) — match จะเปลี่ยนความหมาย
_FORMAT_RULES = {
    "txtAcc_ClaimRef_No": {
        "20": [
            '^[0-9]{6}$|[0-9]{2}[-][0-9]{6}|[0-9]{4}[-][0-9]{5}$',
        ],
        "1203": [
            '^[0-9]{2}[-]{1}[0-9]{3}[-]{1}NMOT[-]{1}[0-9]{6}$',
        ],
        "12": [
            '^[I]{1}[0-9]{8}$',
        ],
        "1059": [
            '^[A-Z]{2}[0-9]{4}[/]{1}[0-9]{6}$|ABI[0-9]{5}[/]{1}[0-9]{4}$|ABC[A-Z0-9]{1,}[/]{1}[0-9]{2}[/]{1}[0-9]{2}$',
        ],
        "1232": [
            '^FCI[0-9]{3}[-][A][0-9]{4}[-][0-9]{6}$|^ACD[0-9]{3}[-][A][0-9]{4}[-][0-9]{6}$|^FCI001-A[0-9]{4}[-][0-9]{6}$',
        ],
        "992": [
            '^[A-Z]{3}[-][0-9]{2}[-][0-9]{6}$|[A-Z]{3}[-][A-Z]{3}[-][A-Z]{1}[-][0-9]{2}[-][0-9]{6}$',
        ],
        "19": [
            '^[F]{1}[M]{1}[0-9]{8}$|[D]{1}[M]{1}[0-9]{8}$|[A]{1}[M]{1}[0-9]{8}$',
        ],
        "2179": [
            '^[0-9]{4}[/][0-9]{4}[/][0-9]{5}$',
        ],
        "17": [
            '^[0-9]{10}$',
        ],
        "11": [
            '^(MO.)[0-9]{3}(-)[0-9]{2}\\/[0-9]{8}$|^[0-9]{5}(RMO)[0-9]{6}$|^[0-9]{3}(-)[0-9]{2}(-RMO-)[0-9]{6}$|^(FN)[0-9]{8}$',
            '^(MO.)[0-9]{3}(-)[0-9]{2}\\/[0-9]{8}$|^[0-9]{5}(RMO)[0-9]{6}$|^[0-9]{3}(-)[0-9]{2}(-RMO-)[0-9]{6}$|^(FN)[0-9]{8}$|^[0-9]{3}(-)[0-9]{2}(-)[A-Z]{3}(-)[0-9]{6}$',
        ],
        "1101": [
            '^(KIT)[-][0-9]{3}[-][M][-][0-9]{2}[-][0-9]{6}|[V,A,K,W][0-9]{11}|[K][0-9]{5}[M][0-9]{6}|[O][0-9]{5}[M][0-9]{6}$',
        ],
        "4": [
            '^(ACD)[0-9]{3}(-)(A)[0-9]{4}(-)[0-9]{6}$',
        ],
        "821": [
            '^[0-9]{8}$',
        ],
    },
    "txtRef_Claim_No": {
        "20": [
            '^C[0-9]{7}$|V[0-9]{7}$|VR[0-9]{6}|K[0-9]{7}$|^[A-Z]{1}[A-Z0-9]{1}[0-9]{6}$',
        ],
        "12": [
            '^[A-Z0-9]{6}[/]{1}[0-9]{3}[/]{1}[0-9]{4}$',
        ],
        "1059": [
            '^[0-9]{13}$|^[1-9]{1}[0-9]{3}[/]{1}[0-9]{9}$|^[0-9]{8}(A)[0-9]{4}$|^[0-9]{8}[A-Z]{1}[0-9]{4}$',
        ],
        "1232": [
            '^FAL[0-9]{7}$|CLM[0-9]{3}-A[0-9]{2}[-]{1}[0-9]{6}$',
        ],
        "1518": [
            '^[0-9]{2}[M]{1}[/]{1}[0-9]{3}000[0-9]{6}$|T[0-9]{2}[-]{1}[0-9]{3}[-]{1}[0-9]{5}$',
            '^[M|P|C]{1}[6]{1}[6|7|8]{1}[A-Z]{2}[C]{1}[-]{1}[0-9]{5}$',
        ],
        "2348": [
            '^[A]{1}[0-9]{5}([C]|[V]|[B]){1}[0-9]{6}$',
        ],
        "992": [
            '^[A-Z]{3}[-][0-9]{2}[-][0-9]{6}$|[A-Z]{3}[-][A-Z]{3}[-][A-Z]{1}[-][0-9]{2}[-][0-9]{6}$',
        ],
        "19": [
            '^[C]{1}[0-9]{7}$|[T]{1}[0-9]{1}[M]{1}[A-Z0-9]{5}$',
        ],
        "11": [
            '^(MO.)[0-9]{3}(-)[0-9]{2}\\/[0-9]{8}$|^[0-9]{5}(CMO)[0-9]{6}$|^[0-9]{2}(-)[0-9]{1}(-)[0-9]{3}(-)[0-9]{6}$|^(CLA-)[0-9]{8}$',
        ],
        "1101": [
            '^(CLM)[-][0-9]{3}[-][M][-][A-Z0-9]{3}[-][0-9]{2}[-][0-9]{6}|[3][0-9]{8}$',
        ],
        "4": [
            '^(CLM)[0-9]{3}(-)(A)[0-9]{2}(-)[0-9]{6}$',
        ],
    },
    "txtAcc_Policy_No": {
        "12": [
            '^[A-Z0-9]{6}[/]{1}[0-9]{3}$',
        ],
        "1232": [
            '^001-ACTP[0-9]{2}[-][0-9]{6}$|001-AMV1[0-9]{2}[-]{1}[0-9]{6}$|001-AMV2[0-9]{2}[-]{1}[0-9]{6}$|001-AMV3[0-9]{2}[-]{1}[0-9]{6}$|001-AMV5[0-9]{2}[-]{1}[0-9]{6}$|001-AMP2[0-9]{2}[-][0-9]{6}$|001-AM2N[0-9]{2}[-][0-9]{6}$|001-AM3N[0-9]{2}[-][0-9]{6}$|001-AM1S[0-9]{2}[-][0-9]{6}$|001-APV1[0-9]{2}[-][0-9]{6}$|001-AWS1[0-9]{2}[-][0-9]{6}$|001-AWS3[0-9]{2}[-][0-9]{6}$|001-AWS5[0-9]{2}[-][0-9]{6}$|[0-9]{3}[-][A-z0-9]{4}[0-9]{2}[-][0-9]{6}$',
        ],
        "992": [
            '^[A-Z]{1}[-][0-9]{1}[-][0-9]{2}[-][0-9]{1}[-][0-9]{6}$|[A-Z]{1}[-][0-9]{1}[-][0-9]{2}[-][A-Z]{1}[-][0-9]{7}$|[A-Z]{2}[-][0-9]{1}[-][0-9]{2}[-][0-9]{1}[-][0-9]{6}$|[A-Z]{3}[-][A-Z]{1}[-][A-Z0-9]{1}[0-9]{2}[-][0-9]{2}[-][0-9]{6}$|[A-Z]{3}[-][A-Z]{1}[-][A-Z0-9]{3}[-][0-9]{2}[-][0-9]{6}$',
        ],
        "19": [
            '^[A-Z0-9]{8}$',
        ],
        "11": [
            '^(MO.)[0-9]{3}(-)[0-9]{2}\\/[0-9]{8}$|^[0-9]{2}(-)[0-9]{2}(-)[0-9]{8}$|^(V)[0-9]{4}(-)[0-9]{8}$|^(VM)[0-9]{3}(-)[0-9]{4}(-)[0-9]{2}(-)[0-9]{6}$|^[0-9]{3}(-)[0-9]{2}\\/[0-9]{7}$|^[M][T][0-9]{14}$|^[M][T][0-9]{13}$',
        ],
        "1101": [
            '^[ก][ท][A-Z]{3}[0-9]{7}|[A-Z0-9]{3}[-][0-9]{8}[/][A-Z]{3}|[0-9]{3}[-][M][-][A-Z0-9]{3}[-][0-9]{2}[-][0-9]{6}$',
        ],
        "4": [
            '^[0-9]{3}(-)((A)[0-9]{5}|(AC)[0-9]{4})(-)[0-9]{6}$',
        ],
    },
    "txtPrb_Number": {
        "1232": [
            '^[0-9]{3}[-][A-Z0-9]{4}[0-9]{2}[-][0-9]{6}$',
        ],
    },
}


def _format_ok(field: str, insurer_code, value) -> bool:
    """None = บริษัทนี้ไม่มีกติการูปแบบ · True = ตรง · False = ผิดรูปแบบ (EMCS จะไม่ให้บันทึก)"""
    pats = _FORMAT_RULES.get(field, {}).get(str(insurer_code or "").strip())
    val = str(value or "").strip()
    if not pats or not val:
        return None
    return any(re.search(p, val) for p in pats)


# บริษัทที่ vlidSurvey มี case แยก และยอมให้ "เลขที่รับแจ้ง" ว่างได้ถ้ามีเลขอ้างอิงอีกช่อง
# (ตรวจจาก JS จริง 2026-07-27: case '1059' = ไอโออิ ใช้เงื่อนไข OR กับ txtRef_Claim_No)
# บริษัทอื่นตกสาย default = **บังคับ txtAcc_ClaimRef_No เสมอ** → ห้ามล้างทิ้ง
_CLAIMREF_OPTIONAL_INSURERS = {"1059"}


def _page_insurer_code(driver) -> str:
    """รหัสบริษัทประกันที่ JS ของ EMCS ใช้ตัดสินเงื่อนไข (getInsurerID → ddlInsurerNameMajor)
    อ่านจากหน้าเว็บโดยตรง เพื่อให้ทุก flow (import / เติม draft / เติมผู้บาดเจ็บ) ได้ค่าเดียวกัน"""
    try:
        return (driver.execute_script(
            "var e=document.getElementById('ddlInsurerNameMajor');return e?e.value:'';")
            or "").strip()
    except Exception:
        return ""


def _field_value(driver, element_id: str) -> str:
    try:
        return (driver.execute_script(
            "var e=document.getElementById(arguments[0]);return e?e.value:'';",
            element_id) or "").strip()
    except Exception:
        return ""


def _checkbox_checked(driver, element_id: str) -> bool:
    try:
        return bool(driver.execute_script(
            "var e=document.getElementById(arguments[0]);return !!(e&&e.checked);", element_id))
    except Exception:
        return False


def _warn_format(driver, field: str, value, label: str, insurer_code: str = None):
    """เตือนเมื่อค่าไม่ตรงแพตเทิร์นที่บริษัทนี้บังคับ — ช่องพวกนี้ห้ามว่างจึงล้างทิ้งไม่ได้
    ได้แต่บอกล่วงหน้าว่าทำไม EMCS ถึงจะไม่ยอมบันทึก จะได้ไม่ต้องไล่หาสาเหตุเอง"""
    code = insurer_code if insurer_code is not None else _page_insurer_code(driver)
    if _format_ok(field, code, value) is False:
        log(f"   ⚠️ {label} '{str(value).strip()}' ไม่ตรงรูปแบบที่บริษัท {code} บังคับ "
            f"— EMCS อาจไม่ยอมให้บันทึกหน้าหลัก")


def _set_or_clear_claim_ref(driver, notify_value, insurer_code: str = None):
    """เลขที่รับแจ้ง (txtAcc_ClaimRef_No, บังคับ *)

    เดิม: รูปแบบไม่ตรง 'ABxx/xx' → ล้างช่องทิ้ง โดยอ้างว่า 'ปล่อยว่าง = ผ่าน'
    ซึ่งจริงเฉพาะไอโออิ (1059) เท่านั้น — ของบริษัทอื่น (เช่นไทยไพบูลย์ 2429 ที่ไม่มี case
    ใน JS เลย) สาย default บังคับช่องนี้เสมอ ล้าง = บันทึกหน้าหลักไม่ผ่านทันที
    ที่ผ่านมารอดเพราะเลขของไทยไพบูลย์บังเอิญมี '/' พอดี (BR10/6905/12524) ไม่ใช่เพราะโค้ดถูก

    2026-08-02: เดิมเช็ค "ขึ้นต้นด้วยอะไรก็ได้แล้วมี /" ซึ่งหลวมเกินไป — ไอโออิบังคับ
    แพตเทิร์นเฉพาะ (HY2010/000001 · ABI12345/0753 · ABCSR0001/01/53) เลขอย่าง
    BR10/6905/12524 ผ่านด่านเก่าแต่ EMCS ตีกลับตอนกดบันทึก ตอนนี้เทียบแพตเทิร์นจริง
    รายบริษัทจาก _FORMAT_RULES (สกัดด้วย tools/emcs_spec.py)
    """
    ref = str(notify_value or "").strip()
    code = str(insurer_code if insurer_code is not None else _page_insurer_code(driver)).strip()
    ok = _format_ok("txtAcc_ClaimRef_No", code, ref)
    if ok is not False:
        # ตรงกติกา หรือบริษัทนี้ไม่มีกติกา → ใส่ได้เลย (ว่างก็ตกไปเช็คด้านล่าง)
        if ref:
            set_text(driver, "txtAcc_ClaimRef_No", ref)
            log(f"   ✓ เลขที่รับแจ้ง: {ref}")
            return
    # เงื่อนไขจริงของ 1059 คือ OR: บ่นเมื่อ txtAcc_ClaimRef_No **และ** txtRef_Claim_No ว่างทั้งคู่
    # → ล้างช่องนี้ได้ก็ต่อเมื่อเลขที่เคลมมีค่าอยู่แล้วเท่านั้น ไม่งั้นว่างทั้งคู่ = บันทึกไม่ผ่าน
    if code in _CLAIMREF_OPTIONAL_INSURERS and _field_value(driver, "txtRef_Claim_No"):
        driver.execute_script(
            "var e=document.getElementById('txtAcc_ClaimRef_No');if(e)e.value='';")
        why = f"รูปแบบไม่ตรง ({ref})" if ref else "ไม่มีเลขที่รับแจ้ง"
        log(f"   – {why} → ปล่อยว่าง (บริษัท {code} ยอมได้เพราะมีเลขที่เคลมแล้ว)")
        return
    if ref:
        set_text(driver, "txtAcc_ClaimRef_No", ref)
        log(f"   ⚠️ เลขที่รับแจ้ง {ref} ไม่ตรงรูปแบบที่บริษัท {code} บังคับ "
            f"แต่ช่องนี้ห้ามว่าง — EMCS อาจไม่ยอมให้บันทึก")
    else:
        log(f"   ⚠️ ไม่มีเลขที่รับแจ้ง และบริษัท {code} บังคับ — บันทึกหน้าหลักจะไม่ผ่าน")


def _fill_policy_extras(driver, data: ClaimData):
    """3 ช่องบล็อกกรมธรรม์ที่ importer ของ EMCS ไม่ได้เซ็ตให้ และ fill_policy ก็ไม่ถูกเรียก
    ในเส้น se-survey (fill_policy มี call site เดียว = fill_one ของสาย ISURVEY)

    ⛔ ต้องเรียกทั้งตอน import ใหม่ **และตอนเปิด draft เดิมมาซ่อม** — เดิมมีแต่ตอน import
       พอ btnUpdate ล้ม (เช่นติดกฎวันที่ของ EMCS) แล้วรันซ้ำด้วย --sesurvey-fill-existing
       3 ช่องนี้จะไม่เคยถูกเขียนเลย เงียบ ๆ · ที่เจ็บสุดคือ **ค่าเสียหายส่วนแรก** ซึ่งเป็นตัวเงิน
       (เจอจากการทดสอบสด 19/08/69 — เคสทดสอบทั้ง 2 ใบ import รอบแรกล้มทั้งคู่)
    ไม่ reuse fill_policy ทั้งก้อน เพราะห้ามแตะบล็อกกรมธรรม์/ประเภทเคลมที่ importer ตั้งไว้แล้ว

    ⚠️ **ยกเว้นประเภทกรมธรรม์** (เพิ่ม 30/08/69) — ช่องนี้ importer ตั้งจาก XML ของเราเอง
    ตอน import ครั้งแรก พอเคสถูกส่งกลับไปแก้แล้วประเภทกรมธรรม์เปลี่ยน โหมดซ่อมไม่เคยเขียน
    ทับ → **EMCS ค้างค่าเก่าตลอดไปเงียบ ๆ** (เจอสด: draft S68426084815 ค้าง '01' ทั้งที่
    ข้อมูลเป็น 'ชั้น 2+' = รหัส '52' แล้ว) · SKIP_UNCHANGED คุมอยู่ = เขียนเฉพาะตอนค่าต่างจริง
    จึงไม่รบกวนเคสที่ importer เพิ่งตั้งค่าถูกอยู่แล้ว
    """
    for fid, val in (("txtAssured_Email", data.assured_email),
                     ("txtDeductible", data.deductible),
                     ("txtDri_Order", data.driver_ticket),
                     ("txtPolicy_Type", data.insure_type)):
        try:
            if str(val or "").strip():
                set_text(driver, fid, val)
        except Exception as e:
            log(f"   ⚠️ เติม {fid} ไม่สำเร็จ: {e}")


def fill_imported(driver, cfg, data: ClaimData, images_folder=None,
                  loss_type: str = "auto", image_type: str = "รูปรถประกัน",
                  severity: str = "เบา", force_new: bool = False,
                  full_billing: bool = True, insurer_code: str = None,
                  allow_continuation: bool = True) -> str:
    """กรอกเคลมผ่านโหมด "นำเข้า XML": ให้ EMCS import ฟอร์มหลักจาก SURV_REPORT XML
    แล้วบอทอุดช่องว่าง/แก้ที่ import ทำพลาด + กรอกส่วนที่ import ไม่แตะ

    ต่างจาก fill_one: ใช้ import_xml_report แทน new_report + ไม่กรอก
    ประเภทเคลม/บริษัท/กรมธรรม์ (import ตั้งให้แล้ว) + บันทึกหน้าหลักด้วย btnUpdate
    ข้อดี: popup ความเสียหายเป็น free-text 20 ช่อง (vs cmdNewReport 8) → รองรับ >8
    ได้ดีกว่าเมื่อชิ้นส่วน match checklist ไม่ได้ / import ลดงานกรอกฟอร์มหลักลงมาก"""
    # งานต่อเนื่อง (มีเรื่องเดิม + invoice ใหม่) → ใช้ flow เดิม (ไม่ import — แก้ครั้งถัดไป)
    if not force_new:
        try:
            existing = find_existing_reports(driver, data.claim_value)
        except Exception as e:
            log(f"   ⚠️ ตรวจเรื่องเดิมไม่สำเร็จ ({type(e).__name__}) — ทำต่อแบบสร้างใหม่")
            existing = []
        cont = continuation_esurvey(existing, data.invoice_value)
        if cont and not allow_continuation:
            # ⛔ กติกา user (02/08/69 · ย้ำ 19/08/69): งานครั้งที่ 2 ของเคสที่มาจาก se-survey
            #    **หัวหน้าทำเอง** บอทรับผิดชอบเฉพาะครั้งที่ 1 — เดิมเส้นนี้ไหลเข้าโหมด
            #    งานต่อเนื่องเงียบ ๆ แล้วไปกรอกหน้าค่าใช้จ่ายทับของที่หัวหน้ากรอกไว้ได้
            raise RuntimeError(
                f"เคลม {data.claim_value} มีเรื่องอยู่แล้วใน EMCS ({cont}) และใบแจ้งหนี้เป็นเลขใหม่ "
                f"({data.invoice_value}) = งานครั้งที่ 2 ขึ้นไป" + chr(10) +
                "งานต่อเนื่องของเคสจาก se-survey เป็นงานของหัวหน้า บอทไม่กรอกให้ "
                "(ทำเฉพาะครั้งที่ 1) — เข้าไปกรอกหน้าค่าใช้จ่ายบน EMCS เอง")
        if cont:
            log(f"EMCS: เคลมนี้มีเรื่องเดิม + invoice ใหม่ → โหมดงานต่อเนื่อง (ต่อจาก {cont})")
            return fill_continuation(driver, cfg, data, cont, full_billing=full_billing,
                                     images_folder=images_folder, image_type=image_type)
        guard_duplicate_report(driver, data, force_new, existing=existing)
    else:
        guard_duplicate_report(driver, data, force_new)

    # นำเข้า XML → สร้าง draft + เติมฟอร์มหลัก ~90% → frmSurvey โหมดแก้
    esurvey = import_xml_report(driver, cfg, data, insurer_code=insurer_code)
    # ตั้งแต่บรรทัดนี้ draft มีอยู่จริงใน EMCS แล้ว (ลบไม่ได้) — ถ้าขั้นถัดไปพัง
    # ผู้เรียกต้องรู้เลข e-Survey เพื่อ mark ฝั่ง se-survey ให้ตรงความจริง ไม่งั้น
    # --sesurvey-fill-existing จะไม่ยอมทำงาน ("ยังไม่เคย import") ทั้งที่ draft เกิดแล้ว
    fill_imported.last_draft_esurvey = esurvey
    main_window = driver.current_window_handle
    resolved_loss = resolve_loss_type(data, loss_type)

    # อุดช่องว่าง/แก้ที่ import ทำพลาด (reuse fill_* เดิม — ค่าจาก ClaimData แหล่งเดียวกับ XML)
    # ไม่แตะ ประเภทเคลม/บริษัท/กรมธรรม์ (import ตั้งถูกแล้ว + เลี่ยง postback layout เคลมสด)
    set_rule_context(claim=data.claim_value, survey_no=data.invoice_value, page="หน้าหลัก")
    reset_filled()          # เริ่มจำค่าที่กรอกของหน้าหลัก (เฟส 2 — อ่านกลับมาเทียบ)
    fill_severity(driver, severity)
    fill_car(driver, data)        # แก้ ddlCType (code-based) + จังหวัด/ยี่ห้อ
    # import เซ็ตจังหวัดแต่ไม่ cascade อำเภอ → บังคับจังหวัดว่างก่อน fill (เลือกใหม่จริง)
    _recascade_province(driver, "ddlDri_ProvinceID")
    fill_driver(driver, data)     # แก้ คำนำหน้า + แยกชื่อ-สกุล + อำเภอผู้ขับขี่
    _recascade_province(driver, "ddlAcc_ProvinceID")
    fill_accident(driver, data, loss_type=resolved_loss)  # อำเภอเกิดเหตุ + ลักษณะความเสียหาย
    fill_verdict(driver, data)

    _fill_policy_extras(driver, data)

    _set_or_clear_claim_ref(driver, data.notify_value)

    try:
        saved = save_main_form(driver, data, button_id="btnUpdate", is_new=False)
    except Exception:
        _verify_after_save(driver, data, "หน้าหลัก (บันทึกไม่ผ่าน)")
        raise
    _verify_after_save(driver, data, "หน้าหลัก")
    verify_car_saved(driver, data,
                     lambda: save_main_form(driver, data, button_id="btnUpdate",
                                            is_new=False))
    esurvey = esurvey or saved
    if not esurvey:
        try:
            esurvey = continuation_esurvey(
                find_existing_reports(driver, data.claim_value),
                data.invoice_value) or ""
        except Exception:
            esurvey = ""

    # ส่วนที่ import ไม่เติม: คู่กรณี (สร้าง row เปล่า)/ผู้บาดเจ็บ/ทรัพย์สิน + ความเสียหาย
    fill_third_parties(driver, data)
    fill_damage_list(driver, data, main_window)
    fill_injuries(driver, data)
    fill_assets(driver, data)

    if images_folder is not None:
        upload_images(driver, images_folder, image_type=image_type,
                      n_opponents=len(data.third_parties or []),
                      n_injuries=len(data.injuries or []),
                      n_assets=len(data.assets or []))

    fill_billing(driver, data, full_billing=full_billing, leave=False)
    return esurvey


def run_import(driver, cfg, data: ClaimData, images_folder=None,
               loss_type: str = "auto", image_type: str = "รูปรถประกัน",
               severity: str = "เบา", force_new: bool = False,
               full_billing: bool = True, insurer_code: str = None,
               allow_continuation: bool = True) -> str:
    """login แล้วกรอกเคลมเดียวผ่านโหมดนำเข้า XML

    allow_continuation=False → เจอ "งานครั้งที่ 2" แล้วหยุด ไม่กรอกให้
    (ใช้กับเส้น --sesurvey-case ตามกติกา user: งานต่อเนื่องของเคสเรา หัวหน้าทำเอง)"""
    login(driver, cfg)
    return fill_imported(driver, cfg, data, images_folder=images_folder,
                         loss_type=loss_type, image_type=image_type,
                         severity=severity, force_new=force_new,
                         full_billing=full_billing, insurer_code=insurer_code,
                         allow_continuation=allow_continuation)


def fill_existing_report(driver, cfg, data: ClaimData, esurvey: str = "",
                         images_folder=None, loss_type: str = "auto",
                         image_type: str = "รูปรถประกัน", severity: str = "เบา",
                         full_billing: bool = True) -> str:
    """เปิด draft 'ที่มีอยู่แล้ว' (import มาแล้ว) → เติมหน้าหลัก + คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน/
    ความเสียหาย/รูป/ค่าใช้จ่าย → บันทึก (btnUpdate) — **ไม่ import ซ้ำ ไม่สร้าง draft ใหม่ ไม่กดส่งงาน**

    ใช้เมื่อ import สร้าง draft ไว้แล้วแต่หน้าหลักยังไม่ครบ (เช่น btnUpdate เคยล้ม) — เปิดเรื่องเดิม
    มาเติมให้ครบ. flow เดียวกับส่วน 'หลัง import' ของ fill_imported เป๊ะ ยกเว้นข้าม import_xml_report
    ⚠️ ควรใช้กับ draft ที่ยังไม่ได้เติมส่วนคู่กรณี/ความเสียหาย/รูป (ไม่งั้นอาจเพิ่ม row ซ้ำ)"""
    login(driver, cfg)
    reports = find_existing_reports(driver, data.claim_value)
    if not reports:
        raise RuntimeError(
            f"ไม่พบ draft ของเคลม {data.claim_value} ใน EMCS — ยังไม่มีเรื่องให้เติม "
            "(ต้อง import สร้าง draft ก่อน)")
    target = _pick_draft_report(reports, esurvey)
    log(f"EMCS: เปิด draft เดิม {target} เพื่อเติมข้อมูลหน้าหลัก (ไม่ import ซ้ำ)")
    wait_clickable(driver, By.XPATH,
                   f"//a[normalize-space(text())='{target}']", 20).click()
    wait_visible(driver, By.ID, "btnUpdate", 20)
    main_window = driver.current_window_handle
    resolved_loss = resolve_loss_type(data, loss_type)

    # โหมดนี้เปิดของเดิมที่กรอกไปแล้ว → เขียนเฉพาะช่องที่ค่าต่างจริง
    # (เดิมพิมพ์ทับทุกช่องซ้ำทั้งหน้า ช้าโดยไม่ได้อะไร — user รายงาน 2026-08-10)
    set_skip_unchanged(True)
    try:
        # เติมหน้าหลัก (เหมือน fill_imported หลัง import)
        # โหมดซ่อมก็ต้องตรวจกลับ — ที่จริงยิ่งต้องตรวจ เพราะเข้ามาเติมของที่รอบก่อนพลาด
        set_rule_context(claim=data.claim_value, survey_no=data.invoice_value,
                         page="หน้าหลัก (โหมดซ่อม)")
        reset_filled()
        fill_severity(driver, severity)
        fill_car(driver, data)
        _recascade_province(driver, "ddlDri_ProvinceID")
        fill_driver(driver, data)
        _recascade_province(driver, "ddlAcc_ProvinceID")
        fill_accident(driver, data, loss_type=resolved_loss)
        fill_verdict(driver, data)
        _fill_policy_extras(driver, data)
        _set_or_clear_claim_ref(driver, data.notify_value)
        try:
            save_main_form(driver, data, button_id="btnUpdate", is_new=False)
        except Exception:
            _verify_after_save(driver, data, "หน้าหลัก (โหมดซ่อม · บันทึกไม่ผ่าน)")
            raise
        _verify_after_save(driver, data, "หน้าหลัก (โหมดซ่อม)")

        # ส่วนที่ import ไม่เติม (คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน/ความเสียหาย/รูป/ค่าใช้จ่าย)
        fill_third_parties(driver, data)
        fill_damage_list(driver, data, main_window)
        fill_injuries(driver, data)
        fill_assets(driver, data)
        if images_folder is not None:
            # โหมดเติม draft เดิม = งานครั้งเดิม → ชุดรูปต้องตรงกับ se-survey เป๊ะ
            # (ไม่ใช่สะสมทับกันไปเรื่อย ๆ) ⛔ ไม่ใช้กับงานต่อเนื่องซึ่งอัปเข้าครั้งใหม่
            upload_images(driver, images_folder, image_type=image_type,
                          n_opponents=len(data.third_parties or []),
                          n_injuries=len(data.injuries or []),
                          n_assets=len(data.assets or []),
                          replace_round=True)
        fill_billing(driver, data, full_billing=full_billing, leave=False)
    finally:
        set_skip_unchanged(False)   # ธงเป็น global — ห้ามค้างไปถึงงานถัดไปในโปรเซสเดียวกัน
    return target


def fill_injured_only_existing(driver, cfg, data: ClaimData, esurvey: str = "",
                              loss_type: str = "auto", severity: str = "เบา") -> str:
    """เปิด draft เดิม → re-save หน้าหลัก (ปลดล็อกเมนู) → เติม **เฉพาะบล็อกผู้บาดเจ็บ** + บันทึก —
    **ไม่แตะคู่กรณี/ความเสียหาย/ทรัพย์สิน/รูป/ค่าใช้จ่าย** (กันเพิ่ม row ซ้ำ + กันอัปรูปซ้ำ)

    ใช้เมื่อบล็อกผู้บาดเจ็บ save ไม่ผ่านตอน import (เช่น รพ.ว่าง ติด required-gate) แล้วแก้ด้วย
    _dash ('-'). หน้าหลัก = record เดียว (overwrite ไม่ซ้ำ) ต้อง re-save เพื่อปลดล็อกเมนูผู้บาดเจ็บ;
    ddlInj_Count เป็น absolute → ได้จำนวนผู้บาดเจ็บพอดี ไม่ซ้ำ. ไม่กดส่งงาน."""
    login(driver, cfg)
    reports = find_existing_reports(driver, data.claim_value)
    if not reports:
        raise RuntimeError(
            f"ไม่พบ draft ของเคลม {data.claim_value} ใน EMCS — ยังไม่มีเรื่องให้เติม")
    target = _pick_draft_report(reports, esurvey)
    log(f"EMCS: เปิด draft เดิม {target} เพื่อเติม 'เฉพาะผู้บาดเจ็บ' (ไม่แตะส่วนอื่น)")
    wait_clickable(driver, By.XPATH,
                   f"//a[normalize-space(text())='{target}']", 20).click()
    wait_visible(driver, By.ID, "btnUpdate", 20)
    resolved_loss = resolve_loss_type(data, loss_type)

    # re-save หน้าหลัก (ปลดล็อกเมนูผู้บาดเจ็บ) — record เดียว overwrite ไม่ซ้ำ
    fill_severity(driver, severity)
    fill_car(driver, data)
    _recascade_province(driver, "ddlDri_ProvinceID")
    fill_driver(driver, data)
    _recascade_province(driver, "ddlAcc_ProvinceID")
    fill_accident(driver, data, loss_type=resolved_loss)
    fill_verdict(driver, data)
    _set_or_clear_claim_ref(driver, data.notify_value)
    save_main_form(driver, data, button_id="btnUpdate", is_new=False)

    # เติมเฉพาะผู้บาดเจ็บ (self-contained: unlock เมนู → set count → fill → save)
    fill_injuries(driver, data)
    return target
