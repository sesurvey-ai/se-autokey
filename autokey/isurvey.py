"""ฝั่งอ่านข้อมูล: login ISURVEY → เปิดเคลม → อ่าน Tab 1-8 → โหลดรูป"""
import time

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from .browser import (
    get_value,
    log,
    wait_clickable,
    wait_loading_gone,
    wait_present,
    wait_value_not_empty,
    wait_visible,
)
from .claim_data import ClaimData
from .images import download_images

# แถบ tab ของหน้ารายละเอียดเคลม (ExtJS ไม่มี id ที่อ่านง่าย จึงยังต้องใช้
# absolute XPath — รวมไว้ที่เดียว ถ้าเว็บเปลี่ยน layout มาแก้ตรงนี้จุดเดียว)
TAB_LINK_XPATH = (
    "/html/body/div[5]/div[2]/div/div/div/div/div/div"
    "/div[1]/div[1]/div[2]/div/a[{n}]"
)

# เมนูนำทางหลังการ login (id ของ ExtJS — ใช้ตามที่พิสูจน์แล้วว่าทำงานได้)
MENU_TREE_HEADER = '//*[@id="treepanel-1024_header-title-textEl"]'
MENU_CASE_LIST = '//*[@id="treeview-1027-record-7"]/tbody/tr/td/div/span'


def login(driver, cfg):
    log("ISURVEY: เปิดหน้า login")
    driver.get(cfg.isurvey_url)
    wait_visible(driver, By.ID, "username-textfield-inputEl")
    driver.find_element(By.NAME, "username-textfield-inputEl").send_keys(
        cfg.isurvey_username
    )
    driver.find_element(By.NAME, "password-textfield-inputEl").send_keys(
        cfg.isurvey_password
    )
    driver.find_element(By.XPATH, '//*[@id="login-button-btnInnerEl"]').click()
    log("ISURVEY: login แล้ว")


def ensure_logged_in(driver, cfg):
    """เปิดเว็บใหม่ — ถ้าเจอหน้า login ก็ login, ถ้า session เดิมยังอยู่ใช้ต่อเลย
    (ใช้ตอนอ่านหลายเคลมต่อกัน: reload หน้าเพื่อกลับสู่สถานะเริ่มต้นทุกเคลม)"""
    driver.get(cfg.isurvey_url)
    try:
        wait_visible(driver, By.ID, "username-textfield-inputEl", 8)
    except TimeoutException:
        log("ISURVEY: session เดิมยังอยู่ — ไม่ต้อง login ใหม่")
        return
    driver.find_element(By.NAME, "username-textfield-inputEl").send_keys(
        cfg.isurvey_username
    )
    driver.find_element(By.NAME, "password-textfield-inputEl").send_keys(
        cfg.isurvey_password
    )
    driver.find_element(By.XPATH, '//*[@id="login-button-btnInnerEl"]').click()
    log("ISURVEY: login แล้ว")


def _case_list_open(driver, timeout) -> bool:
    """หน้ารายการเคลมเปิดอยู่ไหม (ดูจากช่องค้นหาเลขเคลม)"""
    try:
        wait_visible(driver, By.XPATH, '//*[@id="search_claimNo-inputEl"]', timeout)
        return True
    except TimeoutException:
        return False


def open_case_list(driver, attempts: int = 4):
    """กดเมนูไปหน้ารายการเคลม พร้อมตรวจว่าหน้าเปิดจริง

    ถ้ากดเมนูตอนแอปยังโหลดไม่เสร็จ คลิกจะหายเงียบ (เมนูไฮไลต์แต่เนื้อหา
    ไม่เปลี่ยน) — จึงตรวจช่องค้นหาหลังคลิกทุกครั้ง ไม่โผล่ก็กดซ้ำ"""
    for attempt in range(1, attempts + 1):
        try:
            # ถ้าเมนูรายการเคลมยังไม่โผล่ ให้กดหัวข้อเมนูก่อน (กางเมนู)
            try:
                item = wait_visible(driver, By.XPATH, MENU_CASE_LIST, 5)
            except TimeoutException:
                wait_visible(driver, By.XPATH, MENU_TREE_HEADER, 20).click()
                item = wait_visible(driver, By.XPATH, MENU_CASE_LIST, 10)
            item.click()
        except Exception as e:
            log(f"   ⚠️ กดเมนูพลาด (รอบ {attempt}): {type(e).__name__}")

        if _case_list_open(driver, 15):
            log("ISURVEY: เปิดหน้ารายการเคลมแล้ว")
            return
        log(f"   หน้ารายการเคลมยังไม่เปิด (รอบ {attempt}) — กดเมนูใหม่")

    raise RuntimeError(
        f"เปิดหน้ารายการเคลมไม่สำเร็จ (กดเมนู {attempts} รอบแล้ว) — "
        f"เว็บอาจช้าผิดปกติ ลองรันใหม่อีกครั้ง"
    )


