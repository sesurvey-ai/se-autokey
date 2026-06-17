# ISURVEY API — ผลการ probe (2026-06-15)

probe ด้วย `tools/probe_capture.py` (selenium-wire ดัก request จริงตอนเปิดเคลม).
**สรุป: ฝั่งอ่านมี REST-ish JSON API ครบทั้งหมด — เปลี่ยนจาก scrape DOM เป็นยิง API ได้เต็มตัว**
ไม่ต้องเปิด browser/ExtJS เลย. raw dump พร้อม response เต็ม: `runs/logs/isurvey_capture_*.json`

## Auth
- `POST https://cloud.isurvey.mobi/web/php/login.php`  body `username=...&password=...`
  → ตั้ง cookie **`PHPSESSID`**. หลังจากนั้นแนบ cookie นี้ไปทุก request (ใช้ requests.Session ได้เลย)
- พิสูจน์แล้วว่ายิงนอก browser ได้จริง (urllib + cookie คืน JSON)

## Flow ฝั่งอ่าน (key หมุนรอบ `caseID`)
```
claim_no ──listcases──▶ caseID ──getcaseinfo(tab)──▶ ข้อมูลแต่ละแท็บ (JSON)
                                ├─list_parts_ins_car──▶ รายการความเสียหาย
                                └─get-images──────────▶ รายการรูป (มี url ตรง)
```

### 1) เลขเคลม → caseID + แถวสรุป
`GET web/php/supervisor/listcases.php?claim_no=<CLAIM>&claim_status=&claim_date=&page=1&start=0&limit=25`
- คืน `{"total":1,"cases":[{ caseID, claim_type, sttcase_ID, claim_no, notify_no,
  survey_no, surveyorID, accident_datetime, acc_place, acc_province, ... }]}`
- **ได้ `caseID` (เช่น 00000934136) จาก claim_no** — คีย์ที่ใช้เรียกทุก endpoint ต่อไป
- (ไม่ใส่ claim_no = คืนรายการเคลมทั้งหมด 50 แถว/หน้า)

### 2) ข้อมูลรายละเอียดแต่ละแท็บ
`GET web/php/supervisor/getcaseinfo.php?caseID=<CASEID>&tab=tab-<N>_clone`
- คืน `{"success":true,"message":{ ...ข้อมูล... }}` (ข้อมูลจริงอยู่ใต้ `message`)
- แท็บที่ยืนยันแล้ว (ตรงกับที่เรา scrape ทุกวันนี้):
  | tab | ได้อะไร | field ตัวอย่าง |
  |---|---|---|
  | `tab-1_clone` | สรุป | accident_summary, status, rpt_flag, usertype, roleID |
  | `tab-2_clone` | เหตุการณ์ | acc_date, acc_time, acc_lat, acc_lon, acc_type_desc, ref_no |
  | `tab-3_clone` | รถ/ประกัน | plate_no, plate_provinceID, car_brand, car_model, car_color, chassis_no, engine_no, oth_comp_no, oth_insure_companyID |
  | `tab-7_clone` | กรมธรรม์ | policy_no, policy_TypeID, effective_date, expiry_date, car_brand, car_model |
  | `tab-8_clone` | รับแจ้ง | claim_no, ref_no, notify_no, claim_typeID, ins_companyID, surveyorID, surveyor_name |
- คาดว่ามี tab-4/5/6 (คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน) ด้วย — ยังไม่ได้ยิงรอบนี้ (probe ตั้ง include_record_tabs=False) ควรยืนยันกับเคลมที่มีคู่กรณี

### 3) รายการความเสียหายรถประกัน
`GET web/php/supervisor/list_parts_ins_car.php?caseID=<CASEID>&page=1&start=0&limit=25`
- คืน `{"total":N,"parts":[{ partname, damage_type_detail, damaged_level, LABOUR_COST, damage_cost, memo, cl_vID }]}`

### 4) รายการรูป + โหลดตรง  ✅ ทำแล้ว (isurvey_api.download_images)
`GET web/php/supervisor/get-images.php?caseID=<CASEID>&t=<T>`
- คืน `{"images":[{ name, size, mod, url, id }]}` — `url` เป็น relative เช่น
  `CaseFiles/2026-06//i260602822/PICTURES/INS/xxx.jpg?dc=...`
