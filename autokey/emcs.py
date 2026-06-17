"""ฝั่งกรอกข้อมูล: login EMCS → สร้างงานใหม่ → กรอกทุกส่วน → อัปโหลดรูป → ค่าใช้จ่าย

โมเดลความปลอดภัย: "บันทึก" ทุกหน้า = draft แก้ไขได้ สคริปต์กดให้ครบ
จุด commit จริงคือปุ่ม 'ส่งงานใหม่' หน้าค่าใช้จ่าย — ไม่กดให้เด็ดขาด
"""
import re
import time
from pathlib import Path

from rapidfuzz import fuzz, process
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from .browser import (
    accept_alert,
    click_retry,
    fuzzy_select,
    iso_to_thai_date,
    log,
    set_text,
    split_hhmm,
    to_buddhist_date,
    today_buddhist,
    wait_clickable,
    wait_for_image_select,
    wait_for_manual_fill,
    wait_present,
    wait_visible,
)
from .claim_data import ClaimData
from .images import list_images

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
}

# ความเสียหายกรอกได้สูงสุด 8 รายการ (คอลัมน์ A 4 + คอลัมน์ B 4 ตาม layout หน้าเว็บ)
MAX_DAMAGE_ITEMS = 8

# ---------------------------------------------------------------- คู่กรณี
# ฟอร์ม EMCS มีบล็อกรถคู่กรณีเตรียมไว้ 20 คัน: dtlOpo_ctl00..ctl19
OPO_PREFIX = "dtlOpo_ctl{n:02d}_wuOpo_"
MAX_OPPONENTS = 20

# คำนำหน้าชื่อ (เรียงยาว→สั้น เพื่อให้ 'นางสาว' จับก่อน 'นาง')
THAI_TITLES = ["เด็กหญิง", "เด็กชาย", "นางสาว", "ด.ญ.", "ด.ช.", "นาง", "นาย"]


def split_thai_name(full: str):
    """แยก 'นายกัมปนาท เปรมกิจ' → ('นาย', 'กัมปนาท', 'เปรมกิจ')
    (รองรับกรณีคำนำหน้าติดกับชื่อโดยไม่เว้นวรรค)"""
    full = (full or "").strip()
    title = ""
    for t in THAI_TITLES:
        if full.startswith(t):
            title, full = t, full[len(t):].strip()
            break
    parts = full.split()
    first = parts[0] if parts else ""
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    return title, first, last


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