def _scan_rows(driver):
    """อ่านแถวทั้งหมดในตารางผลค้นหา → [(element, เลขเคลม, เลขเซอร์เวย์), ...]
    คืน [] ถ้าตารางกำลัง refresh (element stale/หาย)"""
    try:
        grid = driver.find_element(By.ID, "case_list_grid-body")
        result = []
        for row in grid.find_elements(By.TAG_NAME, "table"):
            tr = row.find_element(By.TAG_NAME, "tr")
            row_claim = tr.find_element(By.XPATH, "td[3]").text.strip()
            row_invoice = tr.find_element(By.XPATH, "td[4]").text.strip()
            result.append((row, row_claim, row_invoice))
        return result
    except (StaleElementReferenceException, NoSuchElementException):
        return []


def _detail_opened(driver, wait_s: int = 15) -> bool:
    """เช็คว่าหน้ารายละเอียดเคลมเปิดแล้วจริง (ช่องเลขเคลม Tab 1 โผล่และมีค่า)"""
    try:
        WebDriverWait(driver, wait_s).until(
            lambda d: d.find_element(
                By.ID, "tab1_claim_no-inputEl"
            ).get_attribute("value") != ""
        )
        return True
    except TimeoutException:
        return False


def _submit_search(driver, claim: str):
    """พิมพ์เลขเคลมในช่องค้นหา (ถ้ายังไม่มี) แล้วกด Enter"""
    search = wait_visible(driver, By.XPATH, '//*[@id="search_claimNo-inputEl"]')
    if search.get_attribute("value") != claim:
        search.clear()
        search.send_keys(claim)
    search.send_keys(Keys.ENTER)


