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

# zip ที่มีรูปบุคคลที่สาม (ได้จากการรันจริง) — TP_* ต้องแยกโฟลเดอร์ tp_<xxx>/
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
            # โฟลเดอร์หลัก = ทุกหมวดที่ไม่ใช่ TP_* (รูปบุคคลที่สามแยกออกหมด)
            check("list_images นับเฉพาะรูปโฟลเดอร์หลัก (ไม่นับ tp_*)",
                  len(images.list_images(tmp))
                  == sum(v for k, v in counts.items() if not k.startswith("TP_")))

# ---- 8.5 archive ย้าย tp_*/ ทุกตัว (tp_veh/tp_person/tp_prop) เข้า _old ----
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    (tmp / "a.jpg").write_bytes(b"x")
    for sub, fn in [("tp_veh", "opo1.jpg"), ("tp_person", "inj1.jpg"),
                    ("tp_prop", "asset1.jpg")]:
        (tmp / sub).mkdir()
        (tmp / sub / fn).write_bytes(b"o")
    images.archive_old_images(tmp)
    check("archive: ย้าย tp_veh/tp_person/tp_prop เข้า _old ครบ",
          all(list((tmp / "_old").rglob(f"{s}/*.jpg")) != []
              and not (tmp / s).exists()
              for s in ("tp_veh", "tp_person", "tp_prop")))

# ---- 8.5.1 extract_zip_images: แยก TP_VEH/TP_PERSON/TP_PROP ใต้ tp_<xxx>/ ----
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    zpath = tmp / "syn.zip"
    import zipfile as _zf
    with _zf.ZipFile(zpath, "w") as z:
        z.writestr("PICTURES/INS/a.jpg", b"INS")
        z.writestr("PICTURES/TP_VEH/1781/v1.jpg", b"V1")
        z.writestr("PICTURES/TP_PERSON/1782/p1.jpg", b"P1")
        z.writestr("PICTURES/TP_PERSON/1782/p2.jpg", b"P2")
        z.writestr("PICTURES/TP_PROP/1783/r1.jpg", b"R1")
    out = tmp / "ext"
    counts = images.extract_zip_images(zpath, out)
    check("zip: นับหมวด TP_PERSON/TP_PROP ได้",
          counts.get("TP_PERSON") == 2 and counts.get("TP_PROP") == 1
          and counts.get("TP_VEH") == 1, str(counts))
    check("zip: TP_PERSON → tp_person/ (มี id ย่อยนำหน้า)",
          [p.name for p in (out / "tp_person").glob("*.jpg")]
          == ["1782_p1.jpg", "1782_p2.jpg"])
    check("zip: TP_PROP → tp_prop/ , TP_VEH → tp_veh/",
          (out / "tp_prop" / "1783_r1.jpg").exists()
          and (out / "tp_veh" / "1781_v1.jpg").exists())
    check("zip: รูปบุคคลที่สามไม่ปนโฟลเดอร์หลัก (เหลือแค่ INS)",
          images.list_images(out) == ["a.jpg"])

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

# ---- 8.6.2 _tp_image_batches generic: ผู้บาดเจ็บ (คนที่N) / ทรัพย์สิน (รายการที่N) ----
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    # ผู้บาดเจ็บ 2 คน — 2 prefix (id ต่อคน)
    (tmp / "tp_person").mkdir()
    (tmp / "tp_person" / "p1_a.jpg").write_bytes(b"PA")
    (tmp / "tp_person" / "p1_b.jpg").write_bytes(b"PB")
    (tmp / "tp_person" / "p2_a.jpg").write_bytes(b"QA")
    b = emcs._tp_image_batches(tmp, "tp_person", 2,
                               "รูปผู้บาดเจ็บ คนที่{i}", "รูปผู้บาดเจ็บคนที่{i}_{seq}")
    got = {lbl: sorted(p.name for p in ps) for lbl, ps in b}
    check("tp_image: ผู้บาดเจ็บ 2 คน → 'คนที่1/คนที่2' + ชื่อสะอาด",
          got == {"รูปผู้บาดเจ็บ คนที่1":
                  ["รูปผู้บาดเจ็บคนที่1_1.jpg", "รูปผู้บาดเจ็บคนที่1_2.jpg"],
                  "รูปผู้บาดเจ็บ คนที่2": ["รูปผู้บาดเจ็บคนที่2_1.jpg"]}, str(got))
with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    (tmp / "tp_prop").mkdir()
    (tmp / "tp_prop" / "x_a.jpg").write_bytes(b"X1")
    (tmp / "tp_prop" / "x_b.jpg").write_bytes(b"X2")
    b = emcs._tp_image_batches(tmp, "tp_prop", 1,
                               "รูปทรัพย์สิน รายการที่{i}", "รูปทรัพย์สินรายการที่{i}_{seq}")
    check("tp_image: ทรัพย์สิน 1 รายการ → 'รายการที่1' + ชื่อสะอาด",
          len(b) == 1 and b[0][0] == "รูปทรัพย์สิน รายการที่1"
          and sorted(p.name for p in b[0][1]) ==
          ["รูปทรัพย์สินรายการที่1_1.jpg", "รูปทรัพย์สินรายการที่1_2.jpg"], str(b))
    check("tp_image: ไม่มีโฟลเดอร์ = []",
          emcs._tp_image_batches(tmp, "tp_person", 1, "x{i}", "y{i}_{seq}") == [])

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

# ---- 9.5 ผู้บาดเจ็บ/ทรัพย์สิน (Tab 5/6) ----
check("PERSON_TYPE_MAP: DV→01 / PV→03 / ON→05",
      emcs.PERSON_TYPE_MAP == {"DV": "01", "PV": "03", "ON": "05"})
check("INJ/ASSET prefix + count cap",
      emcs.INJ_PREFIX.format(n=0) == "dtlInj_ctl00_wuInj_"
      and emcs.ASSET_PREFIX.format(n=1) == "dtlAsset_ctl01_wuAsset_"
      and emcs.MAX_INJURIES == 32 and emcs.MAX_ASSETS == 30)
check("fill_injuries/fill_assets + _save_section generic มีจริง",
      all(hasattr(emcs, f) for f in
          ("fill_injuries", "fill_assets", "_save_section")))

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

# _plate: ลบช่องว่างในทะเบียน (EMCS reject ทะเบียนมีช่องว่าง)
check("_plate ลบช่องว่างทะเบียน",
      emcs._plate("9กฆ 5003") == "9กฆ5003"
      and emcs._plate(" กท 1234 ") == "กท1234"
      and emcs._plate("") == "" and emcs._plate(None) == "")

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
# บั๊ก น.ส. ติดชื่อ (เคลม 2026013144715): driver_name='น.ส.ปฐมาวดี' = ผู้เอาประกัน
check("แยกชื่อ 'น.ส.' (ตัวย่อ) → ตัดคำนำหน้าออก",
      emcs.split_thai_name("น.ส.ปฐมาวดี") == ("น.ส.", "ปฐมาวดี", ""))
_t_ns = claim_data.ClaimData(
    insure_name="นางสาว ปฐมาวดี ช้ายสนิททำ",
    driver_name="น.ส.ปฐมาวดี", driver_surname="ช้ายสนิททำ")
check("คำนำหน้า: น.ส.ติดชื่อ + ผู้ขับ=ผู้เอาประกัน → derive 'นางสาว' ได้ (เดิม match ไม่ได้)",
      emcs._derive_insured_title(_t_ns)[0] == "นางสาว")
check("gender_from_title: น.ส. (ตัวย่อ) → W",
      emcs.gender_from_title("น.ส.ปฐมาวดี") == "W"
      and emcs.gender_from_title("นส.สมหญิง") == "W")

# dry_claim_block_reason: เคลมแห้งแท้ = '' / เคลมสด = เหตุผล (คุม _offer_submit ใส่คำเตือน)
check("dry_claim: ประเภท 2 + ไม่มีคู่กรณี/บาดเจ็บ/ทรัพย์สิน → '' (เคลมแห้งแท้)",
      claim_data.ClaimData(claim_type="2").dry_claim_block_reason() == "")
check("dry_claim: ประเภทไม่ใช่ 2 → เหตุผล (เคลมสด → เตือน)",
      claim_data.ClaimData(claim_type="1").dry_claim_block_reason() != "")
check("dry_claim: มีคู่กรณี → เหตุผล (เคลมสด → เตือน)",
      claim_data.ClaimData(claim_type="2",
                           third_parties=[{"plate_no": "9กฆ5003"}]
                           ).dry_claim_block_reason() != "")

# gender_from_title: อนุมานเพศจากคำนำหน้า (ทิศนี้ชัดเจน 100%) — fallback ตอนเพศว่าง
check("gender_from_title: นางสาว → W",
      emcs.gender_from_title("นางสาว วณิศราภรณ์") == "W")
check("gender_from_title: นาย → M",
      emcs.gender_from_title("นาย อัมพร ปีจอ") == "M")
check("gender_from_title: เด็กชาย/ด.ญ. → M/W",
      emcs.gender_from_title("เด็กชาย ก") == "M"
      and emcs.gender_from_title("ด.ญ. ข") == "W")
check("gender_from_title: ไม่มีคำนำหน้า → '' (ให้คนเลือกเอง)",
      emcs.gender_from_title("สมชาย ใจดี") == ""
      and emcs.gender_from_title("") == "")
# resolve_gender: ISURVEY/XML ก่อน (normalize F→W); ว่าง → fallback คำนำหน้า
check("resolve_gender: explicit ชนะ (M ทับชื่อหญิง)",
      emcs.resolve_gender("M", "นางสาว ก") == "M")
check("resolve_gender: F → normalize เป็น W",
      emcs.resolve_gender("F", "") == "W")
check("resolve_gender: เพศว่าง → อนุมานจากคำนำหน้า",
      emcs.resolve_gender("", "นาย ก") == "M"
      and emcs.resolve_gender("  ", "นางสาว ข") == "W")
check("resolve_gender: เพศว่าง + ชื่อไม่มีคำนำหน้า → ''",
      emcs.resolve_gender("", "ก ข") == "")

# ความเสียหาย checklist (ฟอร์มใหม่): normalize + fuzzy match ชิ้นส่วน → ติ๊ก / fallback อิสระ
check("_norm_damage_part: ตัด (ใหญ่)/ซ้าย/ขวา/ด้าน/ตัวบน",
      emcs._norm_damage_part("กันชนหน้า(ใหญ่)") == "กันชนหน้า"
      and emcs._norm_damage_part("บังโคลนหน้าขวา") == "บังโคลนหน้า"
      and emcs._norm_damage_part("ประตูหน้าด้านซ้าย") == "ประตูหน้า")
check("_damage_side: ซ้าย=0 ขวา=1 ทั้งคู่/ไม่ระบุ=2",
      emcs._damage_side("บังโคลนหน้าซ้าย") == "0"
      and emcs._damage_side("บังโคลนหน้าขวา") == "1"
      and emcs._damage_side("ประตูซ้ายขวา") == "2"
      and emcs._damage_side("กันชนหน้า") == "2")
check("_damage_rank_idx: A-D→0-3 / อื่น→None",
      emcs._damage_rank_idx("A") == "0" and emcs._damage_rank_idx("d") == "3"
      and emcs._damage_rank_idx("X") is None and emcs._damage_rank_idx("") is None)

_dl_parts = ["กันชนหน้า", "กันชนหลัง", "ฝากระโปรงหน้า", "กระจังหน้า",
             "บังโคลนหน้า", "ประตูหน้า"]
check("_match_damage_checklist: ตรงเป๊ะ → idx + score สูง",
      emcs._match_damage_checklist("ฝากระโปรงหน้า", _dl_parts, set())[0] == 2)
check("_match_damage_checklist: '(ใหญ่)' → match ชิ้นหลัก",
      emcs._match_damage_checklist("กันชนหน้า(ใหญ่)", _dl_parts, set())[0] == 0)
check("_match_damage_checklist: 'บังโคลนหน้าซ้าย' → บังโคลนหน้า",
      emcs._match_damage_checklist("บังโคลนหน้าซ้าย", _dl_parts, set())[0] == 4)
check("_match_damage_checklist: 'คิ้วกระจังหน้าตัวบน' substring → ไม่ match (None) → อิสระ",
      emcs._match_damage_checklist("คิ้วกระจังหน้าตัวบน", _dl_parts, set())[0] is None)
check("_match_damage_checklist: ติ๊กแล้ว (used) → ข้าม ไม่ match ซ้ำ",
      emcs._match_damage_checklist("กันชนหน้า", _dl_parts, {0})[0] is None)