def resolve_loss_type(data, requested: str) -> str:
    """เลือกลักษณะความเสียหาย (ddlLoss_ID) อัตโนมัติเมื่อ requested='auto'
    - ไม่มีคู่กรณี → เคลมแห้ง (เหมือน workflow เดิม)
    - มีคู่กรณี → ดูผลคดี: ประกันผิด → 'ชนคู่กรณีเสียหาย' / คู่กรณีผิด →
      'ถูกคู่กรณีชน' / ก้ำกึ่ง → "" (ให้คนเลือกเอง)"""
    if requested != "auto":
        return requested
    if not data.third_parties:
        return "เคลมแห้ง"
    r = data.acc_result or ""
    if "คู่กรณีเป็นฝ่ายผิด" in r:
        return "ถูกคู่กรณีชน"
    if "รถประกันเป็นฝ่ายผิด" in r and "ถูก" not in r:
        return "ชนคู่กรณีเสียหาย"
    log(f"   ผลคดี '{r}' ตีความลักษณะความเสียหายไม่ได้ — เว้นไว้ให้เลือกเอง")
    return ""


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
        sel = Select(driver.find_element(By.ID, select_id))
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
        set_text(driver, p + "txtOpo_Name", owner)
        set_text(driver, p + "txtOpo_Address",
                 tp.get("opo_address", "") or tp.get("address", ""))
        set_text(driver, p + "txtOpo_Type", tp.get("opo_type", ""))

        # รถ
        set_text(driver, p + "txtCar_RegNo", tp.get("plate_no", ""))
        fuzzy_select(driver, p + "ddlCmfg", tp.get("car_brand", ""),
                     label=f"ยี่ห้อรถคู่กรณี {n + 1}")
        set_text(driver, p + "txtCModel", tp.get("car_model", ""))
        set_text(driver, p + "txtChassisNo", tp.get("chassis_no", ""))
        _select_index(driver, p + "ddlCar_Province",
                      int(tp["plate_province_id"])
                      if tp.get("plate_province_id", "").strip().isdigit() else None,
                      label=f"จังหวัดรถคู่กรณี {n + 1}")

        # ผู้ขับขี่
        title, first, last = split_thai_name(tp.get("drv_name", ""))
        if title:
            fuzzy_select(driver, p + "ddlDri_Title_ID", title,
                         label=f"คำนำหน้าผู้ขับขี่คู่กรณี {n + 1}")
        set_text(driver, p + "txtDri_Name01", first)
        set_text(driver, p + "txtDri_LastName01", last)

        gender = tp.get("gender", "").strip().upper()
        if gender in ("M", "F", "W"):
            try:
                idx = "0" if gender == "M" else "1"  # 0=ชาย 1=หญิง
                driver.find_element(By.ID, p + f"rdoGender_{idx}").click()
            except Exception:
                log(f"   ⚠️ เลือกเพศคู่กรณีคันที่ {n + 1} ไม่ได้")

        set_text(driver, p + "txtDri_Age", tp.get("age", ""))
        set_text(driver, p + "wuCale_Dri_BirthDay_txtCalendar",
                 iso_to_thai_date(tp.get("birthdate", "")))
        set_text(driver, p + "txtDri_Adrress", tp.get("address", ""))

        prov_id = tp.get("province_id", "").strip()
        prov_idx = int(prov_id) if prov_id.isdigit() else None
        _select_index(driver, p + "ddlDri_ProvinceID", prov_idx,
                      label=f"จังหวัดผู้ขับขี่คู่กรณี {n + 1}")
        dist_idx = district_index(tp.get("district_id", ""), prov_id)
        if prov_idx and dist_idx:
            time.sleep(1)  # รอ postback โหลดรายการอำเภอตามจังหวัด
            _select_index(driver, p + "ddlDri_DistrictID", dist_idx,
                          label=f"อำเภอผู้ขับขี่คู่กรณี {n + 1}")

        set_text(driver, p + "txtDri_TelNo", tp.get("phone", ""))
        set_text(driver, p + "txtDri_CardID", tp.get("idcard", ""))
        set_text(driver, p + "txtDri_DrvID", tp.get("lic_no", ""))
        set_text(driver, p + "wuCale_Dri_DrvDate_Start_txtCalendar",
                 iso_to_thai_date(tp.get("lic_issue_date", "")))

        # ประกันของคู่กรณี
        fuzzy_select(driver, p + "ddlHave_Insurance", tp.get("insurer", ""),
                     label=f"บริษัทประกันคู่กรณี {n + 1}")
        set_text(driver, p + "txtPolicyNo", tp.get("policy_no", ""))
        set_text(driver, p + "txtClaimNo", tp.get("claim_no", ""))

        # ความเสียหาย + KFK
        cost = tp.get("cost_damage", "").strip()
        if cost and cost != "0":
            set_text(driver, p + "txtCost_Damage", cost)
        if str(tp.get("has_kfk", "")).strip().upper() in ("Y", "YES", "1", "TRUE"):
            try:
                driver.find_element(By.ID, p + "chkHas_KFK").click()
                log(f"   ✓ ติ๊กเข้าสัญญา KFK คันที่ {n + 1}")
            except Exception:
                log(f"   ⚠️ ติ๊ก KFK คันที่ {n + 1} ไม่ได้")

    # บันทึกส่วนรถคู่กรณี (ปุ่มแยกจากบันทึกหน้าหลัก)
    log("EMCS: กดบันทึกรถคู่กรณี")
    wait_clickable(driver, By.ID, "btnSave_Opponent").click()
    accept_alert(driver)
    log("EMCS: บันทึกรถคู่กรณีแล้ว — ตรวจจังหวัด/อำเภอที่เลือกด้วยตาอีกครั้ง")


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