def find_and_open_claim(driver, claim: str, invoice: str = "", timeout: int = 180):
    """ค้นหาเลขเคลมแล้วดับเบิลคลิกแถวที่ตรง (ถ้าระบุ invoice จะเช็คทั้งคู่)

    เว็บโหลดช้าและมี 2 จังหวะที่พลาดได้:
    1. ค้นหาก่อนตารางรอบแรกโหลดเสร็จ → คำสั่งค้นหาไม่ทำงาน/โดนผลรอบแรกทับ
       จึงรอตารางรอบแรกขึ้นก่อนค่อยค้นหา
    2. ผลค้นหามาช้า → โพลรอ และถ้ารอ 30 วิแล้วยังไม่เจอ จะกด Enter
       สั่งค้นหาซ้ำให้เอง จนกว่าจะเจอหรือครบ timeout
    """
    log(f"ISURVEY: ค้นหาเคลม {claim}" + (f" / {invoice}" if invoice else ""))

    # รอตารางรอบแรกโหลดเสร็จก่อน (มีแถวขึ้นแล้ว) ค่อยเริ่มค้นหา
    wait_present(driver, By.ID, "case_list_grid-body", 60)
    wait_loading_gone(driver, 120)
    try:
        WebDriverWait(driver, 60, poll_frequency=1).until(
            lambda d: len(_scan_rows(d)) > 0
        )
        log("   ตารางรอบแรกโหลดเสร็จ — เริ่มค้นหา")
    except TimeoutException:
        log("   ⚠️ ตารางรอบแรกยังว่าง — ลองค้นหาเลย")

    _submit_search(driver, claim)

    def _target_row(d):
        for row, row_claim, row_invoice in _scan_rows(d):
            if row_claim == claim and (not invoice or row_invoice == invoice):
                return row
        return False

    # โพลหาแถวเป้าหมาย — ครบ 30 วิยังไม่เจอจะสั่งค้นหาซ้ำ
    deadline = time.time() + timeout
    found = False
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        log(f"   รอผลการค้นหา (รอบที่ {attempt})...")
        try:
            WebDriverWait(driver, 30, poll_frequency=1).until(_target_row)
            found = True
            break
        except TimeoutException:
            log("   ยังไม่เจอ — กด Enter ค้นหาซ้ำ")
            try:
                _submit_search(driver, claim)
            except Exception:
                pass

    if not found:
        rows_now = _scan_rows(driver)
        seen = "\n".join(f"   แถว: {c} | {v}" for _, c, v in rows_now[:10])
        if len(rows_now) > 10:
            seen += f"\n   ... และอีก {len(rows_now) - 10} แถว"
        raise RuntimeError(
            f"ค้นหาซ้ำ {attempt} รอบใน {timeout} วินาทีแล้วยังไม่พบเคลม {claim}"
            + (f" คู่กับ invoice {invoice}" if invoice else "")
            + f"\nแถวที่เห็นล่าสุด:\n{seen or '   (ตารางว่าง)'}"
            + "\n→ ตรวจสอบว่าเลขเคลมถูกต้อง และลองค้นหาด้วยมือบนหน้าเว็บดูว่าขึ้นไหม"
        )

    # เจอแถวแล้ว — พักให้ตารางนิ่งก่อน แล้วดับเบิลคลิกพร้อม "ตรวจว่าหน้า
    # รายละเอียดเปิดจริง" (คลิกตอน ExtJS เพิ่ง refresh เสร็จ event อาจหลุด
    # โดยไม่มี exception — ถ้าไม่เปิดภายใน 15 วิ จะคลิกซ้ำให้เอง)
    time.sleep(1)
    for attempt in range(1, 5):
        clicked_invoice = None
        try:
            for row, row_claim, row_invoice in _scan_rows(driver):
                if row_claim == claim and (not invoice or row_invoice == invoice):
                    ActionChains(driver).double_click(row).perform()
                    clicked_invoice = row_invoice
                    break
        except StaleElementReferenceException:
            pass

        if clicked_invoice is None:
            log(f"   ⚠️ แถวหายไปตอนจะคลิก (รอบ {attempt}) — สแกนใหม่")
            time.sleep(1)
            continue

        log(f"   ดับเบิลคลิกแล้ว รอหน้ารายละเอียดเปิด (รอบ {attempt})...")
        if _detail_opened(driver, 15):
            log(f"   ✓ เปิดเคลม {claim} ({clicked_invoice})")
            return clicked_invoice
        log("   หน้ารายละเอียดยังไม่เปิด — ดับเบิลคลิกใหม่")

    raise RuntimeError(
        f"ดับเบิลคลิกเคลม {claim} 4 รอบแล้วหน้ารายละเอียดไม่เปิด — "
        f"ลองเปิดด้วยมือบนหน้าเว็บดูว่าเปิดได้ปกติไหม"
    )


def _click_tab(driver, n: int):
    wait_clickable(driver, By.XPATH, TAB_LINK_XPATH.format(n=n)).click()


def go_to_tab(driver, n: int):
    """สลับไป tab ที่ต้องการ (ใช้จากภายนอก เช่น กลับ Tab 1 ไปหาปุ่มดาวน์โหลด)"""
    _click_tab(driver, n)
    time.sleep(1)


def _wait_images_panel(driver, timeout_panel=20, timeout_thumb=12):
    """รอ panel รูปโผล่ แล้วรอ thumbnail ตัวแรก render (โหลดแบบ async)
    ถ้าเคลมไม่มีรูปจริงๆ จะผ่านไปหลัง timeout_thumb โดยไม่ error"""
    wait_present(driver, By.ID, "s-upload_panel1-innerCt", timeout_panel)
    try:
        wait_present(driver, By.CSS_SELECTOR, "div.center-cropped", timeout_thumb)
    except TimeoutException:
        pass


