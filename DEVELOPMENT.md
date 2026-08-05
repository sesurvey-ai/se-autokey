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
| โหมดนำเข้า XML (`--import-xml` / webui checkbox) | ✅ E2E verify 2026-06-24 (draft S68426066006) — EMCS import ฟอร์มหลักจาก SURV_REPORT XML (ปุ่ม imbFileImport_XML) → บอทอุดช่องว่าง/แก้ (cascade อำเภอ + เคลียร์เลขรับแจ้ง) → คู่กรณี/ความเสียหาย **free-text 20 ช่อง** (vs cmdNewReport 8) → ค่าใช้จ่าย. รองรับ >8 ดีขึ้น (ดู §6.4); ยังไม่ verify: ผู้บาดเจ็บ/ทรัพย์สิน, >8 เต็ม 20, รูป, กดส่งจริง |
| ⚙ ตั้งค่าคนคีย์ + 📚 สมุดงาน + ปิดการ์ดอัตโนมัติ | ✅ 2026-08-05 — ตารางคนคีย์ย้ายจากโค้ดไป `settings/keyers.json` แก้ได้จากแท็บ **⚙ ตั้งค่า** (มีผลทันที ไม่ต้องรีสตาร์ต); แท็บ **📚 สมุดงาน** ดูเลขเคลม/เซอร์เวย์ที่ทำไปแล้ว (`runs/jobs.jsonl` บันทึกตอน draft + ตอนส่งสำเร็จ, ค้นได้); การ์ดปิดตัวเองใน 8 วิ หลังบอท verify สถานะบน EMCS (marker `@@JOB_SENT@@`) |
| โหมด "เรื่องเดิม" บนหน้าเว็บ (แท็บ ISURVEY) | ✅ 2026-08-05 — เพิ่มติ๊ก **กรอกต่อบนเรื่องเดิม** (`--fill-existing`) + **อัปเฉพาะรูปเข้าเรื่องเดิม** (`--images-only`, มี ↳ รวมรูปรถประกัน) + ช่องเลข e-Survey; เดิม `_build_cmd` รองรับ `fillexisting` แต่ไม่มี UI ต้องยิง `/run` เอง. ติ๊ก "อัปรูปอย่างเดียว" คู่ "ไม่ยุ่งกับรูปภาพ" = ฟ้องตั้งแต่ยังไม่รัน. verify จริง: draft S68426080794 จาก 0 ใบ → 32 ใบ แยกประเภทถูกทั้งหมด |
| ชี้ช่องที่ต้องแก้บนหน้า EMCS | ✅ 2026-08-05 — บอทหยุดเมื่อไหร่ ตีกรอบแดงกระพริบที่ช่องนั้นบน Chrome + เลื่อนจอไปหา + แถบบอกเหตุผลค้างไว้ + ดึงหน้าต่างขึ้นหน้า; ช่องที่เดาแบบคะแนนต่ำย้อมเหลืองไว้ให้คนตรวจ (ดู §6.9) |
| ตรวจใบขับขี่ผู้เอาประกัน (`--check-license` / webui checkbox) | ⏸️ **โค้ดมี แต่ปิด/ไม่ deploy (user ตัดสิน 2026-06-25: ไม่เปิดใช้ — ช้า + มีคนมอนิเตอร์อยู่แล้ว ไม่คุ้ม).** OCR ในเครื่อง (easyocr) verify รูปจริงแล้ว ✓ (ดู §6.5) แต่ **ถอน torch/easyocr ออกจาก runtime แล้ว** ให้ deploy เบา — โค้ด lazy import ปิด default ไม่กระทบ flow. จะใช้ค่อย `pip install -r requirements-ocr.txt` |

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
- **1 เลขเคลม มีได้หลายเซอร์เวย์ (survey_no) — ต่างสถานะ** เช่น เคลม 2026013049535:
  SEABI-...497 = 'ยกเลิกเคลม' + SEABI-...504 = 'จบงาน' (คนละ surveyor). `listcases.php`
  คืนทุกแถว (ไม่กรองสถานะ); แต่ละ case dict มี `survey_no` + `sttcase_ID` (code: 99≈ยกเลิก,
  100≈จบงาน) + **`close_datetime`** (มีค่า = ปิด/จบงานแล้ว = แถวที่มักต้องคีย์, ว่าง = ยังไม่ปิด)
  — ไม่มี field ชื่อสถานะตรงๆ ใน API (สถานะที่จอ ISURVEY แปลงจาก sttcase_ID)
- **Guard เลือกเซอร์เวย์ (2026-06-24):** `find_case` (API) / `find_and_open_claim` (scrape) —
  **ระบุ invoice → เลือกแถว survey_no ตรงเป๊ะ; ไม่ระบุ + มีหลายเซอร์เวย์ → หยุด+ถาม**
  (raise list ทุกแถว: survey_no + surveyor + close_datetime/สถานะ ให้ผู้ใช้ใส่เลขเซอร์เวย์
  แล้วรันใหม่) — เดิมหยิบแถวแรกโดยไม่ดูสถานะ (เสี่ยงหยิบงานยกเลิก). มีแถวเดียว=ไป
  ต่ออัตโนมัติ. scrape อ่านสถานะจาก `td[1]` (คอลัมน์ 'สถานะ'); verify จริงเคลม 2026013049535

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
- เปิดหลังบันทึก (btnPopUp_DamList) เป็น window ใหม่ (`frmDamage.aspx`)
- **2 แม่แบบ — ต่างกันที่ "วิธีสร้างงาน" (user ยืนยัน 2026-06-23, verify DOM สดผ่าน Chrome MCP):**
  - กด **"สร้างงานใหม่" `cmdNewReport`** → ฟอร์ม **checklist** (รูป 1): มี **checkbox ชิ้นส่วนสำเร็จรูป**
    `dgvDamage_List_ctl{NN}_WuDamL{A|B}_chbDam_Name_0` (23 ชิ้น: กันชนหน้า/หลัง, กระจกบังลม,
    ฝากระโปรง, กระจัง, กระบะ, หลังคา, ไฟ, บังโคลน, ประตู...) ทับบน free-text เดิม (ctl02-05)
  - กด **"นำเข้าข้อมูลแบบ XML" `imbFileImport_XML`** (import SURV_REPORT XML — ฟอร์แมต
    เดียวกับที่บอทอ่านผ่าน `surv_xml.parse_surv_report`) → ฟอร์ม **free-text ล้วน** (รูป 2):
    `dgvOtherDamage_List_ctl0{N}_wuOtherDamL{A|B}_txtDam_Name` (ไม่มี checkbox)
  - **ไม่ใช่ยุค/ไม่ใช่ประเภทรถ** (สมมติฐานเดิมผิด — เก๋งปี 2566 ที่เห็น free-text เพราะนำเข้าข้อมูล)
  - **✅ controlled test ยืนยัน 2026-06-23 (session เดียวกัน ต่างแค่วิธีสร้าง, อ่าน DOM สด):**
    `cmdNewReport` → **S68426065925 = checklist 23 checkbox** + free-text ctl02-05 /
    `imbFileImport_XML` (import ไฟล์ SURV_REPORT_00000932959, เคลม 2026013144105) →
    **S68426065956 = checkbox 0** (มีแค่ se-check-mix 'งานรวม') + **free-text 30 แถว ว่างหมด**
    (DAMAGE_LIST ใน XML ว่าง → import ไม่ pre-fill ความเสียหาย)
  - **บอทใช้ `new_report`→`cmdNewReport` เสมอ → เจอฟอร์ม checklist ทุกครั้ง** → enhancement ตรงเป้า;
    ถ้าอนาคตเปลี่ยนไป import XML → ได้ free-text (checklist=[]) → fallback ช่องอิสระอัตโนมัติ (โค้ดรองรับ)
  - checklist บางแถวมี L/R/A (`rdoDam_Left_Right_0/1/2`) บางแถวมีแต่ระดับ (`rdoDam_Lavel_0-3`); **ไม่มี postback**