check("_match_damage_checklist: checklist ว่าง (ฟอร์มเก่า) → (None,0)",
      emcs._match_damage_checklist("กันชนหน้า", [], set()) == (None, 0))
# prefix match กับชื่อจริง ISURVEY (เคลม 2026013144715) = 'ชิ้นส่วน+คำเสริม+อาการ'
# checklist จริง 22 ชิ้น (ตัดมาเฉพาะที่เกี่ยว) — ต้องได้ 3 ติ๊ก + 3 free-text
_cl = ["กันชนหน้า", "กันชนหลัง", "ฝากระโปรงหน้า", "กระจังหน้า", "ไฟหน้า",
       "หลังคา", "ประตูหน้า"]
_dmg6 = ["ฝากระโปรงหน้า+คิ้ว บุบ", "กันชนหน้า + คิ้ว บุบดุ้งครูด", "กระจังหน้า แตก",
         "ฝาครอบโลโก้ด้านหน้าครูด", "คิ้วครอบไฟหน้าซ้าย ดุ้งครูด", "กรอบป้ายทะเบียนหน้าครูด"]
_used6, _hit6 = set(), []
for _nm in _dmg6:
    _i, _s = emcs._match_damage_checklist(_nm, _cl, _used6)
    if _i is not None:
        _used6.add(_i); _hit6.append(_cl[_i])
check("damage prefix: ชื่อจริง 6 → ติ๊ก 3 (ฝากระโปรงหน้า/กันชนหน้า/กระจังหน้า)",
      _hit6 == ["ฝากระโปรงหน้า", "กันชนหน้า", "กระจังหน้า"], str(_hit6))
check("damage prefix: 'คิ้วครอบไฟหน้า' ไม่ติ๊ก 'ไฟหน้า' (ขึ้นต้นคิ้ว ไม่ใช่ไฟ)",
      emcs._match_damage_checklist("คิ้วครอบไฟหน้าซ้าย ดุ้งครูด", _cl, set())[0] is None)

# ---- โหมดนำเข้า XML: ช่องอิสระความเสียหายแบบ dynamic (cmdNewReport=8 / import=20) ----
class _FakeJS:
    def __init__(self, ret): self._ret = ret
    def execute_script(self, *a, **k): return list(self._ret)


# ฟอร์ม import = 20 ช่อง (ctl02-11 × A/B) ส่งมาสลับลำดับ → ต้องเรียง A ก่อน B (บน→ล่าง)
_imp_raw = []
for _n in range(2, 12):
    _imp_raw.append(f"dgvOtherDamage_List_ctl{_n:02d}_wuOtherDamLB_")
    _imp_raw.append(f"dgvOtherDamage_List_ctl{_n:02d}_wuOtherDamLA_")
_imp_sorted = emcs._free_text_slots(_FakeJS(_imp_raw))
check("_free_text_slots: import form อ่านได้ 20 ช่อง", len(_imp_sorted) == 20,
      str(len(_imp_sorted)))
check("_free_text_slots: เรียงคอลัมน์ A (ctl02-11) ก่อน B",
      _imp_sorted[0] == "dgvOtherDamage_List_ctl02_wuOtherDamLA_"
      and _imp_sorted[9] == "dgvOtherDamage_List_ctl11_wuOtherDamLA_"
      and _imp_sorted[10] == "dgvOtherDamage_List_ctl02_wuOtherDamLB_")
check("_free_text_slots: cmdNewReport 8 ช่อง (ctl02-05 × A/B)",
      len(emcs._free_text_slots(_FakeJS(
          [f"dgvOtherDamage_List_ctl{_n:02d}_wuOtherDamL{_c}_"
           for _c in "AB" for _n in range(2, 6)]))) == 8)


class _ThrowJS:
    def execute_script(self, *a, **k):
        raise RuntimeError("boom")


check("_free_text_slots: อ่าน DOM ไม่ได้ → [] (fallback สูตรเดิม)",
      emcs._free_text_slots(_ThrowJS()) == [])

# ---- โหมดนำเข้า XML: เลือกสาขาประกัน ----
check("_import_branch_value: เลือก 'กรุงเทพ' (ตรงข้อความ)",
      emcs._import_branch_value(_FakeJS(
          [["0", "-- เลือกสาขา --"], ["1778|25265", "กรุงเทพ"]])) == "1778|25265")
check("_import_branch_value: ไม่มีกรุงเทพ → option แรกที่ไม่ใช่ '0'",
      emcs._import_branch_value(_FakeJS(
          [["0", "--"], ["1602|9", "เชียงใหม่"]])) == "1602|9")

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

# enrich ต้องคง third_parties ที่ enrich Tab4 มาแล้ว (กัน --data-json ลบ veh_type)
_xml_48453 = list(pathlib.Path("runs/xml").glob("2026013048453_*.txt"))
if _xml_48453:
    _de = claim_data.ClaimData()
    _de.third_parties = [{"plate_no": "9กฆ5003", "veh_type": "รถจักรยานยนต์",
                          "damages": [{"part": "x"}]}]   # จำลอง enrich Tab 4 แล้ว
    surv_xml.enrich_claim_from_xml(_de, _xml_48453[0])
    check("enrich: คง third_parties ที่ enrich Tab4 (veh_type/damages ไม่หาย)",
          len(_de.third_parties) == 1
          and _de.third_parties[0].get("veh_type") == "รถจักรยานยนต์"
          and len(_de.third_parties[0].get("damages", [])) == 1)
    check("enrich: injuries/assets ยังว่าง → เซ็ตจาก XML (2 / 1)",
          len(_de.injuries) == 2 and len(_de.assets) == 1)

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

# ---- 17.5 wait_for_injury_inputs: marker + parse ค่าจาก webui ----
import io as _io
_spec = [{"name": "นาย ก", "person_type_value": "05", "car_regno": ""}]
_save_webui, _save_stdin = browser._WEBUI, sys.stdin
browser._WEBUI = True
sys.stdin = _io.StringIO('{"persons":[{"person_type":"01","car_regno":"9กฆ5003"}]}\n')
_r = browser.wait_for_injury_inputs(_spec)
check("injury inputs: parse ค่าจาก webui (person_type+เลขทะเบียน)",
      _r == [{"person_type": "01", "car_regno": "9กฆ5003"}], str(_r))
sys.stdin = _io.StringIO("")          # EOF (ไม่มีคนเฝ้า)
check("injury inputs: EOF → None (ใช้ค่า ISURVEY เดิม)",
      browser.wait_for_injury_inputs(_spec) is None)
sys.stdin = _io.StringIO("ขยะ\n")     # JSON พัง
check("injury inputs: JSON พัง → None",
      browser.wait_for_injury_inputs(_spec) is None)
browser._WEBUI = False
check("injury inputs: ไม่ใช่ webui → None (console ไม่ถาม)",
      browser.wait_for_injury_inputs(_spec) is None)
browser._WEBUI, sys.stdin = _save_webui, _save_stdin
check("injury options fallback: 01-05 (รวม 02/04 รถคู่กรณี)",
      [o["value"] for o in browser.INJ_PERSON_TYPE_OPTIONS]
      == ["01", "02", "03", "04", "05"])

# options=... (อ่านจากหน้าจริง dynamic) override fallback ใน marker ที่ส่ง webui
_save_webui2, _save_stdin2, _save_stdout2 = browser._WEBUI, sys.stdin, sys.stdout
browser._WEBUI = True
sys.stdin = _io.StringIO('{"persons":[{"person_type":"02","car_regno":""}]}\n')
_cap = _io.StringIO()
sys.stdout = _cap
browser.wait_for_injury_inputs(
    _spec, options=[{"value": "02", "label": "ผู้ขับขี่ - รถคู่กรณี"}])
sys.stdout = _save_stdout2
_marker_line = [ln for ln in _cap.getvalue().splitlines()
                if ln.startswith(browser.INJURY_INPUTS_MARKER)]
import json as _json0
_payload = _json0.loads(_marker_line[0][len(browser.INJURY_INPUTS_MARKER):])
check("injury options: ส่ง options จากหน้าจริงไป webui (ไม่ใช้ fallback)",
      [o["value"] for o in _payload["person_type_options"]] == ["02"])
browser._WEBUI, sys.stdin = _save_webui2, _save_stdin2

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

# ---- 22b. _save_and_exit_billing: บันทึกหัวบิล ('บันทึกราคา') + กลับ Inbox/Outbox; กัน 'ส่งงาน' ----
# ปุ่มบันทึกราคามี 2 id: ยังไม่เคยบันทึกบิล = btnSurveySave / เคยบันทึกแล้ว = btnSurvey_Update
# (ไม่ได้ผูกกับ hifPostStatus — พิสูจน์แล้วว่า status=1 เจอได้ทั้ง 2 ปุ่ม)
# บอทต้องเจอทั้งคู่ — เคส S68426080392 เคยหาไม่เจอจนต้องกดเอง
class _FakeBtn:
    def __init__(self, val=""):
        self._val, self.text, self.clicked = val, "", False
    def get_attribute(self, k):
        return self._val if k == "value" else None
    def click(self):
        self.clicked = True
    def is_displayed(self):
        return True

def _run_save_exit(present):
    """present = {id ปุ่ม: _FakeBtn} — จำลองว่าหน้า render ปุ่มไหน"""
    clicked_ids = []
    _orig = (emcs.wait_clickable, emcs.click_retry, emcs.accept_alert,
             emcs._field_value)
    emcs.wait_clickable = lambda d, by, value, timeout=10: None
    emcs.click_retry = lambda d, by, value, timeout=15, attempts=3: clicked_ids.append(value)
    emcs.accept_alert = lambda d, timeout=30: ""
    emcs._field_value = lambda d, eid: ""
    try:
        emcs._save_and_exit_billing(_FakeDriver(present))
    finally:
        (emcs.wait_clickable, emcs.click_retry, emcs.accept_alert,
         emcs._field_value) = _orig
    return clicked_ids

_new = _FakeBtn("บันทึกราคา")
_ids = _run_save_exit({"btnSurveySave": _new})
check("save_exit: บิลยังไม่เคยบันทึก → กด btnSurveySave", _new.clicked is True)
check("save_exit: แล้วกดกลับ Inbox/Outbox (imbReturn_In_Out)",
      _ids == ["wuMenuPage1_imbReturn_In_Out"])
_upd = _FakeBtn("บันทึกราคา")
_ids = _run_save_exit({"btnSurvey_Update": _upd})
check("save_exit: บิลเคยบันทึกแล้ว → กด btnSurvey_Update", _upd.clicked is True)
check("save_exit: (กรณีที่ 2) กดกลับ Inbox/Outbox ด้วย",
      _ids == ["wuMenuPage1_imbReturn_In_Out"])
_send = _FakeBtn("ส่งงานใหม่")
_ids2 = _run_save_exit({"btnSurveySave": _send})
check("save_exit: ปุ่มมีคำ 'ส่งงาน' → ไม่กด (กันกดส่งงานพลาด)", _send.clicked is False)
check("save_exit: เจอ 'ส่งงาน' → ไม่กดกลับ Inbox ด้วย (หยุดทันที)", _ids2 == [])
_ids3 = _run_save_exit({})
check("save_exit: ไม่มีปุ่มบันทึกราคาเลย → ไม่กดกลับ Inbox (กันข้อมูลหาย)", _ids3 == [])

# ---- 22c. ยี่ห้อรถ ไทย→อังกฤษ + guard กันเลือก placeholder ('-- ระบุ --') ----
# เคส #104 (จริง): ตัวเลือก ddlCMFG ของ EMCS เป็นอังกฤษล้วน แต่ se-survey ส่ง 'เอ็มจี'
# → fuzzy 0 คะแนน แล้วโค้ดเดิมเลือก '-- ระบุ --' ให้ = ช่องบังคับว่างแบบเงียบ ๆ
from autokey.car_brand import normalize_brand as _nb  # noqa: E402
from autokey.browser import _is_placeholder_option as _isph  # noqa: E402
from autokey.browser import FUZZY_MIN_SCORE as _MINSC  # noqa: E402
from rapidfuzz import fuzz as _fz, process as _pc  # noqa: E402

check("brand: 'เอ็มจี' → 'MG'", _nb("เอ็มจี") == "MG")
check("brand: 'นิสสัน' → 'NISSAN'", _nb("นิสสัน") == "NISSAN")
check("brand: อังกฤษอยู่แล้ว ส่งผ่าน (ISURVEY/XML เดิมไม่กระทบ)",
      _nb("TOYOTA") == "TOYOTA" and _nb("MG") == "MG")