# ------------------------------------------------------------------ อ่านแต่ละ tab

def read_tab1_summary(driver, data: ClaimData):
    log("ISURVEY: อ่าน Tab 1 Summary")
    wait_value_not_empty(driver, "tab1_claim_no-inputEl")
    data.claim_value = get_value(driver, "tab1_claim_no-inputEl")
    data.invoice_value = get_value(driver, "tab1_survey_no-inputEl")
    data.notify_value = get_value(driver, "tab1_notify_no-inputEl")
    data.policy_value = get_value(driver, "tab1_policy_no-inputEl")
    data.claim_type = get_value(driver, "tab1_claim_MtypeID-inputEl")
    data.pay_type = get_value(driver, "tab1_claim_typeID-inputEl")
    data.third_party_condition = get_value(driver, "tab1_thirdParty_type-inputEl")
    data.branch = get_value(driver, "tab1_sys_branch-inputEl")
    data.service_total = get_value(driver, "tab1_SUR_TOTAL-inputEl")
    data.service_vat = get_value(driver, "tab1_SUR_VAT-inputEl")
    data.service_total_net = get_value(driver, "tab1_SUR_TOTAL_NET-inputEl")
    data.surveyor_name = get_value(driver, "tab1_surveyor_name-inputEl")

    # ค่าสำรวจชุด "อนุมัติใน ISURVEY" (INS_*) — คือยอดที่ต้องนำไปกรอก
    # ช่อง "เสนอ" ของ EMCS (ยืนยันโดย user 2026-06-11; ไม่ใช่ชุด SUR_/XML)
    data.bill = {
        "source": "isurvey_screen",
        "invest": get_value(driver, "tab1_INS_INVEST-inputEl"),
        "trans": get_value(driver, "tab1_INS_TRANS-inputEl"),
        "dist": get_value(driver, "tab1_INS_DIST-inputEl"),
        "photo": get_value(driver, "tab1_INS_PHOTO-inputEl"),
        "photo_num": get_value(driver, "tab1_PHOTO_NUM-inputEl"),
        "tel": get_value(driver, "tab1_INS_TEL-inputEl"),
        "insure": get_value(driver, "tab1_INS_INSURE-inputEl"),
        "claim": get_value(driver, "tab1_INS_CLAIM-inputEl"),
        "daily": get_value(driver, "tab1_INS_DAILY-inputEl"),
        "daily_num": get_value(driver, "tab1_DAILY_NUM-inputEl"),
        "other": get_value(driver, "tab1_INS_OTHER-inputEl"),
        "cartow": get_value(driver, "tab1_INS_CARTOW-inputEl"),
        "total": get_value(driver, "tab1_INS_TOTAL-inputEl"),
        "total_net": get_value(driver, "tab1_INS_TOTAL_NET-inputEl"),
    }

    # งาน outsource: ชื่อพนักงานอยู่คนละช่อง — ใช้แทนเมื่อช่องหลักว่าง
    data.oss_company = get_value(driver, "tab1_OSS_company-inputEl")
    data.oss_surveyor = get_value(driver, "tab1_OSS_SurveyorName-inputEl")
    data.oss_phone = get_value(driver, "tab1_OSS_phone-inputEl")
    if not data.surveyor_name.strip() and data.oss_surveyor.strip():
        data.surveyor_name = data.oss_surveyor
        log(f"   ใช้ชื่อพนักงาน outsource เป็นพนักงานสำรวจ: {data.oss_surveyor}"
            + (f" ({data.oss_company})" if data.oss_company.strip() else ""))
    data.arrive_date = get_value(driver, "tab1_arrive_date-inputEl")
    data.arrive_time = get_value(driver, "tab1_arrive_time-inputEl")
    data.finish_date = get_value(driver, "tab1_finish_date-inputEl")
    data.finish_time = get_value(driver, "tab1_finish_time-inputEl")
    data.accident_summary = get_value(driver, "accident_summary-inputEl")


