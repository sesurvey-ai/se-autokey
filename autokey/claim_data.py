"""โครงสร้างข้อมูลเคลมที่อ่านจาก ISURVEY เพื่อส่งต่อให้ EMCS"""
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ป้ายชื่อภาษาไทยของแต่ละ field (ใช้ทำรายงานความครบของข้อมูล)
FIELD_LABELS = {
    "claim_value": "เลขเคลม", "invoice_value": "เลขเซอร์เวย์", "notify_value": "เลขรับแจ้ง",
    "policy_value": "เลขกรมธรรม์", "claim_type": "ประเภทเคลม", "surveyor_name": "พนักงานสำรวจ",
    "arrive_date": "วันถึงที่เกิดเหตุ", "arrive_time": "เวลาถึงที่เกิดเหตุ",
    "finish_date": "วันเสร็จงาน", "finish_time": "เวลาเสร็จงาน",
    "accident_summary": "บันทึกความเห็นหัวหน้า",
    "acc_date": "วันที่เกิดเหตุ", "acc_time": "เวลาเกิดเหตุ", "acc_place": "สถานที่เกิดเหตุ",
    "acc_province": "จังหวัดเกิดเหตุ", "acc_amphur": "เขต/อำเภอเกิดเหตุ",
    "acc_type_desc": "สาเหตุการเกิดเหตุ", "acc_detail": "รายละเอียดการเกิดเหตุ",
    "acc_result": "ผลคดี",
    "insure_plate": "ทะเบียนรถ", "prb_number": "เลขที่ พรบ", "prb_car_type": "ประเภทรถ",
    "plate_province": "จังหวัดรถ", "car_brand": "ยี่ห้อรถ", "car_color": "สีรถ",
    "driver_name": "ชื่อผู้ขับขี่", "driver_surname": "นามสกุลผู้ขับขี่",
    "driver_relation": "ความสัมพันธ์", "driver_age": "อายุผู้ขับขี่",
    "driver_address": "ที่อยู่ผู้ขับขี่", "driver_province": "จังหวัดผู้ขับขี่",
    "driver_amphur": "อำเภอผู้ขับขี่", "driver_phone": "เบอร์โทรผู้ขับขี่",
    "driver_idcard": "บัตรประชาชนผู้ขับขี่", "driver_license_no": "ใบขับขี่เลขที่",
    "driver_license_place": "ใบขับขี่ออกที่", "driver_license_type": "ประเภทใบขับขี่",
    "damage_estimate": "ความเสียหายประมาณ", "driver_birthdate": "วันเกิดผู้ขับขี่",
    "license_issue_date": "วันออกใบขับขี่", "license_expiry_date": "วันหมดอายุใบขับขี่",
    "effective_date": "วันคุ้มครอง", "expiry_date": "วันสิ้นสุดคุ้มครอง",
    "insure_name": "ชื่อผู้เอาประกัน", "insure_type": "ประเภทประกัน",
    "insure_model": "รุ่นรถ", "insure_chassis": "เลขตัวถัง", "insure_engine": "เลขเครื่อง",
    "noti_date": "วันที่รับแจ้ง", "noti_time": "เวลารับแจ้ง",
}

# ประเภทเคลมของ ISURVEY → ชื่อ (ตรงกับ radio ฟอร์ม EMCS index = type-1)
# ประเภทเคลม = claim_MtypeID ของ ISURVEY — ป้ายยกมาจาก master จริง
# (list/masterClaimMType.php: 01 เคลมสด / 02 เคลมแห้ง / 03 ติดตาม / 04 เจรจาสินไหม)
# ISURVEY ส่งรหัสมาแบบไม่เติมศูนย์ ('1','2') จึง key ด้วยเลขล้วน
# หมายเหตุ: อย่าสับสนกับ claim_typeID (masterClaimType: 01 ส่งพนักงาน / 02 เคลมแห้ง)
# ซึ่งคือ "ประเภทการจ่าย" เก็บที่ field pay_type
CLAIM_TYPE_NAMES = {"1": "เคลมสด", "2": "เคลมแห้ง",
                    "3": "ติดตาม", "4": "เจรจาสินไหม"}
DRY_CLAIM_TYPE = "2"        # เคลมแห้ง

