# DEVELOPMENT.md — บันทึกความรู้สำหรับพัฒนาต่อ

> ไฟล์นี้คือ "สมองของโปรเจกต์" — รวมการค้นพบทางเทคนิค การตัดสินใจ บั๊กที่แก้แล้ว
> และงานค้างสำหรับการพัฒนาต่อ อัปเดตล่าสุด: 11 มิ.ย. 2026
> (คู่มือใช้งานทั่วไปอยู่ที่ [README.md](README.md))

---

## 1. สถานะปัจจุบัน

| ส่วน | สถานะ |
|---|---|
| ฝั่งอ่าน ISURVEY (ทุก tab + รูป zip + XML) | ✅ ใช้งานจริง ทดสอบผ่าน |
| ฝั่งกรอก EMCS **เคลมแห้ง** | ✅ ใช้งานจริง E2E ผ่าน (เหลือคนกด "ส่งงานใหม่" ปุ่มเดียว) |
| ฝั่งกรอก EMCS **เคลมสด/คู่กรณี** | 🟢 คู่กรณี + **ผู้บาดเจ็บ (Tab5) + ทรัพย์สิน (Tab6) + รูปครบทุกหมวด** verify 2026-06-18 (ดู §4): กรอก `fill_injuries`/`fill_assets` บันทึกผ่านรอบแรก + อัปรูป tp_person/tp_prop → 'รูปผู้บาดเจ็บ คนที่N'/'รูปทรัพย์สิน รายการที่N' (option dynamic); เหลือ ยี่ห้อ/มีประกันภัยที่ (คู่กรณีไม่มีประกัน) เลือกเอง |
| Batch หลายเคลม (อ่าน+กรอก EMCS ต่อเคลม) | ✅ ใช้ได้ (`--claims`, `--claims-file`) — เคลมไม่แห้ง/มีเรื่องแล้ว = ข้ามอัตโนมัติพร้อมเหตุผล |
| ด่านกันเปิดเรื่องซ้ำ | ✅ ทดสอบกับของจริงผ่าน |
| งานต่อเนื่อง (ครั้งที่ 2,3,… auto-detect) | ✅ เขียน+ทดสอบ 2026-06-18 (ดู §6.2); ฝั่งกรอก+ส่ง (cmdSendFollow) ครบ |
| Deploy เครื่องพนักงาน (พก Python ในตัว) | ✅ `runtime\` = Python 3.13 embeddable + deps ครบ — copy แล้วรันได้ ไม่ต้องลง Python (สร้างด้วย `build-runtime.bat`, ทดสอบ webui เสิร์ฟ HTTP 200 ผ่าน) |
| บันทึกงานลง se-key DB (key.sesurvey.cloud) | ✅ ตรวจ survey_no ซ้ำก่อนกรอก (ซ้ำ=ข้าม) + บันทึก+mark "ส่งแล้ว" ตอนกดส่งงาน (`autokey/sekey_client.py`); ทดสอบ auth+ตรวจซ้ำกับ prod (323k แถว) + **POST จริงผ่านแล้ว** (backfill เคลม 2026013145665 → SEABI-210260601351 → record id 336763, sent=1) |
| เลือกรูปก่อนอัปโหลด EMCS (หน้าเว็บ) | ✅ ก่อน upload หยุดโชว์แกลเลอรี ติ๊กเลือกรูป → อัปโหลดเฉพาะที่เลือก (marker `@@SELECT_IMAGES@@` + webui route `/image` + `upload_images(only=...)`); console = ทุกรูปเหมือนเดิม. ทดสอบ server-side (poll/serve/traversal-block) ผ่าน |
| แกลเลอรีจัดกลุ่มตามหมวด (INS/REPORTS/OTHERS) | ✅ เก็บหมวดผ่าน manifest: `download_images`→`_categories.json`(ชื่อ→หมวด), `process_images_pro`→`_rename_map.json`(ชื่อใหม่→เดิม), `browser._image_categories` รวมแล้วส่งใน marker; webui จัดกลุ่ม+checkbox "เลือกทั้งหมวด" (ไม่มี manifest=OTHERS หมด ปลอดภัย). E2E จริงผ่าน (2026013046414: INS22/REPORTS4/OTHERS1 หมวดรอดผ่าน rename) |
| แผงเลือกประเภทงาน (งานต้น/ตาม/SESV/งานรวม) ตอนส่ง | ✅ ยกตรรกะ work_type จาก se-key `content.js` มาไว้ webui submit pause — `wait_for_submit` ส่ง base_type default (SESV จาก prefix) + รับ {base_type,batch,mix} กลับ; `sekey_client.build_payloads` (งานรวม/SESV = หลาย row, SESV→iSurvey ใช้ SEABI), `save_many`. console=default. ทดสอบ parse+build_payloads+EOF/console ผ่าน (รอ user ทดสอบ UI จริง) |

**นโยบายปัจจุบัน (user กำหนด 11 มิ.ย. 2026): ใช้กับเคลมแห้งเท่านั้น**

---

## 2. สถาปัตยกรรม

```
webui.py               หน้าเว็บ launcher (stdlib ล้วน) — เรียก main.py ผ่าน subprocess
                       (stdin=PIPE, env SE_WEBUI=1) แล้วสตรีม stdout ขึ้นเว็บ;
                       รันหลายงานพร้อมกันได้ (_runs keyed by run_id, เพดาน
                       SE_MAX_CONCURRENT=4) UI การ์ดต่องาน; routes POST
                       /run /poll /stop /continue /forget;
                       จับ marker @@MANUAL_FILL@@ → สถานะ waiting → โชว์ปุ่ม "ดำเนินการต่อ"
start-webui.bat        ดับเบิลคลิกเปิด webui.py ผ่าน runtime\python.exe (ถ้าไม่มี = fallback ไป python ของเครื่อง)
                       (ASCII+CRLF เท่านั้น ห้ามใส่ไทย/LF — cmd พัง)
build-runtime.bat      สร้าง runtime\ ครั้งเดียว: curl โหลด Python 3.13.5 embeddable + get-pip +
                       pip install -r requirements.txt (ต้องมีเน็ต; รันบนเครื่อง dev ครั้งเดียวแล้ว copy)
runtime/               Python 3.13 embeddable + Lib\site-packages (~250MB, gitignored) — พกไปกับโฟลเดอร์
                       python313._pth เปิด `import site` + ใส่ `..` ให้เห็น package ราก (autokey/webui/main)
main.py                จุดเริ่ม CLI — orchestrate ทั้ง flow + ด่านความปลอดภัยทั้งหมด
autokey/
  config.py            โหลด .env (ISURVEY_/EMCS_ USERNAME/PASSWORD) + path
  browser.py           Chrome driver + helper กลาง: log/log_plain (tee ลงไฟล์),
                       fuzzy_select (rapidfuzz + retry stale), click_retry,
                       accept_alert (คืนข้อความ alert), save_debug_snapshot,
                       to_buddhist_date, iso_to_thai_date, wait_*
  claim_data.py        ClaimData dataclass ~60 fields + bill dict + validate()/summary()
  isurvey.py           ฝั่งอ่าน (scrape): login/ensure_logged_in, ค้นเคลม (โพล+คลิกซ้ำ+ตรวจผล),
                       อ่าน tab 1,2,3,7,8 + ค่าสำรวจชุด INS_*, tab 4-6 (fallback),
                       โหลดรูป panel (fallback)
  isurvey_api.py       ฝั่งอ่าน (HTTP API — **ค่าเริ่มต้นแล้ว**; --scrape เพื่อใช้ Selenium):
                       requests.Session → login.php → listcases(เลขเคลม)→caseID →
                       getcaseinfo ทุก tab + list_parts + master* → ClaimData รูปแบบ
                       เดียวกับ scrape (--compare ตรง 8 เคลมแห้ง) + download_images
                       (get-images ทุกหมวด ตรงวิธี zip 27/27); ยังไม่ทำคู่กรณี (เคลมสด→--scrape)
  emcs.py              ฝั่งกรอก: login, ด่านเรื่องซ้ำ, กรอกทุกส่วน, save พร้อมตรวจผล+
                       ซ่อมตัวเอง, คู่กรณี (เคลมสด), ความเสียหาย, อัปโหลดรูป HTML5,
                       Debit Note + ตารางราคา; report_status/is_report_submitted (gate สถานะ),
                       submit_report (กด "ส่งงานใหม่" wuFlow1_cmdSendNew + ปิด SweetAlert + verify)
  surv_xml.py          parse ไฟล์ SURV_REPORT XML → คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน/
                       เพศผู้ขับขี่/bill (fallback)
  isurvey_report.py    แจ้งสถานะ "ส่งงานแล้ว" กลับ ISURVEY (se.isurvey.mobi/srvEMCSrpt.php):
                       keyer_for (เลขท้ายเคลม→คนคีย์) + report_sent (POST, dry_run ได้);
                       gate ด้วย emcs.is_report_submitted (สถานะ EMCS ต้องไม่ใช่ draft)
  sekey_client.py      บันทึกงานที่เสร็จลงฐานข้อมูลกลาง se-key (key.sesurvey.cloud REST API):
                       check_survey (ตรวจ survey_no ซ้ำ) + save_record (POST /api/records
                       upsert + PATCH isurvey_sent=1); enabled เมื่อตั้ง SE_KEY_API_URL/
                       SE_KEY_API_KEY ใน .env (ไม่งั้น no-op) — hook ใน main._sekey_dup_skip + _offer_submit
  images.py            zip export (กดปุ่ม+ยืนยัน+รอไฟล์), แตก zip ตามหมวด,
                       ดาวน์โหลด XML, archive รูปเก่า, template matching wrapper
  processing.py        template matching หา 1.jpg (ของเดิม ย้ายเข้า package)