check("brand: มีรุ่นต่อท้าย 'โตโยต้า วีออส' → 'TOYOTA'", _nb("โตโยต้า วีออส") == "TOYOTA")
check("brand: ว่าง/None → ว่าง", _nb("") == "" and _nb(None) == "")
check("brand: ไทยที่ไม่รู้จัก → คืนค่าเดิม (ไปจบที่หยุดรอคนเลือก)",
      _nb("ยานยนต์ดาวอังคาร") == "ยานยนต์ดาวอังคาร")
# WRatio เป็น case-sensitive + ตัวเลือก EMCS พิมพ์ใหญ่ล้วน → 'Mazda' ดิบเคยไปโดน 'MG'
check("brand: อังกฤษ title case → พิมพ์ใหญ่ (กัน 'Mazda' ไปโดน 'MG')",
      _nb("Mazda") == "MAZDA" and _nb("Toyota") == "TOYOTA")
# กติกา _dash: ฟิลด์บังคับที่ไม่มีข้อมูลใส่ '-' → ห้ามเอาไปเลือกยี่ห้อ ('-ALL-'/'NASA' มีจริง)
check("brand: '-' / 'NA' / 'N/A' (ไม่มีข้อมูล) → ว่าง ไม่ใช่ยี่ห้อ",
      _nb("-") == "" and _nb("NA") == "" and _nb("N/A") == "")
# fuzzy fallback ต้องไม่จับ "ชื่อรุ่น" เป็นยี่ห้อ (ฟอร์จูนเนอร์ = รุ่นของ TOYOTA ไม่ใช่ FORD)
check("brand: ชื่อรุ่นไม่ถูกจับเป็นยี่ห้อ (ฟอร์จูนเนอร์/แอคคอร์ด/พีซีเอ็กซ์)",
      _nb("ฟอร์จูนเนอร์") == "ฟอร์จูนเนอร์" and _nb("แอคคอร์ด") == "แอคคอร์ด"
      and _nb("พีซีเอ็กซ์") == "พีซีเอ็กซ์")
check("brand: TP ที่ควรเข้ายังเข้าครบ (ฮอนด้า ซิตี้ / อีซูซุ ดีแมกซ์ / สะกดเพี้ยน)",
      _nb("ฮอนด้า ซิตี้") == "HONDA" and _nb("อีซูซุ ดีแมกซ์") == "ISUZU"
      and _nb("อีซูสุ") == "ISUZU")
check("brand: ไม่ map ไปป้ายที่ EMCS ไม่มี (GWM ถูกถอด)",
      "GWM" not in set(__import__("autokey.car_brand", fromlist=["x"]).THAI_TO_EMCS.values()))

check("placeholder: '-- ระบุ --' / '--เลือก--' / '- กรุณาเลือก -' = ตัวเลือกหลอก",
      _isph("-- ระบุ --") and _isph("--เลือก--") and _isph("- กรุณาเลือก -"))
check("placeholder: ยี่ห้อจริงไม่ถูกมองเป็น placeholder",
      not _isph("MG") and not _isph("NISSAN") and not _isph("LYNK CO"))

# ตัวเลือกจริงจากดัมพ์หน้า EMCS (ddlCMFG ของ 'เก๋งเอเชีย')
_EMCS_BRANDS = ["-- ระบุ --", "AION", "-ALL-", "BYD", "FORD", "HONDA", "ISUZU",
                "MAZDA", "MG", "MITSUBISHI", "NISSAN", "TOYOTA"]
_bad = _pc.extractOne("เอ็มจี", _EMCS_BRANDS, scorer=_fz.WRatio)
check("regress: ไทยดิบเทียบตัวเลือกอังกฤษ → ได้ placeholder + คะแนนต่ำกว่าเกณฑ์ (ต้องไม่เลือก)",
      _isph(_bad[0]) and _bad[1] < _MINSC)
for _th, _en in (("เอ็มจี", "MG"), ("นิสสัน", "NISSAN"), ("โตโยต้า", "TOYOTA")):
    _ok = _pc.extractOne(_nb(_th), _EMCS_BRANDS, scorer=_fz.WRatio)
    check(f"fix: '{_th}' → normalize → เลือก '{_en}' ได้ (score {_ok[1]:.0f})",
          _ok[0] == _en and _ok[1] >= _MINSC)

# เกณฑ์ยี่ห้อ (BRAND_MIN_SCORE=90): ลิสต์ถูกกรองตามประเภทรถ ยี่ห้อที่ไม่มีในลิสต์
# ต้องไม่ไป "เกาะ" ยี่ห้ออื่น — ค่าถูกได้ ≥90 เสมอ ค่ามั่วสูงสุด 80
from autokey.car_brand import BRAND_MIN_SCORE as _BMS  # noqa: E402
_fp = _pc.extractOne("TRIUMPH", _EMCS_BRANDS + ["TRUMPCHI"], scorer=_fz.WRatio)
check("brand-score: ยี่ห้อที่ไม่มีในลิสต์ (TRIUMPH) ได้ < 90 → ไม่ถูกเลือก",
      _fp[1] < _BMS)
check("brand-score: ค่าที่ถูกต้องยังผ่านเกณฑ์ 90 (TOYOTA / 'MG 3')",
      _pc.extractOne("TOYOTA", _EMCS_BRANDS, scorer=_fz.WRatio)[1] >= _BMS
      and _pc.extractOne("MG 3", _EMCS_BRANDS, scorer=_fz.WRatio)[1] >= _BMS)

# ---- 22c-2. ประเภทรถ + ค่าเสียหาย/KFK คู่กรณี (จากผลตรวจฟิลด์เทียบ master EMCS) ----
# ป้ายจริงของ ddlCType (สกัดจากหน้า EMCS ที่เซฟไว้) — code ฝั่งแอปต้อง map มาให้ตรง verbatim
_EMCS_CTYPE = ['เก๋งเอเชีย', 'เก๋งยุโรป', 'รถจักรยานยนต์', 'รถอื่นๆ', 'กระบะ', 'รถตู้', 'รถบรรทุก']
check("car_type: map ครบ 7 code และเป็นป้าย EMCS verbatim",
      sorted(_main._CAR_TYPE_TH.values()) == sorted(_EMCS_CTYPE))
# เดิม A/E = 'เก๋ง' ทั้งคู่ → WRatio 90 เท่ากันเป๊ะ → extractOne คืนตัวแรก = เก๋งเอเชียเสมอ
_tie = _pc.extractOne('เก๋ง', _EMCS_CTYPE, scorer=_fz.WRatio)
check("car_type regress: 'เก๋ง' ย่อ ๆ แยกเอเชีย/ยุโรปไม่ออก (คะแนนเท่ากัน)",
      _fz.WRatio('เก๋ง', 'เก๋งเอเชีย') == _fz.WRatio('เก๋ง', 'เก๋งยุโรป') and _tie[0] == 'เก๋งเอเชีย')
for _c, _lbl in _main._CAR_TYPE_TH.items():
    _m = _pc.extractOne(_lbl, _EMCS_CTYPE, scorer=_fz.WRatio)
    check(f"car_type: '{_c}' → '{_lbl}' เลือกได้ตรงตัว (score {_m[1]:.0f})",
          _m[0] == _lbl and _m[1] >= 99)

check("money: ทศนิยม/คอมมา/จุดท้าย → รูปแบบที่ EMCS รับ",
      _main._money("17,000") == "17000" and _main._money("8000.00") == "8000"
      and _main._money("8000.") == "8000" and _main._money("1234.5") == "1234.5")
check("money: ค่าพัง/ยาวเกิน maxlength 10 → '' (ดีกว่าโดน EMCS ล้างทิ้งเงียบ ๆ)",
      _main._money("1.2.3") == "" and _main._money("abc") == ""
      and _main._money(None) == "" and _main._money("12345678901") == "")

# คู่กรณี: ค่าเสียหายประมาณ + KFK เคยตกหายเพราะ dict นี้เขียนทับ third_parties ที่ enrich จาก XML
_cd = claim_data.ClaimData()
_cd.third_parties = [{"province_id": "2", "district_id": "227", "lic_type": "10"}]
_main._populate_third_parties_from_report(_cd, {"opposing_parties": [{
    "plate": "6กย5970", "car_type": "เก๋งเอเชีย", "car_brand": "นิสสัน",
    "estimated_cost": "17,000", "kfk": True, "first_name": "สมเกียรติ"}]})
_tp0 = _cd.third_parties[0]
check("คู่กรณี: ค่าเสียหายประมาณถูกส่งต่อ (estimated_cost → cost_damage)",
      _tp0["cost_damage"] == "17000")
check("คู่กรณี: KFK ติ๊กได้ (kfk=True → has_kfk ที่ emcs.py รับ)",
      str(_tp0["has_kfk"]).upper() in ("Y", "YES", "1", "TRUE"))
check("คู่กรณี: ไม่ติ๊ก KFK ถ้าไม่ได้เลือก + ยังคงรหัสจังหวัด/อำเภอจาก XML",
      _tp0["province_id"] == "2" and _tp0["district_id"] == "227")
_cd2 = claim_data.ClaimData()
_main._populate_third_parties_from_report(_cd2, {"opposing_parties": [{"plate": "1กก1"}]})
check("คู่กรณี: ไม่มี kfk/ค่าเสียหาย → ว่าง (ไม่ติ๊ก ไม่กรอก)",
      _cd2.third_parties[0]["has_kfk"] == "" and _cd2.third_parties[0]["cost_damage"] == "")

# ---- 22c-3. ห้ามกด 'ตกลง' กับ confirm ที่ลบข้อมูล + ห้ามเปลี่ยนประเภทรถทับของเดิม ----
# ของจริงจาก eclaim3 (2026-07-25): เปลี่ยน 'ประเภทรถ' บนเรื่องที่บันทึกแล้ว เด้ง confirm
# "การแก้ไขต่อไปนี้ จะทำให้ข้อมูลที่เคยบันทึกไว้แล้ว ถูกลบออกทั้งหมด" — กดตกลง = งานหาย
import autokey.browser as _br  # noqa: E402
_DESTRUCTIVE_TXT = ("การแก้ไขต่อไปนี้ จะทำให้ข้อมูลที่เคยบันทึกไว้แล้ว ถูกลบออกทั้งหมด "
                    "คุณต้องการจะแก้ไขข้อมูลหรือไม่?")


class _FakeAlert:
    def __init__(self, text):
        self.text, self.done = text, None

    def accept(self):
        self.done = "accept"

    def dismiss(self):
        self.done = "dismiss"


def _run_alert(text):
    al = _FakeAlert(text)
    drv = _types.SimpleNamespace(switch_to=_types.SimpleNamespace(alert=al))
    _o = _br.WebDriverWait
    _br.WebDriverWait = lambda *a, **k: _types.SimpleNamespace(until=lambda f: True)
    try:
        _br.accept_alert(drv, timeout=1)
        return al.done, None
    except _br.DestructiveAlert as e:
        return al.done, e
    finally:
        _br.WebDriverWait = _o


_d, _exc = _run_alert(_DESTRUCTIVE_TXT)
check("alert: confirm ที่ลบข้อมูล → กด 'ยกเลิก' + โยน DestructiveAlert (ไม่กดตกลง)",
      _d == "dismiss" and isinstance(_exc, _br.DestructiveAlert))
_d2, _exc2 = _run_alert("บันทึกการแก้ไขเรียบร้อยแล้ว")
check("alert: alert ปกติยังกด 'ตกลง' ตามเดิม", _d2 == "accept" and _exc2 is None)
_d3, _ = _run_alert("กรุณาระบุ ยี่ห้อรถ")
check("alert: คำเตือน validation ยังกด 'ตกลง' (ไม่ใช่ข้อความทำลายข้อมูล)", _d3 == "accept")


def _run_ctype(current, want):
    """เรียก _select_car_type จริง โดยปลอมค่าที่อยู่ในช่อง — คืน (เรียก fuzzy_select ไหม)"""
    called = []
    _o = (emcs._current_select_text, emcs.fuzzy_select)
    emcs._current_select_text = lambda *a, **k: current
    emcs.fuzzy_select = lambda *a, **k: called.append(a[2]) or (a[2], 100)
    try:
        emcs._select_car_type(object(), want)
    finally:
        (emcs._current_select_text, emcs.fuzzy_select) = _o
    return called


check("ประเภทรถ: ช่องว่าง → เลือกให้ตามปกติ", _run_ctype("", "กระบะ") == ["กระบะ"])
check("ประเภทรถ: placeholder → เลือกให้ตามปกติ",
      _run_ctype("-- ระบุ --", "กระบะ") == ["กระบะ"])
check("ประเภทรถ: ค่าเดิมตรงอยู่แล้ว → ไม่แตะ (ไม่ยิง onchange = ไม่มี confirm)",
      _run_ctype("กระบะ", "กระบะ") == [])