# field ที่ EMCS ต้องใช้จริง — ถ้าว่างถือว่าผิดปกติ ต้องให้คนตรวจ
CRITICAL_FIELDS = {
    "claim_value", "invoice_value", "policy_value", "claim_type", "surveyor_name",
    "arrive_date", "arrive_time", "finish_date", "finish_time",
    "acc_date", "acc_time", "acc_place", "acc_province", "acc_amphur", "acc_result",
    "insure_plate", "prb_number", "prb_car_type", "plate_province", "car_brand",
    "driver_name", "effective_date", "expiry_date", "insure_name",
    "noti_date", "noti_time",
}


# ---------------------------------------------------------------- ชื่อ/คำนำหน้า
# อยู่ที่ไฟล์นี้ (ไม่ใช่ emcs.py) เพราะฝั่งอ่าน ISURVEY ต้องใช้ตั้งแต่ตอนแยกชื่อ
# — emcs.py re-export ต่อ ผู้เรียกเดิมจึงเรียก emcs.split_thai_name ได้เหมือนเดิม

# คำนำหน้าชื่อ (เรียงยาว→สั้น เพื่อให้ 'นางสาว' จับก่อน 'นาง')
# รวมตัวย่อ น.ส./นส. ที่ ISURVEY มักติดมากับชื่อ (เช่น drv_name='น.ส.ปฐมาวดี')
# — verify เคลม 2026013144715
THAI_TITLES = ["เด็กหญิง", "เด็กชาย", "นางสาว", "น.ส.", "นส.",
               "ด.ญ.", "ด.ช.", "นาง", "นาย", "คุณ"]

# คำนำหน้าที่ต้องมีช่องว่างคั่นถึงจะนับ — 'คุณ' เป็นต้นคำของชื่อจริงได้
# (คุณากร, คุณัญญา) ถ้าตัดแบบติดกันจะกินชื่อคนหาย
_TITLES_NEED_SPACE = {"คุณ"}

# คำนำหน้า "กลาง ๆ" ที่ไม่ได้บอกอะไรเพิ่ม — พนักงานมักพิมพ์ 'คุณ' นำหน้าชื่อ
# เพราะสุภาพ ไม่ใช่เพราะเป็นคำนำหน้าจริง (เจอจริง 'คุณ พัลลภ ธาดากิจวณิช'
# เคลม 2026013158841 ซึ่งบัตรจริงคือ 'นาย') → ถ้ามีหลักฐานดีกว่าให้ใช้อันนั้นแทน
WEAK_TITLES = {"คุณ"}


def split_thai_name(full: str):
    """แยก 'นายกัมปนาท เปรมกิจ' → ('นาย', 'กัมปนาท', 'เปรมกิจ')
    (รองรับกรณีคำนำหน้าติดกับชื่อโดยไม่เว้นวรรค ยกเว้นคำใน _TITLES_NEED_SPACE)"""
    full = (full or "").strip()
    title = ""
    for t in THAI_TITLES:
        if not full.startswith(t):
            continue
        rest = full[len(t):]
        if t in _TITLES_NEED_SPACE and rest[:1] not in (" ", "\t"):
            continue
        title, full = t, rest.strip()
        break
    parts = full.split()
    first = parts[0] if parts else ""
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    return title, first, last


# คำนำหน้า → เพศ (ทิศนี้ชัดเจน 100% ต่างจากเพศ→คำนำหน้าที่กำกวม): M=ชาย, W=หญิง
# 'คุณ' ไม่อยู่ในตาราง — เป็นคำกลาง บอกเพศไม่ได้
TITLE_GENDER = {
    "นาย": "M", "เด็กชาย": "M", "ด.ช.": "M",
    "นาง": "W", "นางสาว": "W", "น.ส.": "W", "นส.": "W",
    "เด็กหญิง": "W", "ด.ญ.": "W",
}

# อายุที่ถือว่ายังเป็นเด็ก (ด.ช./ด.ญ.) — กรมการปกครองเปลี่ยนคำนำหน้าเมื่ออายุครบ 15
CHILD_TITLE_AGE = 15


def gender_from_title(name: str) -> str:
    """อนุมานเพศจากคำนำหน้าในชื่อ — fallback ตอน ISURVEY/XML ไม่มีเพศ
    เช่น 'นางสาว วณิศราภรณ์' → 'W', 'นาย อัมพร' → 'M'
    คืน 'M' (ชาย) / 'W' (หญิง) / '' (ไม่มีคำนำหน้า/แยกไม่ได้ → ให้คนเลือกเอง)"""
    title, _, _ = split_thai_name(name)
    return TITLE_GENDER.get(title, "")