def guard_duplicate_report(driver, data: ClaimData, force_new: bool):
    """ด่านกันเปิดเรื่องซ้ำ: ถ้าเคลมนี้มีเรื่องใน EMCS แล้ว → หยุดทันที
    (ข้ามด่านได้ด้วย --force-new เมื่อตั้งใจสร้างซ้ำจริงๆ)"""
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
    """หาคำนำหน้าผู้ขับขี่รถประกัน (EMCS บังคับ แต่ ISURVEY ไม่มีให้ตรง)
    1) ถ้าชื่อผู้เอาประกันมีคำนำหน้า และชื่อตรงกับผู้ขับขี่ → ใช้เลย (แม่น)
    2) ไม่งั้นเดาจากเพศ: M→นาย, W/F→นางสาว (ต้องตรวจด้วยตา)
    คืน (title, แหล่งที่มา)"""
    driver_full = f"{data.driver_name} {data.driver_surname}".strip()
    title, first, last = split_thai_name(data.insure_name)
    if title and f"{first} {last}".strip() == driver_full:
        return title, "จากชื่อผู้เอาประกัน"

    g = (data.driver_gender or "").strip().upper()
    if g == "M":
        return "นาย", "เดาจากเพศ"
    if g in ("W", "F"):
        return "นางสาว", "เดาจากเพศ (นาง/นางสาว ตรวจด้วยตาด้วย)"
    return "", ""


def fill_insurer_and_refs(driver, data: ClaimData):
    """เลือกบริษัทประกัน (ตัวเลือกแรกตามเดิม) + เลขเซอร์เวย์/เลขเคลม"""
    log("EMCS: เลือกบริษัทประกัน + เลขอ้างอิง")
    wait_clickable(driver, By.XPATH, '//*[@id="ddlInsurerNameMajor"]/option[2]', 30).click()
    wait_clickable(driver, By.ID, "ddlInsurer_Name", 30)
    driver.find_element(By.XPATH, '//*[@id="ddlInsurer_Name"]/option[2]').click()

    wait_clickable(driver, By.ID, "txtSurv_JobNo")
    set_text(driver, "txtSurv_JobNo", data.invoice_value)
    set_text(driver, "txtRef_Claim_No", data.claim_value)


def fill_policy(driver, data: ClaimData):
    log("EMCS: กรอกข้อมูลกรมธรรม์")
    wait_visible(driver, By.ID, "txtAcc_Policy_No")
    set_text(driver, "txtPrb_Number", data.prb_number)
    set_text(driver, "txtAcc_Policy_No", data.policy_value)
    set_text(driver, "wuCale_Policy_Start_txtCalendar", to_buddhist_date(data.effective_date))
    set_text(driver, "wuCale_Policy_End_txtCalendar", to_buddhist_date(data.expiry_date))
    set_text(driver, "txtAssured_Name", data.insure_name)
    set_text(driver, "txtPolicy_Type", data.insure_type)


def fill_car(driver, data: ClaimData):
    log("EMCS: กรอกรายละเอียดรถยนต์")
    wait_visible(driver, By.ID, "txtCar_RegNo")
    set_text(driver, "txtCar_RegNo", data.insure_plate)
    set_text(driver, "txtCModel2", data.insure_model)
    set_text(driver, "txtChassisNo", data.insure_chassis)
    set_text(driver, "txtEngineNo", data.insure_engine)

    # dropdown แต่ละตัวมี postback — เว้นจังหวะ 1s ตาม workflow เดิม
    # ประเภทรถ/จังหวัดรถ/ยี่ห้อรถ = field บังคับ (required) → ถ้าว่าง/เลือกไม่ได้ หยุดรอคน
    fuzzy_select(driver, "ddlCType", data.prb_car_type, presleep=1,
                 label="ประเภทรถ", required=True)
    fuzzy_select(driver, "ddlCar_Province", data.plate_province, presleep=1,
                 label="จังหวัดรถ", required=True)
    fuzzy_select(driver, "ddlCMFG", data.car_brand, presleep=1,
                 label="ยี่ห้อรถ", required=True)
    fuzzy_select(driver, "ddlCar_Color", data.car_color, presleep=1, label="สีรถ")