def read_tab2_accident(driver, data: ClaimData, download_dir=None):
    log("ISURVEY: อ่าน Tab 2 Accident info")
    _click_tab(driver, 2)
    wait_value_not_empty(driver, "tab2_acc_date-inputEl")
    data.acc_date = get_value(driver, "tab2_acc_date-inputEl")
    data.acc_time = get_value(driver, "tab2_acc_time-inputEl")
    data.acc_place = get_value(driver, "tab2_acc_place-inputEl")
    data.acc_province = get_value(driver, "tab2_acc_provinceID-inputEl")
    data.acc_amphur = get_value(driver, "tab2_acc_amphurID-inputEl")
    data.acc_type_desc = get_value(driver, "tab2_acc_type_desc-inputEl")
    data.acc_detail = get_value(driver, "tab2_surveyor_comment-inputEl")
    data.acc_result = get_value(driver, "tab2_acc_verdictID-inputEl")

    if download_dir is not None:
        _wait_images_panel(driver)
        download_images(driver, download_dir)


def read_tab3_insurance(driver, data: ClaimData, download_dir=None):
    log("ISURVEY: อ่าน Tab 3 Insurance info")
    _click_tab(driver, 3)
    wait_value_not_empty(driver, "tab3_oth_comp_no-inputEl")
    data.insure_plate = get_value(driver, "tab3_plate_no-inputEl")
    data.prb_number = get_value(driver, "tab3_oth_comp_no-inputEl")
    data.prb_car_type = get_value(driver, "tab3_vehTID-inputEl")
    data.plate_province = get_value(driver, "tab3_plate_provinceID-inputEl")
    data.car_brand = get_value(driver, "tab3_car_brand-inputEl")
    data.car_color = get_value(driver, "tab3_car_color-inputEl")

    fullname = get_value(driver, "tab3_drv_name-inputEl")
    parts = fullname.split()
    data.driver_name = parts[0] if parts else ""
    data.driver_surname = parts[1] if len(parts) > 1 else ""

    data.driver_relation = get_value(driver, "tab3_relation-inputEl")
    data.driver_age = get_value(driver, "tab3_age-inputEl")
    data.driver_address = get_value(driver, "tab3_address-inputEl")
    data.driver_province = get_value(driver, "tab3_drv_provinceID-inputEl")
    data.driver_amphur = get_value(driver, "tab3_drv_amphurID-inputEl")
    data.driver_phone = get_value(driver, "tab3_drv_phone-inputEl")
    data.driver_idcard = get_value(driver, "tab3_IDcard_no-inputEl")
    data.driver_license_no = get_value(driver, "tab3_lic_no-inputEl")
    data.driver_license_place = get_value(driver, "tab3_lic_issue_provinceID-inputEl")
    data.driver_license_type = get_value(driver, "tab3_lic_typeID-inputEl")
    data.damage_estimate = get_value(driver, "tab3_D_TOTAL-inputEl")
    data.driver_birthdate = get_value(driver, "tab3_birthdate-inputEl")
    data.license_issue_date = get_value(driver, "tab3_lic_issueDate-inputEl")
    data.license_expiry_date = get_value(driver, "tab3_lic_expireDate-inputEl")

    # ตารางความเสียหาย: td1=ชิ้นส่วน td2=ประเภท td3=ระดับ td4=ราคาประเมิน
    data.damage, data.type_damage, data.rank_damage, data.cost_damage = [], [], [], []
    grid = driver.find_element(By.ID, "tab3_damage_grid-body")
    for row in grid.find_elements(By.TAG_NAME, "table"):
        data.damage.append(row.find_element(By.XPATH, ".//tr/td[1]/div").text)
        data.type_damage.append(row.find_element(By.XPATH, ".//tr/td[2]/div").text)
        data.rank_damage.append(row.find_element(By.XPATH, ".//tr/td[3]/div").text)
        try:
            data.cost_damage.append(
                row.find_element(By.XPATH, ".//tr/td[4]/div").text
            )
        except Exception:
            data.cost_damage.append("")
    log(f"   พบความเสียหาย {len(data.damage)} รายการ")

    if download_dir is not None:
        _wait_images_panel(driver)
        download_images(driver, download_dir)


# อ่านค่า input หลายตัวในครั้งเดียว (เร็วกว่า find_element ทีละตัวมาก)
_JS_GET_VALUES = (
    "return arguments[0].map(id => {"
    "  const e = document.getElementById(id);"
    "  return e ? (e.value || '') : '';"
    "});"
)