def resolve_gender(explicit: str, name: str = "") -> str:
    """เพศจาก ISURVEY/XML ก่อน (normalize F→W, ค่าไทย ชาย/หญิง→M/W); ว่าง → อนุมานจากคำนำหน้าในชื่อ
    คืน 'M' (ชาย) / 'W' (หญิง) / '' (ไม่รู้ — ให้คนเลือกเอง)
    หมายเหตุ: คู่กรณี/ผู้บาดเจ็บจากแอปมือถือส่งเพศเป็นค่าไทย 'ชาย'/'หญิง' (ต่างจากรถประกันที่ส่ง M/F)
    ทั้งทาง XML และ /cases/:id/report — normalize ที่นี่จุดเดียวครอบทุกเส้นทาง"""
    g = (explicit or "").strip().upper()
    if g in ("ชาย", "ชาย ", "MALE"):
        return "M"
    if g in ("หญิง", "หญิง ", "FEMALE"):
        return "W"
    if g == "F":
        g = "W"
    if g in ("M", "W"):
        return g
    return gender_from_title(name)


def title_from_gender_age(gender: str, age="") -> str:
    """คำนำหน้าจาก 'เพศ + อายุ' — ใช้เป็นทางสุดท้ายเมื่อต้นทางไม่มีคำนำหน้าจริง

    ผู้ชายไทยมีคำนำหน้าเดียว → เพศ M แปลว่า 'นาย' แน่นอน (เด็ก <15 = ด.ช.)
    ผู้หญิงแยก นาง/นางสาว จากเพศไม่ได้ → 'คุณ' ซึ่งเป็นตัวเลือกจริงใน EMCS
    (ddlDri_Title_ID: นาย/นาง/นางสาว/ด.ช./ด.ญ./คุณ) หัวหน้าแก้ตอนตรวจได้
    — กติกาเดียวกับที่ใส่ '-' ในช่อง text บังคับที่ไม่มีข้อมูล

    คืน '' เมื่อไม่รู้เพศ (ผู้เรียกต้องหยุดรอคนเลือก)"""
    g = resolve_gender(gender)
    if g not in ("M", "W"):
        return ""
    try:
        yrs = int(str(age or "").strip() or 0)
    except ValueError:
        yrs = 0
    if 0 < yrs < CHILD_TITLE_AGE:
        return "ด.ช." if g == "M" else "ด.ญ."
    return "นาย" if g == "M" else "คุณ"