tools/                 สคริปต์ probe ที่ใช้สำรวจหน้าเว็บ (เก็บไว้ใช้ตอนเว็บเปลี่ยน)
runs/<เคลม>.json       ข้อมูลที่อ่านได้ (ใช้กับ --data-json)
runs/xml/              ไฟล์ SURV_REPORT XML ต่อเคลม
runs/logs/             log ทุกรอบ + screenshot/HTML อัตโนมัติเมื่อ error/จบงาน
downloaded_images/<เคลม>/   รูปต่อเคลม (tp_veh/ = รูปรถคู่กรณี ไม่อัปโหลด)
test_smoke.py          ~50 test ไม่ต้องเปิด browser — รันก่อน commit ทุกครั้ง
```

**หลักการออกแบบที่ใช้ทั้งระบบ** (ได้จากบทเรียนจริง):
1. **ทุก action ต้องตรวจผล** — คลิกแล้วเช็คว่าเกิดผลจริง ไม่เชื่อใจ ExtJS/ASP.NET
2. **เว้นจังหวะหลัง postback** — dropdown ASP.NET ยิงเร็วติดกันค่าจะหาย (presleep=1)
3. **ล้มแล้วเก็บหลักฐานเสมอ** — screenshot + HTML ลง runs/logs/ อัตโนมัติ
4. **อ่านข้อความ alert เสมอ** — มันคือคำตอบว่า validation ติดอะไร
5. **ข้อมูลไม่ครบ = หยุดรอคน ไม่ crash** — เรียก browser.wait_for_manual_fill
   หยุดรอให้คนกรอกเองแล้วไปต่อ (console=Enter, web=ปุ่มดำเนินการต่อผ่าน stdin,
   ไม่มีคนเฝ้า=EOF→ข้ามไม่ค้าง) มี 2 จุด:
   (ก) **ตอนกรอก dropdown** — fuzzy_select(required=True) ที่ว่าง/dropdown
       โหลดไม่ขึ้น (ไม่ throw TimeoutException แล้ว) field รอง (required=False)
       ที่ว่างยังข้ามเงียบเหมือนเดิม
   (ข) **ตอนบันทึกหน้าหลัก** — save_main_form ถ้า EMCS validation ฟ้อง field
       ที่ซ่อม dropdown อัตโนมัติไม่ได้ (เช่น text field 'สถานที่เกิดเหตุ')
       จะหยุดรอให้คนกรอกแล้วกดบันทึกใหม่ (cap 7 รอบกันลูป) — ครอบ text field
       ที่ fuzzy_select ไม่ครอบ เพราะ EMCS บอกชื่อช่องที่ขาดมาตรงๆ

---

## 3. ความรู้ระบบ ISURVEY (cloud.isurvey.mobi)

- **บัญชีเดียวเปิดได้หลาย session พร้อมกัน** (user ยืนยัน 2026-06-15) — webui
  จึงรันหลายงานพร้อมกันได้ alert "session lose!" ที่เคยเจอน่าจะเกิดจากบัญชีถูกใช้
  ที่อื่นพอดี ไม่ใช่ข้อจำกัดจริง (ยังเก็บ retry 1 รอบใน main.py ไว้กันเหนียว)
- ExtJS ทั้งระบบ → element id แบบ `xxx-inputEl`, เมนูใช้ id สุ่มกึ่งคงที่
  (`treepanel-1024`, `treeview-1027-record-7`)
- **Race ที่เจอและกันไว้แล้ว**: ค้นหาก่อนตารางโหลดเสร็จ→โดนทับ / ผลค้นหามาช้า→โพล+
  Enter ซ้ำ / ดับเบิลคลิกหลุดเงียบ→ตรวจ tab1 มีค่า+คลิกซ้ำ / เมนูคลิกไม่ติด→ตรวจ
  ช่องค้นหาโผล่+คลิกซ้ำ / thumbnail รูปโหลด async→รอ .center-cropped
- **Tab รายละเอียดเคลม**: tab1-3,7,8 ใช้ prefix `tabN_` / **tab 4-6 ใช้ prefix
  `othercar_` / `injury_` / `property_`** (lazy render เมื่อคลิกครั้งแรก, ค่าโหลด async,
  บางเคลมไม่แสดงแม้มีข้อมูล → จึงใช้ XML เป็นแหล่งหลักแทน)
- **ค่าสำรวจใน Tab 1 มี 2 ชุด**: `tab1_SUR_*` = เสนอ / **`tab1_INS_* = อนุมัติ ←
  ชุดนี้คือยอดที่เอาไปกรอก EMCS** (user ยืนยัน) มีครบ: INS_INVEST, INS_TRANS,
  INS_DIST, INS_PHOTO+PHOTO_NUM, INS_TEL, INS_INSURE, INS_CLAIM, INS_DAILY+DAILY_NUM,
  INS_OTHER, INS_CARTOW, INS_TOTAL/VAT/TOTAL_NET
- **ปุ่มท้ายหน้า Tab 1**: "ดาวน์โหลดรูปภาพ" (zip) และ "ดาวน์โหลด XML" — ต้องอยู่ Tab 1,
  หลังกดมี Confirm dialog ต้องตอบ Yes (`images._answer_confirm`)
- **zip export**: `export_<เคลม>_<ts>.zip` → `PICTURES/INS` (รถประกัน),
  `REPORTS` (เอกสาร DOC_*.jpg + PDF — เคลม outsource อาจไม่มี), `OTHERS`,
  `TP_VEH/<โฟลเดอร์คัน>/` (รูปรถคู่กรณี — แตกเข้า tp_veh/ ไม่อัปโหลด)
- **XML (SURV_REPORT_<caseid>.txt)**: INSERT_SURV_REPORT_XML — TXN_SURV_REPORT
  (ข้อมูลเคลม), TXN_SURV_CAR (TYPE 0=รถประกัน มี DRI_GENDER!, TYPE 1+=คู่กรณี
  ครบถึงประกัน/กรมธรรม์/เลขเคลมคู่กรณี), TXN_SURV_ASSET, TXN_SURV_BILL (ชุด SUR_ เสนอ)
  / DAMAGE_LIST ว่างเสมอ (รายการความเสียหายต้อง scrape จากจอ) / วันที่ปนทั้ง
  ค.ศ./พ.ศ. ISO → ใช้ `iso_to_thai_date` (เช็คปี<2400)

---

## 4. ความรู้ระบบ EMCS (eclaim3.blueventuregroup.co.th)