# แผนที่ field ของ Tab 4-6 (ได้จากการ probe หน้าเว็บจริง — prefix ไม่ใช่ tabN_)
THIRD_PARTY_FIELDS = {
    "plate_no": "othercar_plate_no-inputEl",
    "plate_province": "othercar_plate_provinceID-inputEl",
    "plate_color": "othercar_plate_color-inputEl",
    "veh_type": "othercar_vehTID-inputEl",
    "car_brand": "othercar_car_brand-inputEl",
    "car_model": "othercar_car_model-inputEl",
    "car_color": "othercar_car_color-inputEl",
    "chassis_no": "othercar_chassis_no-inputEl",
    "engine_no": "othercar_engine_no-inputEl",
    "drv_title": "othercar_drv_title-inputEl",
    "drv_name": "othercar_drv_name-inputEl",
    "drv_gender": "othercar_drv_gender-inputEl",
    "age": "othercar_age-inputEl",
    "birthdate": "othercar_birthdate-inputEl",
    "idcard": "othercar_IDcard_no-inputEl",
    "phone": "othercar_drv_phone-inputEl",
    "address": "othercar_address-inputEl",
    "drv_province": "othercar_drv_provinceID-inputEl",
    "drv_amphur": "othercar_drv_amphurID-inputEl",
    "drv_tumbon": "othercar_drv_tumbonID-inputEl",
    "lic_no": "othercar_lic_no-inputEl",
    "lic_type": "othercar_lic_typeID-inputEl",
    "lic_issue_province": "othercar_lic_issue_provinceID-inputEl",
    "lic_issue_date": "othercar_lic_issueDate-inputEl",
    "lic_expire_date": "othercar_lic_expireDate-inputEl",
    "owner_title": "othercar_owner_title-inputEl",
    "owner_name": "othercar_owner_name-inputEl",
    "owner_phone": "othercar_owner_phone-inputEl",
    "owner_address": "othercar_owner_address-inputEl",
    "owner_province": "othercar_owner_provinceID-inputEl",
    "owner_amphur": "othercar_owner_amphurID-inputEl",
    "owner_tumbon": "othercar_owner_tumbonID-inputEl",
    "insurer": "othercar_oth_insure_companyID-inputEl",
    "insure_type": "othercar_oth_insure_typeID-inputEl",
    "policy_no": "othercar_oth_policy_no-inputEl",
    "prb_company": "othercar_oth_comp_insurer-inputEl",
    "prb_no": "othercar_oth_comp_no-inputEl",
    "accident_no": "othercar_oth_accident_no-inputEl",
    "claim_no": "othercar_claim_no-inputEl",
    "damage_labour": "othercar_D_LABOUR-inputEl",
    "damage_parts": "othercar_D_SPRP-inputEl",
    "damage_other": "othercar_D_OTH-inputEl",
    "damage_total": "othercar_D_TOTAL-inputEl",
    "damage_memo": "othercar_damage_memo-inputEl",
    "recovery": "othercar_recovery_pymt-inputEl",
    "fix_place": "othercar_req_fix_place-inputEl",
    "issue_document": "othercar_issue_document-inputEl",
    "memo": "othercar_memo-inputEl",
    "tow_company": "othercar_tow_company-inputEl",
    "tow_cost": "othercar_tow_cost-inputEl",
    "tow_from": "othercar_tow_from-inputEl",
    "tow_to": "othercar_tow_to-inputEl",
}