@dataclass
class ClaimData:
    # ---- Tab 1: Summary ----
    claim_value: str = ""          # เลขเคลม
    invoice_value: str = ""        # เลขเซอร์เวย์
    notify_value: str = ""         # เลขรับแจ้ง
    policy_value: str = ""         # เลขกรมธรรม์
    claim_type: str = ""           # ประเภทเคลม = claim_MtypeID (ดู CLAIM_TYPE_NAMES)
    pay_type: str = ""             # ประเภทการจ่าย (เช่น ส่งพนักงาน)
    third_party_condition: str = ""  # เงื่อนไขฝ่ายถูก
    branch: str = ""               # ศูนย์
    service_total: str = ""        # ค่าบริการรวมเป็นเงิน
    service_vat: str = ""          # ภาษีมูลค่าเพิ่ม
    service_total_net: str = ""    # จำนวนเงินรวมสุทธิ
    surveyor_name: str = ""        # พนักงานสำรวจ
    oss_company: str = ""          # บริษัท outsource (ถ้าเป็นงาน OSS)
    oss_surveyor: str = ""         # ชื่อพนักงาน outsource
    oss_phone: str = ""            # เบอร์พนักงาน outsource
    arrive_date: str = ""          # วันถึงที่เกิดเหตุ
    arrive_time: str = ""          # เวลาถึงที่เกิดเหตุ
    finish_date: str = ""          # วันเสร็จงาน
    finish_time: str = ""          # เวลาเสร็จงาน
    accident_summary: str = ""     # บันทึกความเห็นหัวหน้า → EMCS 'ผลการดำเนินงาน' (txtAcc_result)
    review_comment: str = ""       # ความเห็นผู้ตรวจสอบ (se-survey review_comment) → EMCS txtAcc_Comment
    surveyor_comment: str = ""     # ความเห็นของเซอร์เวย์ (se-survey surveyor_comment) → EMCS txtSurv_Comment (หน้าค่าใช้จ่าย)

    # ---- Tab 2: Accident info ----
    acc_date: str = ""             # วันที่เกิดเหตุ
    acc_time: str = ""             # เวลาเกิดเหตุ
    acc_place: str = ""            # สถานที่เกิดเหตุ
    acc_province: str = ""         # จังหวัดเกิดเหตุ
    acc_amphur: str = ""           # เขต/อำเภอเกิดเหตุ
    acc_type_desc: str = ""        # สาเหตุการเกิดเหตุ
    acc_detail: str = ""           # รายละเอียดการเกิดเหตุ
    acc_result: str = ""           # ผลคดี
    # ── กลุ่มที่ EMCS "บังคับ" เมื่อผลคดี = รถคู่กรณีเป็นฝ่ายผิด (rdoAcc_Cause01) ──
    # vlidSurvey(): ต้องมี txtAcc_Cause_No + ติ๊ก chkOpo_Result_ อย่างน้อย 1 ใน 5
    acc_fault_opponent_no: str = ""  # คู่กรณีคันที่ (txtAcc_Cause_No)
    opo_results: str = ""          # การเรียกร้องค่าเสียหายจากคู่กรณี (คั่นด้วย , )
    opo_pay: str = ""              # รับเงินจำนวน (txtOpo_Pay)
    opo_recovery: str = ""         # จากจำนวนเงินเรียกร้องทั้งหมด (txtOpo_Recovery_Amount)
    # ข้อความ "ให้หัวหน้าตรวจก่อนกดส่ง" ที่สะสมระหว่างกรอก → ไปโผล่บนการ์ดตอนรอสั่งส่ง
    # (เตือนอย่างเดียว ไม่หยุดกลางทาง — บอทกรอกจนจบแล้วค่อยให้คนตรวจทีเดียว)
    review_notes: list = field(default_factory=list)

    # ---- Tab 3: Insurance info ----
    insure_plate: str = ""         # ทะเบียนรถ
    prb_number: str = ""           # เลขที่ พรบ
    prb_car_type: str = ""         # ประเภทรถ
    plate_province: str = ""       # จังหวัดรถ
    car_brand: str = ""            # ยี่ห้อรถ
    car_color: str = ""            # สีรถ
    car_reg_year: str = ""         # ปีจดทะเบียน (แอปเก็บ พ.ศ. — EMCS ใช้ ค.ศ.)
    # 4 ช่องที่แอปมือถือให้พนักงานกรอก + EMCS มีช่องรองรับ แต่เดิมไม่มีอะไรพาไป
    mileage: str = ""              # เลข กม. รถประกัน → txtKm_No (ของคู่กรณีส่งอยู่แล้ว)
    model_no: str = ""             # หมายเลข Model → txtModelNo
    driver_by_policy: str = ""     # ชื่อผู้ขับขี่ตามกรมธรรม์ → txtDriver_By_Policy
    driver_ticket: str = ""        # ใบสั่งจราจร → txtDri_Order
    car_lost: bool = False         # รถหาย → chkLost_Car
    surveyor_phone: str = ""       # โทรศัพท์ผู้สำรวจ → txtAcc_Tel (อยู่ติดกับ txtAcc_Surv)
    # บล็อกตำรวจ + แอลกอฮอล์ + อีเมล + ค่าเสียหายส่วนแรก — แอปเก็บครบ แต่เดิมบอทไม่เคยกรอก
    # (พึ่ง XML importer ทางเดียว → หายในโหมดเติม draft ที่ข้าม import)
    police_name: str = ""          # → txtPolice_Name
    police_station: str = ""       # → txtPolice_Station
    police_comment: str = ""       # → txtPolice_Comment
    police_date: str = ""          # → wuCale_Police_Date_txtCalendar
    police_book_no: str = ""       # → txtBook_Number (เลขคดี/บันทึกประจำวัน)
    alcohol_test: str = ""         # → rdoAlc_Chk (มี/ไม่มีการตรวจ)
    alcohol_result: str = ""       # → txtAlc_Result (ผลตรวจ)
    assured_email: str = ""        # → txtAssured_Email
    deductible: str = ""           # → txtDeductible (ค่าเสียหายส่วนแรก)
    # ด้านของความเสียหายแต่ละชิ้น ('0'ซ้าย/'1'ขวา/'2'ทั้งคู่) — ขนานกับ damage/rank_damage
    # ว่าง = ให้ปลายทางเดาจากชื่อชิ้นส่วนเหมือนเดิม (flow ISURVEY ที่ชื่อมีข้างอยู่ในตัว)
    side_damage: list = field(default_factory=list)
    # รถยนต์ไฟฟ้า (EV) — se-survey เก็บครบ แต่เดิมไม่มีอะไรพาเข้า EMCS
    ev_type: str = ""              # code BEV/FCEV/HEV/MEV/PHEV (= value ของ ddlEvType)
    ev_battery_no: str = ""        # เลขแบตเตอรี่ (txtBatt_Number)
    ev_charger_no: str = ""        # เลขเครื่องชาร์จ (txtWallcharge_number)
    ev_battery_start: str = ""     # วันเริ่มใช้แบต (wuCale_batt_effdate)
    # การติดตามงาน/นัดหมาย (rdoFlu_Type + txtFlu_No/Detail/Date)
    followup_type: str = ""        # ไม่มีการนัดหมาย / รอการนัดหมาย / มีการนัดหมาย
    followup_count: str = ""
    followup_detail: str = ""
    followup_date: str = ""
    driver_title: str = ""         # คำนำหน้าผู้ขับขี่ (se-survey มี; ISURVEY ไม่มี → เดิม derive จากผู้เอาประกัน)
    driver_name: str = ""          # ชื่อผู้ขับขี่
    driver_surname: str = ""       # นามสกุลผู้ขับขี่
    driver_gender: str = ""        # เพศผู้ขับขี่ (M/W จาก XML — EMCS บังคับ)
    driver_relation: str = ""      # ความสัมพันธ์
    driver_age: str = ""           # อายุผู้ขับขี่
    driver_address: str = ""       # ที่อยู่ผู้ขับขี่
    driver_province: str = ""      # จังหวัดผู้ขับขี่
    driver_amphur: str = ""        # อำเภอผู้ขับขี่
    driver_phone: str = ""         # เบอร์โทรผู้ขับขี่
    driver_idcard: str = ""        # บัตรประชาชนผู้ขับขี่
    driver_license_no: str = ""    # ใบขับขี่เลขที่
    driver_license_place: str = "" # ใบขับขี่ออกที่
    driver_license_type: str = ""  # ประเภทใบขับขี่
    damage_estimate: str = ""      # ความเสียหายประมาณ
    driver_birthdate: str = ""     # วันเกิดผู้ขับขี่
    license_issue_date: str = ""   # วันออกใบขับขี่
    license_expiry_date: str = ""  # วันหมดอายุใบขับขี่

    # ---- Tab 3: รายการความเสียหาย ----
    damage: list = field(default_factory=list)        # ชิ้นส่วน
    type_damage: list = field(default_factory=list)   # ประเภท (ครูด/บุบ)
    rank_damage: list = field(default_factory=list)   # ระดับ (A-D)
    cost_damage: list = field(default_factory=list)   # ราคาประเมินต่อชิ้น (บาท)

    # ---- Tab 4-6: คู่กรณี / ผู้บาดเจ็บ / ทรัพย์สิน ----
    # เก็บเป็น list ของ dict (เคลมแห้งจะเป็น list ว่าง)
    third_parties: list = field(default_factory=list)
    injuries: list = field(default_factory=list)
    assets: list = field(default_factory=list)

    # ---- Tab 7: Policy info ----
    effective_date: str = ""       # วันคุ้มครอง
    expiry_date: str = ""          # วันสิ้นสุดคุ้มครอง
    insure_name: str = ""          # ชื่อผู้เอาประกัน
    insure_type: str = ""          # ประเภทประกัน
    insure_model: str = ""         # รุ่นรถ
    insure_chassis: str = ""       # เลขตัวถัง
    insure_engine: str = ""        # เลขเครื่อง

    # ---- Tab 8: Notify info ----
    noti_date: str = ""            # วันที่รับแจ้ง (= บ.ประกันแจ้งพนักงานสำรวจ)
    noti_time: str = ""            # เวลารับแจ้ง
    # วัน-เวลาที่ "ลูกค้าแจ้ง บ.ประกัน" — คนละจังหวะกับที่ประกันแจ้งสำรวจ (EMCS มี 2 ช่องแยก)
    # ISURVEY ไม่มีข้อมูลนี้ → ว่าง แล้ว fill_accident จะ fallback ไปใช้ noti_* เหมือนเดิม
    call_date: str = ""
    call_time: str = ""
    # "จ่ายงานเวลา" จาก tab-1 Summary (Dispatch) = วันที่ บ.ประกันแจ้งสำรวจภัย ของ EMCS
    # user ยืนยัน 2026-08-06: ให้ใช้ชุดเวลาจาก tab-1 เท่านั้น หน้าการ์ด (tab-8) ไม่ตรง
    dispatch_date: str = ""
    dispatch_time: str = ""

    # ---- อื่นๆ ----
    xml_file: str = ""             # path ไฟล์ SURV_REPORT XML ที่ดาวน์โหลดไว้
    # ค่าสำรวจฝั่ง "เสนอ" จาก XML (invest/trans/photo/tel/daily/other ฯลฯ)
    bill: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def save(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "ClaimData":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def summary(self) -> str:
        """สรุปข้อมูลหลักให้คนตรวจก่อนเริ่มกรอก EMCS"""
        lines = [
            f"เลขเคลม        : {self.claim_value}",
            f"เลขเซอร์เวย์    : {self.invoice_value}",
            f"กรมธรรม์       : {self.policy_value}",
            f"ประเภทเคลม     : {self.claim_type}",
            f"ทะเบียนรถ      : {self.insure_plate} ({self.plate_province})",
            f"รถ             : {self.car_brand} {self.insure_model} สี{self.car_color}",
            f"ผู้ขับขี่       : {self.driver_name} {self.driver_surname} (อายุ {self.driver_age})",
            f"ผู้เอาประกัน    : {self.insure_name}",
            f"วันเกิดเหตุ     : {self.acc_date} {self.acc_time} @ {self.acc_place}",
            f"จังหวัด/อำเภอ  : {self.acc_province} / {self.acc_amphur}",
            f"ผลคดี          : {self.acc_result}",
            f"ความเสียหาย    : {len(self.damage)} รายการ ≈ {self.damage_estimate} บาท",
        ]
        costs = self.cost_damage + [""] * (len(self.damage) - len(self.cost_damage))
        for i, (d, t, r, c) in enumerate(
            zip(self.damage, self.type_damage, self.rank_damage, costs), 1
        ):
            cost_txt = f" | {c} บาท" if str(c).strip() else ""
            lines.append(f"   {i}. {d} | {t} | ระดับ {r}{cost_txt}")

        if self.third_parties:
            lines.append(f"คู่กรณี        : {len(self.third_parties)} ราย")
            for i, tp in enumerate(self.third_parties, 1):
                insurer = tp.get("insurer", "").strip()
                lines.append(
                    f"   {i}. {tp.get('plate_no', '')} "
                    f"{tp.get('car_brand', '')} — {tp.get('drv_name', '')}"
                    + (f" | ประกัน: {insurer}" if insurer else "")
                )
        if self.injuries:
            lines.append(f"ผู้บาดเจ็บ      : {len(self.injuries)} ราย")
            for i, inj in enumerate(self.injuries, 1):
                hos = inj.get("hospital", "")
                lines.append(f"   {i}. {inj.get('name', '')} "
                             f"({inj.get('injure', '')})"
                             + (f" — {hos}" if hos else ""))
        if self.assets:
            lines.append(f"ทรัพย์สินเสียหาย : {len(self.assets)} รายการ")
            for i, a in enumerate(self.assets, 1):
                lines.append(f"   {i}. {a.get('name', '')} "
                             f"≈ {a.get('damage_cost', '')} บาท")

        if self.bill:
            labels = [("invest", "ค่าบริการ"), ("trans", "ค่าเดินทาง"),
                      ("dist", "ค่าระยะทาง"), ("photo", "ค่ารูป"),
                      ("tel", "ค่าโทรศัพท์"), ("insure", "ค่าประกัน"),
                      ("claim", "ค่าเคลม"), ("daily", "ค่าคัดประจำวัน"),
                      ("other", "อื่นๆ"), ("cartow", "ค่ายกลาก")]
            items = []
            for key, name in labels:
                raw = str(self.bill.get(key, "")).replace(",", "").strip()
                try:
                    if float(raw or 0) > 0:
                        items.append(f"{name} {raw}")
                except ValueError:
                    pass
            src = ("จอ ISURVEY ชุดอนุมัติ"
                   if self.bill.get("source") == "isurvey_screen" else "XML")
            total = self.bill.get("total_net") or self.bill.get("total") or ""
            lines.append(
                f"ค่าสำรวจ→EMCS  : {' / '.join(items) if items else '(ทุกรายการ 0)'}"
                + (f" | รวมสุทธิ {total}" if str(total).strip() else "")
                + f" [{src}]"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def claim_type_name(self) -> str:
        t = self.claim_type.strip().lstrip("0")
        return CLAIM_TYPE_NAMES.get(t, f"ไม่ทราบประเภท ({t or 'ว่าง'})")

    def fresh_claim_note(self) -> str:
        """คำอธิบายว่าเคลมนี้ "ไม่ใช่เคลมแห้งแท้" อย่างไร — '' = เคลมแห้งแท้

        ⚠️ เป็นแค่ "ข้อความเตือนให้ตรวจหนักขึ้น" ไม่ใช่ด่านบล็อกแล้ว
        (เดิมชื่อ dry_claim_block_reason ใช้หยุดไม่ให้กรอกเคลมสด เพราะตอนนั้น
        API อ่าน tab-4/5/6 ไม่ได้ → คู่กรณีหายเงียบ ๆ. แก้ที่ต้นเหตุแล้ว
        2026-08-03: อ่านครบทุกประเภทเคลม ด่านจึงไม่มีเหตุผลให้อยู่ต่อ)"""
        if self.claim_type.strip().lstrip("0") != DRY_CLAIM_TYPE:
            return f"ประเภทเคลม = {self.claim_type_name()} (ไม่ใช่เคลมแห้ง)"
        if self.third_parties or self.injuries or self.assets:
            return (f"มีคู่กรณี {len(self.third_parties)} / ผู้บาดเจ็บ "
                    f"{len(self.injuries)} / ทรัพย์สิน {len(self.assets)}")
        return ""

    def validate(self) -> dict:
        """ตรวจความครบของข้อมูล
        คืน {'critical': [field สำคัญที่ว่าง], 'optional': [field รองที่ว่าง],
             'warnings': [ความผิดปกติอื่น]}"""
        critical, optional = [], []
        for fname, label in FIELD_LABELS.items():
            if not str(getattr(self, fname, "")).strip():
                (critical if fname in CRITICAL_FIELDS else optional).append(label)

        warnings = []
        n = len(self.damage)
        if n == 0:
            warnings.append("ไม่มีรายการความเสียหายเลย")
        if not (n == len(self.type_damage) == len(self.rank_damage)):
            warnings.append(
                f"รายการความเสียหายไม่ครบคู่ (ชิ้นส่วน {n} / "
                f"ประเภท {len(self.type_damage)} / ระดับ {len(self.rank_damage)})"
            )
        bad_rank = [r for r in self.rank_damage
                    if r.strip().upper() not in ("A", "B", "C", "D")]
        if bad_rank:
            warnings.append(f"ระดับความเสียหายไม่รู้จัก: {bad_rank}")
        if n > 8:
            warnings.append(f"ความเสียหาย {n} รายการ เกินที่หน้า EMCS รับได้ (8)")
        if "คู่กรณี" in self.acc_result and not self.third_parties:
            warnings.append("ผลคดีกล่าวถึงคู่กรณี แต่ไม่พบข้อมูลคู่กรณีใน Tab 4")
        return {"critical": critical, "optional": optional, "warnings": warnings}

    def validation_report(self) -> str:
        """รายงานความครบของข้อมูลแบบอ่านง่าย ให้คนตัดสินใจก่อนกรอก EMCS"""
        v = self.validate()
        lines = []
        if v["critical"]:
            lines.append(f"❌ field สำคัญที่ยังว่าง ({len(v['critical'])}): "
                         + ", ".join(v["critical"]))
        for w in v["warnings"]:
            lines.append(f"⚠️ {w}")
        if v["optional"]:
            lines.append(f"ℹ️ field รองที่ว่าง ({len(v['optional'])}): "
                         + ", ".join(v["optional"]))
        if not lines:
            lines.append("✅ ข้อมูลครบทุก field")
        return "\n".join(lines)