- **t→หมวด: t=1=OTHERS, t=2=REPORTS, t=3=INS** (t=7=รวมทั้งหมดแต่ category ใน url
  ไม่ชัด — เลยวน t=1..6 แล้วอ่านหมวดจาก path `PICTURES/<CAT>/` แทน, TP_VEH→tp_veh/)
- **URL ดาวน์โหลดจริง = `https://cloud.isurvey.mobi/` + url (ไม่มี `/web/`)** — โหลดด้วย
  cookie (requests.Session) ได้ 200 image/jpeg
- **verify แล้ว: เทียบกับวิธี zip เดิม เคลม 2026013046414 = ตรงกันทุกไฟล์ 27/27
  (INS 22 / REPORTS 4 / OTHERS 1) ชื่อไฟล์+หมวดตรง** (zip เหลือ archive .zip ในโฟลเดอร์
  เป็นไฟล์เดียวที่ต่าง ซึ่งไม่ใช่รูป — API สะอาดกว่า)

### 5) ตาราง lookup / code mapping (web/php/list/master*.php)
ทุกตัว GET → `{"data":[...],"total":N}`. ที่จับได้:
`masterProvince`(provinceID→provincename, 10=กรุงเทพฯ), `masterAmphur`(amphurID→amphurname),
`masterTumbon`, `masterLtCompany`(companyID→companyName, 00001=เอ็ม เอส ไอ จี),
`masterCarBrand`(cbrandID→cbrand_name_en), `masterCarModel`(?carBrand=TOYOTA ฟิลเตอร์ได้),
`masterCarColor`, `masterPlateColor`, `masterVehType`, `masterPolicyType`(01=ประเภท1),
`masterNameTitle`(นาย/นาง..), `masterDrvLicense`, `masterRelation`, `masterClaimType`(01=ส่งพนักงาน,02=เคลมแห้ง),
`masterClaimMType`, `masterClaimTP`(1=ประกันภัย,2=บุคคล,3=ไม่มี), `masterClaimVerdict`(01=รอคำตัดสิน,02=เป็นฝ่ายถูก..),
`masterAccType`, `masterPolice`, `masterStatus`, `masterDamageType`, `masterDamageLevel`(A/B/C/D), `masterCarPart`, `masterFixPlace`
→ **code→ชื่อ ทำ mapping ได้ตรงๆ** (ดีกว่าเดาลำดับ dropdown ฝั่ง EMCS)

## หมายเหตุ
- `get_data_report.php` (เจอจาก probe รอบแรก) เป็นของหน้า "รายงานช่วงวันที่" คนละชุดกับ
  detail — **detail ใช้ getcaseinfo.php ตามด้านบน** (สะอาดกว่า ไม่ต้อง parse XML export เลย)
- selenium-wire 5.1.0 ใช้ได้หลัง `pip install blinker==1.7.0` (รุ่นใหม่ถอด `blinker._saferef`)
- เครื่องมือ probe: `probe_capture.py` (selenium-wire, ครบสุด), `probe_network.py` (perf-log),
  `probe_api_js.py` (อ่าน source ExtJS). password ถูก redact ใน log/dump แล้ว

## สถานะการลงมือ (autokey/isurvey_api.py)
1. ✅ `requests.Session` → login.php → listcases→caseID → getcaseinfo ทุก tab +
   list_parts → `ClaimData` (verify --compare ตรง 8 เคลมแห้ง)
2. ✅ master*.php cache map code→ชื่อ (จังหวัด/อำเภอ/ผลคดี/ประเภทรถ/ใบขับขี่)
3. ✅ โหลดรูปจาก get-images url ตรงด้วย cookie (verify ตรงกับ zip 27/27)
4. ✅ ฝั่งเขียน EMCS คงเป็น browser; main.py flag `--api`/`--compare` (opt-in)
- ⏳ เหลือ: คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน (tab-4/5/6) สำหรับเคลมสด — ยัง probe ไม่ครบ
- ⏳ เหลือ: ทดสอบ `--api` กับ flow กรอก EMCS จริงให้ครบ → แล้วค่อยสลับเป็น default