def fill_driver(driver, data: ClaimData):
    log("EMCS: กรอกข้อมูลผู้ขับขี่")
    wait_visible(driver, By.ID, "txtDri_Name01")

    # เพศผู้ขับขี่ (บังคับ) — rdoGender_0=ชาย(M), rdoGender_1=หญิง(F)
    g = (data.driver_gender or "").strip().upper()
    if g in ("M", "W", "F"):
        idx = "0" if g == "M" else "1"
        driver.find_element(By.ID, f"rdoGender_{idx}").click()
        log(f"   ✓ เพศผู้ขับขี่ = {'ชาย' if g == 'M' else 'หญิง'} (จากข้อมูล ISURVEY)")
    else:
        log("   ⚠️ ไม่ทราบเพศผู้ขับขี่ (ข้อมูล ISURVEY ไม่มี)")
        wait_for_manual_fill("เพศผู้ขับขี่ (ชาย/หญิง)",
                             "ข้อมูล ISURVEY ไม่มีเพศ — เป็น field บังคับ ต้องเลือกเอง")

    # คำนำหน้าผู้ขับขี่ (บังคับ)
    title, source = _derive_insured_title(data)
    if title:
        fuzzy_select(driver, "ddlDri_Title_ID", title,
                     label=f"คำนำหน้าผู้ขับขี่ ({source})")
    else:
        log("   ⚠️ หาคำนำหน้าผู้ขับขี่ไม่ได้")
        wait_for_manual_fill("คำนำหน้าผู้ขับขี่",
                             "ไม่มีข้อมูลให้เดา — เป็น field บังคับ ต้องเลือกเอง")

    set_text(driver, "txtDri_Name01", data.driver_name)
    set_text(driver, "txtDri_LastName01", data.driver_surname)
    set_text(driver, "txtDri_Age", data.driver_age)
    set_text(driver, "txtDri_Address", data.driver_address)
    set_text(driver, "txtDri_TelNo", data.driver_phone)
    set_text(driver, "txtDri_CardID", data.driver_idcard)
    set_text(driver, "txtDri_DrvID", data.driver_license_no)
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


def fill_accident(driver, data: ClaimData, loss_type: str = "เคลมแห้ง"):
    log("EMCS: กรอกรายละเอียดอุบัติเหตุ")
    wait_visible(driver, By.ID, "wuCale_Acc_Date_txtCalendar")

    # วัน-เวลาเกิดเหตุ
    set_text(driver, "wuCale_Acc_Date_txtCalendar", to_buddhist_date(data.acc_date))
    h, m = split_hhmm(data.acc_time)
    set_text(driver, "txtAcc_Date_Hour", h)
    set_text(driver, "txtAcc_Date_Minute", m)

    set_text(driver, "txtAcc_Place", data.acc_place)
    set_text(driver, "txtAcc_Detail", data.acc_detail)
    set_text(driver, "txtAcc_Surv", data.surveyor_name)

    # วัน-เวลาลูกค้าแจ้ง และบริษัทแจ้งพนักงานสำรวจ (ใช้ค่าเดียวกันตามเดิม)
    noti_date = to_buddhist_date(data.noti_date)
    nh, nm = split_hhmm(data.noti_time)
    set_text(driver, "wuCale_Acc_Call_Date_txtCalendar", noti_date)
    set_text(driver, "txtAcc_Call_Date_Hour", nh)
    set_text(driver, "txtAcc_Call_Date_Minute", nm)
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

    # ลักษณะความเสียหาย (เฉพาะเคลมแห้ง) — ถ้าไม่ต้องการให้ส่ง loss_type=""
    if loss_type:
        time.sleep(1)
        try:
            Select(
                driver.find_element(By.ID, "ddlLoss_ID")
            ).select_by_visible_text(loss_type)
            log(f"   ✓ ลักษณะความเสียหาย = {loss_type}")
        except Exception as e:
            log(f"   ⚠️ เลือกลักษณะความเสียหาย '{loss_type}' ไม่ได้: {e}")


