"""ฝั่งกรอกข้อมูล: login EMCS → สร้างงานใหม่ → กรอกทุกส่วน → อัปโหลดรูป → ค่าใช้จ่าย

โมเดลความปลอดภัย: "บันทึก" ทุกหน้า = draft แก้ไขได้ สคริปต์กดให้ครบ
จุด commit จริงคือปุ่ม 'ส่งงานใหม่' หน้าค่าใช้จ่าย — ไม่กดให้เด็ดขาด
"""
import hashlib
import re
import time
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
    set_text,
    set_textarea,
    split_hhmm,
    to_buddhist_date,
    today_buddhist,
    wait_clickable,
    wait_for_image_select,
    wait_for_injury_inputs,
    wait_for_manual_fill,
    wait_present,
    wait_visible,
)
from .car_brand import BRAND_MIN_SCORE, normalize_brand
# ชื่อ/คำนำหน้า/เพศ อยู่ที่ claim_data (ฝั่งอ่าน ISURVEY ต้องใช้ด้วย) — re-export
# ไว้ที่นี่เพื่อให้ผู้เรียกเดิม (main.py, webui.py, test_smoke.py) ไม่ต้องแก้
from .claim_data import (  # noqa: F401
    CHILD_TITLE_AGE,
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
    # ค่าสั้นที่แอปมือถือเก็บจริง (survey_form_screen.dart _faultDropdown เก็บ key ไม่ใช่ label)
    # ต้อง map ตรงตัว ห้ามพึ่ง fuzzy: 'ฝ่ายผิด' ได้ WRatio 90 เท่ากันทั้ง 'รถประกันเป็นฝ่ายผิด'
    # และ 'รถคู่กรณีเป็นฝ่ายผิด' → extractOne ตัดสินด้วยลำดับ dict = เสี่ยงพลิกฝ่ายทั้งสำนวน
    "ฝ่ายผิด": "rdoAcc_Cause00",
    "รถประกันฝ่ายผิด": "rdoAcc_Cause00",
    "คู่กรณีผิด": "rdoAcc_Cause01",
    "ฝ่ายถูกและผิด": "rdoAcc_Cause04",
}

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

    ISURVEY **ไม่มี**ข้อมูล 'ลักษณะความเสียหาย' (มีแต่ 'ลักษณะการเกิดเหตุ'
    = acc_type_desc และ 'ผลคดี' = acc_result) — จึงเดาให้ไม่ได้สำหรับเคลมสด
    - ไม่มีคู่กรณี (เคลมแห้ง) → 'เคลมแห้ง' (โครงสร้างเคลมระบุได้แน่นอน ไม่ใช่การเดา)
    - มีคู่กรณี (เคลมสด) → '' : ไม่มีข้อมูลต้นทาง → fill_accident หยุดรอผู้ใช้เลือกเอง
      บนหน้า EMCS (รูปแบบเดียวกับ field บังคับอื่น เช่น ยี่ห้อ/มีประกันภัยที่)
    - ระบุเอง (--loss-type) → ใช้ตามนั้น"""
    if requested != "auto":
        return requested
    if not data.third_parties:
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


def fill_third_parties(driver, data: ClaimData):
    """กรอกข้อมูลรถคู่กรณีทุกคันจากข้อมูล XML ของ ISURVEY แล้วกดบันทึกรถคู่กรณี

    สำคัญ: ส่วนนี้ถูก disable จาก server จนกว่าจะบันทึกหน้าหลักครั้งแรก
    (พิสูจน์จาก probe: toggle ฝั่ง client ใดๆ ไม่ปลด) — ต้องเรียกหลัง
    save_main_form เท่านั้น / บล็อกคู่กรณีโผล่ด้วย JS showOtherVehicle()
    ทันทีที่เลือกจำนวน"""
    tps = data.third_parties
    if not tps:
        return
    main_window = driver.current_window_handle

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
        if tp.get("veh_type", "").strip():
            fuzzy_select(driver, p + "ddlCType", tp["veh_type"], presleep=0.5,
                         label=f"ประเภทรถคู่กรณี {n + 1}")
            time.sleep(2)   # รอ postback โหลดตัวเลือกยี่ห้อ + ให้ค่าประเภทรถนิ่ง
        else:
            log(f"   - ไม่มีประเภทรถคู่กรณี {n + 1} จาก ISURVEY — เลือกเองตอนตรวจ")
        # ยี่ห้อ — มีตัวเลือกหลังเลือกประเภทรถ; ถ้ายังว่าง (ไม่มี veh_type) ข้าม
        if _select_has_options(driver, p + "ddlCmfg"):
            # ไทย→อังกฤษ: ตัวเลือกยี่ห้อของ EMCS เป็นอังกฤษล้วน แต่ se-survey ส่งไทยมา
            fuzzy_select(driver, p + "ddlCmfg",
                         normalize_brand(tp.get("car_brand", "")),
                         label=f"ยี่ห้อรถคู่กรณี {n + 1}", timeout=5,
                         min_score=BRAND_MIN_SCORE)
        else:
            log(f"   - ข้ามยี่ห้อรถคู่กรณี {n + 1} (เลือกประเภทรถก่อน ตัวเลือกยี่ห้อถึงจะขึ้น)")
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
        missing = _parse_missing_fields(alert_text)
        label = f"ข้อมูล{name}ที่ยังขาด" + (f": {missing}" if missing else "")
        if wait_for_manual_fill(label, reason=(alert_text or "").strip()):
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


def fill_injuries(driver, data: ClaimData):
    """กรอกผู้บาดเจ็บ (Tab 5) — กดเมนู imbInjure_Person → เลือกจำนวน ddlInj_Count
    → กรอกทีละบล็อก (dtlInj_ctl00_wuInj_*) → บันทึก btnSave_InjurePerson
    (รูปแบบเดียวกับคู่กรณี; ปลดล็อกหลังบันทึกหน้าหลัก) — เรียกหลัง save_main_form"""
    injs = data.injuries
    if not injs:
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

        # ประเภทบาดเจ็บ — value ของ ddlWounded_Type = code XML (01-06) ตรงๆ
        wt = (inj.get("wounded_type", "") or "").strip()
        if wt:
            try:
                Select(driver.find_element(By.ID, p + "ddlWounded_Type")
                       ).select_by_value(wt)
                log(f"   ✓ ประเภทบาดเจ็บ (code {wt})")
            except Exception:
                log(f"   ⚠️ เลือกประเภทบาดเจ็บ {n + 1} (code {wt}) ไม่ได้")
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
        # ความสัมพันธ์ผู้บาดเจ็บ — value ของ dropdown = รหัส RELATION ตรงจาก producer
        rel = (inj.get("relation", "") or "").strip()
        if rel:
            try:
                Select(driver.find_element(By.ID, p + "ddlDri_Relation_ID")
                       ).select_by_value(rel)
                log(f"   ✓ ความสัมพันธ์ผู้บาดเจ็บ (code {rel})")
            except Exception:
                log(f"   ⚠️ เลือกความสัมพันธ์ผู้บาดเจ็บ {n + 1} (code {rel}) ไม่ได้")

    _save_section(driver, "btnSave_InjurePerson", "ผู้บาดเจ็บ")


def fill_assets(driver, data: ClaimData):
    """กรอกทรัพย์สิน (Tab 6) — กดเมนู imbAsset → เลือกจำนวน ddlAsset_Count →
    กรอกทีละบล็อก (dtlAsset_ctl00_wuAsset_*) → บันทึก btnSave_Asset
    (รูปแบบเดียวกับคู่กรณี) — เรียกหลัง save_main_form"""
    assets = data.assets
    if not assets:
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

_JS_REPORT_STATUS = r"""
const claim = arguments[0];
let result = null;
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
  const surv = cells.find(c => /SEABI[-\w]/i.test(c)) || "";
  result = {esurvey: t, status: statusIdx >= 0 ? (cells[statusIdx] || "") : "",
            survey_no: surv};
});
return result;
"""


def report_status(driver, claim: str):
    """ค้นเรื่องของเคลมในหน้า EMCS → คืน {esurvey, status, survey_no} (None ถ้าไม่เจอ)"""
    if not (claim or "").strip():
        return None
    wait_visible(driver, By.ID, "txtRef_Claim_No", 20)
    box = driver.find_element(By.ID, "txtRef_Claim_No")
    box.clear()
    box.send_keys(claim.strip())
    driver.find_element(By.ID, "btnSearch").click()
    time.sleep(3)
    return driver.execute_script(_JS_REPORT_STATUS, claim.strip())


def is_report_submitted(driver, claim: str):
    """ตรวจว่าเคลมนี้ "กดส่งงานใหม่แล้วจริงไหม" — gate ก่อนแจ้ง ISURVEY
    คืน (submitted: bool, reason: str). conservative: ต้องเจอเรื่อง + สถานะ
    ไม่ใช่ draft ('รายงานสร้างใหม่') ถึงถือว่าส่งแล้ว"""
    info = report_status(driver, claim)
    if not info:
        return False, "ไม่พบเรื่องของเคลมนี้ใน EMCS"
    st = (info.get("status") or "").strip()
    if not st:
        return False, "อ่านสถานะเรื่องไม่ได้"
    if st in DRAFT_STATUSES:
        return False, f"ยังไม่ได้กดส่งงานใหม่ (สถานะ: {st})"
    return True, f"ส่งงานแล้ว (สถานะ: {st})"


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
INSURER_MAJOR_ID = "1059"   # ไอโออิกรุงเทพประกันภัย (บริษัทเดียวของโปรเจกต์)


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

def fill_claim_type(driver, claim_type: str):
    """เลือกประเภทเคลม (1-4) — หา radio จาก container id ก่อน
    ถ้าไม่ได้ค่อย fallback ไป absolute XPath เดิมที่พิสูจน์แล้วว่าใช้ได้"""
    log(f"EMCS: เลือกประเภทเคลม = {claim_type}")
    container = wait_visible(driver, By.ID, "rdoSurv_Claim_Type")

    idx = int(claim_type) - 1
    if not 0 <= idx <= 3:
        raise ValueError(f"ประเภทเคลมไม่ถูกต้อง: {claim_type!r} (ต้องเป็น 1-4)")

    try:
        radios = container.find_elements(By.TAG_NAME, "input")
        radios[idx].click()
    except Exception:
        driver.find_element(
            By.XPATH,
            "/html/body/form/table[3]/tbody/tr[1]/td[3]/table/tbody/tr/td[2]"
            f"/table/tbody/tr/td[{idx + 1}]/input",
        ).click()


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


def fill_insurer_and_refs(driver, data: ClaimData):
    """เลือกบริษัทประกัน (ตัวเลือกแรกตามเดิม) + เลขเซอร์เวย์/เลขเคลม"""
    log("EMCS: เลือกบริษัทประกัน + เลขอ้างอิง")
    wait_clickable(driver, By.XPATH, '//*[@id="ddlInsurerNameMajor"]/option[2]', 30).click()
    wait_clickable(driver, By.ID, "ddlInsurer_Name", 30)
    driver.find_element(By.XPATH, '//*[@id="ddlInsurer_Name"]/option[2]').click()

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
        if cur == want:
            log(f"   ✓ ประเภทรถ: '{cur}' ตรงกับข้อมูลอยู่แล้ว — ไม่ต้องเปลี่ยน")
            return
        log(f"   ⛔ ประเภทรถบนเรื่องเป็น '{cur}' แต่ข้อมูลว่า '{want}' — "
            "ไม่เปลี่ยนให้ (EMCS จะลบข้อมูลที่บันทึกไว้ทั้งหมด) แก้เองบนหน้าจอถ้าจำเป็น")
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


def _select_car_brand(driver, car_brand, label="ยี่ห้อรถ"):
    """เลือก 'ยี่ห้อรถ' (ddlCMFG) ให้ทน race ของ cascade ประเภทรถ→ยี่ห้อ:
    ตัวเลือกยี่ห้อถูกโหลดจาก onchange postback ของ ddlCType ซึ่งบางครั้ง commit ไม่ทัน
    presleep เดิม → ddlCMFG ว่าง (มีแต่ '-- ระบุ --'). แก้แบบเดียวกับที่คนต้องกดประเภทรถ
    ซ้ำเองบนหน้าเว็บ: รอ list โหลด → ถ้ายังว่างให้ยิง onchange ของ ddlCType ซ้ำแล้วรอ
    → ค่อย fuzzy_select. list ไม่ขึ้นจริง = หยุดรอคน (required)

    ยี่ห้อใช้เกณฑ์เข้ม (min_score=90): ลิสต์ยี่ห้อถูกกรองตามประเภทรถ ยี่ห้อที่ไม่มีใน
    ลิสต์นั้นจะไป "เกาะ" ยี่ห้ออื่นแบบเงียบ ๆ ได้ (TRIUMPH→TRUMPCHI 80) — ค่าที่ถูกต้อง
    ได้ ≥90 เสมอ (TOYOTA 100 / 'MG 3'→MG 90) จึงตัดที่ 90 แล้วให้คนเลือกเองถ้าไม่ถึง"""
    car_brand = normalize_brand(car_brand)   # ไทย→อังกฤษ: ตัวเลือก EMCS เป็นอังกฤษล้วน
    time.sleep(2)   # รอ postback ประเภทรถ โหลดตัวเลือกยี่ห้อ (เท่าจังหวะฝั่งคู่กรณี)
    if not _select_has_options(driver, "ddlCMFG"):
        # repopulate ยี่ห้อ: ยิง __doPostBack ของ ddlCType ตรง ๆ ด้วยค่าที่เลือกอยู่
        # (เดิม dispatchEvent('change') → วิ่งผ่าน checkChangeCType = เด้ง popup ทำลายข้อมูล
        #  แล้วบอทค้าง; postback ตรงให้ผลเดียวกันแต่ไม่มี popup — ยืนยันบน draft จริง)
        try:
            driver.execute_script(
                "setTimeout(function(){__doPostBack('ddlCType','');},50);")
        except Exception:
            pass
        try:
            WebDriverWait(driver, 12).until(
                lambda d: _select_has_options(d, "ddlCMFG"))
        except TimeoutException:
            pass
    if _select_has_options(driver, "ddlCMFG"):
        fuzzy_select(driver, "ddlCMFG", car_brand, label=label,
                     required=True, timeout=5, min_score=BRAND_MIN_SCORE)
    else:
        wait_for_manual_fill(
            label, "ตัวเลือกยี่ห้อยังไม่โหลด (postback ประเภทรถ ไม่สมบูรณ์)",
            select_id="ddlCMFG")


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
                             "ISURVEY ไม่มีเพศ + แยกจากคำนำหน้าไม่ได้ — ต้องเลือกเอง")

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
            "ต้นทางไม่มีคำนำหน้า และไม่มีเพศให้อนุมาน — เลือกเอง")

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
    """เลือกผลคดี (radio) จากข้อความผลคดีของ ISURVEY ด้วย fuzzy matching"""
    log("EMCS: เลือกผลคดี")
    wait_visible(driver, By.ID, "rdoAcc_Cause00")

    if not data.acc_result.strip():
        log("   ⚠️ ไม่มีข้อมูลผลคดีจาก ISURVEY — ข้าม (เลือกเองบนหน้าเว็บ)")
        return

    # ผลคดีเลือกผิด = สลับฝ่ายผิดทั้งสำนวน → ห้ามพึ่ง fuzzy ล้วน
    # ('ฝ่ายผิด' ได้ 90 เท่ากันทั้ง 'รถประกันเป็นฝ่ายผิด' และ 'รถคู่กรณีเป็นฝ่ายผิด'
    #  extractOne จึงตัดสินด้วยลำดับใน dict = เสี่ยงพลิกฝ่าย)
    _res = " ".join(str(data.acc_result).split())
    if _res in CAUSE_RADIO:
        label, score = _res, 100
    else:
        best = process.extractOne(_res, list(CAUSE_RADIO.keys()), scorer=fuzz.WRatio)
        label, score = best[0], best[1]
        _tie = [k for k in CAUSE_RADIO
                if fuzz.WRatio(_res, k) >= score - 1 and CAUSE_RADIO[k] != CAUSE_RADIO[label]]
        if _tie:
            log(f"   ⚠️ ผลคดี '{_res}' คลุมเครือ (คะแนนเท่ากับ {_tie}) — ไม่เดา ข้ามให้คนเลือกเอง")
            wait_for_manual_fill("ผลคดี (ฝ่ายประมาท)",
                                 f"ข้อความ '{_res}' ตรงได้หลายตัวเลือก เลือกเองบนหน้า EMCS")
            return
    log(f"   ✓ ผลคดี: '{_res}' → '{label}' (score {score:.0f})")
    driver.find_element(By.ID, CAUSE_RADIO[label]).click()
    # "การเรียกร้องค่าเสียหายจากคู่กรณี" (chkOpo_Result + ยอดเงิน 2 ช่อง) ไม่ได้ผูกกับผลคดี
    # ยืนยันจากงานจริงที่พนักงานกรอก (เคลมไอโออิ 2026013058298): ผลคดี = rdoAcc_Cause03
    # 'รอสรุปผลคดี' แต่ยังติ๊ก chkOpo_Result_0 ไว้ → เดิมบอทกรอกเฉพาะตอน rdoAcc_Cause01
    # ทำให้เซอร์เวย์ติ๊ก+พิมพ์ยอดเงินไปฟรีทุกเคสที่ผลคดีเป็นอย่างอื่น
    _fill_opponent_fault(driver, data)
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


def _fill_opponent_fault(driver, data: ClaimData):
    """ผลคดี = 'รถคู่กรณีเป็นฝ่ายผิด' → EMCS บังคับ (vlidSurvey) 2 อย่างพร้อมกัน:
    'คู่กรณีคันที่' (txtAcc_Cause_No) + ติ๊ก 'การเรียกร้องค่าเสียหายจากคู่กรณี'
    อย่างน้อย 1 ใน 5 (chkOpo_Result_0..4) — ไม่ครบ = กดบันทึกไม่ผ่าน คนต้องมาเติมเอง

    ⚠️ ต้อง .click() จริง (ห้าม set .checked ผ่าน JS) เพราะ onclick ของ EMCS เป็นตัว
    ปลดล็อกช่อง txtOpo_Pay/txtOpo_Recovery_Amount ถ้าไม่ยิง set_text จะไม่เข้า"""
    # 1) คู่กรณีคันที่ — ใช้ค่าจากรายงานถ้ามี; ไม่มีแต่มีคู่กรณีคันเดียว = คันที่ 1 แน่นอน
    no = str(getattr(data, "acc_fault_opponent_no", "") or "").strip()
    if not no and len(data.third_parties or []) == 1:
        no = "1"
    if no:
        set_text(driver, "txtAcc_Cause_No", no)
        log(f"   ✓ คู่กรณีคันที่ = {no}")
    else:
        log(f"   ⚠️ ไม่รู้ว่าคู่กรณีคันไหนผิด (มี {len(data.third_parties or [])} คัน) — "
            "กรอก 'คู่กรณีคันที่' เองบนหน้าจอ (EMCS บังคับ)")

    # 2) การเรียกร้องค่าเสียหายจากคู่กรณี
    picked = [s.strip() for s in str(data.opo_results or "").split(",") if s.strip()]
    if not picked:
        log("   ⚠️ ไม่มีข้อมูล 'การเรียกร้องค่าเสียหายจากคู่กรณี' — ติ๊กเองบนหน้าจอ "
            "(EMCS บังคับอย่างน้อย 1 ข้อ; บอทไม่ติ๊กมั่วแทนเซอร์เวย์)")
        return
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
        except Exception as e:
            log(f"   ⚠️ ติ๊ก '{t}' ไม่ได้ ({type(e).__name__}) — ติ๊กเองบนหน้าจอ")
            continue
        if idx == 4:      # ติ๊ก 'รับเงินจำนวน' แล้ว EMCS บังคับ 2 ช่องเงินนี้
            set_text(driver, "txtOpo_Pay", data.opo_pay)
            set_text(driver, "txtOpo_Recovery_Amount", data.opo_recovery)
            log(f"   ✓ รับเงิน {data.opo_pay} จากเรียกร้องทั้งหมด {data.opo_recovery}")


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


def _parse_missing_fields(alert_text: str) -> str:
    """ดึงรายชื่อช่องที่ระบบฟ้องจากข้อความ validation (บรรทัดแบบ '1. สถานที่เกิดเหตุ')"""
    if not alert_text:
        return ""
    items = re.findall(r"\d+\.\s*(.+)", alert_text)
    return ", ".join(s.strip() for s in items if s.strip())


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
      (มี cap กันลูปไม่รู้จบ — ถ้าไม่มีคนตอบ/แก้ไม่ได้จะ raise)"""
    auto_heal_left = 2   # จำนวนรอบที่ยอมให้ซ่อม dropdown อัตโนมัติ
    for attempt in range(1, 8):
        log(f"EMCS: กดบันทึกหน้าหลัก ({button_id}, รอบ {attempt})")
        wait_clickable(driver, By.ID, button_id).click()
        alert_text = accept_alert(driver)

        if not is_new:
            # โหมดแก้: สำเร็จเมื่อ alert ไม่ใช่ validation ('กรุณา...')
            if "กรุณา" not in (alert_text or ""):
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

        # validation ไม่ผ่าน — ลองซ่อม dropdown ที่หลุดจาก postback ก่อน (อัตโนมัติ)
        if auto_heal_left > 0 and "กรุณา" in (alert_text or "") \
                and _refill_missing_fields(driver, data, alert_text):
            auto_heal_left -= 1
            log("   ↻ กรอก field (dropdown) ที่หลุดซ้ำแล้ว — ลองบันทึกใหม่")
            continue

        # ซ่อมอัตโนมัติไม่ได้ (เช่น text field ว่าง) → หยุดรอให้คนกรอกช่องที่ฟ้องเอง
        missing = _parse_missing_fields(alert_text)
        label = "ข้อมูลหน้าหลักที่ยังขาด" + (f": {missing}" if missing else "")
        if wait_for_manual_fill(label, reason=(alert_text or "").strip()):
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

        driver.find_element(By.ID, prefix + "txtDam_Name").send_keys(name)
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