check("ประเภทรถ: ค่าเดิมไม่ตรง → ⛔ ไม่เปลี่ยนทับ (EMCS จะลบข้อมูลที่บันทึกไว้)",
      _run_ctype("เก๋งเอเชีย", "เก๋งยุโรป") == [])
check("ประเภทรถ: มีค่าเดิม แต่ต้นทางว่าง → ไม่แตะ", _run_ctype("รถตู้", "") == [])

# ---- 22c-4. ผลคดี 'คู่กรณีผิด' → คู่กรณีคันที่ + การเรียกร้องค่าเสียหาย (EMCS บังคับ) ----
# vlidSurvey(): rdoAcc_Cause01 ติ๊ก → ต้องมี txtAcc_Cause_No + chkOpo_Result_ อย่างน้อย 1/5
class _FakeCb:
    def __init__(self, sel=False):
        self.sel, self.clicks = sel, 0

    def is_selected(self):
        return self.sel

    def click(self):
        self.clicks += 1
        self.sel = not self.sel


def _run_opo_fault(**kw):
    """เรียก _fill_opponent_fault จริง — คืน (ข้อความที่พิมพ์ลงช่อง, checkbox ที่ถูกติ๊ก)"""
    texts, boxes = {}, {i: _FakeCb() for i in range(5)}
    d = claim_data.ClaimData()
    for k, v in kw.items():
        setattr(d, k, v)
    _o = (emcs.set_text, emcs.log)
    emcs.set_text = lambda drv, eid, val: texts.__setitem__(eid, val)
    emcs.log = lambda *a, **k: None
    drv = _types.SimpleNamespace(
        find_element=lambda by, eid: boxes[int(eid.rsplit('_', 1)[1])])
    try:
        emcs._fill_opponent_fault(drv, d)
    finally:
        (emcs.set_text, emcs.log) = _o
    return texts, [i for i, b in boxes.items() if b.sel]


# ค่า 'ฝ่ายประมาท' ทั้ง 7 ตัวของแอป ต้อง fuzzy ไปลง radio ที่ถูกต้อง (โดยเฉพาะ
# 'คู่กรณีผิด' ต้องไม่ไปลง 'รถประกันเป็นฝ่ายผิด' ซึ่งหน้าตาใกล้กันมาก)
for _v, _rid in (('ฝ่ายผิด', 'rdoAcc_Cause00'), ('คู่กรณีผิด', 'rdoAcc_Cause01'),
                 ('ประมาทร่วม', 'rdoAcc_Cause02'), ('รอสรุปผลคดี', 'rdoAcc_Cause03'),
                 ('ฝ่ายถูกและผิด', 'rdoAcc_Cause04'), ('ยกเลิกการเคลม', 'rdoAcc_Cause05'),
                 ('ไปถึงแล้วไม่พบ', 'rdoAcc_Cause06')):
    _hit = _pc.extractOne(_v, list(emcs.CAUSE_RADIO.keys()), scorer=_fz.WRatio)
    check(f"ผลคดี: '{_v}' → {_rid}", emcs.CAUSE_RADIO[_hit[0]] == _rid)

_tx, _bx = _run_opo_fault(acc_fault_opponent_no='2', opo_results='คัดประจำวัน,บัตรติดต่อ')
check("คู่กรณีผิด: กรอก 'คู่กรณีคันที่' ตามข้อมูล + ติ๊กตรง index",
      _tx.get('txtAcc_Cause_No') == '2' and _bx == [0, 3])
_tx, _bx = _run_opo_fault(third_parties=[{'plate_no': '1กก1'}], opo_results='บันทึกยอมรับผิด')
check("คู่กรณีผิด: ไม่มีเลขคัน แต่มีคู่กรณีคันเดียว → เติม '1' ให้เอง",
      _tx.get('txtAcc_Cause_No') == '1' and _bx == [2])
_tx, _bx = _run_opo_fault(third_parties=[{'plate_no': 'ก'}, {'plate_no': 'ข'}],
                          opo_results='คัดประจำวัน')
check("คู่กรณีผิด: คู่กรณีหลายคัน + ไม่รู้เลขคัน → ไม่เดา (ปล่อยให้คนกรอก)",
      'txtAcc_Cause_No' not in _tx and _bx == [0])
_tx, _bx = _run_opo_fault(acc_fault_opponent_no='1', opo_results='')
check("คู่กรณีผิด: ไม่มีข้อมูลติ๊ก → ไม่ติ๊กมั่วแทนเซอร์เวย์", _bx == [])
# ป้ายฝั่งแอปกับ EMCS สะกดต่างกัน 2 ตัว — ติ๊กด้วย index จึงต้องเข้าได้ทั้งคู่
_, _bx1 = _run_opo_fault(acc_fault_opponent_no='1', opo_results='รับหลักฐานจากคู่กรณีผิด')
_, _bx2 = _run_opo_fault(acc_fault_opponent_no='1', opo_results='รับหลักฐานจากคู่กรณี')
check("คู่กรณีผิด: รับได้ทั้งป้ายแอปและป้าย EMCS (index เดียวกัน)", _bx1 == [1] and _bx2 == [1])

# 'รับเงินจำนวน' (index 4) — EMCS บังคับ 2 ช่องเงิน และ รับเงิน <= เรียกร้องทั้งหมด
_tx, _bx = _run_opo_fault(acc_fault_opponent_no='1', opo_results='รับเงิน',
                          opo_pay='5000', opo_recovery='17000')
check("รับเงินจำนวน: เงินครบและไม่เกิน → ติ๊ก + กรอก 2 ช่องเงิน",
      _bx == [4] and _tx.get('txtOpo_Pay') == '5000'
      and _tx.get('txtOpo_Recovery_Amount') == '17000')
_tx, _bx = _run_opo_fault(acc_fault_opponent_no='1', opo_results='รับเงิน', opo_pay='5000')
check("รับเงินจำนวน: ขาดยอดเรียกร้องทั้งหมด → ไม่ติ๊ก (กันบันทึก draft ไม่ผ่าน)",
      _bx == [] and 'txtOpo_Pay' not in _tx)
_tx, _bx = _run_opo_fault(acc_fault_opponent_no='1', opo_results='รับเงิน',
                          opo_pay='20000', opo_recovery='17000')
check("รับเงินจำนวน: รับเงินมากกว่ายอดเรียกร้อง → ไม่ติ๊ก (EMCS ไม่ยอม)", _bx == [])

# ---- 22c-5. หน่วยปี + EV + ติดตามงาน + รหัสบริษัทประกัน ----
check("ปีจดทะเบียน: พ.ศ. → ค.ศ. (EMCS รับแค่ 1900-2026)",
      emcs._year_ad('2567') == '2024' and emcs._year_ad('2566') == '2023')
check("ปีจดทะเบียน: ค.ศ. อยู่แล้ว ไม่แตะ", emcs._year_ad('2024') == '2024')
check("ปีจดทะเบียน: ว่าง/อ่านไม่ออก → ไม่พัง",
      emcs._year_ad('') == '' and emcs._year_ad(None) == '' and emcs._year_ad('ปี67') == 'ปี67')

check("ติดตามงาน: 3 สถานะของแอปตรง radio N/W/Y",
      emcs.FLU_TYPE_RADIO['ไม่มีการนัดหมาย'] == 'rdoFlu_Type_0'
      and emcs.FLU_TYPE_RADIO['รอการนัดหมาย'] == 'rdoFlu_Type_1'
      and emcs.FLU_TYPE_RADIO['มีการนัดหมาย'] == 'rdoFlu_Type_2')

# EV: value ของ ddlEvType = code ตรง ๆ ที่แอปเก็บ → select_by_value ไม่ต้อง fuzzy
_evsel = {}


class _FakeEvSel:
    def __init__(self, ok=True):
        self.ok = ok

    def select_by_value(self, v):
        if not self.ok:
            raise RuntimeError('no such option')
        _evsel['value'] = v


def _run_ev(code, ok=True, prefix=""):
    _evsel.clear()
    texts = {}
    _o = (emcs.Select, emcs.set_text, emcs.log)
    emcs.Select = lambda _e: _FakeEvSel(ok)
    emcs.set_text = lambda drv, eid, val: texts.__setitem__(eid, val)
    emcs.log = lambda *a, **k: None
    drv = _types.SimpleNamespace(find_element=lambda *a, **k: None)
    try:
        emcs._fill_ev(drv, prefix, code, 'BAT-001', 'WC-002', '2026-07-01')
    finally:
        (emcs.Select, emcs.set_text, emcs.log) = _o
    return _evsel.get('value'), texts


_v, _t = _run_ev('BEV')
check("EV: เลือกด้วย code + กรอกเลขแบต/เครื่องชาร์จ/วันเริ่มใช้",
      _v == 'BEV' and _t.get('txtBatt_Number') == 'BAT-001'
      and _t.get('txtWallcharge_number') == 'WC-002'
      and _t.get('wuCale_batt_effdate_txtCalendar', '').endswith('2569'))
_v, _t = _run_ev('')
check("EV: ไม่ใช่รถไฟฟ้า → ไม่แตะช่องไหนเลย", _v is None and _t == {})
_v, _t = _run_ev('HEV', ok=False)
check("EV: เลือกประเภทไม่ได้ → ไม่กรอกช่องอื่นต่อ (ปล่อยให้คนทำ)", _v is None and _t == {})
_v, _t = _run_ev('PHEV', prefix='dtlOpo_ctl00_wuOpo_')
check("EV คู่กรณี: ใช้ prefix + ชื่อ dropdown ddlEv_Type",
      _v == 'PHEV' and _t.get('dtlOpo_ctl00_wuOpo_txtBatt_Number') == 'BAT-001')

from autokey.insurer_map import resolve_insurer_code as _ric  # noqa: E402
check("บริษัทประกัน: resolve ครบทั้ง 7 บริษัทใน dropdown",
      all(_ric(n) == c for n, c in (
          ('ประกันภัยทดสอบ', '1'),
          ('บริษัท เดอะ วัน ประกันภัย จำกัด (มหาชน)', '4'),
          ('ไอโออิกรุงเทพประกันภัย', '1059'),
          ('ฟอลคอนประกันภัย จำกัด (มหาชน)', '1232'),
          ('บริษัท อลิอันซ์ อยุธยา ประกันภัย จำกัด (มหาชน)', '1723'),
          ('บริษัท เจมาร์ท ประกันภัย จํากัด (มหาชน)', '2424'),
          ('บริษัท ไทยไพบูลย์ประกันภัย จำกัด (มหาชน)', '2429'))))
check("บริษัทประกัน: บริษัทนอกลิสต์ → None (หยุด ไม่ import เข้าบริษัทผิด)",
      _ric('บริษัท วิริยะประกันภัย จำกัด (มหาชน)') is None)

# ---- 22c-6. หมวดรูปต่อรายการ: แปลงป้ายแอป → option dynamic ของ EMCS ----
_c = emcs._se_cat_to_emcs
# ⚠️ _se_cat_to_emcs ต้อง "คงป้ายเต็มของแอป" — ห้ามทิ้งคำแยก รถประกัน/รถคู่กรณี
# (เคยแก้ให้เขียนทับเป็น 'รูปผู้บาดเจ็บ คนที่ N' → ถ้า option dynamic ไม่โผล่ รูปฝั่ง
# รถประกันจะ fuzzy ตกไปถัง 'รูปผู้บาดเจ็บรถคู่กรณี' เงียบ ๆ) การเลือกป้าย dynamic
# ทำที่ _resolve_image_type ตอนอัปจริง ซึ่งอ่าน option บนหน้าได้
check("หมวดรูป: คงป้ายเต็มของแอป ไม่ทิ้งคำแยกฝั่งรถ",
      _c('รูปผู้บาดเจ็บรถคู่กรณี คนที่ 2') == 'รูปผู้บาดเจ็บรถคู่กรณี คนที่ 2'
      and _c('รูปผู้บาดเจ็บรถประกัน คนที่ 1') == 'รูปผู้บาดเจ็บรถประกัน คนที่ 1')
check("หมวดรูป: ทรัพย์สิน/คู่กรณีก็คงป้ายเดิม",
      _c('รูปทรัพย์สินอื่นๆของคู่กรณี ชิ้นที่ 3') == 'รูปทรัพย์สินอื่นๆของคู่กรณี ชิ้นที่ 3'
      and _c('รูปรถคู่กรณี คันที่ 2') == 'รูปรถคู่กรณี คันที่ 2')
