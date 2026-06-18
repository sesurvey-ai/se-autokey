"""Smoke test — ทดสอบส่วนที่ไม่ต้องเปิด browser
รัน: python test_smoke.py
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(name)


# ---- 1. import ทุกโมดูล ----
from autokey import browser, claim_data, config, emcs, images, isurvey  # noqa: E402
from autokey.processing import process_images_pro, natural_sort_key  # noqa: E402
check("import ทุกโมดูลใน autokey", True)

import processing as root_shim  # noqa: E402
check("shim processing.py ที่ root ใช้ได้",
      root_shim.process_images_pro is process_images_pro)

# ---- 2. config / .env ----
cfg = config.load_config()
check("โหลด .env ครบ 4 ค่า",
      all([cfg.isurvey_username, cfg.isurvey_password,
           cfg.emcs_username, cfg.emcs_password]))
check("download_dir ชี้ในโปรเจกต์", cfg.download_dir.name == "downloaded_images")
check("template มีอยู่จริง", cfg.template_path.exists(), str(cfg.template_path))

# ---- 3. แปลงวันที่ พ.ศ. ----
check("ค.ศ. → พ.ศ.", browser.to_buddhist_date("24/10/2024") == "24/10/2567")
check("พ.ศ. อยู่แล้วไม่บวกซ้ำ", browser.to_buddhist_date("24/10/2567") == "24/10/2567")
check("วันที่ว่าง → ''", browser.to_buddhist_date("") == "")
check("split_hhmm", browser.split_hhmm("09:35") == ("09", "35"))
check("split_hhmm ว่าง", browser.split_hhmm("") == ("", ""))
check("today_buddhist เป็น พ.ศ.", int(browser.today_buddhist().split("/")[2]) > 2560)

# ---- 4. ClaimData save/load ----
d = claim_data.ClaimData(
    claim_value="2026013105763", invoice_value="SEABI-213260100295",
    claim_type="1", insure_plate="กข1234",
    damage=["กันชนหลังซ้าย", "ฝากระโปรงหลัง"],
    type_damage=["ครูด", "บุบ"], rank_damage=["B", "C"],
)
p = cfg.runs_dir / "_test_smoke.json"
d.save(p)
d2 = claim_data.ClaimData.load(p)
check("ClaimData save/load round-trip", d == d2)
check("summary แสดงผลได้", "2026013105763" in d.summary())
p.unlink()

# ---- 5. fuzzy mapping ผลคดี (เทสบั๊กที่แก้) ----
from rapidfuzz import process, fuzz  # noqa: E402

cases = {
    "รถประกันเป็นฝ่ายผิด": "rdoAcc_Cause00",
    "รถคู่กรณีเป็นฝ่ายผิด": "rdoAcc_Cause01",          # เคสบั๊กเดิม: ไม่เคยถูกคลิก
    "รถคู่กรณีเป็นฝ่ายผิด คู่กรณีคันที่ 1": "rdoAcc_Cause01",
    "ประมาทร่วม": "rdoAcc_Cause02",
    "รอสรุปผลคดี": "rdoAcc_Cause03",
    "ยกเลิกการเคลม": "rdoAcc_Cause05",
}
for text, expect in cases.items():
    best = process.extractOne(text, list(emcs.CAUSE_RADIO.keys()), scorer=fuzz.WRatio)
    got = emcs.CAUSE_RADIO[best[0]]
    check(f"ผลคดี '{text}' → {expect}", got == expect, f"match='{best[0]}'")

# ---- 6. damage grid layout (id ของ 8 ช่อง) ----
expected_prefixes = [
    "dgvOtherDamage_List_ctl02_wuOtherDamLA_",
    "dgvOtherDamage_List_ctl03_wuOtherDamLA_",
    "dgvOtherDamage_List_ctl04_wuOtherDamLA_",
    "dgvOtherDamage_List_ctl05_wuOtherDamLA_",
    "dgvOtherDamage_List_ctl02_wuOtherDamLB_",
    "dgvOtherDamage_List_ctl03_wuOtherDamLB_",
    "dgvOtherDamage_List_ctl04_wuOtherDamLB_",
    "dgvOtherDamage_List_ctl05_wuOtherDamLB_",
]
actual = []
for c in range(8):
    col = "A" if c < 4 else "B"
    row = 2 + (c % 4)
    actual.append(f"dgvOtherDamage_List_ctl0{row}_wuOtherDamL{col}_")
check("damage grid id ตรงกับ notebook เดิมทั้ง 8 ช่อง", actual == expected_prefixes)

# ---- 7. natural sort ลำดับรูปอัปโหลด ----
files = ["รูปรถประกัน10.jpg", "1.jpg", "รูปรถประกัน2.jpg", "รูปรถประกัน3.jpg"]
check("เรียงรูป 1 → 2 → 3 → 10",
      sorted(files, key=natural_sort_key)
      == ["1.jpg", "รูปรถประกัน2.jpg", "รูปรถประกัน3.jpg", "รูปรถประกัน10.jpg"])

# ---- 8. archive_old_images ----
import tempfile, pathlib  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    (tmp / "a.jpg").write_bytes(b"x")
    (tmp / "b.jpg").write_bytes(b"x")
    images.archive_old_images(tmp)
    moved = list((tmp / "_old").rglob("*.jpg"))
    remaining = [f for f in tmp.iterdir() if f.is_file()]
    check("archive ย้ายรูปเก่าครบ ไม่ลบทิ้ง", len(moved) == 2 and not remaining)
    check("list_images ไม่นับโฟลเดอร์ _old", images.list_images(tmp) == [])

# ---- 9. แตก zip export (ใช้ไฟล์ตัวอย่างจริงในโปรเจกต์) ----
sample_zip = pathlib.Path("export_2025013073980_202510271456.zip")
if sample_zip.exists():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        counts = images.extract_zip_images(sample_zip, tmp)
        check("แตก zip นับหมวดถูก (INS 48 + REPORTS 46 + OTHERS 1)",
              counts == {"INS": 48, "REPORTS": 46, "OTHERS": 1}, str(counts))
        check("PDF ไม่หลุดมา", not list(tmp.rglob("*.pdf")))
        check("ไม่มีหมวด TP_VEH = ไม่มีโฟลเดอร์ tp_veh",
              not (tmp / "tp_veh").exists())
else:
    print("[SKIP] ไม่มีไฟล์ zip ตัวอย่าง")

# zip ที่มีรูปรถคู่กรณี (ได้จากการรันจริง) — TP_VEH ต้องแยกโฟลเดอร์
tp_zips = list(pathlib.Path("downloaded_images").glob("*/_zip/export_*.zip"))
if tp_zips:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        counts = images.extract_zip_images(tp_zips[0], tmp)
        if counts.get("TP_VEH"):
            tp_files = list((tmp / "tp_veh").glob("*.jpg"))
            check("รูปรถคู่กรณีแยกไว้ใน tp_veh/",
                  len(tp_files) == counts["TP_VEH"],
                  f"{len(tp_files)} vs {counts}")
            check("list_images ไม่นับรูปคู่กรณี",
                  len(images.list_images(tmp))
                  == sum(v for k, v in counts.items() if k != "TP_VEH"))

# ---- 8.5 archive ย้าย tp_veh/ ด้วย (กันรูปคู่กรณีสะสมเป็น _2/_3) ----
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    (tmp / "a.jpg").write_bytes(b"x")
    (tmp / "tp_veh").mkdir()
    (tmp / "tp_veh" / "opo1.jpg").write_bytes(b"o")
    images.archive_old_images(tmp)
    check("archive: ย้าย tp_veh/ เข้า _old ด้วย",
          (list((tmp / "_old").rglob("tp_veh/opo1.jpg")) != [])
          and not (tmp / "tp_veh").exists())

# ---- 8.6 รูปรถคู่กรณี: dedup เนื้อหา + แบ่งชุดตามคัน (rename=False = ไม่แตะดิสก์) ----
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    tp = tmp / "tp_veh"
    tp.mkdir()
    # 3 ไฟล์ แต่ a กับ a_2 เนื้อหาเดียวกัน (จำลองการโหลดทับเป็น _2)
    (tp / "a.jpg").write_bytes(b"AAA")
    (tp / "a_2.jpg").write_bytes(b"AAA")
    (tp / "b.jpg").write_bytes(b"BBB")
    deduped = emcs._dedup_images([tp / "a.jpg", tp / "a_2.jpg", tp / "b.jpg"])
    check("dedup รูปซ้ำตามเนื้อหา (3→2, เก็บตัวชื่อสั้นก่อน)",
          [p.name for p in deduped] == ["a.jpg", "b.jpg"], str(deduped))

    b1 = emcs._opponent_image_batches(tmp, 1, rename=False)
    check("opo batches: 1 คัน = 1 ชุด 'คันที่1' รูป dedup",
          len(b1) == 1 and b1[0][0] == "รูปรถคู่กรณี คันที่1"
          and len(b1[0][1]) == 2, str(b1))
    b0 = emcs._opponent_image_batches(tmp, 0, rename=False)
    check("opo batches: นับไม่ได้ก็ยังอัปเป็นคันที่1",
          len(b0) == 1 and b0[0][0] == "รูปรถคู่กรณี คันที่1", str(b0))

# 2 คัน แยกตามชื่อโฟลเดอร์คัน (prefix ก่อน '_') → คันที่1/คันที่2
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    tp = tmp / "tp_veh"
    tp.mkdir()
    (tp / "car1_x.jpg").write_bytes(b"1X")
    (tp / "car1_y.jpg").write_bytes(b"1Y")
    (tp / "car2_z.jpg").write_bytes(b"2Z")
    b2 = emcs._opponent_image_batches(tmp, 2, rename=False)
    labels = [lbl for lbl, _ in b2]
    check("opo batches: 2 คันแยกตามโฟลเดอร์ → คันที่1/คันที่2",
          labels == ["รูปรถคู่กรณี คันที่1", "รูปรถคู่กรณี คันที่2"]
          and len(b2[0][1]) == 2 and len(b2[1][1]) == 1, str(b2))

# ไม่มีโฟลเดอร์ tp_veh = ไม่มีชุดคู่กรณี
with tempfile.TemporaryDirectory() as tmp:
    check("opo batches: ไม่มี tp_veh = []",
          emcs._opponent_image_batches(pathlib.Path(tmp), 1) == [])

# ---- 8.6.1 rename รูปคู่กรณี → 'รูปรถคู่กรณีคันที่N_ลำดับ.jpg' (แตะดิสก์จริง) ----
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    tp = tmp / "tp_veh"
    tp.mkdir()
    (tp / "1781_aaa.jpg").write_bytes(b"P1")
    (tp / "1781_aaa_2.jpg").write_bytes(b"P1")        # ซ้ำเนื้อหา → ย้าย _dup
    (tp / "1781_bbb.jpg").write_bytes(b"P2")
    (tp / "undef_ccc.jpg").write_bytes(b"P3")
    b = emcs._opponent_image_batches(tmp, 1)           # rename=True (default)
    names = sorted(p.name for p in b[0][1])
    check("rename: 1 คัน → 'รูปรถคู่กรณีคันที่1_N.jpg' ไล่ลำดับ",
          names == ["รูปรถคู่กรณีคันที่1_1.jpg", "รูปรถคู่กรณีคันที่1_2.jpg",
                    "รูปรถคู่กรณีคันที่1_3.jpg"], str(names))
    check("rename: ไฟล์ชื่อใหม่อยู่บนดิสก์จริง",
          all((tp / n).exists() for n in names))
    check("rename: รูปซ้ำถูกย้ายเข้า _dup/ (ไม่อยู่ในชุดอัป)",
          (tp / "_dup").is_dir() and len(list((tp / "_dup").glob("*.jpg"))) == 1)
    check("rename: list_images เห็นเฉพาะรูปสะอาด 3 รูป (ไม่นับ _dup)",
          images.list_images(tp) == names)
    # idempotent: รันซ้ำได้ชื่อเดิม ไม่ขยับ/ไม่เพิ่มไฟล์
    b2 = emcs._opponent_image_batches(tmp, 1)
    check("rename: รันซ้ำ idempotent (ชื่อเดิม 3 รูป)",
          sorted(p.name for p in b2[0][1]) == names
          and images.list_images(tp) == names, str(b2))

# 2 คัน: rename เป็นคันที่1_*/คันที่2_* แยกกัน
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    tp = tmp / "tp_veh"
    tp.mkdir()
    (tp / "carA_1.jpg").write_bytes(b"A1")
    (tp / "carA_2.jpg").write_bytes(b"A2")
    (tp / "carB_1.jpg").write_bytes(b"B1")
    b = emcs._opponent_image_batches(tmp, 2)
    got = {lbl: sorted(p.name for p in ps) for lbl, ps in b}
    check("rename: 2 คัน → คันที่1_1/_2 + คันที่2_1",
          got == {"รูปรถคู่กรณี คันที่1":
                  ["รูปรถคู่กรณีคันที่1_1.jpg", "รูปรถคู่กรณีคันที่1_2.jpg"],
                  "รูปรถคู่กรณี คันที่2": ["รูปรถคู่กรณีคันที่2_1.jpg"]}, str(got))

# _rename_opponent_files: สลับชื่อชนกันได้ (two-phase) ไม่ทำไฟล์หาย
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    # ชื่อปลายทางของไฟล์หนึ่งไปตรงกับชื่อต้นทางของอีกไฟล์
    (tmp / "รูปรถคู่กรณีคันที่1_2.jpg").write_bytes(b"X")   # ควรกลายเป็น _1 หรือ _2
    (tmp / "zzz.jpg").write_bytes(b"Y")
    src = [tmp / "รูปรถคู่กรณีคันที่1_2.jpg", tmp / "zzz.jpg"]
    out = emcs._rename_opponent_files(src, 1)
    check("rename two-phase: ไม่มีไฟล์หาย (2 ไฟล์)",
          len(out) == 2 and all(p.exists() for p in out)
          and sorted(p.name for p in out) ==
          ["รูปรถคู่กรณีคันที่1_1.jpg", "รูปรถคู่กรณีคันที่1_2.jpg"], str(out))

# ---- 8.7 _pick_draft_report: เลือกเรื่อง draft ที่จะเติมรูป ----
_DRAFT = "S111111111 ... 2026013047934 ... รายงานสร้างใหม่ ... SEABI-1"
_SENT = "S222222222 ... 2026013047934 ... ประกันตรวจสอบรายงาน ... SEABI-2"
check("pick: ระบุ esurvey → ใช้ตามนั้น",
      emcs._pick_draft_report(
          [{"esurvey": "S1", "row": _DRAFT}], "S9") == "S9")
check("pick: draft เดียว → เลือก draft",
      emcs._pick_draft_report(
          [{"esurvey": "S1", "row": _SENT}, {"esurvey": "S2", "row": _DRAFT}])
      == "S2")
check("pick: ไม่มี draft + เรื่องเดียว → ใช้เรื่องนั้น",
      emcs._pick_draft_report([{"esurvey": "S1", "row": _SENT}]) == "S1")
try:
    emcs._pick_draft_report(
        [{"esurvey": "S1", "row": _SENT}, {"esurvey": "S2", "row": _SENT}])
    check("pick: ไม่มี draft + หลายเรื่อง → error", False)
except RuntimeError:
    check("pick: ไม่มี draft + หลายเรื่อง → error", True)

# ---- 10. parse SURV_REPORT XML ----
from autokey import surv_xml  # noqa: E402

old_xml = pathlib.Path("SURV_REPORT_00000858886.txt")
if old_xml.exists():
    parsed = surv_xml.parse_surv_report(old_xml)
    check("XML เก่า: ไม่มีคู่กรณี (รถประกัน TYPE 0 อย่างเดียว)",
          len(parsed["third_parties"]) == 0)
    check("XML เก่า: ทรัพย์สิน 1 รายการ", len(parsed["assets"]) == 1)
    check("XML เก่า: ชื่อทรัพย์สินถูก",
          "เต็นท์" in parsed["assets"][0]["name"])

new_xmls = list(pathlib.Path("runs/xml").glob("2026013144130_*.txt"))
if new_xmls:
    parsed = surv_xml.parse_surv_report(new_xmls[0])
    check("XML ใหม่: คู่กรณี 1 คัน", len(parsed["third_parties"]) == 1)
    tp = parsed["third_parties"][0] if parsed["third_parties"] else {}
    check("XML ใหม่: ทะเบียน/ยี่ห้อ/ประกันคู่กรณีครบ",
          tp.get("plate_no") == "2ขณ4783"
          and tp.get("car_brand") == "MITSUBISHI"
          and "รู้ใจ" in tp.get("insurer", ""), str(tp.get("plate_no")))

# ---- 11. logic กรอกคู่กรณี (เคลมสด) ----
check("แยกชื่อ (คำนำหน้าติดชื่อ)",
      emcs.split_thai_name("นายกัมปนาท เปรมกิจ") == ("นาย", "กัมปนาท", "เปรมกิจ"))
check("แยกชื่อ 'นางสาว' ไม่โดน 'นาง' ตัดก่อน",
      emcs.split_thai_name("นางสาวธมลวรรณ ผดุงโชค")
      == ("นางสาว", "ธมลวรรณ", "ผดุงโชค"))
check("แยกชื่อไม่มีคำนำหน้า",
      emcs.split_thai_name("สมชาย ใจดี") == ("", "สมชาย", "ใจดี"))

# _derive_insured_title: ใช้คำนำหน้าจริงเมื่อชื่อตรง / ไม่ตรง = '' (ไม่เดาจากเพศ)
_t_match = claim_data.ClaimData(
    insure_name="นายสมชาย ใจดี", driver_name="สมชาย", driver_surname="ใจดี")
check("คำนำหน้า: ชื่อตรงผู้เอาประกัน → ใช้คำนำหน้าจริง",
      emcs._derive_insured_title(_t_match)[0] == "นาย")
_t_f = claim_data.ClaimData(
    insure_name="บจก. อินฟินิตี้", driver_name="ธัญญา",
    driver_surname="ปัญกิม", driver_gender="F")
check("คำนำหน้า: หญิง ชื่อไม่ตรง → '' (ไม่เดานางสาว) → หยุดรอคน",
      emcs._derive_insured_title(_t_f)[0] == "")
_t_m = claim_data.ClaimData(
    insure_name="บจก. เอ", driver_name="ก", driver_surname="ข", driver_gender="M")
check("คำนำหน้า: ชาย ชื่อไม่ตรง → '' (ไม่เดานาย) → หยุดรอคน",
      emcs._derive_insured_title(_t_m)[0] == "")

check("วันที่ XML ค.ศ. → พ.ศ.",
      browser.iso_to_thai_date("2023-05-23 00:00:00") == "23/05/2566")
check("วันที่ XML พ.ศ. คงเดิม",
      browser.iso_to_thai_date("2554-09-21 00:00:00") == "21/09/2554")
check("วันที่ XML ว่าง", browser.iso_to_thai_date(" ") == "")

check("อำเภอ 236 = กทม(2) ลำดับ 36", emcs.district_index("236", "2") == 36)
check("อำเภอ 2802 = ปทุมธานี(28) ลำดับ 2", emcs.district_index("2802", "28") == 2)
check("อำเภอ 1203 = ชุมพร(12) ลำดับ 3", emcs.district_index("1203", "12") == 3)
check("อำเภอไม่ตรงจังหวัด → None", emcs.district_index("236", "5") is None)

_dry = claim_data.ClaimData(acc_result="รถประกันเป็นฝ่ายผิด")
_tp_we_wrong = claim_data.ClaimData(
    acc_result="รถประกันเป็นฝ่ายผิด", third_parties=[{"plate_no": "x"}])
_tp_they_wrong = claim_data.ClaimData(
    acc_result="รถคู่กรณีเป็นฝ่ายผิด คู่กรณีคันที่ 1",
    third_parties=[{"plate_no": "x"}])
_tp_both = claim_data.ClaimData(
    acc_result="รถประกันเป็นฝ่ายถูกและผิด", third_parties=[{"plate_no": "x"}])
check("loss auto: เคลมแห้ง (ไม่มีคู่กรณี)",
      emcs.resolve_loss_type(_dry, "auto") == "เคลมแห้ง")
# เคลมสด (มีคู่กรณี): ISURVEY ไม่มีข้อมูลลักษณะความเสียหาย → '' เสมอ (หยุดรอคนเลือก)
check("loss auto: มีคู่กรณี+ประกันผิด → '' (คนเลือกเอง)",
      emcs.resolve_loss_type(_tp_we_wrong, "auto") == "")
check("loss auto: มีคู่กรณี+คู่กรณีผิด → '' (คนเลือกเอง)",
      emcs.resolve_loss_type(_tp_they_wrong, "auto") == "")
check("loss auto: มีคู่กรณี+ก้ำกึ่ง → '' (คนเลือกเอง)",
      emcs.resolve_loss_type(_tp_both, "auto") == "")
check("loss ระบุเองไม่ถูกทับ",
      emcs.resolve_loss_type(_tp_both, "เคลมแห้ง") == "เคลมแห้ง")

# ---- 12. parser ค่าสำรวจ (bill) ----
bill_xmls = list(pathlib.Path("runs/xml").glob("2026013043395_*.txt"))
if bill_xmls:
    parsed = surv_xml.parse_surv_report(bill_xmls[0])
    b = parsed.get("bill", {})
    check("bill: ค่าบริการเสนอ 300", emcs._money(b.get("invest")) == 300.0,
          str(b.get("invest")))
    check("bill: ค่าเดินทาง 0", emcs._money(b.get("trans")) == 0.0)
check("_money แปลงค่าว่าง/comma",
      emcs._money(" ") == 0.0 and emcs._money("1,250.50") == 1250.5)

# ---- 13. ด่านเคลมแห้ง (type-based) ----
_dry2 = claim_data.ClaimData(claim_type="2")
_fresh1 = claim_data.ClaimData(claim_type="1")
_appt3 = claim_data.ClaimData(claim_type="3")
_dry2_tp = claim_data.ClaimData(claim_type="2",
                                third_parties=[{"plate_no": "x"}])
check("type 2 ไม่มีคู่กรณี = เคลมแห้งแท้",
      _dry2.dry_claim_block_reason() == "")
check("type 1 = บล็อก (เคลมสด)",
      "เคลมสด" in _fresh1.dry_claim_block_reason())
check("type 3 = บล็อก (เคลมนัดหมาย)",
      "เคลมนัดหมาย" in _appt3.dry_claim_block_reason())
check("type 2 แต่มีคู่กรณี = บล็อก (กันข้อมูลเพี้ยน)",
      "คู่กรณี" in _dry2_tp.dry_claim_block_reason())

# bill จากหน้าจอ (INS_*) ต้องไม่ถูก XML ทับ
if bill_xmls:
    _d = claim_data.ClaimData(
        bill={"source": "isurvey_screen", "invest": "700.00"})
    surv_xml.enrich_claim_from_xml(_d, bill_xmls[0])
    check("bill หน้าจอ (700) ไม่ถูก XML (300) ทับ",
          _d.bill.get("invest") == "700.00", str(_d.bill.get("invest")))
    _d2 = claim_data.ClaimData()  # ไม่มีข้อมูลหน้าจอ → fallback XML
    surv_xml.enrich_claim_from_xml(_d2, bill_xmls[0])
    check("ไม่มี bill หน้าจอ → fallback XML",
          emcs._money(_d2.bill.get("invest")) == 300.0)

# ---- 14. isurvey_api: ฟังก์ชันแปลง + diff (ไม่ต่อเน็ต/ไม่เปิด browser) ----
from autokey import isurvey_api as _api  # noqa: E402
check("_ddmmyyyy: ISO→dd/mm/yyyy คง ค.ศ.",
      _api._ddmmyyyy("2026-06-09") == "09/06/2026")
check("_ddmmyyyy: ว่าง/None → ''",
      _api._ddmmyyyy("") == "" and _api._ddmmyyyy(None) == "")
check("isurvey_api._money: comma/None",
      _api._money("1,050.00") == 1050.0 and _api._money(None) == 0.0)

import main as _main  # noqa: E402
_sa = {"acc_date": "09/06/2026", "claim_type": "2",
       "bill": {"tel": "", "invest": "500.00"}}
_sb = {"acc_date": "09/06/2026", "claim_type": "2",
       "bill": {"tel": "0.00", "invest": "500.00"}}
check("diff_claim_data: เงิน ''=0.00 ถือว่าตรง", _main.diff_claim_data(_sa, _sb) == [])
_diffs = _main.diff_claim_data({"acc_place": "ก", "bill": {}},
                               {"acc_place": "ข", "bill": {}})
check("diff_claim_data: ค่าต่างจริงถูกจับ",
      _diffs == [("acc_place", "ก", "ข")], str(_diffs))
check("diff_claim_data: ข้าม xml_file",
      _main.diff_claim_data({"xml_file": "a"}, {"xml_file": "b"}) == [])

# ---- 15. keyer_for: คนคีย์ตามเลขท้ายเลขเคลม ----
from autokey import isurvey_report as _rep  # noqa: E402
check("keyer ลงท้าย 5 = วิสุดา", _rep.keyer_for("2026013145915") == "วิสุดา ดอนหมัน")
check("keyer ลงท้าย 2 = กัญญารัตน์", _rep.keyer_for("2026013145682") == "กัญญารัตน์ เสนคำ")
check("keyer ลงท้าย 0 = วรนุช", _rep.keyer_for("2026013145910") == "วรนุช น้ำพุ")
check("keyer ลงท้าย 9 = สุทิษา", _rep.keyer_for("2026013145919") == "สุทิษา พงษ์แขก")
check("keyer ว่าง → ''", _rep.keyer_for("") == "" and _rep.keyer_for("abc") == "")
# report_sent ต้องไม่ยิงจริงถ้า dry_run / ขาด creds
_r = _rep.report_sent(cfg, "2026013145915", "SEABI-x", dry_run=True)
check("report_sent dry_run ไม่ยิง + payload ครบ",
      _r["payload"]["EMCSstatus"] == "send" and _r["payload"]["EMCSby"] == "วิสุดา ดอนหมัน")

# ---- 16. sekey_client: บันทึกงานลง se-key DB (ไม่ต่อเน็ต) ----
from autokey import sekey_client as _sk  # noqa: E402
import types as _types  # noqa: E402

check("sekey _parse_check: ไม่มีใน DB → ไม่ซ้ำ",
      _sk._parse_check({"survey_count": 0, "survey_sent_count": 0})["exists"] is False)
_pc = _sk._parse_check({"survey_count": 2, "survey_sent_count": 1})
check("sekey _parse_check: 2 แถว + ส่งแล้ว → exists+sent",
      _pc["exists"] is True and _pc["sent"] is True and _pc["count"] == 2)
_pc2 = _sk._parse_check({"survey_count": 1, "survey_sent_count": 0})
check("sekey _parse_check: มีแต่ยังไม่ส่ง → exists ไม่ sent",
      _pc2["exists"] is True and _pc2["sent"] is False)
check("sekey _parse_check: body ไม่ใช่ dict → ปลอดภัย",
      _sk._parse_check(None)["exists"] is False)

_cfg_on = _types.SimpleNamespace(sekey_api_url="https://x", sekey_api_key="k")
_cfg_off = _types.SimpleNamespace(sekey_api_url="https://x", sekey_api_key="")
check("sekey enabled: มี url+key = เปิด", _sk.enabled(_cfg_on) is True)
check("sekey enabled: ไม่มี key = ปิด", _sk.enabled(_cfg_off) is False)

_skr = _sk.save_record(_cfg_on, "2026013145915", "SEABI-213260100295", dry_run=True)
check("sekey save dry_run: payload ครบ + keyer ตามเลขท้าย + mark sent",
      _skr["ok"] and _skr["payload"]["claim_no"] == "2026013145915"
      and _skr["payload"]["survey_no"] == "SEABI-213260100295"
      and _skr["payload"]["keyer"] == "วิสุดา ดอนหมัน"
      and _skr["payload"]["work_type"] == "งานต้น"
      and _skr["payload"]["upsert_pending"] is True
      and _skr["sent"] is True)
_skoff = _sk.save_record(_cfg_off, "2026013145915", "SEABI-x")
check("sekey save: ปิดใช้งาน → ok=False ไม่ยิง", _skoff["ok"] is False)

_dd = claim_data.ClaimData(claim_value="2026013145915", invoice_value="SEABI-x")
check("main._sekey_dup_skip: ปิด se-key → ทำต่อ ('')",
      _main._sekey_dup_skip(_cfg_off, _dd) == "")

# ---- 17. browser._parse_selected: เลือกรูปอัปโหลด (กรองชื่อที่มีจริง) ----
_files = ["1.jpg", "รูปรถประกัน2.jpg", "รูปรถประกัน3.jpg"]
check("parse_selected: เลือกบางรูป + กรองชื่อแปลกปลอม",
      browser._parse_selected('{"selected":["1.jpg","ghost.jpg","รูปรถประกัน3.jpg"]}', _files)
      == ["1.jpg", "รูปรถประกัน3.jpg"])
check("parse_selected: เลือกว่าง → [] (ไม่อัปโหลดเลย)",
      browser._parse_selected('{"selected":[]}', _files) == [])
check("parse_selected: JSON พัง → None (อัปโหลดทุกรูป)",
      browser._parse_selected("ขยะ", _files) is None)
check("parse_selected: ไม่มีคีย์ selected → None",
      browser._parse_selected('{"foo":1}', _files) is None)
check("parse_selected: selected ไม่ใช่ list → None",
      browser._parse_selected('{"selected":"x"}', _files) is None)

# ---- 18. browser._image_categories: หมวดของรูปจาก manifest ----
import json as _json
with tempfile.TemporaryDirectory() as _d:
    _d = pathlib.Path(_d)
    (_d / "_categories.json").write_text(_json.dumps({
        "a.jpg": "INS", "DOC_supv_comment-0.jpg": "REPORTS", "x.jpg": "OTHERS",
    }), encoding="utf-8")
    (_d / "_rename_map.json").write_text(_json.dumps({
        "1.jpg": "DOC_supv_comment-0.jpg", "รูปรถประกัน2.jpg": "a.jpg",
    }), encoding="utf-8")
    _cat = browser._image_categories(_d, ["1.jpg", "รูปรถประกัน2.jpg", "x.jpg", "ghost.jpg"])
    check("image_categories: 1.jpg→REPORTS (ผ่าน rename_map)", _cat["1.jpg"] == "REPORTS")
    check("image_categories: รูปรถประกัน2→INS (ผ่าน rename_map)", _cat["รูปรถประกัน2.jpg"] == "INS")
    check("image_categories: x.jpg ไม่ rename →OTHERS ตรง", _cat["x.jpg"] == "OTHERS")
    check("image_categories: ไม่มีใน manifest →OTHERS (fallback)", _cat["ghost.jpg"] == "OTHERS")
with tempfile.TemporaryDirectory() as _d2:
    check("image_categories: ไม่มี manifest → OTHERS ทั้งหมด",
          browser._image_categories(pathlib.Path(_d2), ["1.jpg"])["1.jpg"] == "OTHERS")

# ---- 19. sekey_client: derive_base_type + build_payloads (ลอกจาก extension) ----
check("derive_base_type: SEABI → งานต้น", _sk.derive_base_type("SEABI-1") == "งานต้น")
check("derive_base_type: SESV → SESV", _sk.derive_base_type("SESV-12345678") == "SESV")
_p = _sk.build_payloads("C1", "SEABI-1", keyer="k", base_type="งานต้น")
check("build_payloads: งานต้น = 1 row (mix ว่าง)",
      len(_p) == 1 and _p[0]["work_type"] == "งานต้น" and _p[0]["invoice_mix"] == "")
check("build_payloads: งานตาม = 1 row",
      _sk.build_payloads("C1", "SEABI-1", base_type="งานตาม")[0]["work_type"] == "งานตาม")
_p = _sk.build_payloads("C1", "SEABI-1", base_type="งานต้น", batch=True,
                        mix_values=["SEABI-2", "SEABI-3"])
check("build_payloads: งานรวม = 1 primary + 2 followup",
      len(_p) == 3 and _p[0]["work_type"] == "งานต้น" and _p[0]["invoice_mix"] == ""
      and _p[1]["work_type"] == "งานรวม" and _p[1]["survey_no"] == "SEABI-2"
      and _p[1]["invoice_mix"] == "SEABI-1" and _p[2]["survey_no"] == "SEABI-3")
_p = _sk.build_payloads("C1", "SESV-1", base_type="SESV", batch=False,
                        mix_values=["SEABI-A", "SEABI-B"])
check("build_payloads: SESV primary ผูก mix[0] (SEABI)",
      _p[0]["work_type"] == "SESV" and _p[0]["survey_no"] == "SESV-1"
      and _p[0]["invoice_mix"] == "SEABI-A")
check("build_payloads: SESV ล็อก batch + followup = mix[1:]",
      len(_p) == 2 and _p[1]["work_type"] == "งานรวม"
      and _p[1]["survey_no"] == "SEABI-B" and _p[1]["invoice_mix"] == "SESV-1")

# ---- 20. surv_xml: parse ผู้บาดเจ็บ (TXN_SURV_INJ) + คู่กรณี + ทรัพย์สิน ----
_xml = """<TXN_SURV_REPORT>
 <TXN_SURV_CAR><TYPE>0</TYPE><CAR_REGNO>กข1234</CAR_REGNO></TXN_SURV_CAR>
 <TXN_SURV_CAR><TYPE>1</TYPE><CAR_REGNO>1กฐ9717</CAR_REGNO><CMFG>HONDA</CMFG><OPO_NAME>นาย อัมพร ปีจอ</OPO_NAME></TXN_SURV_CAR>
 <TXN_SURV_INJ><INJ_SEQ>1</INJ_SEQ><NAME>นางสาว วณิศราภรณ์</NAME><AGE>29</AGE><HOS_NAME>รพ.บ้านบึง</HOS_NAME><INJURE>เจ็บหน้าอก</INJURE><GENDER>F</GENDER><PERSON_TYPE>DV</PERSON_TYPE></TXN_SURV_INJ>
 <TXN_SURV_INJ><INJ_SEQ>2</INJ_SEQ><NAME>นาย อัมพร ปีจอ</NAME><AGE>55</AGE><INJURE>เข่าถลอก</INJURE><GENDER>M</GENDER><PERSON_TYPE>ON</PERSON_TYPE></TXN_SURV_INJ>
 <TXN_SURV_ASSET><ASSET_SEQ>1</ASSET_SEQ><ASSET_DESC>ผลไม้</ASSET_DESC><COST_DAMAGE>2000</COST_DAMAGE></TXN_SURV_ASSET>