### โมเดลความปลอดภัย (สำคัญที่สุด)
- **"บันทึก" ทุกหน้า = draft แก้ไขได้** — สคริปต์กดให้หมด
- **จุด commit จริง = ปุ่ม "ส่งงานใหม่" (`wuFlow1_cmdSendNew`) หน้าค่าใช้จ่าย —
  ห้ามสคริปต์กดเด็ดขาด** (ตัวหาปุ่มกรองคำว่า 'ส่งงาน' ทิ้งตั้งแต่ JS)
- validation ไม่ผ่าน = ไม่เกิด draft (ดีต่อการ retry)

### ฟอร์มหลัก "ข้อมูลทั่วไป" (สร้างงานใหม่)
- ASP.NET WebForms ~1,700 fields, id คงที่ (txtXxx/ddlXxx/rdoXxx)
- **Field บังคับที่ไม่อยู่ใน ISURVEY จอหลัก** (เดิม notebook ค้าง "รออัพเดท"):
  - `rdoHev_Car_0/1` = รถเสียหาย หนัก/เบา → CLI `--severity` (default เบา)
  - `rdoGender_0/1` = เพศ (0=ชาย M, 1=หญิง W/F) → ดึงจาก ISURVEY/XML ก่อน;
    **ว่าง → `resolve_gender()` อนุมานจากคำนำหน้าในชื่อ** (`gender_from_title`:
    นาย/เด็กชาย/ด.ช.→M, นาง/นางสาว/เด็กหญิง/ด.ญ.→W — ทิศ title→เพศ ชัดเจน 100%);
    ไม่มีคำนำหน้าเลย → หยุดรอคนเลือก ใช้ fallback นี้ทั้งผู้ขับขี่ประกัน/คู่กรณี/ผู้บาดเจ็บ
  - `ddlDri_Title_ID` = คำนำหน้า → `_derive_insured_title` ใช้คำนำหน้าจริงจากชื่อ
    ผู้เอาประกันเฉพาะตอนตรงกับผู้ขับขี่; **ไม่ตรง → หยุดรอคนเลือก** (ทิศ เพศ→คำนำหน้า
    กำกวม M แยก นาย/เด็กชาย ไม่ได้ — ไม่เดา); เพศผู้ขับขี่ก็ยืมคำนำหน้าที่ match นี้มา fallback
- **validation แยกตามบริษัทประกัน** (`validForm()` switch ตาม value ของ
  ddlInsurerNameMajor — ของเรา = ไอโออิกรุงเทพ id **1059**): เลขเคลม 13 หลักผ่าน,
  `validFormat` ข้ามค่าว่าง, เลขที่รับแจ้ง (txtAcc_ClaimRef_No) ไม่บังคับถ้าเว้นว่าง
- **Postback race**: เลือก dropdown ติดกันเร็วเกิน ค่าตัวแรกหายเงียบ →
  ทุก fuzzy_select ใน fill_accident/fill_driver ใช้ presleep=1 +
  `save_main_form` มีระบบซ่อมตัวเอง: อ่าน alert → กรอกซ้ำ field ที่ฟ้อง → ลองใหม่ 3 รอบ
- **ตรวจบันทึกสำเร็จ**: alert "บันทึก...หมายเลข e-Survey คือ Sxxx" + ปุ่ม
  `btnPopUp_DamList` เปลี่ยนเป็น enabled (ก่อนบันทึก = disabled)

### ส่วนรถคู่กรณี (เคลมสด)
- **ปลดล็อกหลังบันทึกหน้าหลักเท่านั้น** (server ส่ง disabled มา client toggle ไม่ได้)
- `ddlOpo_Count` เลือกจำนวนคัน → JS `showOtherVehicle()` เปิดบล็อกทันที (ไม่ postback)
- บล็อก 20 คัน: `dtlOpo_ctl{00-19}_wuOpo_<field>` — map ครบใน `THIRD_PARTY_FIELDS`
- บันทึกด้วยปุ่มแยก `btnSave_Opponent` + alert
- **⚠️ ป้ายทะเบียนห้ามมีช่องว่าง** (verify 2026-06-18): ISURVEY ให้ '9กฆ 5003' แต่
  EMCS **server reject เงียบๆ** ("ไม่สามารถบันทึกรถคู่กรณีได้ กรุณาตรวจสอบข้อมูล" —
  client validForm ผ่าน แต่ server ไม่รับ) → ต้องลบช่องว่างเป็น '9กฆ5003' (`_plate()`
  ใช้ทุกทะเบียน: คู่กรณี/ผู้บาดเจ็บ/รถประกัน) — debug ยากเพราะ error เป็น generic
- **คู่กรณีไม่มีประกัน** (มอไซค์ ฯลฯ): ISURVEY ไม่มี insurer/policy/claim → เลือก
  `ddlHave_Insurance` = **'ไม่มีบริษัทประกันภัย'** + กรอก `txtPolicyNo`/`txtPolicy_Type`/
  `txtClaimNo` = **'-'** (ไอโออิ validForm บังคับ 3 ช่องนี้เสมอ ไม่ข้ามแม้ไม่มีประกัน —
  case no-insurance gate เป็นของบริษัทอื่น). gate ใน `fill_third_parties`: ถ้า
  insurer+policy+claim+insure_type ว่างหมด → no-insurance branch
- **ลำดับกรอก (user ยืนยัน):** ข้อมูลทั่วไป (+คู่กรณี+ความเสียหายรถประกัน) → ผู้บาดเจ็บ
  → ทรัพย์สิน → รูปประกอบ → ค่าใช้จ่าย — `fill_one` เรียง fill_third_parties +
  fill_damage_list (แท็บหลัก) ก่อน fill_injuries/fill_assets (กดเมนูไปแท็บอื่น)
- **แก้ draft เดิม (สถานะ 'รายงานสร้างใหม่' ยังไม่ส่ง):** กด 'แก้ไข' `btnUpdate` ที่หน้า
  ข้อมูลทั่วไป (`wuMenuPage1_imbGeneral_Survey`) เข้าโหมดแก้ได้ — ไม่ต้อง 'สร้างใหม่' ซ้ำ
- **สูตรจังหวัด/อำเภอ**: รหัส ISURVEY = ลำดับ option ใน dropdown EMCS
  (เรียง ก-ฮ เหมือนกัน: 2=กรุงเทพฯ, 28=ปทุมธานี) / รหัสอำเภอ =
  `<รหัสจังหวัด><ลำดับ 2 หลัก>` เช่น 236=กทม เขต 36 ดอนเมือง, 1203=ชุมพร อ.3 ปะทิว
  (ยืนยัน 3 เคสจริง) → `_select_index`