check("หมวดรูป: ป้าย canonical ของ EMCS (เส้น zip) ต้องผ่าน ไม่ตกเป็น 'รูปประกอบ'",
      _c('รูปผู้บาดเจ็บ คนที่ 2') == 'รูปผู้บาดเจ็บ คนที่ 2'
      and _c('รูปทรัพย์สิน รายการที่ 3') == 'รูปทรัพย์สิน รายการที่ 3')


def _run_resolve(cat, opts):
    _o = (emcs.Select, emcs.log)
    emcs.Select = lambda _e: _types.SimpleNamespace(
        options=[_types.SimpleNamespace(text=o) for o in opts])
    emcs.log = lambda *a, **k: None
    drv = _types.SimpleNamespace(find_element=lambda *a, **k: None)
    try:
        return emcs._resolve_image_type(drv, cat)
    finally:
        (emcs.Select, emcs.log) = _o


_BASE_OPTS = ['-- ทั้งหมด --', 'รูปประกอบ', 'รูปรถประกัน', 'รูปรถคู่กรณี',
              'รูปผู้บาดเจ็บรถประกัน', 'รูปผู้บาดเจ็บรถคู่กรณี', 'รูปทรัพย์สินอื่นๆของคู่กรณี']
check("resolve: มี option dynamic → ใช้ป้าย dynamic (ผูกกับคนที่ N)",
      _run_resolve('รูปผู้บาดเจ็บรถคู่กรณี คนที่ 2',
                   _BASE_OPTS + ['รูปผู้บาดเจ็บ คนที่ 1', 'รูปผู้บาดเจ็บ คนที่ 2'])
      == 'รูปผู้บาดเจ็บ คนที่ 2')
check("resolve: ไม่มี option dynamic → ถอยไปป้ายฐาน 'ฝั่งถูก' ไม่ข้ามไปคู่กรณี",
      _run_resolve('รูปผู้บาดเจ็บรถประกัน คนที่ 2', _BASE_OPTS) == 'รูปผู้บาดเจ็บรถประกัน')
check("resolve: มี dynamic แค่ถึงคนที่ 3 แต่ขอคนที่ 12 → ถอยไปป้ายฐาน (ไม่เกาะคนที่ 1)",
      _run_resolve('รูปผู้บาดเจ็บรถคู่กรณี คนที่ 12',
                   _BASE_OPTS + ['รูปผู้บาดเจ็บ คนที่ 1', 'รูปผู้บาดเจ็บ คนที่ 3'])
      == 'รูปผู้บาดเจ็บรถคู่กรณี')
check("resolve: ทรัพย์สิน 'ชิ้นที่ N' → 'รูปทรัพย์สิน รายการที่ N' เมื่อมี option",
      _run_resolve('รูปทรัพย์สินอื่นๆของคู่กรณี ชิ้นที่ 2',
                   _BASE_OPTS + ['รูปทรัพย์สิน รายการที่ 2']) == 'รูปทรัพย์สิน รายการที่ 2')
check("resolve: หมวดธรรมดาไม่มีเลข → ส่งผ่านตามเดิม",
      _run_resolve('รูปรถประกัน', _BASE_OPTS) == 'รูปรถประกัน')

check("หมวดรูป: หมวดฐานไม่มีเลข → คงเดิม",
      _c('รูปรถประกัน') == 'รูปรถประกัน'
      and _c('รูปผู้บาดเจ็บรถคู่กรณี') == 'รูปผู้บาดเจ็บรถคู่กรณี')
check("หมวดรูป: ว่าง/ไม่รู้จัก → 'รูปประกอบ'",
      _c('') == 'รูปประกอบ' and _c('รูปอะไรไม่รู้') == 'รูปประกอบ')
import inspect as _insp  # noqa: E402
# ⚠️ regression: ลำดับ radio แอลกอฮอล์ของ EMCS สลับกับที่คนทั่วไปเดา
# label จริง: rdoAlc_Chk_0 = "ไม่มีการตรวจ" / rdoAlc_Chk_1 = "มีการตรวจ"
_alcsrc = _insp.getsource(emcs._fill_police_and_alcohol)
check("แอลกอฮอล์: ไม่ได้ตรวจ → ติ๊ก rdoAlc_Chk_0 (ไม่ใช่ _1)",
      "'0' if no_test else '1'" in _alcsrc, "เคยเขียนกลับด้านมาแล้ว")

# บล็อกตำรวจ + แอลกอฮอล์ + อีเมล + ค่าเสียหายส่วนแรก — แอปเก็บครบแต่เดิมบอทไม่เคยกรอก
_d_pol = claim_data.ClaimData()
_main._populate_claim_from_report(_d_pol, {
    'acc_police_name': 'พ.ต.ท. บุรินทร์ ทองก่อ', 'acc_police_station': 'สภ.สำโรงเหนือ',
    'acc_police_comment': 'รอสอบปากคำ', 'acc_police_date': '07/10/2568',
    'acc_police_book_no': 'บ.123/68', 'acc_alcohol_test': 'ตรวจแล้ว',
    'acc_alcohol_result': '35 mg%', 'assured_email': 'a@b.com', 'deductible': '2000'})
check("ตำรวจ/แอลกอฮอล์: map จาก report ครบ",
      (_d_pol.police_name, _d_pol.police_station, _d_pol.police_book_no,
       _d_pol.alcohol_result, _d_pol.assured_email, _d_pol.deductible)
      == ('พ.ต.ท. บุรินทร์ ทองก่อ', 'สภ.สำโรงเหนือ', 'บ.123/68', '35 mg%', 'a@b.com', '2000'))
_src_all = _insp.getsource(emcs)
for _eid in ('txtPolice_Name', 'txtPolice_Station', 'txtPolice_Comment', 'txtBook_Number',
             'wuCale_Police_Date_txtCalendar', 'rdoAlc_Chk_', 'txtAlc_Result',
             'txtAssured_Email', 'txtDeductible'):
    check(f"บอทกรอก {_eid} แล้ว (เดิม 0 hit)", _eid in _src_all)
# ตีความ 'ไม่ได้ตรวจ' → radio ไม่มีการตรวจ (index 1) และไม่กรอกช่องผล
_alc = _insp.getsource(emcs._fill_police_and_alcohol)
check("แอลกอฮอล์: ตีความ 'ไม่ได้ตรวจ' เป็น 'ไม่มีการตรวจ'",
      "ไม่ได้ตรวจ" in _alc and "no_test" in _alc)

# หน้าค่าใช้จ่าย: กติกา user 2026-08-03 — กรอกมาก/น้อยตาม "ต้นทางข้อมูล"
#   ISURVEY (หัวหน้ากรอกไว้แล้ว) = เต็มหน้า | se-survey (หัวหน้าจะกรอกเอง) = แค่ 2 ช่อง
# (ก่อนหน้านี้: 2026-07-27 กรอก 2 ช่องทุกกรณี — commit 9719228 ถอด fill_fee_table/set_textarea)
_fb = _insp.getsource(emcs.fill_billing)
_cut = _fb.index("if not full_billing:")       # ก่อนบรรทัดนี้ = กรอกทุกต้นทาง
check("ค่าใช้จ่าย: กรอกเลขที่ใบแจ้งหนี้ + วันที่วางบิล ทุกต้นทาง",
      0 < _fb.index('"txtBill_No"') < _cut
      and _fb.index('"wuCale_Bill_Date_txtCalendar"') < _cut)
check("ค่าใช้จ่าย (ISURVEY): กรอก 3 ช่องสรุป (ผลการดำเนินงาน/ความเห็นผู้ตรวจสอบ/เซอร์เวย์)",
      all(i in _fb for i in ("txtAcc_result", "txtAcc_Comment", "txtSurv_Comment")))
check("ค่าใช้จ่าย (ISURVEY): กรอกตารางราคาคอลัมน์ 'เสนอ' จาก data.bill",
      "fill_fee_table(driver, data.bill)" in _fb)
check("ค่าใช้จ่าย (se-survey): ไม่แตะ 3 ช่องสรุป — หัวหน้ากรอกเองใน EMCS",
      all(_fb.index(i) > _cut for i in
          ("txtAcc_result", "txtAcc_Comment", "txtSurv_Comment")))
check("ค่าใช้จ่าย (se-survey): ไม่แตะตารางราคา",
      _fb.index("fill_fee_table(driver, data.bill)") > _cut)
check("ค่าใช้จ่าย (se-survey): ยังกด 'บันทึกราคา' ก่อน return (ไม่งั้นหัวบิลไม่ติด)",
      _cut < _fb.index("_save_and_exit_billing(driver)") < _fb.index("return", _cut))
# call site ต้องผูกกับต้นทางจริง: se-survey → full_billing=False, ISURVEY → ตาม flag
_main_src = _insp.getsource(_main) if hasattr(_main, "__file__") else open(
    "main.py", encoding="utf-8").read()
check("call site: เส้นทาง se-survey ส่ง full_billing=False",
      _main_src.count("full_billing=False") == 2)
check("call site: เส้นทาง ISURVEY ส่ง full_billing=not args.no_save_price",
      _main_src.count("full_billing=not args.no_save_price") == 2)
# คอลัมน์อนุมัติของบริษัทประกัน (txtIns_*) disabled บนหน้าจริง — บอทต้องไม่แตะ
# และยอดรวม/VAT JS คำนวณเอง ห้ามพิมพ์ทับ
_ft = _insp.getsource(emcs.fill_fee_table)
# เทียบเฉพาะ id ที่ถูกส่งเป็น string literal จริง — docstring พูดถึง txtIns_* อยู่ (อธิบายว่าไม่แตะ)
check("ค่าใช้จ่าย: ไม่แตะคอลัมน์อนุมัติ txtIns_* (ของบริษัทประกัน)",
      '"txtIns_' not in _ft and "'txtIns_" not in _ft)
check("ค่าใช้จ่าย: ไม่พิมพ์ทับยอดรวม/VAT (JS คำนวณเอง)",
      not any(i in _ft for i in ("txtTotalPrice", "txtVatPrice", "txtGrandTotalPrice")))
check("ค่าใช้จ่าย: ไม่มี data.bill → ไม่เขียนเลขมั่ว (return ทันที)",
      "if not bill:" in _ft)

# "การเรียกร้องค่าเสียหายจากคู่กรณี" ไม่ผูกกับผลคดี — งานจริงติ๊กไว้ทั้งที่ผลคดี = รอสรุปผลคดี
_fv = _insp.getsource(emcs.fill_verdict)
check("เรียกร้องคู่กรณี: กรอกทุกผลคดี ไม่ใช่เฉพาะ rdoAcc_Cause01",
      '_fill_opponent_fault(driver, data)' in _fv
      and 'if CAUSE_RADIO[label] == "rdoAcc_Cause01":' not in _fv)

# ความเสียหายคู่กรณี: อ่านจำนวนช่องจริงจาก DOM (ฟอร์ม import มี 20 ไม่ใช่ 8)
_fod = _insp.getsource(emcs.fill_opponent_damage)
check("ความเสียหายคู่กรณี: อ่าน slot จริงจาก DOM แทน hardcode 8",
      '_free_text_slots(driver)' in _fod and '_slots[c]' in _fod)
check("ความเสียหายคู่กรณี: สูตร fallback เดิมให้ prefix ซ้ำเมื่อเกิน 8 (เหตุผลที่ต้องอ่าน slot)",
      len({f"ctl0{2 + (c % 4)}_{'A' if c < 4 else 'B'}" for c in range(12)}) == 8)

# รายละเอียดการเกิดเหตุ (แอปหน้า 5) → txtAcc_Detail
_fa = _insp.getsource(emcs.fill_accident) if hasattr(emcs, 'fill_accident') else ''
check("รายละเอียดการเกิดเหตุ: acc_detail → txtAcc_Detail",
      'txtAcc_Detail' in _insp.getsource(emcs))

# ประเภทผู้บาดเจ็บ: EMCS มี 5 ตัว แต่ XML มีรหัสแค่ 3 (DV/PV/ON) → ต้องพาป้ายไทยจากแอปไปเอง
_d_inj = claim_data.ClaimData()
_main._populate_claim_from_report(_d_inj, {'injured_persons': [
    {'name': 'นาย พาสกรณ์ มากพูน', 'cid': '3140500344748',
     'person_type': 'ผู้ขับขี่ - รถคู่กรณี', 'gender': 'ชาย'},
    {'name': 'น.ส. อุมาพร รื่นภาคลาภ', 'cid': '3100201875903',
     'person_type': 'ผู้ขับขี่ - รถประกัน', 'gender': 'หญิง'}]})
check("ผู้บาดเจ็บ: ดึงจาก report ได้ (เดิมมาจาก XML ทางเดียว)", len(_d_inj.injuries) == 2)
check("ผู้บาดเจ็บ: key ตรงกับที่ fill_injuries อ่าน (citizen_id/injure/car_regno)",
      'citizen_id' in _d_inj.injuries[0] and 'gender' in _d_inj.injuries[0])