INJURY_FIELDS = {
    "title": "injury_title-inputEl",
    "name": "injury_person_name-inputEl",
    "gender": "injury_gender-inputEl",
    "age": "injury_age-inputEl",
    "birthdate": "injury_birthdate-inputEl",
    "idcard": "injury_IDcard_no-inputEl",
    "phone": "injury_person_phone-inputEl",
    "occupation": "injury_occupation-inputEl",
    "address": "injury_address-inputEl",
    "province": "injury_provinceID-inputEl",
    "amphur": "injury_amphurID-inputEl",
    "tumbon": "injury_tumbonID-inputEl",
    "injury_type": "injury_injury_type-inputEl",
    "injury_detail": "injury_injury_detail-inputEl",
    "hospital": "injury_hospital-inputEl",
    "medical_cost": "injury_medical_cost-inputEl",
    "ins_tp": "injury_cvTID-inputEl",
    "on_vehicle": "injury_on_vehID-inputEl",
    "related": "injury_related_accidentID-inputEl",
    "insurer": "injury_oth_insure_companyID-inputEl",
    "policy_no": "injury_oth_policy_no-inputEl",
    "accident_no": "injury_oth_accident_no-inputEl",
    "recovery": "injury_recovery_pymt-inputEl",
    "memo": "injury_memo-inputEl",
}

ASSET_FIELDS = {
    "name": "property_prop_name-inputEl",
    "type": "property_prop_typeID-inputEl",
    "value": "property_prop_value-inputEl",
    "damage_cost": "property_damage_cost-inputEl",
    "damage_detail": "property_prop_damage_detail-inputEl",
    "location": "property_prop_location-inputEl",
    "province": "property_prop_provinceID-inputEl",
    "amphur": "property_prop_amphurID-inputEl",
    "tumbon": "property_prop_tumbonID-inputEl",
    "owner_title": "property_owner_title-inputEl",
    "owner_name": "property_owner_name-inputEl",
    "owner_phone": "property_owner_phone-inputEl",
    "owner_idcard": "property_owner_IDcard_no-inputEl",
    "owner_address": "property_owner_address-inputEl",
    "owner_province": "property_owner_provinceID-inputEl",
    "owner_amphur": "property_owner_amphurID-inputEl",
    "owner_tumbon": "property_owner_tumbonID-inputEl",
    "resp_name": "property_resp_name-inputEl",
    "resp_phone": "property_resp_phone-inputEl",
    "resp_idcard": "property_resp_IDcard_no-inputEl",
    "resp_address": "property_resp_address-inputEl",
    "relation": "property_relation-inputEl",
    "insurer": "property_oth_insure_companyID-inputEl",
    "policy_no": "property_oth_policy_no-inputEl",
    "accident_no": "property_oth_accident_no-inputEl",
    "deduct": "property_TPDD-inputEl",
    "recovery": "property_recovery_pymt-inputEl",
    "memo": "property_memo-inputEl",
}


def _read_record_tab(driver, tab_no: int, anchor_id: str, fields: dict,
                     label: str, wait_data: int = 8) -> list:
    """อ่าน tab ที่เป็นแบบฟอร์ม record (Tab 4-6)

    หน้าเหล่านี้ render ครั้งแรกเมื่อถูกคลิก (lazy) และ "ค่า" โหลดตามมาแบบ
    async — รอ anchor โผล่ แล้วโพลอ่านค่าซ้ำจนกว่าจะมีค่าหรือครบ wait_data
    วินาที (เคลมที่ไม่มีข้อมูลจะเสียเวลารอเท่า wait_data แล้วคืน list ว่าง)

    หมายเหตุ: ถ้าเคลมมีหลาย record (เช่น คู่กรณีหลายคัน) หน้าเว็บจะมี dropdown
    เลือกคัน ซึ่ง id เป็นแบบสุ่ม (combo-NNNN) ยัง iterate อัตโนมัติไม่ได้ —
    ตอนนี้อ่าน record ที่แสดงอยู่ จะมี warning ใน validation ช่วยเตือน
    """
    log(f"ISURVEY: อ่าน Tab {tab_no} {label}")
    _click_tab(driver, tab_no)
    try:
        wait_present(driver, By.ID, anchor_id, 20)
    except TimeoutException:
        log(f"   ⚠️ Tab {tab_no} ไม่ render ใน 20 วิ — ข้าม ({label})")
        return []

    ids = list(fields.values())
    deadline = time.time() + wait_data
    while True:
        values = driver.execute_script(_JS_GET_VALUES, ids)
        record = dict(zip(fields.keys(), values))
        if any(str(v).strip() for v in record.values()):
            log(f"   ✓ พบข้อมูล{label}")
            return [record]
        if time.time() >= deadline:
            log(f"   ไม่มีข้อมูล{label}")
            return []
        time.sleep(1)