def _rename_clean_files(paths, name_tmpl: str, idx: int):
    """เปลี่ยนชื่อ paths (list[Path] โฟลเดอร์เดียวกัน เรียงแล้ว) เป็น
    name_tmpl.format(i=idx, seq=ลำดับ) + นามสกุลเดิม — แพทเทิร์นเดียวกับรูปรถประกัน
    (คอลัมน์รายการใน EMCS = ชื่อไฟล์นี้). two-phase กันชนชื่อ + idempotent
    name_tmpl เช่น 'รูปรถคู่กรณีคันที่{i}_{seq}' / 'รูปผู้บาดเจ็บคนที่{i}_{seq}'"""
    if not paths:
        return []
    folder = paths[0].parent
    targets = [f"{name_tmpl.format(i=idx, seq=s)}{p.suffix.lower()}"
               for s, p in enumerate(paths, start=1)]
    if all(p.name == t for p, t in zip(paths, targets)):
        return list(paths)                       # ชื่อถูกหมดแล้ว — ไม่แตะ
    # phase 1: ทุกไฟล์ → ชื่อชั่วคราว (กันชนกับชื่อเป้าที่ไฟล์อื่นถืออยู่)
    temps = []
    for j, p in enumerate(paths):
        tmp = folder / f"__tpren_{j}{p.suffix.lower()}"
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