check("ผู้บาดเจ็บ: ป้ายไทยฝั่งคู่กรณี → 02 (XML ยุบเป็น ON แยกไม่ได้)",
      emcs.PERSON_TYPE_LABEL[_d_inj.injuries[0]['person_type']] == '02')
check("ผู้บาดเจ็บ: ครบทั้ง 5 ตัวเลือกของ ddlPerson_Type",
      sorted({emcs.PERSON_TYPE_LABEL[k] for k in
              ['ผู้ขับขี่ - รถประกัน', 'ผู้ขับขี่ - รถคู่กรณี', 'ผู้โดยสาร - รถประกัน',
               'ผู้โดยสาร - รถคู่กรณี', 'บุคคลภายนอกรถ']})
      == ['01', '02', '03', '04', '05'])
check("ผู้บาดเจ็บ: รหัส XML เดิม (ISURVEY) ยังใช้ได้",
      emcs.PERSON_TYPE_MAP == {'DV': '01', 'PV': '03', 'ON': '05'})

# EMCS ฝั่งคู่กรณีไม่มี dropdown คำนำหน้าที่ใช้จริง — งานจริงใส่ในชื่อ ('นาย พาสกรณ์ มากพูน')
_d_tp = claim_data.ClaimData()
_main._populate_third_parties_from_report(_d_tp, {'opposing_parties': [
    {'title': 'นาย', 'first_name': 'พาสกรณ์', 'last_name': 'มากพูน'}]})
check("คู่กรณี: คำนำหน้าถูกต่อหน้าชื่อ (แอปบังคับเลือก แต่เดิมถูกทิ้ง)",
      _d_tp.third_parties[0]['drv_name'] == 'นาย พาสกรณ์ มากพูน',
      repr(_d_tp.third_parties[0]['drv_name']))
_d_tp2 = claim_data.ClaimData()
_main._populate_third_parties_from_report(_d_tp2, {'opposing_parties': [
    {'first_name': 'สมชาย', 'last_name': 'ใจดี'}]})
check("คู่กรณี: ไม่มีคำนำหน้า → ไม่มีเว้นวรรคนำหน้า",
      _d_tp2.third_parties[0]['drv_name'] == 'สมชาย ใจดี',
      repr(_d_tp2.third_parties[0]['drv_name']))

# ข้อความสรุป 3 ช่องบนหน้าค่าใช้จ่ายเป็น textarea — งานจริงเขียน bullet ~20 บรรทัด ห้ามยุบ
_d_txt = claim_data.ClaimData()
_NL = chr(10)
_main._populate_claim_from_report(
    _d_txt, {'survey_result': _NL.join(['ข้อ 1', '  ข้อ 2  ', '', 'ข้อ 3'])})
check("ผลการดำเนินงาน: คงบรรทัดใหม่ (ตัดช่องว่างหัวท้ายรายบรรทัด)",
      _d_txt.accident_summary == _NL.join(['ข้อ 1', 'ข้อ 2', '', 'ข้อ 3']),
      repr(_d_txt.accident_summary))

# เลขที่รับแจ้ง: ห้ามล้างทิ้งสำหรับบริษัทที่ไม่มี case ใน JS (ตกสาย default = บังคับเสมอ)
check("เลขที่รับแจ้ง: มีเฉพาะไอโออิ 1059 ที่ยอมให้ว่าง",
      emcs._CLAIMREF_OPTIONAL_INSURERS == {'1059'},
      str(emcs._CLAIMREF_OPTIONAL_INSURERS))

# ชื่อชิ้นส่วนบนแผนภาพมี ซ้าย/ขวา ในตัวแล้ว — ต่อท้ายซ้ำได้ 'ประตูหน้าซ้ายซ้าย' (12/19 ชิ้นโดน)
# ชื่อชิ้นส่วน = ป้าย checklist EMCS verbatim (ไม่มีข้างในชื่อ) + ด้านส่งแยกเป็น radio index
_dmg = _main._report_damage_items([
    {'part': 'ประตูหน้า', 'pos': 'L', 'level': 'M'},
    {'part': 'บังโคลนหลัง', 'pos': 'R', 'level': 'L'},
    {'part': 'กันชนหน้า', 'pos': 'A', 'level': 'H'},
    {'part': 'กระจกมองข้าง', 'pos': 'L', 'level': 'L'},
])
check("ความเสียหาย: ชื่อชิ้นส่วนไม่ถูกต่อ ซ้าย/ขวา (ต้องตรง checklist EMCS)",
      [x[0] for x in _dmg] == ['ประตูหน้า', 'บังโคลนหลัง', 'กันชนหน้า', 'กระจกมองข้าง'],
      str([x[0] for x in _dmg]))
check("ความเสียหาย: ระดับ → rank A-D ยังถูก",
      [x[1] for x in _dmg] == ['B', 'A', 'C', 'A'])
check("ความเสียหาย: ด้าน → index radio rdoDam_Left_Right (L=0 R=1 A=2)",
      [x[2] for x in _dmg] == ['0', '1', '2', '0'], str([x[2] for x in _dmg]))
# ชื่อทุกชิ้นบนแผนภาพต้องอยู่ใน checklist 22 ชิ้นของ EMCS ไม่งั้นตกช่องอิสระ
_EMCS22 = ['กันชนหน้า', 'กันชนหลัง', 'กระจกบังลมหน้า', 'กระจกบังลมหลัง', 'ฝากระโปรงหน้า',
           'ฝากระโปรงหลัง', 'กระจังหน้า', 'กระบะ', 'หลังคา', 'แผงท้าย', 'ฝาปิดท้าย', 'แค็ป',
           'ไฟหน้า', 'ไฟท้าย', 'บังโคลนหน้า', 'บังโคลนหลัง', 'ประตูหน้า', 'ประตูหลัง',
           'ไฟเลี้ยวหน้า', 'ไฟเลี้ยวหลัง', 'กระจกมองข้าง', 'บันได']
_diag = pathlib.Path(
    'C:/Users/i9/Desktop/se-survey/mobile/lib/widgets/car_damage_diagram.dart')
if _diag.exists():
    import re as _re
    _used = sorted(set(_re.findall(r"_cell\('([^']+)'", _diag.read_text(encoding='utf-8'))))
    check("แผนภาพมือถือ: ชื่อชิ้นส่วนทุกตัวอยู่ใน checklist 22 ของ EMCS",
          all(u in _EMCS22 for u in _used), str([u for u in _used if u not in _EMCS22]))

# 4 ช่องที่แอปเก็บ + EMCS มีช่อง แต่เดิมไม่มีอะไรพาไป
_d4 = claim_data.ClaimData()
_main._populate_claim_from_report(_d4, {
    'mileage': '45000', 'model_no': 'MDL-9', 'driver_by_policy': 'สมชาย ใจดี',
    'acc_surveyor_phone': '0812345678'})
check("พาไป EMCS: เลข กม./Model/ผู้ขับตามกรมธรรม์/โทรผู้สำรวจ",
      (_d4.mileage, _d4.model_no, _d4.driver_by_policy, _d4.surveyor_phone)
      == ('45000', 'MDL-9', 'สมชาย ใจดี', '0812345678'),
      f"{_d4.mileage}/{_d4.model_no}/{_d4.driver_by_policy}/{_d4.surveyor_phone}")

# ผลคดี: ทุกค่าที่แอปเก็บจริงต้อง exact-match — ห้ามตกไป fuzzy
# ('ฝ่ายผิด' ได้ WRatio 90 เท่ากันทั้ง 'รถประกันเป็นฝ่ายผิด'/'รถคู่กรณีเป็นฝ่ายผิด' = พลิกฝ่ายได้)
_APP_FAULT = {'ฝ่ายผิด': 'rdoAcc_Cause00', 'ฝ่ายถูกและผิด': 'rdoAcc_Cause04',
              'คู่กรณีผิด': 'rdoAcc_Cause01', 'ประมาทร่วม': 'rdoAcc_Cause02',
              'รอสรุปผลคดี': 'rdoAcc_Cause03', 'ยกเลิกการเคลม': 'rdoAcc_Cause05',
              'ไปถึงแล้วไม่พบ': 'rdoAcc_Cause06'}
for _v, _rid in _APP_FAULT.items():
    check(f"ผลคดี (ค่าจริงจากแอป): '{_v}' → {_rid} แบบ exact",
          emcs.CAUSE_RADIO.get(_v) == _rid, str(emcs.CAUSE_RADIO.get(_v)))
from rapidfuzz import fuzz as _fz
_amb = {emcs.CAUSE_RADIO[k] for k in emcs.CAUSE_RADIO if _fz.WRatio('ฝ่ายผิด', k) >= 89}
check("ผลคดี: 'ฝ่ายผิด' ยังคลุมเครือถ้าใช้ fuzzy (เหตุผลที่ต้อง exact)", len(_amb) > 1, str(_amb))

# EMCS แยก "ลูกค้าแจ้ง บ.ประกัน" กับ "บ.ประกันแจ้งสำรวจ" — เดิมบอทยัดค่าเดียวกันทั้งคู่
# (เจอตอนตรวจ draft S68426076667: ได้ 14:27 ทั้งที่ต้นทาง 14:10) → เวลาตอบสนองเพี้ยน
_d_two = claim_data.ClaimData()
_main._populate_claim_from_report(_d_two, {
    'acc_customer_report_date': '26/05/2569|14:10',
    'acc_insurance_notify_date': '26/05/2569|14:27'})
check("เวลาแจ้ง: ลูกค้าแจ้ง ≠ ประกันแจ้งสำรวจ (ไม่ยัดค่าเดียวกัน)",
      (_d_two.call_date, _d_two.call_time) == ('26/05/2569', '14:10')
      and (_d_two.noti_date, _d_two.noti_time) == ('26/05/2569', '14:27'),
      f"{_d_two.call_time} vs {_d_two.noti_time}")
_d_one = claim_data.ClaimData()
_main._populate_claim_from_report(_d_one, {'acc_insurance_notify_date': '26/05/2569|14:27'})
check("เวลาแจ้ง: ไม่มีเวลาลูกค้าแจ้ง (ISURVEY) → call_* ว่าง ให้ fill_accident fallback",
      (_d_one.call_date, _d_one.call_time) == ('', '')
      and _d_one.noti_time == '14:27')

# รูป "ยืนยันถึงที่เกิดเหตุ" = หลักฐานภายในของ se-survey ห้ามส่งเข้า EMCS (กติกา user 2026-07-26)
check("arrival: arrival.jpg = รูปยืนยันถึงที่เกิดเหตุ → ข้าม",
      _main._is_arrival_photo('arrival.jpg') and _main._is_arrival_photo('ARRIVAL.JPG')
      and _main._is_arrival_photo('arrival_2.jpg'))
check("arrival: รูปสำนวนปกติต้องไม่ถูกข้าม",
      not _main._is_arrival_photo('insured_car_1784940691177_0.jpg')
      and not _main._is_arrival_photo('arrivalcar.jpg')
      and not _main._is_arrival_photo('รูปรถประกัน_1.jpg'))

# zip export = "แหล่งรูป" ได้ด้วย ไม่ใช่แค่แหล่งหมวด — เคสที่ข้อมูลมาจาก XML export ล้วน
# (พนักงานไม่ได้ถ่ายผ่านแอป) เดิมไม่มีรูปขึ้น EMCS เลยเพราะ API คืนรูป 0 ใบ
import zipfile as _zf  # noqa: E402
_zdir = pathlib.Path(tempfile.mkdtemp())
_zp = _zdir / "export_TEST-CLAIM-9_202607.zip"
with _zf.ZipFile(_zp, "w") as _z:
    for _p in ("PICTURES/INS/a.jpg", "PICTURES/ACC_MAP/m1.jpg", "PICTURES/ACC_MAP/m2.jpg",
               "PICTURES/TP_VEH/1/t.jpg", "PICTURES/OTHERS/o.jpg", "PICTURES/INS/skip.pdf"):
        _z.writestr(_p, b"\xff\xd8\xff\xe0jpegdata")
_zcfg = _types.SimpleNamespace(sesurvey_zip_dir=str(_zdir), base_dir=_zdir)
_zout = pathlib.Path(tempfile.mkdtemp()) / "imgs"
_zres = _main._images_from_zip_drop(_zcfg, "TEST-CLAIM-9", _zout)
check("zip เป็นแหล่งรูป: เจอ zip ของเคลม → แตกรูปลงโฟลเดอร์", _zres == str(_zout))
check("zip เป็นแหล่งรูป: รูปรถประกัน/แผนที่/ประกอบ อยู่โฟลเดอร์หลัก + คู่กรณีแยก tp_veh",
      len(list(_zout.glob("*.jpg"))) == 4 and len(list((_zout / "tp_veh").rglob("*.jpg"))) == 1)
