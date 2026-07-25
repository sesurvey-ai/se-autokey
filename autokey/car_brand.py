"""แปลง "ยี่ห้อรถ" ภาษาไทย → ป้ายในดรอปดาวน์ EMCS (ddlCMFG / wuOpo_ddlCmfg)

ทำไมต้องมี: ตัวเลือกยี่ห้อใน EMCS เป็น **อังกฤษล้วน** (ดัมพ์หน้าจริง 2026-07-25:
'AION', 'MG', 'NISSAN', 'TOYOTA', ... 70 ยี่ห้อ + '-- ระบุ --') แต่ se-survey
(แอปมือถือ/OCR) เก็บเป็น **ไทย** ('เอ็มจี', 'นิสสัน') → fuzzy WRatio ได้ 0 คะแนน
แล้วไปลงเอยที่ '-- ระบุ --' (เคส #104: ยี่ห้อรถประกัน + รถคู่กรณี ว่างทั้งคู่)
ฝั่ง ISURVEY เดิมส่งอังกฤษมาอยู่แล้ว (CMFG='ATOYOTA' → 'TOYOTA') จึงไม่เคยเจอ

ตารางนี้ = ไทย → ป้าย EMCS; ค่าที่เป็นอังกฤษอยู่แล้วส่งผ่านตามเดิม
"""

from rapidfuzz import fuzz, process

# ไทย → ป้ายที่ EMCS ใช้ (ยืนยันจากดัมพ์ ddlCMFG ของ 'เก๋งเอเชีย' + ตาราง BRAND
# ของ se-survey backend/src/services/xmlExport.service.ts ที่ map ไทย→รหัสพอร์ทัล)
THAI_TO_EMCS = {
    # เก๋ง/กระบะ ญี่ปุ่น-เกาหลี (เจอบ่อยสุด)
    "โตโยต้า": "TOYOTA", "โตโยตา": "TOYOTA", "ทาโยต้า": "TOYOTA",
    "ฮอนด้า": "HONDA", "ฮอนดา": "HONDA",
    "อีซูซุ": "ISUZU", "อีซูสุ": "ISUZU", "อิซูซุ": "ISUZU",
    "นิสสัน": "NISSAN", "นิสัน": "NISSAN",
    "มิตซูบิชิ": "MITSUBISHI", "มิตซูบิซิ": "MITSUBISHI",
    "มาสด้า": "MAZDA", "มาสดา": "MAZDA",
    "ซูซูกิ": "SUZUKI", "ซุซุกิ": "SUZUKI",
    "ซูบารุ": "SUBARU", "ซุบารุ": "SUBARU",
    "ไดฮัทสุ": "DAIHATSU", "ดัทสัน": "DATSUN", "แดวู": "DAEWOO",
    "เลกซัส": "LEXUS", "เล็กซัส": "LEXUS",
    "ฮุนได": "HYUNDAI", "ฮุนไดย์": "HYUNDAI", "ฮุนแด": "HYUNDAI",
    "เกีย": "KIA",
    "ซันยง": "SSANGYONG", "ซังยง": "SSANGYONG",
    "ฮีโน่": "HINO", "ฮีโน": "HINO", "ฟูโซ่": "FUSO", "ฟูโซ": "FUSO",
    # อเมริกา/ยุโรป
    "ฟอร์ด": "FORD", "เชฟโรเลต": "CHEVROLET", "เชฟโรเล็ต": "CHEVROLET",
    "เมอร์เซเดส-เบนซ์": "BENZ", "เมอร์เซเดส": "BENZ", "เบนซ์": "BENZ", "เบนซ": "BENZ",
    "บีเอ็มดับเบิลยู": "BMW", "บีเอ็มดับบลิว": "BMW", "บีเอ็มดับเบิ้ลยู": "BMW",
    "วอลโว่": "VOLVO", "วอลโว": "VOLVO",
    "ปอร์เช่": "PORSCHE", "ปอร์เช": "PORSCHE",
    "ออดี้": "AUDI", "อาวดี้": "AUDI",
    "เปอโยต์": "PEUGEOT", "เรโนลต์": "RENAULT", "โฟล์คสวาเกน": "VOLKSWAGEN",
    "แลนด์โรเวอร์": "LAND ROVER", "จากัวร์": "JAGUAR", "มินิ": "MINI",
    "สแกนเนีย": "SCANIA",
    # จีน/EV (EMCS เพิ่มมาเยอะ)
    "เอ็มจี": "MG", "เอ็มยี": "MG",
    "บีวายดี": "BYD", "เนต้า": "NETA", "เนตา": "NETA", "โอร่า": "ORA", "โอรา": "ORA",
    "ฮาวาล": "HAVAL", "เชอรี่": "CHERY", "เชอร์รี่": "CHERY",
    "วินฟาสต์": "VINFAST", "ซีเคอร์": "ZEEKR", "ลิงค์แอนด์โค": "LYNK CO",
    "ฉางอาน": "CHANGAN", "จีลี่": "GEELY", "อ๋าวเหว่ย": "AION", "ไอออน": "AION",
    "หลิงเป่า": "LEAPMOTOR", "ทาทา": "TATA", "โปรตอน": "PROTON", "เตสลา": "TESLA",
    "เทสล่า": "TESLA", "วูหลิง": "WULING", "เจ็ตทัวร์": "JETOUR", "เจคู": "JAECOO",
    "โอโมดา": "OMODA", "แทงค์": "TANK", "เวย์": "WEY",
    # ยี่ห้อที่ยืนยันว่ามีในดรอปดาวน์จริง (ดัมพ์ ddlCMFG) แต่ยังไม่มีคีย์ไทย
    "สโกด้า": "SKODA", "สโกดา": "SKODA", "ฟอมม์": "FOMM", "ลักซ์เจน": "LUXGEN",
    "เนียว": "NIO", "เอ็กซ์เผิง": "XPENG", "โซตี้": "ZOTYE", "มาฮินดรา": "MAHINDRA",
    "เปโรดัว": "PERODUA", "อาวาตาร์": "AVATR", "หงฉี": "HONGQI", "ทรัมป์ชี": "TRUMPCHI",
    "ไป่จวิ้น": "BAOJUN", "ต้าเฉิง": "DFM", "นาซ่า": "NAZA",
    # หมายเหตุ: 'เกรทวอลล์' ไม่มีป้ายใน EMCS (ขายใต้ HAVAL) — ไม่ใส่ ปล่อยให้คนเลือกเอง
    # ดีกว่าเดาแล้วไปลง BORGWARD
    # มอเตอร์ไซค์
    "ยามาฮ่า": "YAMAHA", "ยามาฮา": "YAMAHA",
    "คาวาซากิ": "KAWASAKI", "เวสป้า": "VESPA", "จีพีเอ็กซ์": "GPX",
    "ไทรอัมพ์": "TRIUMPH", "ดูคาติ": "DUCATI", "เบเนลลี่": "BENELLI",
    "รอยัลเอนฟิลด์": "ROYAL ENFIELD", "ฮาร์เลย์": "HARLEY DAVIDSON",
}