def upload_images(driver, folder, image_type: str = "รูปรถประกัน", only=None,
                  n_opponents: int = 0, n_injuries: int = 0, n_assets: int = 0):
    """อัปโหลดรูปทั้งหมด: รูปรถประกัน (หลัก) + บุคคลที่สาม (tp_veh/tp_person/tp_prop)

    - รูปรถประกัน: เลือกประเภท image_type ('รูปรถประกัน') — only คุมว่าจะอัปรูปไหน
      (None = ให้ผู้ใช้เลือกบนหน้าเว็บ / console = ทุกไฟล์; list ว่าง = ไม่อัป)
    - รูปคู่กรณี (tp_veh/) → 'รูปรถคู่กรณี คันที่N' / ผู้บาดเจ็บ (tp_person/) →
      'รูปผู้บาดเจ็บ คนที่N' / ทรัพย์สิน (tp_prop/) → 'รูปทรัพย์สิน รายการที่N'
      (option dynamic — โผล่หลังบันทึก section นั้นแล้ว ซึ่ง upload รันหลัง fill_*)
      แยกตามรายการด้วยจำนวน n_opponents/n_injuries/n_assets"""
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
        if only is None:
            only = wait_for_image_select(folder, files)
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

    for r in targets:
        log(f"   จะลบ: [{r['seq']}] {r['name']}  (ประเภท '{r['type']}', "
            f"IMAGEID {r['image_id']}, แนบ {r['added']})")
        if not r["chk"].is_selected():
            r["chk"].click()

    # ตรวจซ้ำหน้างาน: ที่ติ๊กอยู่จริง ต้องเท่ากับเป้าหมายเป๊ะ ๆ
    ticked = [r["name"] for r in list_report_images(driver) if r["chk"].is_selected()]
    if sorted(ticked) != sorted(r["name"] for r in targets):
        raise RuntimeError(
            f"หยุด: ติ๊กไม่ตรงเป้า — ติ๊กอยู่ {ticked} แต่ตั้งใจลบ "
            f"{[r['name'] for r in targets]}")

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

    # หลัง postback ตารางถูก render ใหม่ — อ่านเร็วไปจะเจอ StaleElementReference/แถวยังไม่ครบ
    after = []
    for attempt in range(5):
        try:
            after = list_report_images(driver)
            if after:
                break
        except StaleElementReferenceException:
            pass
        time.sleep(1)
    if not after:
        raise RuntimeError(
            "ลบไปแล้วแต่อ่านตารางรูปหลังลบไม่ได้ — เปิด EMCS ตรวจด้วยตาว่าลบถูกใบไหม")
    gone = {r["name"] for r in before} - {r["name"] for r in after}
    want_set = {r["name"] for r in targets}
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
    upload_images(driver, images_folder, image_type=image_type,
                  only=(None if include_main else []),
                  n_opponents=len(data.third_parties or []),
                  n_injuries=len(data.injuries or []),
                  n_assets=len(data.assets or []))
    return target