### ส่วนผู้บาดเจ็บ (Tab 5) + ทรัพย์สิน (Tab 6) — เคลมสด ✅ verify 2026-06-18
**โครงสร้างเหมือนคู่กรณีเป๊ะ** (ปลดล็อกหลังบันทึกหน้าหลัก) — `fill_injuries`/`fill_assets`
ลอกแพทเทิร์น `fill_third_parties`: กดเมนู → เลือกจำนวน → กรอกทีละบล็อก → บันทึก +
`_save_section` (generic แทน `_save_opponents` เดิม — ตรวจ alert 'กรุณา' จริง/หยุดรอ)
- **ผู้บาดเจ็บ:** เมนู `wuMenuPage1_imbInjure_Person` → `ddlInj_Count` (1-5) →
  บล็อก `dtlInj_ctl{00..}_wuInj_<field>` → `btnSave_InjurePerson`
  - **ชื่อใช้ช่อง `txtInj_Name` เดี่ยว** (txtInj_Name01/LastName01 = layout สำรองที่ซ่อน
    เหมือน txtDri_Name คู่กรณี) → `_is_displayed` เลือกช่องที่โชว์อัตโนมัติ
  - `ddlPerson_Type` **เป็น dynamic** — ตัวเลือกเปลี่ยนตามว่าเคลมมีคู่กรณีไหม:
    ไม่มีคู่กรณี = 3 ตัว (**01**=ผู้ขับขี่-รถประกัน / **03**=ผู้โดยสาร-รถประกัน /
    **05**=บุคคลภายนอกรถ); **มีคู่กรณีจะเพิ่ม 02**=ผู้ขับขี่-รถคู่กรณี + **04**=ผู้โดยสาร-รถคู่กรณี
    - XML มีแต่ `PERSON_TYPE` หยาบ (DV/PV/ON, ไม่บอกว่ารถประกันหรือคู่กรณี) → `PERSON_TYPE_MAP`
      (DV→01/PV→03/ON→05) เป็น **fallback** เท่านั้น
    - **smart default:** ถ้าชื่อผู้บาดเจ็บ fuzzy-match ชื่อผู้ขับขี่คู่กรณี (`tp.drv_name`,
      WRatio ≥85) → default **02** (ผู้ขับขี่-รถคู่กรณี) — แก้บั๊กภานุพงศ์ที่เคยได้ 05 ผิด
    - `fill_injuries` **กดเมนู+เลือกจำนวนก่อน** เพื่อให้บล็อก render → อ่านตัวเลือกจริง
      (`_read_person_type_options` ผ่าน JS จาก `dtlInj_ctl00_wuInj_ddlPerson_Type`) แล้ว
      ส่ง options dynamic นั้นไป webui (ผู้ใช้เห็น 02/04 ครบ ไม่ใช่ fallback) — แล้วค่อย
      `wait_for_injury_inputs(spec, options=...)`
  - `ddlWounded_Type` value = **code XML ตรงๆ** (01=เล็กน้อย 02=ปานกลาง 03=สาหัส
    04=ทุพพลภาพ 05=เสียชีวิตก่อนรักษา 06=หลังรักษา) → `select_by_value(wounded_type)`
  - ฟิลด์: `txtInj_Age/txtCitizen_ID/txtInj_Job/txtCar_RegNo/txtInj_Address/txtInj_Tel_No/
    txtInj_Hos_Name/txtInj_Cost` + `txtInj_Injure`(textarea) + `rdoGender_0/1`
  - **⚠️⚠️ `txtCar_RegNo` (เลขทะเบียน) EMCS เติมให้อัตโนมัติจาก `ddlPerson_Type`**
    (verify หน้าจริง 2026-06-19): เลือก 01/03 (รถประกัน) → เติมทะเบียนรถประกัน,
    02/04 (รถคู่กรณี) → เติมทะเบียนคู่กรณี, 05 (บุคคลภายนอกรถ) → ไม่ auto-fill (ไม่มีรถผูก)
    → **ใส่คำว่า `'บุคคลภายนอก'` ลงช่องทะเบียนแทน** (ให้ผ่าน gate)
    - **นี่คือ root cause จริงของ billing gate**: เลขทะเบียนผู้บาดเจ็บบังคับก่อนเข้าหน้า
      ค่าใช้จ่าย (alert "ไม่สามารถไปหน้า [ค่าใช้จ่าย] ได้ ... เลขทะเบียน คนที่ N") — เดิม
      (1) person_type map ผิด (ON→05) → ไม่ auto-fill, (2) ต่อให้แก้ person_type ถูก
      โค้ดก็ยัง `set_text(txtCar_RegNo, '')` **เขียนทับค่า auto-fill ด้วยค่าว่าง** → gate เด้ง
    - **แก้:** หลัง `select_by_value(pt)` ยิง `dispatchEvent(change)` + `sleep(0.6)` ให้ JS
      เติมทะเบียน → **อ่าน readback `get_attribute('value')`: ถ้ามีค่าแล้วห้ามเขียนทับ**;
      ลำดับ: ผู้ใช้กรอก/override (ค่าต่างจาก auto เช่นนั่งรถคันที่ 3) > auto-fill > ถ้าว่าง
      และ pt=='05' ใส่ `'บุคคลภายนอก'` > ไม่งั้นเตือน
    - webui (ดู §5): `wait_for_injury_inputs` **หลังเลือกจำนวน** (อ่าน options dynamic) —
      ฟอร์มต่อคน (dropdown ประเภท smart default + ช่องเลขทะเบียน **ไม่บังคับ** เพราะ
      auto-fill); console/EOF = ใช้ smart default (ส่วนใหญ่ auto-fill ครบ → ไม่ติด gate)
- **ทรัพย์สิน:** เมนู `wuMenuPage1_imbAsset` → `ddlAsset_Count` (1-5) →
  บล็อก `dtlAsset_ctl{00..}_wuAsset_<field>` → `btnSave_Asset`
  - `txtAsset_Desc`(ชื่อ) / `txtAsset_Damage`+`txtAsset_Damage_Cause`(textarea) /
    `txtCost_Damage` / `txtOwner`(เจ้าของ) / `txtAddress` / `txtTel_No`
- **เรียกใน `fill_one` หลัง `fill_third_parties`** (gate ด้วย `if not data.injuries/assets`)
- verify เคลม 2026013048453 → S68426064959: ผู้บาดเจ็บ 2 + ทรัพย์สิน 1 บันทึกผ่านรอบแรก
  ทุกฟิลด์ (probe `tools/probe_inj_asset.py` + harness `tools/test_inj_asset.py`)
- **เหลือ:** อัปรูป tp_person/tp_prop เป็นประเภท 'รูปผู้บาดเจ็บ'/'รูปทรัพย์สิน' (ต้อง probe
  ชื่อ option ใน ddlImage_Type_Html5) + คำนำหน้าผู้บาดเจ็บ (ddlInj_Title_ID) ยังไม่แยกกรอก
  (ชื่อเต็มรวมคำนำหน้าใน txtInj_Name อยู่แล้ว ผ่าน validation)

### Popup ความเสียหาย
- เปิดหลังบันทึก (btnPopUp_DamList) เป็น window ใหม่
- รับ **8 รายการ** (คอลัมน์ A/B × แถว ctl02-05): `dgvOtherDamage_List_ctl0{2-5}_wuOtherDamL{A|B}_*`
- ซ้าย/ขวา/ทั้งคู่ ดูจากคำในชื่อชิ้นส่วน, ระดับ A-D → radio Lavel_0-3
- เกิน 8 → log เตือนให้เติมเอง (ยังไม่รู้ว่า popup เพิ่มแถวได้ไหม — ดู §6.3)

### หน้าอัปโหลดรูป (HTML5 UI)
- ลำดับสำคัญ: เลือก `ddlImage_Type_Html5` ก่อน → input `#selectedFile` (ซ่อน+disabled)
  จะ enable → send_keys ทุก path คั่น `\n` รวดเดียว (multiple) → `btnUpload` →
  ปิดกล่องผล (.close) → **sleep 2 กัน stale** (หน้า refresh)
- UI เก่า (ทีละไฟล์ + ddlImageType{n} ต่อแถว) ยังมี fallback ในโค้ด
- **รูปหลายประเภทในรอบเดียว:** `upload_images` รวมทุกชุด (รูปหลัก + บุคคลที่สามแต่ละราย)
  → **นำทาง `wuMenuPage1_imbImage` ครั้งเดียว** แล้วอัปทุกชุดบนหน้าเดิม (`_upload_one_batch`,
  ไม่กดเมนูซ้ำ) — **บั๊กเดิม: หลังอัปชุดแรก เมนู imbImage = `disabled` (อยู่หน้านี้แล้ว)
  กดซ้ำ = TimeoutException** แต่ฟอร์มอัปโหลด (`ddlImage_Type_Html5`) ยังอยู่ → อัปต่อได้เลย
  (verify 4 ชุดต่อกันบนหน้าเดียวผ่าน)
- **รูปรถคู่กรณี (เคลมสด):** รูปใน `tp_veh/` (โหลดจาก Tab 4) → เลือกประเภท
  `'รูปรถคู่กรณี คันที่N'` (วิธีเลือก dropdown + อัปเหมือนรูปรถประกันทุกอย่าง)
  — **option จริงในระบบ = `'รูปรถคู่กรณี คันที่ 1'` (มีเว้นวรรคก่อนเลข)** ✅ verify
  หน้าจริง 2026-06-18 (เคลม 2026013047934 → S68426064657): ส่ง `'คันที่1'` fuzzy
  จับถูก score 98