</TXN_SURV_REPORT>"""
with tempfile.TemporaryDirectory() as _xd:
    _xp = pathlib.Path(_xd) / "SURV_REPORT_test.txt"
    _xp.write_text(_xml, encoding="utf-8")
    _parsed = surv_xml.parse_surv_report(_xp)
    check("surv_xml: ผู้บาดเจ็บ TXN_SURV_INJ → 2 คน (เคยพลาดเพราะหา TXN_SURV_INJURY)",
          len(_parsed["injuries"]) == 2)
    _i0 = _parsed["injuries"][0] if _parsed["injuries"] else {}
    check("surv_xml: ฟิลด์ผู้บาดเจ็บครบ (name/hospital/injure/person_type)",
          _i0.get("name") == "นางสาว วณิศราภรณ์" and _i0.get("hospital") == "รพ.บ้านบึง"
          and _i0.get("injure") == "เจ็บหน้าอก" and _i0.get("person_type") == "DV")
    check("surv_xml: คู่กรณี (CAR TYPE!=0) = 1", len(_parsed["third_parties"]) == 1)
    check("surv_xml: ทรัพย์สิน = 1", len(_parsed["assets"]) == 1)

# ---- 21. emcs.continuation_esurvey: ตรวจงานต่อเนื่อง (มีเรื่องเดิม + invoice ใหม่) ----
_exist = [{"esurvey": "S68426056403",
           "row": "S68426056403 SEABI-172260500053 2026013041465 ..."}]
check("continuation: มีเรื่องเดิม + invoice ใหม่ → คืน e-Survey เดิม",
      emcs.continuation_esurvey(_exist, "SEABI-372260600032") == "S68426056403")
check("continuation: invoice อยู่ในเรื่องเดิมแล้ว → None (ซ้ำจริง ไม่ใช่ต่อเนื่อง)",
      emcs.continuation_esurvey(_exist, "SEABI-172260500053") is None)
check("continuation: ไม่มีเรื่องเดิม → None (สร้างใหม่ได้)",
      emcs.continuation_esurvey([], "SEABI-372260600032") is None)
check("continuation: ไม่มี invoice → None",
      emcs.continuation_esurvey(_exist, "") is None)

# ---- 22. emcs._find_submit_button: รองรับทั้งส่งงานใหม่ + ส่งผลงานต่อเนื่อง ----
class _FakeEl:
    def __init__(self, disp=True, en=True):
        self._d, self._e = disp, en

    def is_displayed(self):
        return self._d

    def is_enabled(self):
        return self._e


class _FakeDriver:
    """find_element คืน element เฉพาะ id ที่กำหนด; นอกนั้น raise (เลียนแบบ NoSuchElement)"""
    def __init__(self, present):
        self.present = present

    def find_element(self, by, value):
        if value in self.present:
            return self.present[value]
        raise Exception("no such element")

    def find_elements(self, by, value):
        return []


_btn, _lab = emcs._find_submit_button(_FakeDriver({"wuFlow1_cmdSendNew": _FakeEl()}))
check("find_submit: เจอ cmdSendNew → 'ส่งงานใหม่'",
      _btn is not None and _lab == "ส่งงานใหม่")
_btn, _lab = emcs._find_submit_button(_FakeDriver({"wuFlow1_cmdSendFollow": _FakeEl()}))
check("find_submit: เจอแต่ cmdSendFollow → 'ส่งผลงานต่อเนื่อง'",
      _btn is not None and _lab == "ส่งผลงานต่อเนื่อง")
_btn, _lab = emcs._find_submit_button(_FakeDriver({
    "wuFlow1_cmdSendNew": _FakeEl(), "wuFlow1_cmdSendFollow": _FakeEl()}))
check("find_submit: มีทั้งคู่ → เลือก 'ส่งงานใหม่' ก่อน (ลำดับแรก)",
      _lab == "ส่งงานใหม่")
_btn, _lab = emcs._find_submit_button(_FakeDriver({}))
check("find_submit: ไม่มีปุ่ม → (None,'')", _btn is None and _lab == "")

# ---- 23. webui._build_cmd: โหมดเคลม (dry = เคลมแห้ง / fresh = เคลมสด) ----
import webui as _webui  # noqa: E402
_cmd, _e = _webui._build_cmd({"claims": "2026013041465", "claimmode": "dry"})
check("build_cmd dry: ไม่มี --allow-fresh/--scrape",
      _e is None and "--allow-fresh" not in _cmd and "--scrape" not in _cmd)
_cmd, _e = _webui._build_cmd({"claims": "2026013041465", "claimmode": "fresh"})
check("build_cmd fresh: มี --allow-fresh + --scrape",
      _e is None and "--allow-fresh" in _cmd and "--scrape" in _cmd)
_cmd, _e = _webui._build_cmd({"claims": "2026013041465"})
check("build_cmd ไม่ระบุโหมด: = เคลมแห้ง (ไม่ allow-fresh)",
      _e is None and "--allow-fresh" not in _cmd)

print("\n" + ("ALL PASS ✅" if not failures else f"FAILED ❌: {failures}"))
sys.exit(1 if failures else 0)