# ------------------------------------------------------------------ ค่าใช้จ่าย

def _money(value) -> float:
    """แปลงข้อความจำนวนเงินจาก XML เป็นตัวเลข ('300.00' → 300.0, ว่าง → 0)"""
    try:
        return float(str(value).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _type_fee(driver, elem_id: str, value, label: str):
    """พิมพ์ค่าลงช่องราคา แล้วกด Tab ให้ JS ของหน้าคำนวณยอดรวม"""
    el = driver.find_element(By.ID, elem_id)
    el.clear()
    el.send_keys(str(value), Keys.TAB)
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


def _save_and_exit_billing(driver):
    """โหมด draft-park (se-survey ⚡ นำเข้า / เติม draft เดิม): บันทึกหัวบิล
    (เลขที่ใบแจ้งหนี้ + วันที่วางบิล) ด้วยปุ่ม 'บันทึกราคา' — id ต่างกันตามสถานะงาน
    (`btnSurveySave` draft ใหม่ / `btnSurvey_Update` เปิดมาแก้, ดู _PRICE_SAVE_BUTTONS)
    เป็นปุ่มบันทึกเดียวของหน้า จึงเลี่ยงไม่ได้ถ้าอยากให้หัวบิลติด
    (user อนุญาตให้กดแล้ว 2026-07-27) — จากนั้นกดกลับหน้า Inbox/Outbox
    (wuMenuPage1_imbReturn_In_Out) = 'ออกจากเรื่อง' ปลดล็อกให้คนอื่นเปิดต่อได้
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
    # (2) กลับหน้า Inbox/Outbox = ออกจากเรื่องที่ทำเสร็จ → ปลดล็อกให้คนอื่นเข้าต่อได้
    try:
        click_retry(driver, By.ID, "wuMenuPage1_imbReturn_In_Out")
        try:
            accept_alert(driver, timeout=10)   # เผื่อมี confirm/alert ตอนออกจากเรื่อง — กด 'ตกลง'
        except Exception:
            pass
        log("EMCS: กลับหน้า Inbox/Outbox แล้ว — ออกจากเรื่อง ปลดล็อก (คนอื่นเปิดต่อได้)")
    except Exception as e:
        log(f"   ⚠️ กดปุ่มกลับ Inbox/Outbox (wuMenuPage1_imbReturn_In_Out) ไม่ได้ "
            f"({type(e).__name__}) — กดกลับ/ออกจากเรื่องเองเพื่อปลดล็อก")


def fill_billing(driver, data: ClaimData, full_billing: bool = True,
                 navigate: bool = True):
    """หน้าค่าใช้จ่าย — **กรอกมาก/น้อยขึ้นกับต้นทางข้อมูล** (กติกา user 2026-08-03)
    แล้วกด "บันทึกราคา" (เป็น draft แก้ได้ — จุดส่งงานจริงคือปุ่ม 'ส่งงานใหม่'
    ซึ่งสคริปต์ไม่กดให้เด็ดขาด ต้องตรวจแล้วกดเอง)

    | ต้นทาง | full_billing | กรอกอะไร |
    |---|---|---|
    | **ISURVEY** (มีข้อมูลหัวหน้าครบแล้ว) | True | เต็มหน้า |
    | **se-survey** (ระบบเราเอง) | False | **แค่ 2 ช่อง** เลขที่ใบแจ้งหนี้ + วันที่วางบิล |

    เหตุผล: งาน ISURVEY หัวหน้ากรอกความเห็น+เรทราคาไว้ในระบบเดิมแล้ว ยกมาได้เลย ·
    งาน se-survey หัวหน้ายังไม่ได้กรอก จะไปกรอกใน EMCS เอง — บอทเติมให้จะกลายเป็นขยะ
    ที่หัวหน้าต้องมาลบทิ้ง

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
                    reason=alert_text):
                click_retry(driver, By.ID, "wuMenuPage1_imbSpend")
            else:
                log("   → ข้ามหน้าค่าใช้จ่าย — เข้า/กรอกเองภายหลัง")
                return
        except TimeoutException:
            log("   ⚠️ หน้าค่าใช้จ่ายไม่โหลด (txtBill_No ไม่โผล่) — ข้าม กรอกเอง")
            return
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
        _save_and_exit_billing(driver)
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
    _save_and_exit_billing(driver)
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