_THAI_KEYS = list(THAI_TO_EMCS)
# เกณฑ์ fuzzy ของ "คีย์ไทย": ค่าที่ควรเข้า (สะกดเพี้ยน/มีรุ่นต่อท้าย) ต่ำสุด 83
# ('อีซุซุ') ส่วนชื่อ "รุ่น" ที่ไม่ควรเข้าเกาะอยู่ที่ 80 พอดี ('ฟอร์จูนเนอร์'→'ฟอร์ด' = FORD!)
_MIN_FUZZY = 83
# คีย์สั้น ('มินิ','เกีย','ทาทา') ถูกจับเป็น substring ของคำอื่นได้ 90 → บังคับตรงเป๊ะเท่านั้น
_MIN_KEY_LEN = 5

# เกณฑ์ fuzzy ตอน "เลือกใน dropdown ยี่ห้อ" (ส่งให้ fuzzy_select ผ่าน min_score):
# ลิสต์ยี่ห้อถูกกรองตามประเภทรถ ยี่ห้อที่ไม่มีในลิสต์จะไปเกาะยี่ห้ออื่นเงียบ ๆ ได้
# วัดจากลิสต์จริง: ค่าถูกต้อง ≥90 เสมอ (TOYOTA 100 / ATOYOTA 92 / 'MG 3' 90)
# ค่ามั่วสูงสุด 80 (TRIUMPH→TRUMPCHI) → ตัดที่ 90 แยกขาด
BRAND_MIN_SCORE = 90

# ค่าที่ "ไม่ใช่ยี่ห้อ" — กติกา _dash ของโปรเจกต์ใส่ '-' ให้ฟิลด์บังคับที่ไม่มีข้อมูล
# ถ้าปล่อยผ่าน '-' จะไปโดน option '-ALL-' และ 'NA' จะไปโดน 'NASA' (มีจริงในลิสต์)
_NOT_A_BRAND = {"", "N/A", "NA", "ALL", "NONE", "NULL", "0"}


def _has_thai(s: str) -> bool:
    return any("฀" <= c <= "๿" for c in s)


def normalize_brand(value) -> str:
    """คืนป้ายยี่ห้อที่ EMCS ใช้ (อังกฤษพิมพ์ใหญ่); ไม่รู้จัก → คืนค่าเดิม, ไม่ใช่ยี่ห้อ → ''

    - อังกฤษอยู่แล้ว ('TOYOTA', 'Mazda', 'MG 3')    → พิมพ์ใหญ่ (WRatio เป็น case-sensitive
      ตัวเลือก EMCS พิมพ์ใหญ่ล้วน — 'Mazda' ดิบเคยไปโดน 'MG')
    - ไทยตรงตาราง ('เอ็มจี')                        → 'MG'
    - ไทยสะกดเพี้ยน/มีรุ่นต่อท้าย ('โตโยต้า วีออส')  → fuzzy คีย์ไทย ≥83 → 'TOYOTA'
    - '-', 'NA', 'N/A' (= ไม่มีข้อมูล)              → '' (ให้ไปจบที่หยุดรอคนเลือก)
    - ไทยที่ไม่รู้จัก                                 → คืนค่าเดิม (ให้ fuzzy_select ตัดสิน
      แล้วไปจบที่ 'หยุดรอคนเลือก' ตาม guard กันเลือก placeholder)
    """
    s = str(value or "").strip()
    if s.upper().strip("-./ ") in _NOT_A_BRAND:
        return ""
    if not _has_thai(s):
        return s.upper()
    hit = THAI_TO_EMCS.get(s)
    if hit:
        return hit
    best = process.extractOne(s, _THAI_KEYS, scorer=fuzz.WRatio)
    if best and best[1] >= _MIN_FUZZY and len(best[0]) >= _MIN_KEY_LEN:
        return THAI_TO_EMCS[best[0]]
    return s
