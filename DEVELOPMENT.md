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
| ฝั่งกรอก EMCS **เคลมสด/มีคู่กรณี** | ⏸️ **พักไว้** — โค้ดเขียนเสร็จแต่ติดข้อมูลต้นทางไม่ครบ (ดู §6.1) มี hard stop ใน main.py เปิดด้วย `--allow-fresh` |
| Batch หลายเคลม (อ่าน+กรอก EMCS ต่อเคลม) | ✅ ใช้ได้ (`--claims`, `--claims-file`) — เคลมไม่แห้ง/มีเรื่องแล้ว = ข้ามอัตโนมัติพร้อมเหตุผล |
| ด่านกันเปิดเรื่องซ้ำ | ✅ ทดสอบกับของจริงผ่าน |
| โหมด `--resume` (เปิดเรื่องเดิมทำต่อ) | 📋 ออกแบบแล้ว ยังไม่ได้เขียน (ดู §6.2) |
| Deploy เครื่องพนักงาน (พก Python ในตัว) | ✅ `runtime\` = Python 3.13 embeddable + deps ครบ — copy แล้วรันได้ ไม่ต้องลง Python (สร้างด้วย `build-runtime.bat`, ทดสอบ webui เสิร์ฟ HTTP 200 ผ่าน) |
| บันทึกงานลง se-key DB (key.sesurvey.cloud) | ✅ ตรวจ survey_no ซ้ำก่อนกรอก (ซ้ำ=ข้าม) + บันทึก+mark "ส่งแล้ว" ตอนกดส่งงาน (`autokey/sekey_client.py`); ทดสอบ auth+ตรวจซ้ำกับ prod (323k แถว) + **POST จริงผ่านแล้ว** (backfill เคลม 2026013145665 → SEABI-210260601351 → record id 336763, sent=1) |
| เลือกรูปก่อนอัปโหลด EMCS (หน้าเว็บ) | ✅ ก่อน upload หยุดโชว์แกลเลอรี ติ๊กเลือกรูป → อัปโหลดเฉพาะที่เลือก (marker `@@SELECT_IMAGES@@` + webui route `/image` + `upload_images(only=...)`); console = ทุกรูปเหมือนเดิม. ทดสอบ server-side (poll/serve/traversal-block) ผ่าน |
| แกลเลอรีจัดกลุ่มตามหมวด (INS/REPORTS/OTHERS) | ✅ เก็บหมวดผ่าน manifest: `download_images`→`_categories.json`(ชื่อ→หมวด), `process_images_pro`→`_rename_map.json`(ชื่อใหม่→เดิม), `browser._image_categories` รวมแล้วส่งใน marker; webui จัดกลุ่ม+checkbox "เลือกทั้งหมวด" (ไม่มี manifest=OTHERS หมด ปลอดภัย). E2E จริงผ่าน (2026013046414: INS22/REPORTS4/OTHERS1 หมวดรอดผ่าน rename) |

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
  - `rdoGender_0/1` = เพศผู้ขับขี่ (0=ชาย M, 1=หญิง F) → ดึงจาก XML insured
  - `ddlDri_Title_ID` = คำนำหน้า → derive จากชื่อผู้เอาประกันถ้าตรงกับผู้ขับขี่
    ไม่งั้นเดาจากเพศ (M=นาย, F=นางสาว+log เตือน)
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
- **สูตรจังหวัด/อำเภอ**: รหัส ISURVEY = ลำดับ option ใน dropdown EMCS
  (เรียง ก-ฮ เหมือนกัน: 2=กรุงเทพฯ, 28=ปทุมธานี) / รหัสอำเภอ =
  `<รหัสจังหวัด><ลำดับ 2 หลัก>` เช่น 236=กทม เขต 36 ดอนเมือง, 1203=ชุมพร อ.3 ปะทิว
  (ยืนยัน 3 เคสจริง) → `_select_index`

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
2. **ลักษณะความเสียหาย (ddlLoss_ID) บังคับสำหรับเคลมสด** แต่ `resolve_loss_type`
   คืนค่าว่างเมื่อผลคดีกำกวม → ต้องเลือกเสมอ (อาจถาม user เป็น CLI arg หรือ
   default ตามประเภท)
- โค้ดคู่กรณี (fill_third_parties) เขียนเสร็จแล้ว ยังไม่เคยรันผ่านจุดบันทึกจริง
- เปิดใช้: `--allow-fresh`

### 6.2 โหมด `--resume` (ออกแบบแล้ว ยังไม่เขียน)
เจอเรื่องเดิม → เปิดเรื่องล่าสุด (คลิกลิงก์ e-Survey ในผลค้นหา — **หน้านี้ยังไม่เคย
probe**) → ตรวจสถานะรายส่วน → ทำเฉพาะส่วนที่ขาด:
- ข้อมูลทั่วไปบันทึกแล้ว = ไม่แตะ (ห้าม send_keys ทับ — จะกลายเป็นต่อท้าย!)
- ความเสียหายมีแถวแล้ว = ข้าม / รูปยังไม่มี = อัปโหลด / ค่าใช้จ่ายว่าง = กรอก+บันทึก

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
- รูป tp_veh/ อัปโหลดเป็นประเภท "รูปรถคู่กรณี" (เมื่อเปิดเคลมสด)
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