def fill_verdict(driver, data: ClaimData):
    """เลือกผลคดี (radio) จากข้อความผลคดีของ ISURVEY ด้วย fuzzy matching"""
    log("EMCS: เลือกผลคดี")
    wait_visible(driver, By.ID, "rdoAcc_Cause00")

    if not data.acc_result.strip():
        log("   ⚠️ ไม่มีข้อมูลผลคดีจาก ISURVEY — ข้าม (เลือกเองบนหน้าเว็บ)")
        return

    best = process.extractOne(
        data.acc_result, list(CAUSE_RADIO.keys()), scorer=fuzz.WRatio
    )
    label, score = best[0], best[1]
    log(f"   ✓ ผลคดี: '{data.acc_result}' → '{label}' (score {score:.0f})")
    driver.find_element(By.ID, CAUSE_RADIO[label]).click()


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
        "ประเภทรถ": lambda: fuzzy_select(
            driver, "ddlCType", data.prb_car_type,
            presleep=1, label="ประเภทรถ (ซ่อม)"),
        "คำนำหน้าผู้ขับขี่": lambda: fuzzy_select(
            driver, "ddlDri_Title_ID", _derive_insured_title(data)[0],
            presleep=1, label="คำนำหน้า (ซ่อม)"),
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


def save_main_form(driver, data: ClaimData):
    """กดบันทึกหน้าหลัก แล้ว "ตรวจว่าบันทึกสำเร็จจริง"

    - บันทึกสำเร็จ → ปุ่มข้อมูลความเสียหาย (btnPopUp_DamList) ถูกปลดล็อก
    - validation ไม่ผ่าน → alert บอกรายการที่ขาด:
        1) ลองซ่อม dropdown ที่ค่าหลุดจาก postback race อัตโนมัติก่อน (สูงสุด 2 รอบ)
        2) ถ้าซ่อมอัตโนมัติไม่ได้ (เช่น text field ว่างอย่าง 'สถานที่เกิดเหตุ')
           → หยุดรอให้คนกรอกช่องที่ฟ้องเองบนหน้า EMCS แล้วลองบันทึกใหม่
      (มี cap กันลูปไม่รู้จบ — ถ้าไม่มีคนตอบ/แก้ไม่ได้จะ raise)"""
    auto_heal_left = 2   # จำนวนรอบที่ยอมให้ซ่อม dropdown อัตโนมัติ
    for attempt in range(1, 8):
        log(f"EMCS: กดบันทึกหน้าหลัก (รอบ {attempt})")
        wait_clickable(driver, By.ID, "btnSave").click()
        alert_text = accept_alert(driver)

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