def submit_report(driver, cfg, claim):
    """commit งาน: กดปุ่มส่งงานที่มีบนหน้าค่าใช้จ่าย (โหมดแก้ของ draft — live session
    ที่เพิ่งกรอกเสร็จ) — รองรับทั้ง 'ส่งงานใหม่' (งานใหม่) และ 'ส่งผลงานต่อเนื่อง'
    (งานต่อเนื่อง ครั้งที่ 2,3,…) — แล้ว verify ว่าสถานะเปลี่ยนเป็น 'ส่งงานแล้ว' จริง

    คืน (ok: bool, msg: str). จะกดเฉพาะเมื่อ "เจอปุ่ม" (= เป็น draft) เท่านั้น —
    เป็น gate ในตัว (สถานะอื่นไม่มีปุ่มนี้)"""
    btn, label = _find_submit_button(driver)
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
    time.sleep(2)
    try:
        accept_alert(driver, timeout=5)        # เผื่อมี JS alert (ปกติไม่มี)
    except Exception:
        pass
    # หลังกดส่งสำเร็จจะมี SweetAlert modal "สำเร็จ! ส่งงานใหม่...เรียบร้อยแล้ว" → กด OK ปิด
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
    time.sleep(2)

    # verify: กลับหน้ารายการ → ค้นสถานะใหม่ ต้องไม่ใช่ draft แล้ว
    try:
        goto_mainpage(driver, cfg, "")
        info = report_status(driver, claim)
    except Exception as e:
        return False, (f"กดส่งแล้วแต่ตรวจสถานะไม่ได้ ({type(e).__name__}) — "
                       "ตรวจบน EMCS เอง")
    st = (info or {}).get("status", "").strip()
    if st and st not in DRAFT_STATUSES:
        return True, f"ส่งงานสำเร็จ (สถานะ → {st})"
    return False, (f"กดส่งแล้วแต่สถานะยังเป็น '{st or 'อ่านไม่ได้'}' — "
                   "อาจไม่สำเร็จ ตรวจเอง")


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

    รูป: user ยืนยัน 2026-08-03 ว่า **งานครั้งที่ 2 เป็นต้นไปต้องใส่รูปด้วย**
    (เดิมค้างไว้ไม่สรุป เส้นนี้จึงไม่เคยอัปรูปเลย) — รูปใน EMCS ผูกกับ "เคลม" ไม่ใช่
    ครั้งที่ โควตา 80 ใบจึงเป็นของทั้งเคลมร่วมกัน upload_images ตัดให้พอดีโควตาที่
    เหลืออยู่แล้ว (image_quota_left) ครั้งที่ 2 จึงเติมต่อจากที่ครั้งแรกใช้ไป

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
                      n_assets=len(data.assets or []))
    fill_billing(driver, data, full_billing=full_billing, navigate=moved)
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

    fill_claim_type(driver, data.claim_type)
    fill_severity(driver, severity)
    fill_insurer_and_refs(driver, data)
    fill_policy(driver, data)
    fill_car(driver, data)
    fill_driver(driver, data)
    fill_accident(driver, data, loss_type=resolved_loss)
    fill_verdict(driver, data)

    esurvey = save_main_form(driver, data)
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

    fill_billing(driver, data, full_billing=full_billing)
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