_zcat = _json.loads((_zout / "_categories.json").read_text(encoding="utf-8"))
check("zip เป็นแหล่งรูป: เขียนหมวดให้ครบ (ACC_MAP → รูปแผนที่เกิดเหตุ)",
      sorted(_zcat.values()) == sorted(['รูปรถประกัน', 'รูปแผนที่เกิดเหตุ',
                                        'รูปแผนที่เกิดเหตุ', 'รูปประกอบ']))
check("zip เป็นแหล่งรูป: ไม่มี zip ของเคลมนั้น → None (ไม่พัง)",
      _main._images_from_zip_drop(_zcfg, "NO-SUCH-CLAIM", _zout) is None)
# แตก zip ซ้ำ (dry-run หลายรอบก่อน live) ต้องไม่งอกไฟล์ _2/_3 → ไม่งั้นอัปรูปซ้ำเข้า EMCS
_main._images_from_zip_drop(_zcfg, "TEST-CLAIM-9", _zout)
_main._images_from_zip_drop(_zcfg, "TEST-CLAIM-9", _zout)
check("zip เป็นแหล่งรูป: แตกซ้ำ 3 รอบ = ไฟล์เท่าเดิม (idempotent ไม่งอก _2/_3)",
      len(list(_zout.glob("*.jpg"))) == 4
      and len(list((_zout / "tp_veh").rglob("*.jpg"))) == 1
      and not list(_zout.rglob("*_2.jpg")),
      str(sorted(p.name for p in _zout.glob("*.jpg"))))

check("หมวดรูป zip: ACC_MAP → 'รูปแผนที่เกิดเหตุ' (เดิมตกไป 'รูปประกอบ')",
      images.ZIP_CAT_TO_EMCS.get('ACC_MAP') == 'รูปแผนที่เกิดเหตุ')
check("หมวดรูป zip: INS/OTHERS/REPORTS ยังเหมือนเดิม",
      images.ZIP_CAT_TO_EMCS['INS'] == 'รูปรถประกัน'
      and images.ZIP_CAT_TO_EMCS['OTHERS'] == 'รูปประกอบ'
      and images.ZIP_CAT_TO_EMCS['REPORTS'] == 'รูปประกอบ')
check("หมวดรูป: zip export ใช้ป้ายเดียวกับ EMCS",
      images._TP_EXPORT_LABEL['TP_PERSON'].format(n=2) == 'รูปผู้บาดเจ็บ คนที่2'
      and images._TP_EXPORT_LABEL['TP_PROP'].format(n=2) == 'รูปทรัพย์สิน รายการที่2')

# ---- 22c-7. คำนำหน้า canonical + ตัดอักขระที่ EMCS ไม่รับ (noTyping) ----
_TITLE_OPTS = ['- คำนำหน้า -', 'นาย', 'นาง', 'นางสาว', 'ด.ช.', 'ด.ญ.', 'คุณ']
for _t in emcs.THAI_TITLES:
    _canon = emcs.EMCS_TITLE.get(_t, _t)
    check(f"คำนำหน้า: '{_t}' → '{_canon}' อยู่ในตัวเลือกจริงของ EMCS", _canon in _TITLE_OPTS)
# ของเดิม: โยนคำเต็มเข้า fuzzy ได้ผิดแบบเงียบ ('เด็กชาย'→'นาย' 72 ผ่านเกณฑ์ 40)
check("คำนำหน้า regress: คำเต็มดิบ fuzzy ไปผิดตัว (เหตุผลที่ต้อง map + ตัดที่ 90)",
      _pc.extractOne('เด็กชาย', _TITLE_OPTS, scorer=_fz.WRatio)[0] == 'นาย'
      and _pc.extractOne('น.ส.', _TITLE_OPTS, scorer=_fz.WRatio)[0] == 'ด.ช.')
check("คำนำหน้า: เกณฑ์ 90 — ค่าที่ map แล้วได้ 100 ทุกตัว",
      all(_pc.extractOne(emcs.EMCS_TITLE.get(t, t), _TITLE_OPTS, scorer=_fz.WRatio)[1] >= 90
          for t in emcs.THAI_TITLES))


class _FakeInput:
    def __init__(self, onkeypress=""):
        self._k, self.value = onkeypress, None

    def get_attribute(self, k):
        return self._k if k == "onkeypress" else None

    def clear(self):
        pass

    def send_keys(self, v):
        self.value = v


def _run_set_text(val, onkeypress=""):
    el = _FakeInput(onkeypress)
    drv = _types.SimpleNamespace(find_element=lambda *a, **k: el,
                                 execute_script=lambda *a, **k: None)
    _o = _br.log
    _br.log = lambda *a, **k: None
    try:
        _br.set_text(drv, "txtOwner", val)
    finally:
        _br.log = _o
    return el.value


check("noTyping: ช่องที่ EMCS บล็อกอักขระพิเศษ → ตัดออกก่อนส่ง (ค่าตรงกันทุก layout)",
      _run_set_text('บริษัท ทางด่วนกรุงเทพ จำกัด (มหาชน)', 'return noTyping(event)')
      == 'บริษัท ทางด่วนกรุงเทพ จำกัด มหาชน')
check("noTyping: ช่องปกติไม่ถูกแตะ (วงเล็บ/ทับ ยังอยู่ครบ)",
      _run_set_text('123/45 ซ.ทองหล่อ (ปากซอย)') == '123/45 ซ.ทองหล่อ (ปากซอย)')
check("noTyping: ตัดแล้วเหลือว่าง → ใส่ '-' (ช่องบังคับ)",
      _run_set_text('@#$%', 'return noTyping(event)') == '-')
check("noTyping: จุด/ขีด เป็นอักขระที่ EMCS ยอม — ไม่ถูกตัด",
      _run_set_text('บจก. เอ-บี', 'return noTyping(event)') == 'บจก. เอ-บี')

# ---- 22c-8. verify หลังบันทึก: ประเภทรถ/ยี่ห้อ ต้องติดจริง ----
# ยืนยันบน draft จริง 2026-07-26: บอท log '✓ ประเภทรถ 90' แต่วันถัดมาเปิดเรื่อง
# ทั้งประเภทรถและยี่ห้อเป็น '-- ระบุ --' = เลือกได้บนจอ แต่ไม่ commit ตอนบันทึก
def _run_verify(seq, want_type='เก๋งเอเชีย', want_brand='MG'):
    """seq = ค่าที่อ่านได้แต่ละรอบ [(ctype, cmfg), ...] — คืน (ผลลัพธ์, กรอกซ้ำกี่ครั้ง, บันทึกซ้ำกี่ครั้ง)"""
    state = {'i': 0, 'refill': 0, 'saves': 0}
    d = claim_data.ClaimData()
    d.prb_car_type, d.car_brand = want_type, want_brand
    _o = (emcs._current_select_text, emcs._select_car_type, emcs._select_car_brand, emcs.log)
    def cur(_drv, eid):
        i = min(state['i'], len(seq) - 1)
        return seq[i][0] if eid == 'ddlCType' else seq[i][1]
    emcs._current_select_text = cur
    emcs._select_car_type = lambda *a, **k: state.__setitem__('refill', state['refill'] + 1)
    emcs._select_car_brand = lambda *a, **k: state.__setitem__('refill', state['refill'] + 1)
    emcs.log = lambda *a, **k: None
    def save():
        state['saves'] += 1
        state['i'] += 1
    try:
        ok = emcs.verify_car_saved(object(), d, save)
    finally:
        (emcs._current_select_text, emcs._select_car_type,
         emcs._select_car_brand, emcs.log) = _o
    return ok, state['refill'], state['saves']


check("verify: ค่าติดครบตั้งแต่รอบแรก → ผ่าน ไม่กรอกซ้ำ",
      _run_verify([('เก๋งเอเชีย', 'MG')]) == (True, 0, 0))
check("verify: ยี่ห้อว่าง → กรอกซ้ำ+บันทึกซ้ำ แล้วผ่าน",
      _run_verify([('เก๋งเอเชีย', '-- ระบุ --'), ('เก๋งเอเชีย', 'MG')]) == (True, 1, 1))
check("verify: ว่างทั้งคู่ → กรอกซ้ำ 2 ช่อง แล้วผ่าน",
      _run_verify([('-- ระบุ --', '-- ระบุ --'), ('เก๋งเอเชีย', 'MG')]) == (True, 2, 1))
check("verify: ซ่อมแล้วยังไม่ติด → คืน False (ฟ้อง ไม่รายงานสำเร็จลวง)",
      _run_verify([('-- ระบุ --', '-- ระบุ --'), ('-- ระบุ --', '-- ระบุ --')])[0] is False)
check("verify: ต้นทางไม่มีข้อมูลยี่ห้อ → ไม่บังคับ (ผ่าน)",
      _run_verify([('เก๋งเอเชีย', '-- ระบุ --')], want_brand='') == (True, 0, 0))

# ---- 22d. fuzzy_select guard (end-to-end ด้วย dropdown ปลอม) ----
# placeholder ของ EMCS ไม่ได้ชื่อ '-- ระบุ --' เหมือนกันทุกช่อง ('-- จังหวัด --',
# '-- เขต --', '- คำนำหน้า -') → ตัดสินจาก value="0"/"" ของ option ตัวแรกแทน
import autokey.browser as _br  # noqa: E402


class _FOpt:
    def __init__(self, text, value):
        self.text, self._v = text, value

    def get_attribute(self, _k):
        return self._v


class _FSel:
    def __init__(self, opts):
        self.options = [_FOpt(t, v) for t, v in opts]
        self.picked = None

    def select_by_visible_text(self, t):
        self.picked = t


def _run_fuzzy(opts, value, **kw):
    """เรียก fuzzy_select จริงกับ dropdown ปลอม → คืน (ที่เลือก, ถูกถามให้กรอกเองไหม)"""
    fake, asked = _FSel(opts), []
    _o = (_br.Select, _br.wait_present, _br.wait_for_manual_fill,
          _br._current_select_text)
    _br.Select = lambda _e: fake
    _br.wait_present = lambda *a, **k: None
    _br.wait_for_manual_fill = lambda *a, **k: asked.append(a[0]) or False
    _br._current_select_text = lambda *a, **k: fake.picked or ""
    _drv = _types.SimpleNamespace(find_element=lambda *a, **k: None)
    try:
        _br.fuzzy_select(_drv, "ddlX", value, wait_options=False, **kw)
    finally:
        (_br.Select, _br.wait_present, _br.wait_for_manual_fill,
         _br._current_select_text) = _o
    return fake.picked, bool(asked)


# ตรงเป๊ะต้องมาก่อน fuzzy: WRatio ให้สตริงสั้นที่เป็นคำนำหน้าชนะตัวเต็มได้
_CAUSE = [("-- ระบุ --", "0"), ("รถหายโดยการฉ้อฉล", "70"),
          ("รถหายโดยการฉ้อฉล ตามสัญญาประกันภัย(A.P.HONDA)", "45")]
_pick, _ask = _run_fuzzy(_CAUSE, "รถหายโดยการฉ้อฉล ตามสัญญาประกันภัย(A.P.HONDA)")
check("guard: ค่าที่ลอก master มาเป๊ะ ไม่ถูก fuzzy แย่งไปตัวที่สั้นกว่า",
      _pick == "รถหายโดยการฉ้อฉล ตามสัญญาประกันภัย(A.P.HONDA)" and not _ask)
# กรณีที่ค่าจากแอป "ไม่" ตรง master (ค่าเก่าก่อนแก้ลิสต์) — fuzzy ยังเลือกตัวสั้นผิดอยู่
# = เหตุผลที่ต้องลอกป้ายให้ตรง master ตั้งแต่ต้นทาง ไม่ใช่หวังให้ fuzzy กู้ให้
check("regress: ค่าเก่าที่ไม่ตรง master → fuzzy เลือกตัวสั้นผิด (ต้องแก้ที่ต้นทาง)",
      _pc.extractOne("รถหายโดยการฉ้อฉล ตามสัญญาประกันภัย",
                     [o for o, _ in _CAUSE], scorer=_fz.WRatio)[0] == "รถหายโดยการฉ้อฉล")

# guard เลขลำดับใน fuzzy_select — 'คนที่ 12' ต้องไม่ไปเกาะ 'คนที่ 1'
_pick, _ask = _run_fuzzy([('-- ระบุ --', '0'), ('รูปผู้บาดเจ็บ คนที่ 1', '14001'),
                          ('รูปผู้บาดเจ็บ คนที่ 3', '14003')], 'รูปผู้บาดเจ็บ คนที่ 12')