- **เปลี่ยนชื่อรูปคู่กรณีก่อนอัป** (`_rename_opponent_files`, เรียกใน
  `_opponent_image_batches` ก่อนสร้างชุด): rename บนดิสก์เป็น
  **`รูปรถคู่กรณีคันที่<คัน>_<ลำดับ>.jpg`** (แพทเทิร์นเดียวกับรูปรถประกัน
  `รูปรถประกันN.jpg` — คอลัมน์รายการใน EMCS จะสะอาด ไม่ใช่ชื่อดิบ
  `1781..._rn_image_picker...`). two-phase กันชนชื่อ + idempotent (รันซ้ำชื่อเดิม)
  - dedup ย้ายรูปซ้ำ (ไฟล์ `_2`) เข้า `tp_veh/_dup/` (ไม่ลบ; list_images ไม่นับ subfolder)
  - ✅ verify หน้าจริง 2026-06-18: 30 ไฟล์ → 15 สะอาด `คันที่1_1..15.jpg` อัปขึ้น
    S68426064657 ครบ ไม่มีชื่อดิบตกค้าง (user ลบของเก่าก่อน re-upload)
  - `_opponent_image_batches(folder, n_opponents)`: **dedup ตามเนื้อหา** (กันไฟล์
    `_2/_3` จากโหลดทับ) → คู่กรณี 1 คัน = รูปทั้งหมด 'คันที่1'; หลายคันแยกตามชื่อ
    โฟลเดอร์คัน (prefix ก่อน `_`) ถ้าได้กลุ่ม=จำนวนคันพอดี ไม่งั้นรวมเป็นคันที่1+เตือน
  - `fill_one` ส่ง `n_opponents=len(data.third_parties)` ให้ `upload_images`
  - `archive_old_images` ย้าย `tp_*/` (tp_veh/tp_person/tp_prop) เข้า `_old/` ด้วย
- **รูปผู้บาดเจ็บ (tp_person/) + ทรัพย์สิน (tp_prop/)** ✅ verify 2026-06-18:
  generalize `_opponent_image_batches`→`_tp_image_batches(folder, subdir, count, type_tmpl,
  name_tmpl)` (+ `_rename_clean_files`) ใช้ร่วม 3 หมวด:
  - **`ddlImage_Type_Html5` เป็น dynamic** — เพิ่ม option ต่อราย **หลังบันทึก section นั้น**:
    `'รูปรถคู่กรณี คันที่ N'` (หลังบันทึกคู่กรณี), **`'รูปผู้บาดเจ็บ คนที่ N'`** (value 1400N,
    หลังบันทึกผู้บาดเจ็บ), **`'รูปทรัพย์สิน รายการที่ N'`** (value 1500N, หลังบันทึกทรัพย์สิน)
    — `upload_images` รันหลัง `fill_injuries/fill_assets` พอดี option จึงมีครบ (fuzzy score 98)
    — ถ้า section ยังไม่บันทึก จะเหลือ option generic (คู่กรณี = 'รูปรถคู่กรณี' score 90, fallback ได้)
  - tp_person/tp_prop แยกตามรายด้วย prefix โฟลเดอร์ย่อย (id ต่อคน/ชิ้น) = จำนวนผู้บาดเจ็บ/ทรัพย์สิน
  - ชื่อไฟล์สะอาด `รูปผู้บาดเจ็บคนที่N_ลำดับ.jpg` / `รูปทรัพย์สินรายการที่N_ลำดับ.jpg`
  - `fill_one`/`add_images_only` ส่ง `n_injuries`/`n_assets` ให้ `upload_images`
  - verify เคลม 2026013048453 → S68426064959: ผู้บาดเจ็บคนที่1(6)/คนที่2(1)/ทรัพย์สินที่1(5)
    อัปขึ้นตารางครบ ชื่อสะอาด (4 ชุดต่อกันบนหน้าเดียว ไม่ crash)

### หน้าค่าใช้จ่าย (Debit Note)
- เมนู `wuMenuPage1_imbSpend` (ใช้ click_retry — หน้าเพิ่ง refresh ชอบ stale)
- กรอก: `txtBill_No` (เลขเซอร์เวย์), `wuCale_Bill_Date_txtCalendar` (วันนี้ พ.ศ.),
  `txtAcc_result` (สรุปความเห็นหัวหน้า)
- **ตารางราคา (ช่องเสนอเท่านั้น — user กำหนด)**: ค่าบริการ `txtNum_Investigate` ×
  `txtInvestigate_UnitPrice` / ค่าเดินทาง `txtNum_Transport` × `txtTransport_UnitPrice` /
  ค่ารูป `txtNum_Photo` × `txtPhoto_UnitPrice` (ISURVEY ให้ยอดรวม → หารจำนวน) /
  `txtSur_Tel`, `txtSur_Insure`, `txtSur_Claim`+`txtSur_Percent_Claim`, `txtSur_Daily` /
  อื่นๆ `txtOther_Desc`+`txtOther_UnitPrice` — พิมพ์แล้วกด **Tab** ให้ JS คำนวณ
- ช่องอนุมัติ `txtIns_*` = disabled (ของบริษัทประกัน) แตะไม่ได้โดยโครงสร้าง
- ปุ่มบันทึก = `btnSurveySave` "บันทึกราคา" (enable เมื่อมีราคา) → กด + alert
- ค่าระยะทาง (INS_DIST) / ค่ายกลาก (INS_CARTOW) ยังไม่มีช่อง map → log เตือนถ้ามียอด

### หน้ารายการงาน (frmMainPage) + ด่านกันเรื่องซ้ำ
- ค้นหา: `txtRef_Claim_No` + `btnSearch` → แถวผลมีลิงก์ e-Survey (`S` + ตัวเลข)
- `guard_duplicate_report`: ค้นก่อนสร้างงานเสมอ → เจอ = หยุดพร้อมรายการ
  (ข้าม: `--force-new`) — ทดสอบกับเคลมที่มี 5 เรื่องจริงแล้ว
- ปุ่มอื่นบนหน้านี้: `cmdNewReport`, `imbFileImport_XML` (import XML — น่าสนใจ อนาคต
  อาจ import SURV_REPORT ตรงๆ แทนกรอกฟอร์ม! ยังไม่เคยลอง), inbox ตามสถานะ `dgvInbox_*`

---

## 5. ประวัติบั๊กที่แก้แล้ว (ย่อ — เผื่อเจออาการคล้ายกัน)

| อาการ | สาเหตุ | ทางแก้ (อยู่ในโค้ดแล้ว) |
|---|---|---|
| ค้นเคลมแล้ว "ไม่พบ" ทั้งที่มี | อ่านตารางก่อนเว็บ refresh | รอตารางแรกโหลด + โพล + Enter ซ้ำ |
| เปิดเคลมไม่ได้ log บอกเปิดแล้ว | ดับเบิลคลิกหลุดเงียบ | ตรวจ tab1 มีค่า + คลิกซ้ำ 4 รอบ |
| เมนูคลิกแล้วหน้าไม่เปลี่ยน | คลิกตอนแอปยังโหลด | ตรวจช่องค้นหาโผล่ + คลิกซ้ำ |
| โหลดรูป panel ได้ 0 | thumbnail โหลด async | รอ .center-cropped ก่อนอ่าน |
| ปุ่ม zip/XML หาไม่เจอ | อยู่ท้าย Tab 1 + สะกดต่างได้ | go_to_tab(1) + หาหลายตัวสะกด |
| กดปุ่มโหลดแล้วค้าง 5 นาที | Confirm dialog รอ Yes | _answer_confirm + ปิด dialog ค้าง |
| session lose! กลางคัน | บัญชีถูกใช้ที่อื่นพอดี (ไม่ใช่ลิมิต session) | จับ alert → login ใหม่ → ลองซ้ำ |
| บันทึก EMCS เงียบ ไม่ผ่านจริง | alert คือ validation ไม่ใช่ยืนยัน | อ่าน alert + ตรวจ btnPopUp enabled |
| ค่า dropdown หายสุ่มๆ หลังเลือก | postback race ทับกัน | presleep=1 + ซ่อมตัวเองจาก alert |
| งาน outsource "พนักงานสำรวจ" ว่าง (ฝั่ง API) | `isurvey_api` อ่าน `OSS_SurveyorName` มาเก็บ `oss_surveyor` แต่ไม่ fallback เข้า `surveyor_name` เหมือน scrape (useOSS=Y → ช่อง surveyor_name ว่าง) | ใส่ fallback ใน `read_claim`: `surveyor_name` ว่าง + มี `oss_surveyor` → ใช้ `oss_surveyor` (verify 2026013042095 → 'นายเกษม นามวิชา') — ตรงกับ scrape, `--compare` ตรงด้วย |
| อัปโหลดรูป timeout | UI ใหม่ input ซ่อน+disabled | เลือกประเภทก่อน → ส่งทุกไฟล์รวดเดียว |
| stale ตอนเปลี่ยนหน้า | หน้า refresh หลังปิดกล่อง | click_retry + sleep 2 |
| ยอดเงินผิด (300 แทน 700) | ใช้ XML SUR_ (เสนอ) | เปลี่ยนเป็นจอ ISURVEY ชุด INS_ (อนุมัติ) |
| เปิดเรื่องซ้ำทุกครั้งที่รัน | ไม่เช็คก่อนสร้าง | guard_duplicate_report |

