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
check("loss auto: เคลมแห้ง", emcs.resolve_loss_type(_dry, "auto") == "เคลมแห้ง")
check("loss auto: ประกันผิด+คู่กรณี → ชนคู่กรณีเสียหาย",
      emcs.resolve_loss_type(_tp_we_wrong, "auto") == "ชนคู่กรณีเสียหาย")
check("loss auto: คู่กรณีผิด → ถูกคู่กรณีชน",
      emcs.resolve_loss_type(_tp_they_wrong, "auto") == "ถูกคู่กรณีชน")
check("loss auto: ก้ำกึ่ง → ว่าง (คนเลือกเอง)",
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

print("\n" + ("ALL PASS ✅" if not failures else f"FAILED ❌: {failures}"))
sys.exit(1 if failures else 0)