check("guard: เลขลำดับไม่ตรง → ไม่เลือกให้ (กันรูปติดผิดคน)", _pick is None and _ask)
_pick, _ask = _run_fuzzy([('-- ระบุ --', '0'), ('รูปผู้บาดเจ็บ คนที่ 1', '14001'),
                          ('รูปผู้บาดเจ็บ คนที่ 2', '14002')], 'รูปผู้บาดเจ็บ คนที่ 2')
check("guard: เลขตรง → เลือกได้ปกติ", _pick == 'รูปผู้บาดเจ็บ คนที่ 2' and not _ask)
# guard ต้องจับเฉพาะ "คำบอกลำดับ" — ยี่ห้อที่พ่วงรุ่นต้องไม่โดนตัดทิ้ง
_pick, _ask = _run_fuzzy([('-- ระบุ --', '0'), ('MAZDA', '67A'), ('MG', '515A')],
                         'MG 3', min_score=_BMS)
check("guard: ยี่ห้อพ่วงรุ่น ('MG 3') ยังเลือก MG ได้ — ไม่โดน guard เลขลำดับ",
      _pick == 'MG' and not _ask)

_DISTRICTS = [("-- เขต --", "0"), ("เขตวัฒนา", "221"), ("เขตบึงกุ่ม", "227")]
_pick, _ask = _run_fuzzy(_DISTRICTS, "เขตวัฒนา")
check("guard: ค่าตรง → เลือกปกติ ไม่ถามคน", _pick == "เขตวัฒนา" and not _ask)
_pick, _ask = _run_fuzzy(_DISTRICTS, "-")
check("guard: '-' (ไม่มีข้อมูล) → ไม่เลือก '-- เขต --' แต่หยุดถามคน",
      _pick is None and _ask)
_pick, _ask = _run_fuzzy([("- คำนำหน้า -", "0"), ("นาย", "1"), ("นาง", "2")], "-")
check("guard: placeholder ชื่ออื่น ('- คำนำหน้า -') ก็ไม่ถูกเลือก", _pick is None and _ask)
_pick, _ask = _run_fuzzy([("-- ระบุ --", "0"), ("MG", "515A"), ("NISSAN", "70A")],
                         "เอ็มจี")
check("guard: ยี่ห้อไทยดิบ (บั๊ก #104) → ไม่เลือก placeholder แต่หยุดถามคน",
      _pick is None and _ask)
_pick, _ask = _run_fuzzy([("-- ระบุ --", "0"), ("TRUMPCHI", "1"), ("MG", "2")],
                         "TRIUMPH", min_score=_BMS)
check("guard: min_score=90 กันยี่ห้อเกาะผิด (TRIUMPH ไม่กลายเป็น TRUMPCHI)",
      _pick is None and _ask)

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
# nosaveprice → --no-save-price (โหมดทดสอบ ไม่บันทึกราคา); ไม่ติ๊ก = ไม่มี
_cmd, _e = _webui._build_cmd({"claims": "2026013041465", "nosaveprice": True})
check("build_cmd nosaveprice: มี --no-save-price",
      _e is None and "--no-save-price" in _cmd)
_cmd, _e = _webui._build_cmd({"claims": "2026013041465"})
check("build_cmd default: ไม่มี --no-save-price (บันทึกราคาตามปกติ)",
      _e is None and "--no-save-price" not in _cmd)
# forcenew → --force-new (สร้างเรื่องใหม่แม้มีเรื่องเดิม); ไม่ติ๊ก = ไม่มี (กันเปิดซ้ำ)
_cmd, _e = _webui._build_cmd({"claims": "2026013048453", "forcenew": True})
check("build_cmd forcenew: มี --force-new",
      _e is None and "--force-new" in _cmd)
_cmd, _e = _webui._build_cmd({"claims": "2026013048453"})
check("build_cmd default: ไม่มี --force-new (ด่านกันเปิดเรื่องซ้ำทำงาน)",
      _e is None and "--force-new" not in _cmd)
# importxml → --import-xml (โหมดนำเข้า XML); ไม่ติ๊ก = ไม่มี
_cmd, _e = _webui._build_cmd({"claims": "2026013144715", "importxml": True})
check("build_cmd importxml: มี --import-xml",
      _e is None and "--import-xml" in _cmd)
_cmd, _e = _webui._build_cmd({"claims": "2026013144715"})
check("build_cmd default: ไม่มี --import-xml (โหมดกรอกฟอร์มปกติ)",
      _e is None and "--import-xml" not in _cmd)
# checklicense → --check-license (ตรวจใบขับขี่ด้วย OCR); ไม่ติ๊ก = ไม่มี
_cmd, _e = _webui._build_cmd({"claims": "2026013144715", "checklicense": True})
check("build_cmd checklicense: มี --check-license",
      _e is None and "--check-license" in _cmd)
_cmd, _e = _webui._build_cmd({"claims": "2026013144715"})
check("build_cmd default: ไม่มี --check-license (ไม่ตรวจใบขับขี่)",
      _e is None and "--check-license" not in _cmd)

# ---- 24. find_case guard: หลายเซอร์เวย์ + ไม่ระบุ invoice → หยุด+ถาม ----
from autokey import isurvey_api as _iapi  # noqa: E402
_api = _iapi.ISurveyAPI(cfg)
_two = {"cases": [
    {"caseID": "1", "claim_no": "X", "survey_no": "SE-A",
     "surveyor_name": "ก", "close_datetime": ""},
    {"caseID": "2", "claim_no": "X", "survey_no": "SE-B",
     "surveyor_name": "ข", "close_datetime": "2026-06-23 11:25"},
]}
_api._get = lambda *a, **k: _two
_raised = ""
try:
    _api.find_case("X", "")
except RuntimeError as _ex:
    _raised = str(_ex)
check("find_case: หลายเซอร์เวย์ + ไม่ระบุ invoice → หยุด (list ทั้ง 2 แถว)",
      "SE-A" in _raised and "SE-B" in _raised and "ปิดงาน" in _raised)
check("find_case: ระบุ invoice → เลือกแถวที่ survey_no ตรง",
      _api.find_case("X", "SE-B")["caseID"] == "2")
_api._get = lambda *a, **k: {"cases": [
    {"caseID": "9", "claim_no": "X", "survey_no": "SE-A"}]}
check("find_case: แถวเดียว ไม่ระบุ invoice → ไม่หยุด (เลือกเลย)",
      _api.find_case("X", "")["caseID"] == "9")
check("_multi_survey_msg: ขึ้น ✓ ปิดงาน เฉพาะแถวที่ close_datetime มีค่า",
      "✓ ปิดงาน 2026-06-23 11:25" in _iapi._multi_survey_msg("X", _two["cases"])
      and _iapi._multi_survey_msg("X", _two["cases"]).count("ยังไม่ปิดงาน") == 1)

# ---- 25. license_ocr: ตรวจหา+อ่านใบขับขี่ (ส่วน pure-python ไม่ต้องมี easyocr) ----
from autokey import license_ocr as _lic  # noqa: E402

# license_score: fuzzy match ทนต่อ OCR เพี้ยน (บัตรเคลือบมัน) — ข้อความจริงจากรูปทดสอบ
_garbled = ("ประเรศไทย\nไบอนญาตชับรถยนลลวนปบคอล\nKINGDOM OFFTHATAND\n"
            "ฉบับ67004060\nmiss phatmarika anyamanee")
check("license_score: OCR เพี้ยน (fuzzy) ยังตรวจเจอ (>=2)",
      _lic.license_score(_garbled) >= 2, str(_lic.license_score(_garbled)))
check("license_score: ข้อความชัดเจน → ครบ 4 กลุ่ม",
      _lic.license_score("ใบอนุญาตขับรถยนต์ส่วนบุคคล\nKingdom of Thailand\n"
                         "Private Car Driving Licence") == 4)
check("license_score: ไม่ใช่ใบขับขี่ → < 2",
      _lic.license_score("กันชนหน้าซ้าย บุบ\nประตูหลังขวา ครูด") < 2)
# is_license_text: ต้องมี keyword หมวดใบขับขี่จริง ไม่ใช่แค่ ประเทศ+รถยนต์
check("is_license_text: ใบขับขี่ (มี keyword ใบอนุญาตขับ) → True",
      _lic.is_license_text(_garbled) is True)
check("is_license_text: เอกสารอื่น (ประเทศ+รถยนต์ ไม่มีคำว่าใบขับขี่) → False",
      _lic.is_license_text("ประเทศไทย\nรถยนต์นั่งส่วนบุคคล\nคู่มือจดทะเบียน")
      is False
      and _lic.license_score("ประเทศไทย\nรถยนต์นั่งส่วนบุคคล") == 2)

# parse_license_fields: ดึงฟิลด์ที่ OCR แม่น (เลข 8/13 หลัก + วันที่ + ชื่อ)
_lf = _lic.parse_license_fields([
    "ใบอนุญาตขับรถยนต์ส่วนบุคคล", "Private Car Driving Licence",
    "ฉบับที่ 67004060", "Issue Date 19 February 2024",
    "Expiry Date 6 July 2029", "MISS PHATTHARIKA ANYAMANEE",
    "Birth Date 6 July 1986", "ID No. 1 1014 00724 82 9",
])
check("parse: เลขใบขับขี่ 8 หลัก", _lf["license_no"] == "67004060")
check("parse: เลขบัตร 13 หลัก (ยุบช่องว่าง)", _lf["id_no"] == "1101400724829")
check("parse: ชื่ออังกฤษ (ตัดคำนำหน้า MISS)",
      _lf["name_en"] == "PHATTHARIKA ANYAMANEE")
check("parse: วันออก/หมดอายุ/เกิด (อังกฤษ → dd/mm/yyyy)",
      _lf["issue_date"] == "19/02/2024" and _lf["expiry_date"] == "06/07/2029"
      and _lf["birth_date"] == "06/07/1986")
check("parse: ประเภท (รถยนต์ส่วนบุคคล)", _lf["card_type"] == "รถยนต์ส่วนบุคคล")

# วันที่ไทย พ.ศ. → ค.ศ. + จัดประเภทตาม keyword
_lf_th = _lic.parse_license_fields([
    "วันออกใบอนุญาต 19 กุมภาพันธ์ 2567",
    "วันสิ้นอายุ 6 กรกฎาคม 2572"])
check("parse: วันที่ไทย พ.ศ.→ค.ศ. + แยก issue/expiry",
      _lf_th["issue_date"] == "19/02/2024"
      and _lf_th["expiry_date"] == "06/07/2029")

# เลขบัตรถูกตัดข้ามบรรทัด (OCR แยกบรรทัด) — ยังรวมเป็น 13 หลักได้
check("parse: เลขบัตรข้ามบรรทัด → รวมเป็น 13 หลัก",
      _lic._find_id_no("1 1014 00724\n82 9") == "1101400724829")
check("parse: เลขแค่ 12 หลัก (OCR ตกหลัก) → ไม่รับเป็นเลขบัตร",
      _lic._find_id_no("1014 00724 82 3") == "")

# _find_name_en: รับตัวพิมพ์เล็ก (EasyOCR คืน lowercase) + เลือกบรรทัดมีคำนำหน้าก่อน
check("name: รับ lowercase",
      _lic._find_name_en(["miss phatmarika anyamanee"]) == "PHATMARIKA ANYAMANEE")
check("name: คำบนบัตร (KINGDOM/THAILAND) ไม่ถูกหยิบเป็นชื่อ",
      _lic._find_name_en(["KINGDOM OFFTHATAND"]) == "")

# cross_check: เทียบเลขใบขับขี่/เลขบัตรกับข้อมูลเคลม (ยุบขีด/ช่องว่าง)
_lic_data = claim_data.ClaimData(
    driver_license_no="67004060", driver_idcard="1-1014-00724-82-9")
_cc = _lic.cross_check({"license_no": "67004060", "id_no": "1101400724829"},
                       _lic_data)
check("cross_check: เลขตรง → match True ทั้งสอง",
      len(_cc) == 2 and all(c["match"] for c in _cc))
_cc2 = _lic.cross_check({"license_no": "99999999", "id_no": ""}, _lic_data)
check("cross_check: เลขใบขับขี่ไม่ตรง → match False + ข้ามฟิลด์ที่ว่าง",
      len(_cc2) == 1 and _cc2[0]["match"] is False)

print("\n" + ("ALL PASS ✅" if not failures else f"FAILED ❌: {failures}"))
sys.exit(1 if failures else 0)