def fill_damage_list(driver, data: ClaimData, main_window: str):
    """เปิด popup ความเสียหาย กรอกทุกรายการ บันทึก แล้วสลับกลับหน้าหลัก

    Layout ของหน้า: ตาราง 2 คอลัมน์ (A ซ้าย / B ขวา) คอลัมน์ละ 4 แถว
    id ของช่อง: dgvOtherDamage_List_ctl0{2-5}_wuOtherDamL{A|B}_...
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

    items = list(zip(data.damage, data.type_damage, data.rank_damage))
    if len(items) > MAX_DAMAGE_ITEMS:
        log(f"   ⚠️ มี {len(items)} รายการ แต่หน้าเว็บรับได้ {MAX_DAMAGE_ITEMS} — "
            f"รายการที่เหลือต้องกรอกเองภายหลัง")

    for c, (name, _dtype, rank) in enumerate(items[:MAX_DAMAGE_ITEMS]):
        col = "A" if c < 4 else "B"
        row = 2 + (c % 4)  # ctl02..ctl05
        prefix = f"dgvOtherDamage_List_ctl0{row}_wuOtherDamL{col}_"

        driver.find_element(By.ID, prefix + "txtDam_Name").send_keys(name)

        # ซ้าย/ขวา/ทั้งคู่ ดูจากคำในชื่อชิ้นส่วน
        if "ซ้าย" in name and "ขวา" in name:
            side = "2"
        elif "ขวา" in name:
            side = "1"
        elif "ซ้าย" in name:
            side = "0"
        else:
            side = "2"
        driver.find_element(By.ID, prefix + f"rdoDam_Left_Right_{side}").click()

        # ระดับความเสียหาย A-D
        rank_idx = {"A": "0", "B": "1", "C": "2", "D": "3"}.get(rank.strip().upper())
        if rank_idx is not None:
            driver.find_element(By.ID, prefix + f"rdoDam_Lavel_{rank_idx}").click()
        else:
            log(f"   ⚠️ ระดับความเสียหาย '{rank}' ไม่รู้จัก (รายการ: {name}) — ข้าม")

        log(f"   ✓ [{c + 1}] {name} | side={side} | rank={rank}")

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

def upload_images(driver, folder, image_type: str = "รูปรถประกัน", only=None):
    """เข้าหน้ารูปประกอบ เลือกประเภทรูป เพิ่มรูปทุกไฟล์ แล้วกดอัปโหลด

    หน้าอัปโหลดเป็น UI แบบ HTML5: เลือกประเภทก่อน (input file ถูก disable
    จนกว่าจะเลือก) แล้วส่งทุกไฟล์เข้า input ตัวเดียว (multiple) รวดเดียว
    — ถ้าไม่เจอ UI ใหม่จะ fallback ไปแบบเก่า (ทีละไฟล์ + ประเภทต่อแถว)

    only: list ชื่อไฟล์ที่จะอัปโหลด (None = ให้ผู้ใช้เลือกบนหน้าเว็บ /
    console = ทุกไฟล์); ส่ง list ว่าง = ไม่อัปโหลดเลย"""
    files = list_images(folder)
    if not files:
        log("EMCS: ไม่มีรูปให้อัปโหลด — ข้าม")
        return

    # ให้ผู้ใช้เลือกรูปที่จะอัปโหลด (หน้าเว็บ); console/ไม่ตอบ = ทุกรูปตามเดิม
    if only is None:
        only = wait_for_image_select(folder, files)
    if only is not None:
        chosen = set(only)
        files = [f for f in files if f in chosen]
        if not files:
            log("EMCS: ผู้ใช้ไม่ได้เลือกรูปใดเลย — ข้ามการอัปโหลดรูป")
            return

    log(f"EMCS: อัปโหลดรูป {len(files)} ไฟล์ (ประเภท '{image_type}')")
    click_retry(driver, By.ID, "wuMenuPage1_imbImage")

    try:
        wait_present(driver, By.ID, "ddlImage_Type_Html5", 15)
        html5_ui = True
    except TimeoutException:
        html5_ui = False

    if html5_ui:
        # 1) เลือกประเภทรูป → ระบบ enable ช่องเลือกไฟล์ให้
        fuzzy_select(driver, "ddlImage_Type_Html5", image_type,
                     label="ประเภทรูป")
        WebDriverWait(driver, 10).until(
            lambda d: d.find_element(By.ID, "selectedFile").is_enabled()
        )
        # 2) ส่งทุกไฟล์ในครั้งเดียว (input รับ multiple, คั่นด้วย \n)
        paths = "\n".join(str(Path(folder) / name) for name in files)
        driver.find_element(By.ID, "selectedFile").send_keys(paths)
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
        for name in files:
            time.sleep(0.5)
            driver.find_element(By.XPATH, "//input[@type='file']").send_keys(
                str(Path(folder) / name)
            )
            log(f"   + {name}")
        rows = driver.find_element(By.ID, "fileList").find_elements(
            By.XPATH, ".//table/tbody/tr"
        )
        for c in range(1, len(rows)):
            Select(
                driver.find_element(By.ID, f"ddlImageType{c}")
            ).select_by_visible_text(image_type)
        log(f"   ✓ ตั้งประเภทรูป '{image_type}' ครบ {len(rows) - 1} แถว")
        driver.find_element(By.ID, "btnUpload").click()

    # รออัปโหลดเสร็จ (ปุ่มปิดกล่องแจ้งผลโผล่) — เผื่อเวลาสำหรับรูปจำนวนมาก
    try:
        wait_clickable(driver, By.CLASS_NAME, "close", 600).click()
    except TimeoutException:
        log("   ⚠️ ไม่เห็นกล่องแจ้งผลอัปโหลด — ตรวจผลบนหน้าจอด้วย")
    time.sleep(2)  # ปิดกล่องแล้วหน้า refresh — พักให้นิ่งก่อนไปหน้าถัดไป
    log("EMCS: อัปโหลดรูปเสร็จ")


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


def fill_billing(driver, data: ClaimData):
    """หน้าค่าใช้จ่าย: เลข invoice + วันที่วางบิล(วันนี้ พ.ศ.) + สรุปความเห็น
    แล้วกด "บันทึก" (เป็น draft แก้ได้ — จุดส่งงานจริงคือปุ่ม 'ส่งงานใหม่'
    ซึ่งสคริปต์ไม่กดให้เด็ดขาด ต้องตรวจแล้วกดเอง)"""
    log("EMCS: กรอกหน้าค่าใช้จ่าย")
    click_retry(driver, By.ID, "wuMenuPage1_imbSpend")

    wait_visible(driver, By.ID, "txtBill_No", 15)
    set_text(driver, "txtBill_No", data.invoice_value)
    set_text(driver, "wuCale_Bill_Date_txtCalendar", today_buddhist())
    set_text(driver, "txtAcc_result", data.accident_summary)

    # ตารางราคา — กรอกเฉพาะช่อง "เสนอ" จากข้อมูล XML
    fill_fee_table(driver, data.bill)

    # ปุ่มบันทึกของหน้านี้คือ btnSurveySave ('บันทึกราคา') ซึ่งจะ enable
    # ก็ต่อเมื่อกรอกตารางราคาค่าสำรวจครบ — ถ้ายัง disabled แปลว่าต้องให้คน
    # กรอกราคาก่อน (ห้ามแตะปุ่ม 'ส่งงานใหม่' เด็ดขาดเช่นเดิม)
    try:
        btn = driver.find_element(By.ID, "btnSurveySave")
        if btn.is_enabled():
            btn.click()
            try:
                accept_alert(driver, timeout=8)
            except Exception:
                pass  # บางจังหวะไม่มี alert ยืนยัน
            log("EMCS: กดบันทึกราคาแล้ว ✅ — เหลือตรวจสอบและกด "
                "'ส่งงานใหม่' ด้วยตัวเองเมื่อพร้อม (สคริปต์จะไม่กดให้)")
        else:
            log("EMCS: กรอกหน้าค่าใช้จ่ายแล้ว — ปุ่ม 'บันทึกราคา' ยัง disabled "
                "(ต้องกรอกตารางราคาค่าสำรวจก่อน) ตรวจ/กรอกราคา แล้วบันทึก+"
                "ส่งงานเอง")
    except Exception:
        log("   ⚠️ ไม่เจอปุ่ม 'บันทึกราคา' — กรอกข้อมูลให้ครบแล้ว "
            "ตรวจและบันทึกเองบนหน้าจอ")


# --------------------------------------------------------------- ส่งงาน (commit)
def _find_submit_button(driver):
    """หาปุ่ม 'ส่งงานใหม่' (commit) — id หลัก wuFlow1_cmdSendNew, fallback หาโดย text
    ปุ่มนี้มีเฉพาะสถานะ 'รายงานสร้างใหม่' (draft) ในโหมดแก้ — สถานะอื่นจะไม่เจอ"""
    try:
        return driver.find_element(By.ID, "wuFlow1_cmdSendNew")
    except Exception:
        pass
    try:
        for el in driver.find_elements(
                By.CSS_SELECTOR, "input[type=submit],input[type=button],button"):
            if (el.get_attribute("value") or el.text or "").strip() == "ส่งงานใหม่":
                return el
    except Exception:
        pass
    return None


def submit_report(driver, cfg, claim):
    """commit งาน: กดปุ่ม 'ส่งงานใหม่' (ต้องอยู่หน้าค่าใช้จ่ายโหมดแก้ของ draft —
    live session ที่เพิ่งกรอกเสร็จ) แล้ว verify ว่าสถานะเปลี่ยนเป็น 'ส่งงานแล้ว' จริง

    คืน (ok: bool, msg: str). จะกดเฉพาะเมื่อ "เจอปุ่ม" (= เป็น draft) เท่านั้น —
    เป็น gate ในตัว (สถานะอื่นไม่มีปุ่มนี้)"""
    btn = _find_submit_button(driver)
    if btn is None:
        return False, ("ไม่เจอปุ่ม 'ส่งงานใหม่' — งานนี้อาจไม่ใช่ draft "
                       "หรือไม่ได้อยู่หน้าค่าใช้จ่ายโหมดแก้")
    try:
        if not btn.is_enabled():
            return False, "ปุ่ม 'ส่งงานใหม่' ยัง disabled (ข้อมูล/ราคายังไม่ครบ?)"
    except Exception:
        pass

    log("EMCS: กดปุ่ม 'ส่งงานใหม่' (commit งาน)")
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


# ------------------------------------------------------------------ flow รวม

def fill_one(driver, cfg, data: ClaimData, images_folder=None,
             loss_type: str = "auto", image_type: str = "รูปรถประกัน",
             severity: str = "เบา", force_new: bool = False) -> str:
    """กรอกเคลมเดียวจนจบ (driver ต้องอยู่หน้ารายการงาน EMCS แล้ว)
    คืนเลข e-Survey ของเรื่องที่สร้าง

    การ "บันทึก" ทุกหน้าเป็นแค่ draft แก้ไขได้ — สคริปต์กดบันทึกให้ครบ
    จุดส่งงานจริงคือปุ่ม 'ส่งงานใหม่' หน้าค่าใช้จ่าย ซึ่งสคริปต์
    **ไม่กดให้เด็ดขาด** / มีด่านกันเปิดเรื่องซ้ำ (ข้ามด้วย force_new)"""
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
    # เคลมสด: ส่วนคู่กรณีถูกปลดล็อกหลังบันทึกหน้าหลักเท่านั้น
    fill_third_parties(driver, data)
    fill_damage_list(driver, data, main_window)

    if images_folder is not None:
        upload_images(driver, images_folder, image_type=image_type)

    fill_billing(driver, data)
    return esurvey


def run_fill(driver, cfg, data: ClaimData, images_folder=None,
             loss_type: str = "auto", image_type: str = "รูปรถประกัน",
             severity: str = "เบา", force_new: bool = False) -> str:
    """login แล้วกรอกเคลมเดียว (flow เดิมสำหรับรันทีละเคลม)"""
    login(driver, cfg)
    return fill_one(driver, cfg, data, images_folder=images_folder,
                    loss_type=loss_type, image_type=image_type,
                    severity=severity, force_new=force_new)