- **`fill_damage_list` enhancement (2026-06-23):** อ่าน checklist จาก DOM (`JS_READ_DAMAGE_CHECKLIST`)
  → `_match_damage_checklist` (normalize ตัด (..)/ซ้าย/ขวา/ด้าน/ตัวบน-ล่าง ด้วย `_norm_damage_part`
  **matching = prefix (หลัก) + fuzz.ratio (fallback)** — แก้หลัง E2E 2026-06-23:
  ชื่อจริง ISURVEY = 'ชิ้นส่วน+คำเสริม+อาการ' เช่น `'ฝากระโปรงหน้า+คิ้ว บุบ'`, `'กระจังหน้า แตก'`
  → **prefix:** ชื่อ(normalize) ขึ้นต้นด้วยชิ้นส่วน checklist (ยาวสุด) → ติ๊ก; `'คิ้วครอบไฟหน้า'`
  ไม่ขึ้นต้นด้วย `'ไฟหน้า'` → ไม่ติ๊ก (กัน substring/คนละชิ้น) → **fallback** fuzz.ratio ≥88 (พิมพ์ผิดเล็กน้อย)
  → ติ๊ก checkbox + `_damage_side` (L/R/A) + `_damage_rank_idx` (A-D); ติ๊ก/radio ผ่าน
  `execute_script("arguments[0].click()")`; ชิ้นที่ไม่ match → fallback ช่อง free-text เดิม (สูงสุด 8)
  - ฟอร์มเก่า/อ่าน checklist ไม่ได้ → checklist ว่าง → ลง free-text ทั้งหมดเหมือนเดิม (ปลอดภัย)
  - **✅ E2E verify 2026-06-23 (เคลม 2026013144715 → S68426065957):** flow ครบ + อ่าน checklist 22 ชิ้น;
    unit test ชื่อจริง 6 รายการ → **ติ๊ก 3 (ฝากระโปรงหน้า/กันชนหน้า/กระจังหน้า) + free-text 3** (ฝาครอบโลโก้/
    คิ้วครอบไฟหน้า/กรอบป้าย) ✓ smoke 162. **ยังไม่ verify การติ๊กบนหน้าสด** (run #2 ใช้ logic เก่า=free-text หมด)
  - **พ่วงแก้บั๊ก น.ส.:** `THAI_TITLES`/`TITLE_GENDER` เพิ่ม น.ส./นส. + `_derive_insured_title`/`fill_driver`
    ตัดคำนำหน้าที่ติดชื่อ (`driver_name='น.ส.ปฐมาวดี'`) — เดิม derive คำนำหน้าไม่ได้ + กรอกชื่อมี 'น.ส.' ซ้ำ
- เกิน 8 (free-text) → log เตือนให้เติมเอง (ดู §6.3)

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
- **บอทกรอกหน้านี้มาก/น้อยตาม "ต้นทางข้อมูล" (กติกา user 2026-08-03)**

  | ต้นทาง | `full_billing` | กรอกอะไร | ทำไม |
  |---|---|---|---|
  | **ISURVEY** | `True` | เต็มหน้า | หัวหน้ากรอกความเห็น+เรทราคาไว้ในระบบเดิมแล้ว ยกมาได้เลย |
  | **se-survey** | `False` | **แค่ 2 ช่อง** (เลขที่ใบแจ้งหนี้ + วันที่วางบิล) | หัวหน้าจะกรอกเองใน EMCS — บอทเติมให้ = ขยะที่ต้องมาลบ |

  เต็มหน้า = หัวบิล + 3 ช่องสรุป (`txtAcc_result` ผลการดำเนินงาน / `txtAcc_Comment`
  ความเห็นผู้ตรวจสอบ / `txtSurv_Comment` ความเห็นเซอร์เวย์) + ตารางราคาคอลัมน์ "เสนอ"
  — ต้นทางไม่มีข้อมูล = ข้ามช่องนั้น (ไม่ทับของเดิม ไม่เขียนเลขมั่ว) ·
  ยอดรวม/VAT ปล่อย JS คำนวณ ห้ามพิมพ์ทับ
- **ทั้ง 2 ต้นทางกด "บันทึกราคา" เสมอ** — ไม่กด = หัวบิลไม่ติด + เรื่องค้างล็อก
  `--no-full-billing` (ชื่อเดิม `--no-save-price`) = บังคับโหมด 2 ช่อง ไม่ได้แปลว่าไม่กดบันทึก
  (ประวัติ: กติกา 2026-07-27 ให้กรอก 2 ช่องทุกกรณี — commit `9719228` ถอด
  `fill_fee_table`/`set_textarea` ออกจนกลายเป็น dead code จนถึง 2026-08-03)
- ปุ่มบันทึก = "บันทึกราคา" → กด + alert *"บันทึกการแก้ไขเรียบร้อยแล้ว"*.
  ⚠️ **บนจอมีปุ่มเดียว แต่ id มี 2 แบบ — หน้าหนึ่ง render ตัวเดียว**

  | id | title | เมื่อไหร่ |
  |---|---|---|
  | `btnSurveySave` | Survey บันทึก | ใบแจ้งหนี้ครั้งนี้ **ยังไม่เคยถูกบันทึก** |
  | `btnSurvey_Update` | Survey แก้ไข | **เคยบันทึกแล้ว** (เปิดมาแก้) |

  บอทลองทั้งคู่ (`_PRICE_SAVE_BUTTONS`) — ปลอดภัยกว่าเดาเงื่อนไข
  **ตัวชี้ขาดไม่ใช่ `hifPostStatus`** (เรื่อง S68426080392 status=1 ทั้งก่อนและหลังบันทึกบิล
  แต่ปุ่มเปลี่ยนจาก `btnSurveySave` → `btnSurvey_Update`)
  **เคยพลาด 2 รอบในเรื่องเดียวกัน:** (1) ตรวจหน้าเดียวแล้วเขียนว่า "ไม่มี `btnSurveySave`
  อยู่จริง" → draft ใหม่หาปุ่มไม่เจอ หัวบิลไม่ถูกบันทึก (user ต้องพิมพ์+กดเอง)
  (2) เจอ 2 หน้าที่ต่างกัน 2 ตัวแปรพร้อมกัน แล้วชี้ผิดตัวว่า `hifPostStatus` เป็นเหตุ
- ค่าระยะทาง (INS_DIST) / ค่ายกลาก (INS_CARTOW) ยังไม่มีช่อง map → log เตือนถ้ามียอด

### หน้าเว็บ: รายการงานจบแล้วจาก ISURVEY (`/isurvey-cases`)
ใช้ **รายงาน** `report/get_data_report.php?report_type=enquiry` (`con_date=2`,
`date_from`, `date_to`) — **ไม่ใช่** `supervisor/listcases.php`

| | listcases.php | get_data_report.php |
|---|---|---|
| จำนวนต่อครั้ง | **ตัน 50 แถว** | ครบ (1,094 แถว/4 วัน) |
| paging | **ใช้ไม่ได้** (start/page เท่าไหร่ก็ชุดเดิม) | ใช้ได้ (`total` จริง) |
| sort | **ถูกเมิน** (ไม่ใส่วันที่ = ได้งานปี 2020) | เรียงเองฝั่งเราจาก `finish_dt` |
| ช่วงวันที่ | "ตั้งแต่วันที่" อย่างเดียว | **from–to** |
| สถานะ | รหัส `sttcase_ID` (100 = จบงาน) | ป้ายไทย `stt_desc` |
| นำเข้า EMCS แล้วหรือยัง | **ไม่มี** | **`EMCSstatus` / `EMCSby` / `EMCSdate`** |

- `EMCSstatus == 'send'` = นำเข้า EMCS แล้ว — **ISURVEY เซ็ตเอง บอทไม่เขียนกลับ**
  (user ยืนยัน 2026-08-04) → แถวนั้นติ๊กเลือกไม่ได้ + มีแบดจ์บอกใคร/เมื่อไหร่
- ⚠️ เคยใช้ `close_datetime` เป็นเกณฑ์ "จบงาน" — **ผิด** ว่างทุกแถว
- สูตรนี้มาจากโปรเจกต์ **se-report** ซึ่งอ่านรายงานชุดเดียวกันอยู่แล้ว (อ่านอย่างเดียว ไม่แตะ)
- หน้ารายการยังไม่ตรวจความครบตอนโหลด — ดูหัวข้อถัดไป


### หน้าเว็บ: ตรวจก่อนนำเข้า (`/isurvey-check`)
กด **🔍 ตรวจ** ต่อแถว → อ่านเคลมจริงแล้วบอกว่ากรอกจนจบเองได้ไหม แยก 2 ระดับ:

- **blockers** = บอทกรอกต่อไม่ได้ **ต้องเลือกก่อน** — ส่ง dropdown ตัวเลือกจริงของ EMCS
  ไปให้เลือกบนหน้าเว็บ แล้วส่งต่อเป็น `--loss-type` / `--driver-title`
  → ไม่ต้องรอบอทไปหยุดกลางทางบน EMCS แล้วค่อยเลือก
- **warnings** = ช่องสำคัญที่ว่าง ให้ดูก่อนกด แต่บอทเดินต่อได้

⚠️ **แยก 2 ระดับเพราะจงใจ** — "ลักษณะความเสียหาย" ไม่ใช่ "ข้อมูลไม่ครบ" แต่คือ
**ต้นทางไม่มีช่องนี้เลย** (ISURVEY ไม่มี · `<LOSS_ID>` ใน XML ก็ว่าง) เคลมที่มีคู่กรณี
จะติดข้อนี้ 100% ทุกใบ ถ้าปนกับ warning ทั่วไป คนจะเห็นเตือนทุกแถวจนเลิกอ่าน
เคลมแห้งไม่ติด เพราะเดาได้จากโครงสร้าง (ไม่มีคู่กรณี = "เคลมแห้ง")

คำนำหน้าผู้ขับขี่ติดเฉพาะเมื่อชื่อผู้ขับขี่ไม่ตรงผู้เอาประกัน (ยกมาใช้ไม่ได้) —
ไม่เดาจากเพศ เพราะแยก นาง/นางสาว ไม่ได้

ไม่ตรวจตอนโหลดรายการ (50 เรื่อง = ยิง getcaseinfo 50 ครั้ง ช้า + ISURVEY timeout บ่อย)

⚠️ **เตือนยอดค่าสำรวจเป็น 0** — งานสถานะ "จบงาน" ปกติต้องมียอดแล้ว (กติกา user)
ถ้ายังเป็น 0 แปลว่ายอดตามมาทีหลัง → นำเข้าตอนนี้ตารางราคาใน EMCS จะเป็น 0 ตาม
เป็น **warning ไม่ใช่ blocker** (บางงานอาจไม่มีค่าสำรวจจริง — คนตัดสิน)
พบจริง: เคลม 2026013160275 จบงานแล้วแต่ `INS_*`/`SUR_*` เป็น 0 ทั้งชุด

**คิวนำเข้าหลายเรื่อง** — ติ๊กเลือก → 🔍 ตรวจที่เลือก → ⚡ นำเข้าที่เลือก
- **รันทีละเรื่องเสมอ** (ไม่ขนาน) — EMCS ล็อกเรื่องรายตัว + โควตารูปเป็นของเคลม
  รอเรื่องก่อนหน้า `status != running` แล้วค่อยเริ่มเรื่องถัดไป
- **ตรวจให้ครบก่อนเริ่มคิว** — เรื่องไหนยังเลือกค่าไม่ครบ คิวจะไม่เริ่มเลย (ไม่ใช่เริ่มแล้ว
  ไปค้างกลางทาง) ป้องกันสถานะ "รันไปครึ่งคิวแล้วติด"
- คิวอยู่ฝั่ง browser โดยตั้งใจ — ปิดแท็บ = คิวหยุด ไม่มีอะไรวิ่งต่อโดยไม่มีคนดู
  (งานนี้เขียนลงระบบประกันจริง)

### หน้าเว็บ: ฝั่ง se-survey — ตรวจก่อนนำเข้า (`/sesurvey-check`) + คิว
ตรวจจาก **XML + report ของจริง ไม่เปิด Chrome ไม่แตะ EMCS** — เร็วกว่ารัน dry-run
แล้วไล่อ่าน log เอง (วิธีเดิม) และได้คำตอบเป็น "✅ พร้อม / ⛔ ไม่พร้อม" ตรง ๆ

**ตรวจคนละเรื่องกับฝั่ง ISURVEY** — ที่นี่ข้อมูลมาจากแอปมือถือซึ่งฟิลด์ตรง EMCS
เกือบหมด (ลักษณะความเสียหาย/คำนำหน้ามาครบ) จึง**ไม่มี dropdown ให้เลือกบนหน้าเว็บ**
สิ่งที่พลาดได้คือ "ดึงของไม่ครบ": XML/report ดึงไม่ได้ · ประเภทรถ/จังหวัด/
ลักษณะความเสียหาย ว่าง (EMCS บังคับ → บอทจะหยุดรอกลางทาง)

⚠️ ต้องดึง **2 อย่าง** ไม่ใช่แค่ XML — `report` คือค่าไทยที่ `fill_*` ใช้เลือก dropdown
บังคับของ EMCS (`_populate_claim_from_report`) ขาดแล้วบอทหยุดรอแม้ XML จะครบ

**คิว** ใช้กติกาเดียวกับฝั่ง ISURVEY: รันทีละเคส · ตรวจก่อนทุกเคส ไม่พร้อม = หยุดคิว ·
เคสที่ `emcs_imported_at` แล้วติ๊กเลือกไม่ได้ (กันนำเข้าซ้ำ)

### ตารางแปลงรหัส ISURVEY ↔ EMCS (`autokey/isurvey_emcs_map.py`)
**สองระบบใช้รหัสคนละชุดกับของอย่างเดียวกัน** — ส่งรหัสดิบข้ามระบบ = เลือกผิดเงียบ ๆ ไม่มี error

| ของ | ISURVEY | EMCS |
|---|---|---|
| ชลบุรี | 20 (รหัสราชการ) | 9 (เรียงตามตัวอักษร) |
| ใบขับขี่รถยนต์ส่วนบุคคล | 15 | 19 |
| อำเภอ | `<จว.ISURVEY><ลำดับ2หลัก>` (3607) | `<จว.EMCS><ลำดับ2หลัก>` (1107) |

- สร้างด้วย `python tools/build_code_map.py` (จับคู่ด้วย **ชื่อ** จาก dropdown จริง
  ใน `runs/emcs_spec.json` กับตาราง master ของ ISURVEY) — **ไฟล์ที่ได้เป็น generated อย่าแก้มือ**
- อำเภอไม่ต้องมีตาราง: รูปแบบเหมือนกัน เปลี่ยนแค่ส่วนรหัสจังหวัด (ลำดับอำเภอเรียงตรงกัน)
- แปลงไม่ได้ = คืน `''` ปล่อยว่างให้คนเลือก **ห้ามเดา**
- ⚠️ จับคู่ด้วยชื่อพลาดได้เพราะ **EMCS สะกดผิดในตัวเลือกเอง** ('ใบขับขี่รถยนต์ส่วนบุคคคล'
  ค เกิน) → ตัวสร้างใช้ fuzzy แล้ว log ทุกคู่ที่ชื่อไม่ตรงเป๊ะให้ตรวจ
- ยืนยันแล้วกับเคลม 2026013147939: จังหวัดทะเบียน/จังหวัด+อำเภอผู้ขับขี่/ประเภทใบขับขี่
  ตรงกับ XML รูปแบบ EMCS ที่ ISURVEY ส่งออกเอง ครบ 4/4 (เฉลยคนละทางกับที่ใช้สร้างตาราง)
- **ผู้บาดเจ็บ**: `related_accidentID` → `DV/PR/ON` (EMCS ฝั่งฟอร์มมีแค่ 3 กลุ่ม —
  อะไรที่ไม่ใช่ "รถประกัน" ตกกลุ่มบุคคลภายนอก) · `injury_type` → `ddlWounded_Type`
  ⚠️ ISURVEY ไม่มี master ของระดับบาดเจ็บ ตัวอย่างจริงมีแค่ `I`→`02` ตามที่ ISURVEY
  ส่งออกเอง — ค่าอื่นคืน `''` ปล่อยว่าง
- **ที่อยู่**: ของผู้บาดเจ็บต่อ `ตำบล,อำเภอ,จังหวัด` ท้ายบ้านเลขที่ แต่ของ**เจ้าของทรัพย์สินไม่ต่อ**
  (ยืนยันจาก XML จริง)
- ยืนยันแล้วกับเคลม 2026013058298 (บาดเจ็บ 3 ราย + ทรัพย์สิน 1): ตรงกับ XML **48/48 ช่อง**
- **เส้น `--scrape` ใช้ตารางเดียวกัน**: อ่าน "รหัสดิบ" หลัง combo ด้วย ExtJS `getValue()`
  (`_JS_GET_RAW`) ซึ่งตรงกับที่ API คืนเป๊ะ แล้วแปลงด้วย map ตัวเดียวกัน —
  ไม่ต้องทำตารางจับคู่ด้วย "ชื่อ" แยกอีกชุด · datefield คืน object → ใช้ค่าที่แสดงแทน
  ⚠️ ฟอร์มไม่มีช่อง `relation` ของคู่กรณี (API มีจาก DB) = ข้อจำกัดของการอ่านหน้าจอ

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
| กดบันทึกหน้าหลักแล้วโปรแกรมตายด้วย `TimeoutException` เปล่า ๆ | onclick ของ `btnSave` คือ `if (validForm()==false) return false; ... __doPostBack(...)` — ถ้า `validForm()` (JS ของ EMCS) ปัดตกหรือพังกลางทาง จะ**ไม่มีทั้ง alert และ postback** แต่ `save_main_form` เขียนไว้ว่า "กดแล้วต้องมี alert เสมอ" `accept_alert` เลยหมดเวลาแล้วโยนทะลุออกไป (เจอจริง เคลม 2026013059072) | จับ `TimeoutException` → ถือว่า "validation ไม่ผ่านแบบไม่มีข้อความ" ตกไปเส้นหยุดรอคนกรอกตามปกติ + `_diagnose_save_click` เรียก `validForm()` เองเพื่อเก็บ error/alert จริงมาบอก; โหมดแก้ (`btnUpdate`) ต้องไม่ตีความ "เงียบ" ว่าสำเร็จ |
| นามสกุลผู้ขับขี่หายทั้งคำ | ISURVEY เก็บชื่อรวมสตริงเดียว มักมีคำนำหน้าติดมา — โค้ดตัดด้วย `split()[0]/[1]` จึงได้ ชื่อ='คุณ' นามสกุล='พัลลภ' และทิ้งคำที่ 3 (เคลม 2026013158841 ชื่อจริง 'คุณ พัลลภ ธาดากิจวณิช') | แยกด้วย `split_thai_name` ทั้งฝั่ง API และ scrape → คำนำหน้าเข้า `driver_title`, ที่เหลือเป็นชื่อ/นามสกุลเต็ม (verify 6 เคลมจริง) |

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

**เคลมสดส่งงานผ่าน webui ได้แล้ว — ✅ 2026-06-19:** ปลด gate `dry_claim_block_reason()` ที่
`main.py` (เดิมเสนอส่งเฉพาะเคลมแห้ง) → **เคลมสดก็เข้า `_offer_submit`** (ปุ่ม "✅ ส่งงาน + แจ้ง
ISURVEY" + เลือกประเภทงาน + กด cmdSendNew + ยิง ISURVEY + บันทึก se-key เหมือนเคลมแห้งทุกอย่าง)
— เคลมสดมี **คำเตือนพิเศษ** (`reason` ส่งเข้า marker → `.pause-reason`) ให้ตรวจคู่กรณี/ผู้บาดเจ็บ/
ทรัพย์สิน + ราคาก่อนกดส่ง; ยังคงหลัก **ไม่กด cmdSendNew เองจนผู้ใช้กดปุ่ม + confirm**

**webui checkbox เพิ่ม (2026-06-19):** `--no-save-price` (ทดสอบไม่เซฟราคา) + `--force-new`
(สร้างเรื่องใหม่แม้มีเรื่องเดิม — มี confirm กันเผลอ เพราะ draft ลบไม่ได้)

**เหลือทำ:** (1) verify หลังส่งต่อเนื่องยังใช้ status check เดิม (`report_status` ไม่ใช่ draft) — ยังไม่ยืนยันว่า
row status ของเคลม flip ถูกหลัง `cmdSendFollow` (ดูจริงตอนส่งครั้งแรก) (2) verify ส่งเคลมสดจริงผ่าน webui
ครบ loop (cmdSendNew → ISURVEY → se-key 1 row งานต้น) — ยังไม่เคยกดส่งจริงสำหรับเคลมสด

### 6.3 ความเสียหาย > 8 รายการ — **บรรเทาแล้วด้วย checklist enhancement (2026-06-23)**
- ฟอร์ม `cmdNewReport` (บอทใช้): ช่อง **free-text แค่ 8** (`dgvOtherDamage_List` A/B × ctl02-05)
  **+ checkbox ชิ้นส่วนสำเร็จรูป 23 ตัว** (`dgvDamage_List`, ครึ่งบน) ← ที่บอทไม่เคยใช้
- ฟอร์ม `imbFileImport_XML`: free-text **30 แถว** (เยอะกว่ามาก แต่ไม่พก DAMAGE_LIST + บอทไม่ใช้ path นี้)
- **enhancement `fill_damage_list`: ชิ้นที่ match checklist → ติ๊ก checkbox (ไม่กิน slot free-text);
  ที่เหลือลง free-text (≤8)** → ความจุรวมเพิ่มจาก 8 → **สูงสุด ~31** (23 checkbox + 8 free-text)
  - เคลม 13 รายการ: ถ้า ≥5 ชิ้น match checklist → ลงครบ (ที่เหลือ ≤8 ลง free-text พอดี)
  - ยัง overflow ได้ถ้าชิ้นส่วน match checklist น้อย (free-text ยังตัน 8) — แต่ดีขึ้นมาก
- ⏳ ยังไม่ verify E2E การติ๊กจริง (ดู §Popup ความเสียหาย)

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
- ~~**แกลเลอรีเลือกรูปจัดกลุ่มเป็น OTHERS หมด**~~ ✅ **แก้แล้ว 2026-08-05**
  (เจอกับเคลม 2026013059072: 22/22 รูปขึ้น OTHERS ปุ่ม "เลือกทั้งหมวด" ใช้ไม่ได้)
  ต้นเหตุ: `browser._image_categories` แปลงชื่อผ่าน `_rename_map.json` ก่อนเสมอ
  แต่ตั้งแต่ `images._rewrite_manifest` เขียน `_categories.json` กลับด้วย sha1 หลัง
  rename คีย์ก็เป็น "ชื่อปัจจุบัน" อยู่แล้ว → การแปลงกลายเป็นการแปลงทิ้ง ชี้ไปชื่อ
  ต้นทางฝั่งเซิร์ฟเวอร์ (`DOC_Claimform.jpg`) ที่ไม่มีใน manifest
  แก้เป็น "ลองชื่อปัจจุบันก่อน แล้วค่อยถอยไป rename map" (fallback ไว้ให้โฟลเดอร์เก่า)
  **verify บนหน้าเว็บจริงแล้ว** (ยิงงานปลอมเข้า webui ทดสอบ ไม่แตะ EMCS): แกลเลอรีขึ้น
  `🚗 รูปรถประกัน (18)` + `📄 เอกสาร/ใบรับงาน (4)` พร้อมปุ่ม "เลือกทั้งหมวด" ต่อกลุ่ม —
  4 ใบในกลุ่มเอกสาร = ใบเคลม + ความเห็นหัวหน้า ตรงกับที่ไล่จาก `_rename_map.json` จริง
  **กระทบแค่การจัดกลุ่มบนจอ** — ตอนอัปโหลดจริงแยกประเภทถูกมาตลอด เพราะ
  `upload_images` ใช้เส้นข้อมูลคนละเส้น (ยืนยันจากหน้า EMCS: รูปประกอบ×5 /
  รูปรถประกัน×17 / รูปรถคู่กรณี คันที่ 1×10 ตรงหมด)

### 6.4 อื่นๆ
- **`imbFileImport_XML` บนหน้ารายการ EMCS** — ถ้า import SURV_REPORT XML ได้ตรงๆ
  อาจแทนการกรอกฟอร์มเกือบทั้งหมด (ตัวเปลี่ยนเกม — ควรสำรวจ!)
  - **ทดลองจริง 2026-06-23 (S68426065956):** import ฟอร์แมตเดียวกับที่บอทอ่าน
    (`surv_xml.parse_surv_report`) สำเร็จ → ลงข้อมูลหลัก (ทะเบียน/ยี่ห้อ/รุ่น/ตัวถัง/เพศ/เลขเคลม) ครบ
  - **แต่ import ต่างจากบอท:** (1) ชื่อ-สกุล **ไม่แยก** (ชื่อเต็มลงช่องชื่อ สกุลว่าง — บอท `split_thai_name` แยกให้)
    (2) คำนำหน้าว่าง (XML ไม่มี) (3) **ประเภทรถเชื่อ CTYPECODE จาก XML ตรงๆ** (D-MAX/code A → ขึ้น 'เก๋งเอเชีย' ทั้งที่เป็นกระบะ)
    (4) **DAMAGE_LIST ว่าง → ฟอร์มความเสียหายเปล่า** (ต้องกรอกเองอยู่ดี) → import เป็น "ตั้งต้น" ได้ แต่ยังต้องเก็บรายละเอียด
  - **แผน user (2026-06-23): เพิ่มตัวเลือก "นำเข้า XML" เป็น mode** — แก้ปัญหาความเสียหาย >8 ได้แน่นอน
    เพราะ**ฟอร์ม import มี free-text 30 ช่อง** (vs cmdNewReport 8) → ใส่ได้เกิน 8 ไม่ตัน
    - แต่ฟอร์ม import = **ไม่มี checklist** → ใช้ enhancement ติ๊ก checkbox ไม่ได้ (ลง free-text ล้วน — แต่มี 30 ช่อง)
    - ต้องทำเพิ่ม: บอท import → **เติม/แก้ field ที่ import ไม่ครบ** (แยกชื่อ-สกุล/คำนำหน้า/ประเภทรถ) + **เติมความเสียหายลง 30 ช่อง**
    - **2 ทางแก้ >8 ใช้ร่วมกันได้:** (A) cmdNewReport + checklist (8+22≈30, ถ้า match) สำหรับเคสปกติ /
      (B) imbFileImport_XML (free-text ตรงๆ) สำหรับเคสหนัก/ชิ้นแปลกที่ match checklist ไม่ได้
  - **✅ probe flow นำเข้า XML ครบ (2026-06-24, `tools/probe_import_xml.py` — เคลม 2026013144715):**
    กลไกยืนยันหน้าจริง (login→dump DOM สด, --do-upload สร้าง draft จริง):
    1. หน้า `frmMainPage` → `imbFileImport_XML` = **`<input type=image>` ASP.NET ImageButton**
       (onclick ว่าง = submit form ปกติ ไม่เปิด OS dialog) → คลิกแล้ว navigate ไป **`frmFileImportXML.aspx`**
    2. หน้า import มี: `ddlInsurerNameMajor` (bootstrap **selectpicker**, native select ซ่อน tabindex=-98 →
       ต้อง set ด้วย JS: `s.value='1059';dispatchEvent('change');selectpicker('refresh')`),
       `ddlInsurerBRList` (สาขา — โหลด lazy หลังเลือกบริษัท, value=`'1778|25265'`=กรุงเทพ; เลือก option
       ที่ value ขึ้นต้น INSURERBRID),  `inpImport` (**`<input type=file style=display:none>`** ใน `<label>`
       'เลือกไฟล์' → `send_keys` path ตรงได้ ไม่เปิด dialog), `btnImport` (`<input type=button
       style=display:none>` ใน `<label>` 'นำเข้าข้อมูล' → **JS click** `getElementById('btnImport').click()`),
       `txtSRREFID`/`txtFileName` (auto)
    3. หลัง `btnImport` → **SweetAlert (กดปิด .swal-button 'OK')** → navigate ไป **`frmSurvey.aspx`**
       (draft สร้างแล้ว เข้าโหมดแก้ — ปุ่มบันทึก = **`btnUpdate`** ไม่ใช่ btnSave; มี `btnCancel`)
  - **import เติมฟอร์มหลักให้ ~90% (verify dump 2026013144715):** ประเภทเคลม (`rdoSurv_Claim_Type`),
    บริษัทประกัน+สาขา, `txtSurv_JobNo`/`txtAcc_ClaimRef_No`/`txtRef_Claim_No`/`txtPrb_Number`/กรมธรรม์+วันที่/
    `txtAssured_Name`/`txtPolicy_Type`, รถ (ทะเบียน/จังหวัด/`ddlCMFG`=TOYOTA ✓/สี/ตัวถัง), **เพศ
    (`rdoGender_1` ✓ import เติมให้!)**, ผู้ขับขี่ (relation/age/bday/address/จังหวัด/โทร/บัตร/ใบขับขี่/place),
    อุบัติเหตุ (วัน-เวลา/สถานที่/จังหวัด/รายละเอียด/cause/call/surv/reach/finish), **`ddlOpo_Count=1` +
    render บล็อกคู่กรณี `dtlOpo_ctl00` ให้** (ตามจำนวนคู่กรณีใน XML)
  - **import ทิ้งช่องว่าง/ทำพลาด → บอทต้องแก้:** (1) **`rdoHev_Car`** (รถเสียหายหนัก/เบา) ว่างทั้งคู่
    (2) **`ddlDri_Title_ID`** (คำนำหน้า) ว่าง (3) **`txtDri_Name01`** = ชื่อเต็มติดคำนำหน้า (`น.ส.ปฐมาวดี
    ช้ายสนิททำ`), `txtDri_LastName01` ว่าง → ต้อง split (4) **`ddlDri_DistrictID`** (อำเภอผู้ขับขี่) ว่าง
    (จังหวัดเติมแต่อำเภอไม่เติม) (5) **`ddlAcc_DistrictID`** (อำเภอเกิดเหตุ) ว่าง (6) **`ddlLoss_ID`**
    (ลักษณะความเสียหาย) ว่าง (7) **`ddlCType`** = code-based (`เก๋งเอเชีย` จาก CTYPECODE — ผิดสำหรับกระบะ)
    (8) **คู่กรณีทุกฟิลด์ว่าง** (สร้างบล็อกแต่ไม่ลงข้อมูล — `dtlOpo_ctl00_wuOpo_txtOpo_Name/txtDri_Name/
    txtCar_RegNo/ddlCType/ddlHave_Insurance` ฯลฯ ว่างหมด → บอทต้อง fill_third_parties เต็ม)
  - **popup ความเสียหายหลัง import = free-text 20 ช่อง ไม่มี checklist** (แก้ตัวเลขจากที่เคยเดา 30):
    `dgvOtherDamage_List_ctl02..ctl11_wuOtherDamL{A|B}_txtDam_Name` (10 แถว × 2 คอลัมน์ A/B = 20),
    `chbDam_Name` = 0 → enhancement ติ๊ก checklist ใช้ไม่ได้กับ path นี้ (ลง free-text ล้วน แต่ได้ 20 > 8)
  - **✅ สร้าง+verify E2E แล้ว (2026-06-24, mode `--import-xml`):** `emcs.import_xml_report()` (flow ข้อ 1-3)
    → `fill_imported()` reuse fill_* อุด/แก้ช่องว่าง → save `btnUpdate` (`save_main_form(is_new=False)`) →
    `fill_third_parties`/`fill_damage_list` (free-text dynamic)/`fill_injuries`/`fill_assets` → images + billing;
    `main.run_import_xml` (อ่าน scrape เพื่อ XML+คู่กรณี) + webui checkbox "นำเข้าด้วย XML"
    - **fill_damage_list อ่าน slot free-text จาก DOM แบบ dynamic** (`_free_text_slots`): cmdNewReport=8
      (ctl02-05×AB) / import=20 (ctl02-11×AB) — แทน hardcode `ctl0{row}` เดิม (พังเมื่อ row>9: ctl010)
    - **บั๊กที่เจอ+แก้ระหว่าง E2E (สำคัญ — import เฉพาะ):**
      (1) **cascade จังหวัด→อำเภอ:** import เซ็ตจังหวัดไว้แต่ไม่ fire onchange → fill_* เลือกจังหวัดเดิมซ้ำ
      ไม่ fire (Selenium ไม่คลิก option ที่ selected) → อำเภอไม่โหลด → fuzzy_select timeout. แก้ด้วย
      **`_recascade_province`** (บังคับจังหวัด→ว่างผ่าน postback จริง ก่อน `fill_driver`/`fill_accident`
      → เลือกใหม่เป็น 'การเปลี่ยนจริง' → อำเภอโหลด) — เรียก ddlDri_ProvinceID + ddlAcc_ProvinceID
      (2) **เลขที่รับแจ้ง (txtAcc_ClaimRef_No):** import เติมค่า ISURVEY ดิบ '2026097275' (ผิดรูปแบบ
      ไอโออิ ABxxx/xxx) → validation reject; flow ปกติเว้นว่าง=ผ่าน → **เคลียร์ด้วย JS** ก่อน save
      (`set_text(..,'')` ใช้ไม่ได้ — ข้ามค่าว่าง ไม่ลบของเดิม)
    - **✅ E2E verify (เคลม 2026013144715 → draft S68426066006, harness `tools/test_import_xml.py`):**
      import→อุดฟอร์มหลักครบ (ประเภทรถ/เพศ/คำนำหน้า/แยกชื่อ/อำเภอ 2 ตัว/loss)→save btnUpdate ผ่าน→
      คู่กรณี 1+ความเสียหายคู่กรณี 4→ความเสียหายรถประกัน 6 ลง free-text (import ไม่มี checklist)→
      ค่าใช้จ่าย; **ลักษณะความเสียหายเคลมสดยังต้องเลือกเอง** (loss_type — test ใส่ placeholder 'เคลมแห้ง')
    - **ยังไม่ verify (ทำงานได้ตามตรรกะ/unit test):** เคสมีผู้บาดเจ็บ/ทรัพย์สิน (import สร้าง row ให้ไหม?
      fill_injuries/assets สร้างเองได้อยู่แล้ว) · ความเสียหาย >8 จริงเต็ม 20 slot (E2E ทดสอบ 6) ·
      อัปรูปในโหมด import (skip ตอนทดสอบ) · กดส่งจริง (cmdSendNew ผ่าน _offer_submit — shared)
    - **`tools/probe_import_xml.py`** (probe flow, --do-upload สร้าง draft) + **`tools/test_import_xml.py`**
      (harness ทดสอบ run_import ตรง ข้าม se-key gate)
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

### 6.5 ตรวจ/อ่านใบขับขี่ผู้เอาประกัน (`--check-license`) — ⏸️ ทำแล้ว+verify แต่ user ตัดสินไม่เปิดใช้ (2026-06-25)

> **สถานะ:** โค้ดอยู่ครบ ใช้ได้จริง แต่ **user ตัดสินใจไม่เปิดใช้** — เหตุผล: ช้า
> (~2วิ/รูป) + ไม่มีประโยชน์เพราะมีคนมอนิเตอร์ตรวจรูปอยู่แล้ว. **ถอน torch/easyocr
> ออกจาก `runtime/` แล้ว** (ลด deploy size) — โค้ด import easyocr แบบ lazy จึงไม่พัง
> (คืน `{'available': False}`). จะกลับมาใช้ค่อย `pip install -r requirements-ocr.txt`.
> ด้านล่างเก็บไว้เป็นความรู้/ผลทดสอบ เผื่อรื้อมาใช้

**เป้าหมาย (user เลือก):** หาว่าในชุดรูปมีใบขับขี่ไหม ถ้ามี → อ่านรายละเอียด, ใช้ **OCR ในเครื่อง** (เลือก EasyOCR)

**โมดูล `autokey/license_ocr.py`** (เสียบใน `main.check_license` ต่อจาก `prepare_images`
ทั้ง flow scrape + API; gate ด้วย flag `--check-license` / webui checkbox `checklicense`):
- `find_and_read_license(folder)` — สแกนรูปในโฟลเดอร์หลัก (ไม่รวม tp_*) ทีละรูป →
  OCR → ให้คะแนน keyword → คืน fields ของใบที่ใช่
- `_matched_groups` / `license_score` / `is_license_text` — ตรวจว่าเป็นใบขับขี่ด้วย
  **fuzzy match (rapidfuzz partial_ratio ≥80)** เพราะ OCR บัตรเคลือบมัน spell เพี้ยน
  มาก (เช่น 'ประเทศไทย'→'ประเรศไทย', 'KINGDOM OF THAILAND'→'KINGDOM OFFTHATAND').
  4 กลุ่ม keyword: th_license/en_license/country/vehicle — **ต้องเจอกลุ่มใบขับขี่
  (th/en_license) จริงด้วย** ไม่งั้นแค่ ประเทศ+รถยนต์ จะไปชนเล่มทะเบียนรถ
- `parse_license_fields` — regex ดึง: เลขใบขับขี่ (8 หลัก), เลขบัตร ปชช. (13 หลัก
  ยุบช่องว่าง/ข้ามบรรทัด), ชื่ออังกฤษ (เลือกบรรทัดมีคำนำหน้า MR/MISS, รับ lowercase),
  วันออก/หมดอายุ/เกิด (อังกฤษ + ไทย พ.ศ.→ค.ศ.), ประเภทบัตร
- `cross_check(fields, data)` — เทียบเลขใบขับขี่ vs `driver_license_no`, เลขบัตร vs
  `driver_idcard` (ยุบ non-digit) → log ✓ตรง/✗ไม่ตรง
- บันทึกผล `runs/<เคลม>_license.json`

**ผลทดสอบรูปจริง (2026-06-25, รูปใบขับขี่จาก zip export):**
- ✅ ตรวจเจอใบขับขี่ท่ามกลางรูป INS 32 รูป + อ่านได้: เลขใบขับขี่ `67004060`,
  เลขบัตร `1101400724829`, ชื่อ `PHATMARIKA ANYAMANEE`, cross-check ✓✓ ตรง
- ⚠️ **รูป zip export เล็กมาก ~800×450px** → ต้อง **upscale ×2 (INTER_CUBIC) ก่อน OCR**
  (`ocr_image` ปรับ target_dim=1600 อัตโนมัติ) — ไม่ขยายจะอ่านเลขบัตร/ชื่อไม่ออก
- ⚠️ **วันที่/ประเภทบัตร อ่านไม่ค่อยได้** ที่ res นี้ (OCR เพี้ยนเกิน) — เลข 8/13 หลัก
  กับชื่อ ใช้ได้, วันที่เป็น best-effort (มักว่าง)
- ⚠️ **ช้า ~2วิ/รูป** (CPU). early-exit เมื่อเจอใบที่มีเลขใบขับขี่แล้ว แต่ถ้าใบอยู่
  ท้ายๆ/ไม่มีใบ จะสแกนครบ (เช่น 32 รูป ~60-95วิ) → จึง opt-in ปิด default

**ติดตั้ง (เครื่อง dev แล้ว copy runtime\):** `requirements-ocr.txt` —
`pip install torch torchvision --index-url .../whl/cpu` ก่อน แล้ว `pip install easyocr`
(torch CPU เลี่ยง CUDA ที่ใหญ่). โมดูล import easyocr แบบ lazy — ไม่ลง = คืน
`{'available': False}` ไม่ทำให้ flow หลักล้ม

**ยังไม่ทำ (ถ้าจะต่อ):** ตรวจวันหมดอายุ ณ วันเกิดเหตุ (ต้องอ่านวันที่ให้แม่นก่อน —
ติดที่ res รูป), เทียบชื่อไทย OCR vs `driver_name` (fuzzy), เร่งความเร็ว (GPU/จำกัด N รูป)

### 6.6 คำนำหน้าผู้ขับขี่รถประกัน — ✅ ปิดด่านแล้ว 2026-08-04 (ไม่ต้องพึ่ง OCR)

**ปัญหาเดิม:** `ddlDri_Title_ID` เป็นช่องบังคับของ EMCS แต่ ISURVEY ไม่มีให้ตรง ๆ →
บอทหยุดรอคนเลือกแทบทุกเคส (4/6 เคลมที่สุ่มตรวจ)

**สิ่งที่พบจากการยิง API จริง (tab-3 `Driver`):**

| key | ค่า | สรุป |
|---|---|---|
| `drv_title` | `None` / `''` ทุกเคส | มีในโครงสร้าง แต่**ไม่มีใครกรอก** |
| `drv_gender` | `'M'` / `'F'` **มีค่าเสมอ** | ใช้ได้จริง — เดิมโค้ดอ่านถูกแล้วแต่ไม่ได้ใช้ต่อ |
| `drv_name` | `'คุณ พัลลภ ธาดากิจวณิช'` | คำนำหน้ามักติดมาในช่องชื่อ |

**บันได 4 ขั้นที่ใช้ (`emcs._derive_insured_title`) — หยุดที่ขั้นแรกที่ได้ค่า:**

1. คำนำหน้าที่ต้นทางให้มา (`driver_title`) — se-survey กรอกบนมือถือ / ISURVEY ที่แยกจากชื่อ
2. คำนำหน้าติดมากับชื่อผู้ขับขี่
3. ผู้ขับขี่เป็นคนเดียวกับผู้เอาประกัน → ยกคำนำหน้าของผู้เอาประกันมา
4. **เพศ + อายุ** (`claim_data.title_from_gender_age`) — ชาย = `นาย` แน่นอน 100%
   (ผู้ชายไทยมีคำนำหน้าเดียว), เด็ก <15 = `ด.ช./ด.ญ.`, **หญิงผู้ใหญ่ = `คุณ`**
   เพราะแยก นาง/นางสาว จากเพศไม่ได้

**`คุณ` = ค่ากลาง ไม่ใช่คำนำหน้าจริง** (`claim_data.WEAK_TITLES`) — ถ้าเจอจากต้นทาง
จะ**ไม่**หยุดที่ขั้นนั้น แต่เก็บไว้แล้วไปหาหลักฐานที่ดีกว่าต่อ (เจอจริง: ISURVEY เขียน
'คุณ พัลลภ' แต่บัตรจริง + ชื่อผู้เอาประกันคือ 'นาย'). เป็นกติกาเดียวกับที่ใส่ `-`
ในช่อง text บังคับที่ไม่มีข้อมูล — ปล่อยเป็น draft ให้หัวหน้าแก้ตอนตรวจ
(webui ขึ้น warning เมื่อได้ `คุณ`; EMCS มีตัวเลือกนี้จริง: นาย/นาง/นางสาว/ด.ช./ด.ญ./**คุณ**)

**`คุณ` ต้องมีช่องว่างคั่นถึงจะนับเป็นคำนำหน้า** (`_TITLES_NEED_SPACE`) ไม่งั้นกินชื่อจริง
อย่าง 'คุณากร'/'คุณัญญา' หาย

**ผลกับเคลมจริง 6 ใบ — เหลือ 0 ด่าน:** 158841/147939/158857 → `นาย` (จากชื่อผู้เอาประกัน),
058298 → `น.ส.` (จากชื่อผู้ขับขี่), 159949 → `นาย` (จากเพศ), 059072 → `คุณ` (หญิง)

**ยังต้องใช้ OCR ถ้าจะได้ นาง/นางสาว จริง** — ใบขับขี่มีคำนำหน้าไทยชัด และ ISURVEY
มี `lic_no`/`IDcard_no`/`birthdate` อยู่แล้วใช้ล็อกว่ารูปไหนใช่ + ยืนยัน OCR ไม่มั่ว
(ตรวจกับเคลม 158841 ตรงเป๊ะ 3/3) — ดู §6.5 เรื่องเครื่องมือ OCR

### 6.7 ลักษณะความเสียหาย (`ddlLoss_ID`) — ✅ ปิดด่านแล้ว 2026-08-04

**ปัญหาเดิม:** ช่องบังคับของ EMCS ที่ ISURVEY ไม่มี → บอทหยุดถาม**ทุกเคลมที่มีคู่กรณี**

**กุญแจ:** ISURVEY มี 'ลักษณะการเกิดเหตุ' (`acc_type_desc`, master
`list/masterAccType.php` = **58 รายการ** key `acc_typeID`/`accident_type`) ซึ่ง
**ละเอียดกว่า** `ddlLoss_ID` (21 ตัว) และ **บอกทิศอยู่ในตัวคำอยู่แล้ว**:

| ISURVEY | ทิศ | → EMCS |
|---|---|---|
| `เฉี่ยว/เบียดคู่กรณี` (9109) | เราชนเขา | ชนคู่กรณีเสียหาย |
| `คู่กรณีเฉี่ยวชน` (9203) | เขาชนเรา | ถูกคู่กรณีชน |

→ **ไม่ต้องใช้ 'ผลคดี' มาช่วยแยกทิศ** (สมมติฐานแรกผิด — ตรวจ master แล้วเจอว่า
ISURVEY แยกโค้ด 91xx = รถประกันชน / 92xx = คู่กรณีชน ไว้ให้แล้ว)

**ตาราง `autokey/loss_type_map.py`** — แปลง **34/58**, จงใจไม่แปลง **24** พร้อมเหตุผล
ราย item ใน `ACC_TYPE_UNMAPPED` (คละสาเหตุ เช่น 'ชนคู่กรณีและถูกชน' / EMCS ไม่มีคู่
เช่น 'ตกหลุม', 'ชนสัตว์'). แปลงไม่ได้ → `''` แล้วหยุดถามเหมือนเดิม —
**เดาผิดแย่กว่าถาม** เพราะช่องนี้กำหนดแนวทางจ่ายสินไหม

เทสคุมไว้: ค่าปลายทางต้องอยู่ใน `EMCS_LOSS_TYPES` (21 ตัวจริง) ทุกตัว ·
ห้ามคำซ้ำระหว่างสองตาราง · สองตารางรวมกันต้องครบ 58 พอดี
(verify กับ master จริง: ขาด 0 เกิน 0)

**'เคลมแห้ง' มาจากประเภทเคลมที่ ISURVEY แจ้ง ไม่ใช่เดาจากจำนวนคู่กรณี** (user ชี้ 2026-08-04)

ISURVEY มี **2 ฟิลด์ชื่อคล้ายกันมาก อย่าสับสน** (ทั้งคู่มีคำว่า 'เคลมแห้ง' ใน master):

| ฟิลด์ | master | ค่า | เก็บที่ |
|---|---|---|---|
| `claim_MtypeID` | `masterClaimMType` (4) | 01 เคลมสด · **02 เคลมแห้ง** · 03 ติดตาม · 04 เจรจาสินไหม | `claim_type` ← **ตัวนี้คือ "ประเภทเคลม"** |
| `claim_typeID` | `masterClaimType` (2) | 01 ส่งพนักงาน · 02 เคลมแห้ง | `pay_type` (ประเภทการจ่าย) |

`resolve_loss_type` จึงเป็น: `claim_MtypeID == 2` → `เคลมแห้ง` · ที่เหลือ → ตารางแปลง ·
แปลงไม่ได้ → หยุดถาม (ถ้าอ่านประเภทเคลมไม่ติดเลย ค่อยถอยไปใช้เกณฑ์เดิม 'ไม่มีคู่กรณี')

เกณฑ์เดิม (`not third_parties → เคลมแห้ง`) เป็นแค่ตัวแทนที่พลาดได้ — เคลมสดที่ยัง
ไม่ระบุคู่กรณี (ชนแล้วหนี / ยังหาตัวไม่เจอ) จะถูกมองเป็นเคลมแห้งทั้งที่ ISURVEY บอกว่าไม่ใช่

⚠️ ป้าย `CLAIM_TYPE_NAMES` เดิมผิด 2 ตัว — 03 เคยเขียน 'เคลมนัดหมาย', 04 เคยเขียน
'งานติดตาม' ของจริงคือ **03 ติดตาม / 04 เจรจาสินไหม** (แก้แล้ว + เทสล็อกไว้)

**ผลกับเคลมจริง 6 ใบ — แปลงได้หมด 0 ด่าน:**

| เคลม | ประเภทเคลม | ลักษณะการเกิดเหตุ | → ddlLoss_ID |
|---|---|---|---|
| 059072 | เคลมสด | ถอยชนคู่กรณี | ชนคู่กรณีเสียหาย |
| 058298 / 159949 | เคลมสด | เฉี่ยว/เบียดคู่กรณี | ชนคู่กรณีเสียหาย |
| 147939 | **ติดตาม** | คู่กรณีเฉี่ยวชน | ถูกคู่กรณีชน |
| 158841 / 158857 | **เคลมแห้ง** | ชนวัสดุ/สิ่งของ · ตกหลุม | เคลมแห้ง |

### 6.7.5 ปุ่มบันทึกของ EMCS + วงจรชีวิตเรื่อง (เสียเวลาไล่หลายชั่วโมง — อ่านก่อนแก้)

**ชุดปุ่มเปลี่ยนตามสถานะเรื่อง** — เช็คเสมอว่ากำลังอยู่สถานะไหนก่อนจะกดอะไร

| สถานะ | ปุ่มที่โชว์ | id | ด่านฝั่ง JS |
|---|---|---|---|
| ยังไม่บันทึก (สร้างใหม่) | บันทึก | `btnSave` | `validForm()` เข้ม |
| ยังไม่บันทึก | บันทึกฉบับร่าง | `btnSaveDraft` | `noValid()` หลวม |
| บันทึกแล้ว | ยกเลิก / **แก้ไข** | `btnCancel` / `btnUpdate` | — |

- **เลข e-Survey เกิดตอนกด "บันทึก" (`btnSave`) เท่านั้น** — "บันทึกฉบับร่าง" เซฟสิ่งที่พิมพ์
  ไว้กันหาย แต่**ไม่สร้างเลข ไม่เปิดเรื่อง** ปุ่มความเสียหายยังล็อกอยู่ บอทเดินต่อไม่ได้
- **ตราบใดที่ยังไม่กด "ส่งงานใหม่" ที่หน้าค่าใช้จ่าย ยังกด "แก้ไข" แก้ข้อมูลได้เรื่อย ๆ**
  (กติกา user 2026-08-04) → เรื่องที่กรอกค้างไม่ต้องยกเลิกทิ้ง ใช้ `--fill-existing` กรอกต่อได้

**บทเรียนจากการไล่บั๊กครั้งนี้ (เคลม 2026013059072):** อาการคือ "กดบันทึกแล้วเงียบสนิท
ไม่มีทั้ง alert และ postback" แล้วผมไล่ผิดทาง 2 รอบ — เดาว่าเป็น validation ไม่ผ่าน
(ช่อง 'การเรียกร้องค่าเสียหายจากคู่กรณี') แล้วเดาว่าเป็น `btnSaveDraft` ที่ควรใช้แทน
**ทั้งคู่ผิด** ความจริงคือ **จังหวะกด**: บอทกดปุ่มในวินาทีเดียวกับที่เพิ่งเลือก dropdown
ที่มี postback (`ddlLoss_ID`) คลิกเลยหลุดก่อน handler ทำงาน

ตัวตัดสินคือ **user กดปุ่มเดียวกัน ข้อมูลชุดเดิม หน้าเดิม แล้วผ่านทันที** — เมื่อคนทำได้
แต่บอททำไม่ได้ ให้สงสัย "วิธี/จังหวะที่บอทกด" ก่อนสงสัยข้อมูล
(แก้ที่ `_click_save_button`: รอหน้านิ่ง → กด → ยืนยันว่าปุ่มถูก disable/เปลี่ยนข้อความ)

**⚠️ ยังไม่หายบนเส้น `--fill-existing` (เจอสดอีก 2026-08-05, เคลมเดิม draft S68426080794):**
`_click_save_button` แก้เส้น `btnSave` (สร้างใหม่) ได้ แต่พอกด **`btnUpdate`** ของเรื่องที่
บันทึกแล้ว ยังเงียบเหมือนเดิม — บอทลองซ้ำ 33 วิ แล้วยอมแพ้ 2 รอบติด ต้องให้คนกดปุ่มเอง
บนหน้าจอถึงจะผ่าน

→ **นี่คือคอขวดจริงของงาน ไม่ใช่เรื่อง UI**: จากจุดหยุดทั้งหมด 3 ครั้งในรอบนั้น
2 ครั้งเป็นปุ่มบันทึกเงียบ เหลือแค่ 1 ครั้งที่เป็น "ข้อมูลต้นทางไม่มี" จริง ๆ

#### ทำไมมันเงียบ — อ่านจากซอร์ส EMCS แล้ว (2026-08-05)

ดัมป์ `runs/logs/error_emcs_2026013059072_20260804_225233.html` มาแกะ onclick ของปุ่ม:

```js
onclick="if (typeof(Page_ClientValidate) == 'function') {…}
         if (validForm() == false) { return false; }      ← ตายตรงนี้ เงียบสนิท
         this.value='Please wait...'; this.disabled=true;
         __doPostBack('btnUpdate','');"
```

`validForm()` = `if (vlidSurvey() == true) { เช็ค format → AlertSummary ถ้าพัง } else { return false }`
และบรรทัดสุดท้ายของ `vlidSurvey()` คือ

```js
//AlertSummary(strJoinText, objControlName, 'R');   //--- 'R' = Require Field ---
```

**คนเขียน EMCS คอมเมนต์บรรทัดเตือนทิ้งไว้** — ช่องบังคับขาดเมื่อไหร่ ปุ่มถูกปัดตกโดย
ไม่ขึ้นข้อความสักตัวอักษร (หน้านี้ **ไม่มี ASP.NET validator** เลย `Page_Validators` = 0
จึงไม่มีดอกจันแดงให้ดูด้วย)

> ⚠️ **แต่กับดักนี้ไม่ใช่สาเหตุของเคส 2026013059072** — ทดสอบแล้ว ดูหัวข้อถัดไป
> มันเป็นกับดัก "ที่รออยู่" ต่างหาก: วันไหนช่องบังคับขาดจริง EMCS จะเงียบแบบไม่มี
> เบาะแสเลย ตัว `_read_validform` มีไว้รับเคสนั้น

ของที่เราอยากได้เวลามันเงียบ**ยังค้างอยู่ในตัวแปร global**:

| ตัวแปร | เก็บอะไร |
|---|---|
| `strJoinText` | ชื่อช่องที่ขาด ต่อกันด้วย `,` (มี `,` ปิดท้ายเสมอ) |
| `objControlName` | **id ของช่องแรกที่ผิด** ← เอาไปตีกรอบแดงได้เลย |

**สิ่งที่ทำ (`_read_validform`):** เรียก `validForm()` เองแล้วอ่าน 2 ตัวแปรนี้ออกมา →
- ข้อความหยุดรอเปลี่ยนจาก *"validForm() คืน false โดยไม่บอกเหตุผล"* เป็นรายชื่อช่องจริง
  (ขึ้นบรรทัดต่อข้อ = รูปแบบเดียวกับ alert ของ EMCS → `_missing_field_list` แยกต่อได้)
- ส่ง `objControlName` เป็น `focus_ids` ให้ §6.9 ตีกรอบแดงที่ช่องนั้นบนหน้าจอ
- **ไม่กดซ้ำ** เมื่อรู้ว่าช่องบังคับขาด (เดิมวนกด 3 ครั้ง × รอบละ ~11 วิ ฟรี ๆ)
- ถ้า `validForm()` ผ่านแล้วแต่คลิกยังไม่ติดครบ 3 ครั้ง → **ยิง `__doPostBack` ตรง**
  แบบเดียวกับที่ onclick ทำทุกบรรทัด (ยังผ่านด่าน `validForm()` ก่อนเสมอ ไม่ข้ามด่านตรวจ)

⚠️ ตัวแปรพวกนี้เป็น global ของหน้า EMCS — ถ้าวันหนึ่งเขาแก้สคริปต์ ให้ดัมป์ HTML มาแกะใหม่
อย่าเดา

#### เทสสาเหตุแบบไม่ต้องแตะ EMCS — เสิร์ฟ HTML ที่เซฟไว้แล้วรัน JS จริง (2026-08-05)

`driver.page_source` ที่ `save_debug_snapshot` เก็บไว้ = หน้า EMCS ทั้งหน้า **พร้อมสคริปต์
inline** เอามาเสิร์ฟ localhost แล้วเปิดในเบราว์เซอร์ เรียก `validForm()` ได้จริงเลย
(ไฟล์ JS ภายนอกไม่ติดมา → `CheckRadioBtnValid` / `moment` หาย จำลองเองได้)

ผลกับ snapshot ตอนที่บอทกดแล้วเงียบ (`error_emcs_2026013059072_20260804_225233.html`):

| ตรวจ | ผล |
|---|---|
| `validForm` / `vlidSurvey` / `strJoinText` / `objControlName` / `__doPostBack` | มีครบ อ่านได้จาก `window` ✓ |
| `vlidSurvey()` | **ผ่าน** (เดินต่อจนชน `moment`) = ช่องบังคับ**ไม่ได้ขาด** |
| `AlertSummary()` | ใช้ `alert()` ธรรมดา → ถ้า format พังบอท**เห็นแน่** ไม่ใช่ป๊อปอัปในหน้า |
| `validForm()` โยน error | `_read_validform` คืน `ok=None` + เก็บข้อความไว้ ไม่ crash ✓ |
| ยิง `__doPostBack` ตรง | ปุ่ม `แก้ไข`→`Please wait...`+disabled, `__EVENTTARGET=btnUpdate`, form submit ✓ |

**สรุปสาเหตุจริงของเคสนี้: คลิกไม่ถึง onclick** (ข้อมูลครบ ด่านตรวจผ่านหมด) ตรงกับที่
`_diagnose_save_click` รายงานไว้ตั้งแต่แรกว่า *"validForm() ผ่านตอนเรียกเอง แต่กดปุ่มแล้ว
ไม่มีอะไรเกิดขึ้น"* → **ตัวแก้ที่ตรงเป้าคือเส้นยิง `__doPostBack` ตรง** ส่วน `_read_validform`
เป็นตาข่ายรับกับดัก AlertSummary ที่ยังไม่เคยเกิด

#### ตัวดักคลิก — ครั้งหน้าที่เงียบ ต้องบอกได้เองว่าเงียบตรงไหน

ยัง**ไม่รู้ว่าทำไมคลิกถึงไม่ถึง** และบังคับให้เกิดตอนรันจริงไม่ได้ (เกิดไม่สม่ำเสมอ —
บอทกดไม่ติด 2 รอบ แต่คนกดปุ่มเดียวกันบนหน้าเดียวกันผ่านทันที) จึงติดตัวดักไว้บนปุ่ม
**ก่อนคลิกทุกครั้ง** (`_arm_click_probe`) แล้วอ่านทีหลัง (`_read_click_probe`):

| `got` | `ran` | อื่น ๆ | แปลว่า |
|---|---|---|---|
| false | – | `over` = ชื่อ element | **มีอะไรทับจุดที่คลิก** (overlay/spinner ของ postback) |
| false | – | `over` ว่าง | ปุ่มเลื่อน/ถูกวาดใหม่ระหว่างคลิก |
| true | false | – | event ถึงปุ่มแล้วแต่ onclick ไม่ทำงาน (handler หลุดจาก DOM) |
| true | true | `err` | onclick พังกลางทาง (JS error) |
| true | true | `ret` = false | onclick ปัดตกเอง = `validForm()` false → ข้อมูลไม่ครบ |
| true | true | `ret` ≠ false | onclick ผ่านหมดแต่ postback ไม่ออก |

ตัวห่อ onclick **คืนค่าเดิมเสมอ** → พฤติกรรมปุ่มไม่เปลี่ยน (`return false` ยังยกเลิก submit ได้)
และวัด `elementFromPoint` **หลัง** `scrollIntoView` เพราะ selenium ก็เลื่อนก่อนคลิก
(ไม่งั้นได้ `(นอกจอ)` หลอกทุกครั้งที่ปุ่มอยู่ใต้ fold)

verify กับหน้า EMCS จริง (เสิร์ฟ snapshot แล้วคลิกในเบราว์เซอร์) ครบทุกทาง: ปกติ /
เอา div โปร่งใสทับ → `DIV#fakeOverlay` / `validForm` คืน false / โยน JS error

### 6.8 `tools/dry_stops.py` — ดูว่าบอทจะหยุดตรงไหน โดยไม่แตะ EMCS

`--read-only` อ่าน ISURVEY อย่างเดียวเลยไม่เห็นจุดหยุดฝั่ง EMCS ส่วนรันจริงก็สร้าง
draft บนระบบบริษัทประกันซึ่ง**ลบไม่ได้ ยกเลิกได้อย่างเดียว** สคริปต์นี้อยู่ตรงกลาง:
อ่าน ISURVEY จริง → เอา**ฟังก์ชันตัดสินใจตัวเดียวกับที่บอทใช้** (`resolve_loss_type`,
`_derive_insured_title`, `resolve_gender`, `normalize_brand`, ตรรกะผลคดี) มารันกับ
**ตัวเลือกจริงของ EMCS** ใน `runs/emcs_spec.json` แล้วบอกว่าช่องไหนจะผ่าน/จะหยุด

```powershell
runtime\python.exe tools\dry_stops.py 2026013059072
runtime\python.exe tools\dry_stops.py 2026013147939 SEABI-312260600389
```

**ข้อจำกัดที่ต้องรู้ (เขียนไว้ในสคริปต์ด้วย):**
- dropdown ที่ **ลิสต์ถูกกรองตามช่องก่อนหน้า** (อำเภอ←จังหวัด, ยี่ห้อ←ประเภทรถ)
  สเปกเป็น snapshot ของชุดเดียว → fuzzy ข้ามชุดไม่มีความหมาย จึงคืน ❔ ไม่ใช่ ✅/⛔
  (เคยรายงานผิดตอนแรก: `'เมืองชลบุรี' → 'อำเภอธัญบุรี' score 52` ขึ้น ✅ ทั้งที่คนละจังหวัด)
- จุดที่ **EMCS เป็นคนตัดสินตอนกดบันทึก** (validation ฟ้อง / ตัวเลือกไม่โหลดเพราะ
  cascade race / ช่องบังคับก่อนเข้าหน้าค่าใช้จ่าย) ทำนายล่วงหน้าไม่ได้เลย

**ผลรัน 6 เคลมจริง (2026-08-04): จุดหยุดที่ทำนายได้ = 1 จาก 6 ใบ** —
เคลม 059072 `vehTID` ของคู่กรณีว่างในต้นทาง (พนักงานไม่ได้กรอกประเภทรถคู่กรณี)
ไม่ใช่บั๊กของ mapping — ตรวจ raw tab-4 ยืนยันแล้ว

### 6.8.5 ตั้งค่าคนคีย์ + สมุดงาน + ปิดการ์ดเอง — ✅ 2026-08-05

**คนคีย์ (`settings/keyers.json`)** — ชื่อที่ส่งไปกับการแจ้ง ISURVEY (`EMCSby`) มาจาก
**เลขท้ายของเลขเคลม** อย่างเดียว ไม่ได้ดูว่าใครนั่งกดปุ่ม (คนละ 2 เลข: 0-1, 2-3, …)
เดิม hard-code ใน `isurvey_report.py` → ย้ายมาไฟล์ แก้จากแท็บ **⚙ ตั้งค่า** ได้
- `keyer_for()` อ่านไฟล์**ทุกครั้ง** → แก้แล้วมีผลกับงานถัดไปทันที ไม่ต้องรีสตาร์ต
- ไฟล์หาย/พัง/ไม่ครบ → ถอยไป `DEFAULT_KEYERS` ในโค้ดทีละเลข (งานต้องไม่ล้มเพราะไฟล์ตั้งค่า)
- POST `/settings` บังคับให้มีชื่อครบ 10 เลข และรับเฉพาะจากหน้า operator ในเครื่อง
  (ไม่รับ cross-origin) — บอทไม่ยิงแจ้ง ISURVEY ถ้าหาคนคีย์ไม่ได้อยู่แล้ว

**สมุดงาน (`runs/jobs.jsonl`)** — JSONL append อย่างเดียว บันทึก 2 จังหวะ:
`draft` (กรอกครบ) และ `sent` (กด "ส่งงานใหม่" + **verify สถานะบน EMCS ผ่านแล้ว**)
บันทึกก่อนยิง ISURVEY/se-key เพราะ "ส่งบน EMCS แล้ว" เป็นข้อเท็จจริงที่ต้องเก็บ
ต่อให้ 2 ระบบหลังยิงพลาด · บรรทัดเสียบางบรรทัดไม่ทำให้ทั้งไฟล์อ่านไม่ได้
· `runs/` อยู่ใน .gitignore = สมุดของแต่ละเครื่อง ไม่ปนกัน

**ปิดการ์ดเอง** — `browser.announce_sent()` พิมพ์ marker `@@JOB_SENT@@` หลัง verify
→ webui เก็บใส่ `run["sent"]` → หน้าเว็บนับถอยหลัง 8 วิแล้วเรียก `/forget` ให้
⚠️ ต้อง `return` ออกจาก `renderRun` เมื่อ `c.autoClose` แล้ว ไม่งั้น poll (1.2 วิ)
วาดสถานะทับ ข้อความกะพริบสลับ "ปิดใน N วิ" กับ "เสร็จแล้ว ✅" (เจอตอนเทส แก้แล้ว)

### 6.9 ชี้ช่องที่ต้องแก้บนหน้า EMCS (ตีกรอบแดง/ย้อมเหลือง) — ✅ 2026-08-05

ปัญหาเดิม: บอทหยุดแล้วบอกได้แค่ **"ชื่อช่อง"** เป็นข้อความใน log/หน้าเว็บ คนต้องไป
ไล่หาเองว่าอยู่ตรงไหนในฟอร์ม EMCS ที่ยาวเป็นหน้าจอ ๆ ยิ่ง alert ของ EMCS กดตกลง
แล้วหายไปเลย อ่านย้อนไม่ได้ว่ามันฟ้องอะไรบ้าง

ตอนนี้ยิง CSS/JS เข้าไปในหน้า EMCS ตรง ๆ (`autokey/browser.py`):

| ฟังก์ชัน | ทำอะไร |
|---|---|
| `highlight_wait(driver, ids, title, reason, labels=…)` | ตีกรอบ**แดงกระพริบ**ที่ช่อง + เลื่อนจอไปหา + แถบแจ้งเตือนมุมล่างขวา (ค้างไว้ กดปิดเองได้) + เปลี่ยน title เป็น `⏸️ …` ให้เห็นบน taskbar |
| `bring_to_front(driver)` | ดึงหน้าต่าง Chrome ขึ้นหน้า (CDP `Page.bringToFront`) |
| `mark_check(driver, id, note)` | ย้อม**เหลือง**ช่องที่บอทเดาแบบคะแนนต่ำ + ใส่ tooltip — ค้างไว้ให้คนตรวจก่อนกดส่งงาน |
| `highlight_clear(driver)` | เก็บกรอบแดง+แถบ (เหลืองยังอยู่) |

จุดที่ต้องรู้:
- **`labels=` คือของสำคัญ** — validation ของ EMCS ฟ้องมาเป็น "ชื่อช่อง" ไม่ใช่ id
  (`1. สถานที่เกิดเหตุ`) JS จึงหา cell ที่ข้อความ**เกือบเท่ากับชื่อช่องพอดี** (กัน
  ไปเจอ `<td>` ที่ครอบทั้งหน้า) แล้วไล่ `nextElementSibling` หา input/select ตัวแรก
  — ฟอร์ม EMCS เป็นตาราง ป้ายกับช่องอยู่คนละ `<td>` ในแถวเดียวกัน
  ทดสอบกับหน้าจำลองโครงสร้างเดียวกันแล้ว: แถวที่มี 2 คู่ (จังหวัด|เขต) ชี้ถูกช่อง
- **ทุกอย่างหายเมื่อหน้า postback** — ตั้งใจ ไฮไลต์คือป้ายชั่วคราว ไม่ใช่ state
- ยิง JS ผ่าน `_safe_js` ที่**กลืน error ทุกชนิด** (browser ปิดไปแล้ว / มี alert ค้าง /
  หน้าเปลี่ยน) — ของแถมพังได้ แต่ห้ามทำให้การหยุดรอพัง
- มี dropdown ให้เลือกบนหน้าเว็บอยู่แล้ว → **ไม่**ดึง Chrome ขึ้นหน้า (แย่งโฟกัสจาก
  หน้าที่ผู้ใช้กำลังจะกด) แต่ยังตีกรอบแดงไว้ให้

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
#        --import-xml (นำเข้า XML) --check-license (ตรวจใบขับขี่ด้วย OCR)

# ก่อนแก้โค้ดทุกครั้ง
python test_smoke.py                                # ~50 tests ไม่เปิด browser

# เครื่องมือสำรวจ (ใช้เมื่อเว็บเปลี่ยนหรือทำฟีเจอร์ใหม่ — อ่านอย่างเดียวทั้งหมด)
python tools\dump_tabs.py --claim <เคลม>           # dump field ทุก tab ISURVEY
python tools\probe_tabs456.py --claim <เคลม>       # diff id + tab bar + context menu
python tools\probe_emcs.py                          # dump ฟอร์มสร้างงาน EMCS (ไม่บันทึก)
python tools\probe_opo_unlock.py                    # เช็คเงื่อนไขปลดล็อกส่วนคู่กรณี
python tools\probe_mainpage.py                      # dump หน้ารายการงาน EMCS
python tools\probe_license_ocr.py <รูป>             # ลอง preprocess OCR ใบขับขี่ (upscale/CLAHE)
python -m autokey.license_ocr <รูป|โฟลเดอร์>        # ทดสอบ detect+อ่านใบขับขี่
# ผล dump เก็บใน runs/*.json — discovery เดิมยังอยู่ ใช้อ้างอิง id ได้เลย
```

**วิธี debug เมื่อพัง**: ดู `runs/logs/run_*.log` (มีทุกอย่างรวม alert text) +
`error_*.png/.html` (สภาพหน้า ณ วินาทีพัง — HTML ใช้แกะ id/validation ได้)