def read_tab4_third_party(driver, data: ClaimData):
    data.third_parties = _read_record_tab(
        driver, 4, "othercar_plate_no-inputEl", THIRD_PARTY_FIELDS, "คู่กรณี"
    )


def read_tab5_injury(driver, data: ClaimData):
    data.injuries = _read_record_tab(
        driver, 5, "injury_person_name-inputEl", INJURY_FIELDS, "ผู้บาดเจ็บ"
    )


def read_tab6_asset(driver, data: ClaimData):
    data.assets = _read_record_tab(
        driver, 6, "property_prop_name-inputEl", ASSET_FIELDS, "ทรัพย์สิน"
    )


def read_tab7_policy(driver, data: ClaimData):
    log("ISURVEY: อ่าน Tab 7 Policy info")
    _click_tab(driver, 7)
    wait_value_not_empty(driver, "tab7_effective_date-inputEl")
    data.effective_date = get_value(driver, "tab7_effective_date-inputEl")
    data.expiry_date = get_value(driver, "tab7_expiry_date-inputEl")
    data.insure_name = get_value(driver, "tab7_assured_name-inputEl")
    data.insure_type = get_value(driver, "tab7_policy_TypeID-inputEl")
    data.insure_model = get_value(driver, "tab7_car_model-inputEl")
    data.insure_chassis = get_value(driver, "tab7_chassis_no-inputEl")
    data.insure_engine = get_value(driver, "tab7_engine_no-inputEl")


def read_tab8_notify(driver, data: ClaimData):
    log("ISURVEY: อ่าน Tab 8 Notify info")
    _click_tab(driver, 8)
    wait_value_not_empty(driver, "tab8_notified_date-inputEl")
    data.noti_date = get_value(driver, "tab8_notified_date-inputEl")
    data.noti_time = get_value(driver, "tab8_notified_time-inputEl")


def collect_panel_images(driver, folder):
    """โหลดรูปจาก panel ของ Tab 2 และ Tab 3 (โหมด fallback เมื่อ zip ใช้ไม่ได้)
    ใช้ตอนอยู่บนหน้ารายละเอียดเคลมแล้วเท่านั้น"""
    for n in (2, 3):
        _click_tab(driver, n)
        _wait_images_panel(driver)
        download_images(driver, folder)


def read_record_tabs(driver, data: ClaimData):
    """อ่าน Tab 4-6 จากหน้าจอ (ใช้เมื่อไม่มี XML ให้ parse)"""
    read_tab4_third_party(driver, data)
    read_tab5_injury(driver, data)
    read_tab6_asset(driver, data)


def read_all(driver, download_dir=None, expect_claim: str = "",
             include_record_tabs: bool = True) -> ClaimData:
    """อ่านข้อมูลทุก tab ที่ใช้กรอก EMCS
    - download_dir: โหลดรูปจาก panel Tab 2/3 ลงโฟลเดอร์นี้ (None = ไม่โหลด)
    - expect_claim: หยุดทันทีเมื่อหน้าที่เปิดไม่ใช่เคลมที่ขอ
    - include_record_tabs: อ่าน Tab 4-6 จากหน้าจอด้วยไหม
      (ตั้ง False เมื่อจะใช้ข้อมูลจากไฟล์ XML แทน — เร็วและนิ่งกว่า)"""
    data = ClaimData()
    read_tab1_summary(driver, data)

    if expect_claim and data.claim_value.strip() != expect_claim.strip():
        raise RuntimeError(
            f"หน้าที่เปิดเป็นเคลม {data.claim_value} ไม่ตรงกับที่ขอ ({expect_claim}) "
            f"— หยุดก่อนเพื่อความปลอดภัย"
        )
    read_tab2_accident(driver, data, download_dir)
    read_tab3_insurance(driver, data, download_dir)
    if include_record_tabs:
        read_record_tabs(driver, data)
    read_tab7_policy(driver, data)
    read_tab8_notify(driver, data)
    log("ISURVEY: อ่านข้อมูลครบแล้ว")
    return data