def fill_imported(driver, cfg, data: ClaimData, images_folder=None,
                  loss_type: str = "auto", image_type: str = "รูปรถประกัน",
                  severity: str = "เบา", force_new: bool = False,
                  full_billing: bool = True, insurer_code: str = None) -> str:
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
    fill_severity(driver, severity)
    fill_car(driver, data)        # แก้ ddlCType (code-based) + จังหวัด/ยี่ห้อ
    # import เซ็ตจังหวัดแต่ไม่ cascade อำเภอ → บังคับจังหวัดว่างก่อน fill (เลือกใหม่จริง)
    _recascade_province(driver, "ddlDri_ProvinceID")
    fill_driver(driver, data)     # แก้ คำนำหน้า + แยกชื่อ-สกุล + อำเภอผู้ขับขี่
    _recascade_province(driver, "ddlAcc_ProvinceID")
    fill_accident(driver, data, loss_type=resolved_loss)  # อำเภอเกิดเหตุ + ลักษณะความเสียหาย
    fill_verdict(driver, data)

    _set_or_clear_claim_ref(driver, data.notify_value)

    saved = save_main_form(driver, data, button_id="btnUpdate", is_new=False)
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

    fill_billing(driver, data, full_billing=full_billing)
    return esurvey


def run_import(driver, cfg, data: ClaimData, images_folder=None,
               loss_type: str = "auto", image_type: str = "รูปรถประกัน",
               severity: str = "เบา", force_new: bool = False,
               full_billing: bool = True, insurer_code: str = None) -> str:
    """login แล้วกรอกเคลมเดียวผ่านโหมดนำเข้า XML"""
    login(driver, cfg)
    return fill_imported(driver, cfg, data, images_folder=images_folder,
                         loss_type=loss_type, image_type=image_type,
                         severity=severity, force_new=force_new,
                         full_billing=full_billing, insurer_code=insurer_code)


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

    # เติมหน้าหลัก (เหมือน fill_imported หลัง import)
    fill_severity(driver, severity)
    fill_car(driver, data)
    _recascade_province(driver, "ddlDri_ProvinceID")
    fill_driver(driver, data)
    _recascade_province(driver, "ddlAcc_ProvinceID")
    fill_accident(driver, data, loss_type=resolved_loss)
    fill_verdict(driver, data)
    _set_or_clear_claim_ref(driver, data.notify_value)
    save_main_form(driver, data, button_id="btnUpdate", is_new=False)

    # ส่วนที่ import ไม่เติม (คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน/ความเสียหาย/รูป/ค่าใช้จ่าย)
    fill_third_parties(driver, data)
    fill_damage_list(driver, data, main_window)
    fill_injuries(driver, data)
    fill_assets(driver, data)
    if images_folder is not None:
        upload_images(driver, images_folder, image_type=image_type,
                      n_opponents=len(data.third_parties or []),
                      n_injuries=len(data.injuries or []),
                      n_assets=len(data.assets or []))
    fill_billing(driver, data, full_billing=full_billing)
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