---

## 6. Backlog งานพัฒนาต่อ (เรียงตามความสำคัญ)

### 6.1 กลับมาเปิดเคลมสด (พักไว้ — user ตัดสินใจ 11 มิ.ย. 2026)
ติด 2 จุดจากเทสจริง (เคลมที่ทดสอบ validation ฟ้อง "บัตรประชาชนเลขที่ +
ลักษณะความเสียหาย"):
1. **ข้อมูลต้นทางไม่ครบ** (เลขบัตร ปชช ว่างใน ISURVEY) → ทำ pre-check:
   เพิ่มใน `validate()` เช็ค field ที่ EMCS เคลมสดบังคับ แล้วเตือน**ก่อน**เริ่มกรอก
   จะได้กลับไปเติมใน ISURVEY ก่อน
2. ~~**ลักษณะความเสียหาย (ddlLoss_ID) บังคับสำหรับเคลมสด**~~ ✅ แก้แล้ว 2026-06-18:
   **ISURVEY ไม่มีข้อมูลลักษณะความเสียหาย** (มีแต่ `acc_type_desc`='ลักษณะการเกิดเหตุ'
   + `acc_result`='ผลคดี') → `resolve_loss_type` เคลมสด คืน `''` เสมอ (เลิกเดาจากผลคดี),
   `fill_accident` ใช้ `fuzzy_select(ddlLoss_ID, required=True)` → เคลมสดหยุดรอผู้ใช้
   เลือกเองบนหน้า EMCS (รูปแบบเดียวกับ ยี่ห้อ/มีประกันภัยที่); เคลมแห้ง=‘เคลมแห้ง’ อัตโนมัติ
- โค้ดคู่กรณี (fill_third_parties) เขียนเสร็จแล้ว ยังไม่เคยรันผ่านจุดบันทึกจริง
- เปิดใช้: `--allow-fresh`

**อัปเดต 2026-06-17 (probe เคลมสด 2026013144960 — มีครบคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน; ส่ง EMCS แล้ว จึง probe เฉพาะฝั่งอ่าน):**
- **แก้บั๊ก `surv_xml`: tag ผู้บาดเจ็บจริง = `TXN_SURV_INJ`** (เดิมหา `TXN_SURV_INJURY` → ไม่เคย parse). ฟิลด์: `NAME/AGE/CITIZEN_ID/JOB/CAR_REGNO/ADDRESS/TEL_NO/HOS_NAME/COST/INJURE/GENDER/PERSON_TYPE`(DV=ผู้ขับขี่รถประกัน, ON=คู่กรณี/อื่น)`/WOUNDED_TYPE` → map แล้ว (verify 2 คนจริง + smoke inline XML)
- **ฝั่งอ่าน ISURVEY ครบทั้ง 3 แล้ว:** คู่กรณี (`TXN_SURV_CAR` TYPE≠0), ผู้บาดเจ็บ (`TXN_SURV_INJ`), ทรัพย์สิน (`TXN_SURV_ASSET`: ASSET_DESC/ASSET_DAMAGE/ASSET_DAMAGE_CAUSE/COST_DAMAGE/OWNER/ADDRESS/TEL_NO) — **API `getcaseinfo` tab-4/5/6 ใช้ไม่ได้** (`not found ikey`) ต้องใช้ XML
- ~~**ที่เหลือ = ฝั่งกรอก EMCS:** probe Injury/Asset + เขียน fill_injuries/fill_assets~~
  ✅ **เสร็จ+verify 2026-06-18** (เคลม 2026013048453 → S68426064959, ดู §4): probe ด้วย
  `tools/probe_inj_asset.py`, เขียน `fill_injuries`/`fill_assets` ลอก `fill_third_parties`,
  ทดสอบด้วย `tools/test_inj_asset.py` บน draft เดิม — บันทึกผ่านรอบแรกทุกฟิลด์

**อัปเดต 2026-06-18 (เทสต์กรอกเคลมสดจริงครั้งแรก — เคลม 2026013047934 คู่กรณี1/เจ็บ0/ทรัพย์0 → draft S68426064657):**
- ✅ หน้าหลัก + ความเสียหาย + รูป + ค่าใช้จ่าย (บันทึกราคา) กรอก+save ผ่านหมด (เคลมสด type 1 ผ่าน `--allow-fresh`)
- 🔧 **แก้ `fill_third_parties` (probe บล็อกคู่กรณี prefix `dtlOpo_ctl00_wuOpo_`):**
  1. ชื่อผู้ขับขี่คู่กรณี = ช่อง **`txtDri_Name`** (เดี่ยว มองเห็น) ไม่ใช่ `txtDri_Name01` (ซ่อน — เดิมเซ็ตผิดช่อง validation ฟ้อง 'ชื่อผู้ขับขี่')
  2. เพิ่ม `_save_opponents`: ตรวจ validation จริง — alert มี 'กรุณา' = ไม่ผ่าน → หยุดรอคนเติมช่องที่ฟ้อง + retry (เดิมกิน alert แล้ว**แจ้งสำเร็จลวง**)
- **คู่กรณีฟอร์มมีช่อง `*` เยอะที่ ISURVEY มักไม่มี** → หลังแก้ชื่อ เหลือ validation ฟ้อง 2 ช่อง: **มีประกันภัยที่ (`ddlHave_Insurance`) + ประเภทรถ (`ddlCType`)** — ISURVEY ไม่มีข้อมูล → flow ใหม่หยุดรอคนเลือกบนหน้าจอ (webui interactive) แล้ว retry → save ผ่าน (verify ชื่อหลุดจาก error แล้ว)
- ✅ **แก้ timing/ฟิลด์ซ่อนคู่กรณีแล้ว (verify draft เดิม):**
  - `ddlCmfg`(ยี่ห้อ) **ผูกกับ** `ddlCType`(ประเภทรถ) — ตัวเลือกยี่ห้อว่างจนกว่าจะเลือกประเภทรถ → เช็ค `_select_has_options` ก่อน ถ้าว่าง=ข้าม (เดิม timeout 10 วิ)
  - จังหวัด/อำเภอผู้ขับขี่คู่กรณี (`ddlDri_ProvinceID/DistrictID`) **ซ่อนใน layout นี้** (ฟอร์มใช้ "ที่อยู่ปัจจุบัน" เดี่ยว) → เช็ค `_is_displayed` ก่อน ถ้าซ่อน=ข้าม (เดิม ElementNotInteractable + timeout)
  - ผล: fill คู่กรณีเหลือ ~2 วิ (เดิม ~22), ไม่มี timeout/pause ลวง → เหลือฟ้อง **2 ช่องจริง: ประเภทรถ + มีประกันภัยที่** (ISURVEY ไม่มี รหัสรถเป็น code 'A' เฉยๆ) → `_save_opponents` หยุดรอผู้ใช้เลือก แล้ว retry
**อัปเดต 2026-06-18 รอบ 2 — อ่านคู่กรณีจากหน้าจอ Tab 4 (ของยากผ่าน):** XML ให้คู่กรณีแค่ basics +
รหัส (ประเภทรถ='A', ประกัน/ความเสียหาย ว่าง) — **ข้อมูลจริงอยู่บนหน้าจอ Tab 4 ต้องเลือกจาก dropdown
ก่อน** (Tab 5/6 ผู้บาดเจ็บ/ทรัพย์สิน ก็เหมือนกัน — user ยืนยัน)
- กลไก (isurvey.py): combo = ExtJS auto-id `combo-NNNN` (displayField=plate_no, valueField=ikey),
  store โหลด lazy → `expand()` ก่อน → `setValue(ikey)`+fire `select` → ฟอร์ม+grid โหลด
- ฟังก์ชัน: `_find_record_combo`/`_combo_records`/`_combo_select`/`_read_opo_damage_grid` +
  `enrich_third_parties_from_tab4` (เรียกใน main.py หลัง XML enrich, --scrape) → เติม
  `veh_type`(ประเภทรถอ่านได้)/`insure_type`(ประกันประเภท)/`policy_no`(กรมธรรม์)/`damages`(grid รายชิ้น)
- fill (emcs.py `fill_third_parties`): `ddlCType`(จาก veh_type, +`time.sleep(2)` รอ postback ยี่ห้อ) +
  `txtPolicy_Type`(insure_type) → **คู่กรณีบันทึกผ่าน validation อัตโนมัติ** (verify เคลม 2026013047934)
- **แก้บั๊ก `set_text`: เคลียร์ก่อนกรอกเสมอ** (เดิม send_keys ต่อท้าย → ค่าซ้ำ 'DW...DW...' บน re-fill/postback) — ดีกับทุก fill
- ✅ **ความเสียหายคู่กรณี — เขียน+ทดสอบแล้ว (verify):** popup `frmDamage.aspx` มี **ทั้ง** checkbox
  ชิ้นส่วนสำเร็จรูป **และช่อง free-text `dgvOtherDamage_List_ctl0{2-5}_wuOtherDamL{A|B}_txtDam_Name`**
  (โครงสร้างเดียวกับความเสียหายรถประกัน!) → `fill_opponent_damage` reuse แพทเทิร์น `fill_damage_list`:
  พิมพ์ชื่อชิ้นส่วน + ด้าน(ซ้าย/ขวาจากชื่อ) + ระดับ(A/B/C/D→index) → `btnSave` (ทำหลังบันทึกคู่กรณีสำเร็จ)
  — verify เคลม 2026013047934 (กันชนหลังซ้าย/บังโคลนหลังซ้าย ด้านซ้าย ระดับ B บันทึกผ่าน)
- ⚠️ เหลือ (ไม่บังคับ/ผู้ใช้เลือกเอง): (1) **ยี่ห้อ** ไม่ auto (ตั้งใจข้าม — ถ้าเลือกยี่ห้อ MG จะ override
  ประเภทรถเป็นเก๋งยุโรป; ปล่อยว่างเพื่อให้ประเภทรถ=เก๋งเอเชีย ตรง ISURVEY แล้วคนเลือกยี่ห้อเอง)
  (2) มีประกันภัยที่ = รหัสบริษัท (เช่น '135') ต้อง map→ชื่อ หรือเลือกเอง

### 6.2 งานต่อเนื่อง (ครั้งที่ 2,3,…) — ✅ เขียน + ทดสอบจริงแล้ว 2026-06-18
**โมเดล: 1 เลขเคลม → มีได้หลาย invoice (เลขเซอร์เวย์)** งานครั้งเดียวไม่จบ ทำต่อครั้งที่ 1,2,3,…
อ้างเลขเคลมเดิม **เปลี่ยน invoice** เพื่อเบิกเงิน → ฝั่งอ่านใช้ claim+invoice (find_case รองรับ)

**ทำงานอัตโนมัติใน `fill_one`** (ไม่ต้องมี flag): เคลมมีเรื่องเดิมใน EMCS + invoice ใหม่
(ยังไม่อยู่ในแถวเรื่องเดิม) → `continuation_esurvey()` คืนเลข e-Survey เดิม → เข้าโหมดงานต่อเนื่อง
แทนสร้างใหม่ (guard_duplicate_report เหลือ raise เฉพาะ invoice ซ้ำจริง — รับ `existing=` กันค้นซ้ำ)

**flow จริงใน EMCS (probe 2026-06-18 — ต่างจากที่คาดไว้):**
1. คลิกลิงก์เลข e-Survey ในผลค้นหา → `frmSurvey.aspx` (หน้าเดิม ไม่เปิด tab ใหม่)
2. เข้าหน้าค่าใช้จ่าย `wuMenuPage1_imbSpend` → `frmBilling.aspx`
3. กด **"งานต่อเนื่อง" `wuFlow1_cmdFollow`** → **JS confirm** "คุณยืนยันที่จะเพิ่มงานต่อเนื่องดังกล่าวหรือไม่!!!" → accept_alert
   → ⚠️ **EMCS สร้างครั้งใหม่แล้ว "เด้งกลับหน้ารายการ" (`frmMainpage.aspx`)** — ไม่อัปเดตหน้าเดิม!
4. **เปิดเรื่องซ้ำ** (ค้นเคลม + คลิกลิงก์ + เข้าหน้าค่าใช้จ่าย) → ครั้งใหม่ถูกเลือกอัตโนมัติ (`ddlAdd_No`
   selected=ครั้งล่าสุด) + ช่องปลดล็อก (`txtBill_No` enabled, ว่าง) + ปุ่ม `btnSurveySave`("บันทึกราคา")
   & `wuFlow1_cmdSendFollow`("ส่งผลงานต่อเนื่อง") โผล่ / `cmdFollow` หาย
5. กรอก **หน้าค่าใช้จ่ายเท่านั้น** (reuse `fill_billing(navigate=False)`): invoice ใหม่ + วันที่วางบิล + ตารางราคา
   — **clear `txtBill_No`/วันที่ก่อนกรอก** (set_text ต่อท้ายไม่ทับ) — ไม่แตะข้อมูลทั่วไป/คู่กรณี/ความเสียหาย (อยู่ครั้งที่ 1)
6. ส่ง: `wuFlow1_cmdSendFollow` — **ยังไม่เขียนใน submit_report** (ตอนนี้กดแค่ cmdSendNew)

**ตัวชี้วัดกันสร้างครั้งเกินตอน re-run: `txtBill_No.is_enabled()`** = อยู่ครั้ง draft แล้ว → กรอกเลย
(ไม่กด cmdFollow ซ้ำ) / disabled = ครั้งล่าสุดส่งแล้ว → กด cmdFollow สร้างครั้งใหม่

**ฟังก์ชัน:** `continuation_esurvey()` · `_open_report_billing()` · `start_continuation()` ·
`fill_continuation()` (เรียกจาก `fill_one`) · `fill_billing(save_price=, navigate=)` + readback log ·
flag `--no-save-price` (กรอกครบแต่ไม่กดบันทึกราคา — หยุดให้คนตรวจ)

**ทดสอบจริง 2026-06-18:** เคลม 2026013041465 (เคลมสด มีคู่กรณี2/เจ็บ2/ทรัพย์1 แต่ต่อเนื่อง=หน้าค่าใช้จ่ายล้วน)
— เรื่องเดิม S68426056403/SEABI-172260500053 (ครั้งที่ 1) → ทำครั้งที่ 2 invoice SEABI-372260600032
(ค่าบริการ 700/คัดประจำวัน 50, วันที่ 18/06/2569) สำเร็จ หยุดก่อนบันทึกราคา ✓ (เลขเดิม 2025013136813 = อีกตัวอย่าง)

**ส่งงานต่อเนื่อง — ✅ เขียนแล้ว 2026-06-18:** `submit_report` กดปุ่มส่งตัวที่มีบนหน้า (`_find_submit_button`
ลอง `cmdSendNew` → `cmdSendFollow` → fallback by text) ไม่ว่าจะงานใหม่/ต่อเนื่อง + webui confirm ปรับข้อความเป็นกลาง
→ เคลมแห้งที่เป็นงานต่อเนื่อง (งานต้น→งานตาม) ส่งผ่านปุ่ม webui ได้ครบ (fill auto-detect → `_offer_submit` (type 2) → cmdSendFollow)

**เหลือทำ:** (1) verify หลังส่งต่อเนื่องยังใช้ status check เดิม (`report_status` ไม่ใช่ draft) — ยังไม่ยืนยันว่า
row status ของเคลม flip ถูกหลัง `cmdSendFollow` (ดูจริงตอนส่งครั้งแรก) (2) `--no-save-price` ยังเป็น CLI flag เท่านั้น
(webui ไม่ส่ง — production เซฟราคาปกติ) (3) เคลมสด/นัดหมาย ต่อเนื่องผ่าน webui ยังติดด่าน dry-claim + ไม่ส่ง `--allow-fresh`

### 6.3 ความเสียหาย > 8 รายการ
popup รับ 8 (A/B × 4) — ต้องสำรวจว่ามีปุ่มเพิ่มแถว/หน้าถัดไปไหม
(เคลมทดสอบมี 13 รายการ → กรอก 8 เตือน 5)

### 6.3.5 ประเด็นที่รู้แล้วแต่ยังไม่แก้
- **ประเภทเคลม (rdoSurv_Claim_Type radio = ISURVEY type-1)**: 1=เคลมสด,
  2=เคลมแห้ง, 3=เคลมนัดหมาย, 4=งานติดตาม — ด่านเคลมแห้งเช็ค `claim_type == "2"`
  ตรงๆ (ไม่พึ่งข้อมูลคู่กรณีอย่างเดียว เพราะ XML โหลดพลาดได้)
- **ฟอร์ม EMCS เคลมสด layout ต่างจากเคลมแห้ง** — เช่น `txtAcc_Surv` ไม่มีบน
  ฟอร์ม type 1 → fill_accident ปัจจุบันใช้กับ type 2 เท่านั้น ถ้าเปิดเคลมสด
  ต้อง probe ฟอร์ม type 1 แยก
- **XML ดาวน์โหลดตัวที่ 2 ใน session เดียวกัน flaky** (ตัวแรกผ่าน ตัวถัดไป
  บางทีไม่มา) — ใส่ prefs automatic_downloads แล้วยังเป็น, timeout ลดเหลือ 90s
  และระบบไม่พึ่ง XML ในจุดสำคัญแล้ว (ด่านใช้ type, bill ใช้จอ) — ถ้าจะแก้จริง
  ลอง: เปิด tab ใหม่ต่อการดาวน์โหลด หรือใช้ requests+cookies โหลด URL ตรง

### 6.4 อื่นๆ
- **`imbFileImport_XML` บนหน้ารายการ EMCS** — ถ้า import SURV_REPORT XML ได้ตรงๆ
  อาจแทนการกรอกฟอร์มเกือบทั้งหมด (ตัวเปลี่ยนเกม — ควรสำรวจ!)
- ~~รูป tp_veh/ อัปโหลดเป็นประเภท "รูปรถคู่กรณี"~~ ✅ เขียน+**verify หน้าจริงแล้ว**
  2026-06-18 (`upload_images` n_opponents + `_opponent_image_batches`) — 1 คัน =
  'รูปรถคู่กรณี คันที่ 1' (option จริงมีเว้นวรรค, fuzzy จับถูก score 98); อัป 15 รูป
  เข้า S68426064657 ครบ. เหลือ: แกลเลอรีเลือกรูปหน้าเว็บยังไม่โชว์รูปคู่กรณี
  (อัปครบทุกไฟล์เสมอ) + หลายคันยังไม่ได้ verify การ map คันที่ 2+
- **โหมด `--images-only` (เติมรูปเข้า draft เดิม)** ✅ เขียนแล้ว 2026-06-18:
  `emcs.add_images_only` → ค้นเรื่องเดิม → `_pick_draft_report` (เลือกเรื่อง draft
  อัตโนมัติ จากสถานะ 'รายงานสร้างใหม่' ในแถว / ระบุ `--esurvey` เจาะจงได้) →
  `open_report_images` (คลิกลิงก์ e-Survey → รอเมนูรูป) → `upload_images(only=[])`
  = อัปเฉพาะรูปรถคู่กรณี (กันอัปรูปรถประกันซ้ำที่อัปไปแล้ว; `--include-main-images`
  ถ้าจะอัปรูปหลักด้วย). ใช้ตอนกรอกเรื่อง+อัปรูปรถประกันไปแล้ว เหลือเติมรูปคู่กรณี
  — ไม่สร้างเรื่องใหม่/ไม่แตะข้อมูลทั่วไป/คู่กรณี/ความเสียหาย/ค่าใช้จ่าย
- ค่าระยะทาง/ค่ายกลาก หา field บน Debit Note
- 1.jpg ของงาน outsource (ไม่มีใบรับงาน SE — ถามทีมว่าใช้รูปไหนนำ)
- ~~บัญชี ISURVEY แยกสำหรับบอท~~ (ไม่จำเป็นแล้ว — บัญชีเดียวหลาย session ได้)
- ผู้บาดเจ็บ/ทรัพย์สิน (หน้า imbInjure_Person / imbAsset ยังไม่เคยแตะ)

---

## 7. คำสั่ง + เครื่องมือ dev

```powershell
# หน้าเว็บ (ผู้ใช้ทั่วไป) — ดับเบิลคลิก start-webui.bat หรือ:
python webui.py                                    # เปิด http://127.0.0.1:8765
#   ใส่เลขเคลม → กดรัน → ดู log สด; ติ๊ก read-only/skip-images, เลือกเบา-หนักได้
#   เป็น launcher บางๆ: build คำสั่งจากฟอร์ม → subprocess main.py -y → stream stdout
#   แก้ flow การทำงานที่ main.py ที่เดียว webui.py ไม่ต้องแตะ (มันแค่เรียก CLI)

# ใช้งานประจำ (เคลมแห้ง)
python main.py --claim <เลขเคลม>                  # flow เต็ม
python main.py --claim <เลขเคลม> --read-only       # อ่านอย่างเดียว (ปลอดภัย 100%)
python main.py --claims-file claims.txt --read-only # อ่านชุด
python main.py --data-json runs/<เคลม>.json        # กรอก EMCS จากข้อมูลที่อ่านไว้

# เติมรูปเข้า draft เดิม (กรอกเรื่อง+อัปรูปรถประกันไปแล้ว เหลือรูปรถคู่กรณี)
python main.py --images-only --data-json runs/<เคลม>.json   # อัปเฉพาะรูปรถคู่กรณี (tp_veh/)
#   --esurvey Sxxx  เจาะจงเรื่อง | --include-main-images  อัปรูปรถประกันด้วย

# Flags: -y (ไม่หยุดถาม) --severity หนัก|เบา --loss-type <ชื่อ|auto|"">
#        --force-new (สร้างซ้ำ) --allow-fresh (เปิดเคลมสด) --skip-images
#        --images-from panel (โหลดรูปแบบเก่า) --no-xml --threshold 0.75

# ก่อนแก้โค้ดทุกครั้ง
python test_smoke.py                                # ~50 tests ไม่เปิด browser

# เครื่องมือสำรวจ (ใช้เมื่อเว็บเปลี่ยนหรือทำฟีเจอร์ใหม่ — อ่านอย่างเดียวทั้งหมด)
python tools\dump_tabs.py --claim <เคลม>           # dump field ทุก tab ISURVEY
python tools\probe_tabs456.py --claim <เคลม>       # diff id + tab bar + context menu
python tools\probe_emcs.py                          # dump ฟอร์มสร้างงาน EMCS (ไม่บันทึก)
python tools\probe_opo_unlock.py                    # เช็คเงื่อนไขปลดล็อกส่วนคู่กรณี
python tools\probe_mainpage.py                      # dump หน้ารายการงาน EMCS
# ผล dump เก็บใน runs/*.json — discovery เดิมยังอยู่ ใช้อ้างอิง id ได้เลย
```

**วิธี debug เมื่อพัง**: ดู `runs/logs/run_*.log` (มีทุกอย่างรวม alert text) +
`error_*.png/.html` (สภาพหน้า ณ วินาทีพัง — HTML ใช้แกะ id/validation ได้)
