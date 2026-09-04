# -*- coding: utf-8 -*-
"""เทสสัญญาของตัวแปลง ISURVEY → se-survey (isurvey_to_sesurvey.build_case)

ล็อกกติกาการแปลงค่าที่เคยพังแล้วทีละเรื่อง (03/09/69 เคส #221 + audit 3 เคส) ไม่ให้กลับมาอีก
ข้อมูลในเทสเป็นของสมมติที่ **โครงคีย์ตรงกับ API จริง** (ดู tools/audit_isurvey_converter.py ที่ดัมป์ของจริงไว้)

รัน:  python -m pytest tests/test_isurvey_to_sesurvey.py -q   หรือ   python tests/test_isurvey_to_sesurvey.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import isurvey_to_sesurvey as conv   # noqa: E402


class FakeAPI:
    """โครงเดียวกับ ISurveyAPI เฉพาะที่ build_case เรียก — record ห่อคีย์เหมือนของจริง (driver/patient/property)"""

    def __init__(self):
        self.tabs = {
            1: {"Claim": {"claim_no": "2026019999999", "survey_no": "SEABI-220260899999", "notify_no": "2026147000",
                          "surveyor_name": "SEC343 นาย มี วงษ์สุวรรณ", "acc_verdictID": "03", "claim_MtypeID": "2"},
                "Dispatch": {"dispatch_date": "2026-08-28", "dispatch_time": "14:41", "arrive_date": "2026-08-28",
                             "arrive_time": "15:43", "finish_date": "2026-08-28", "finish_time": "16:09"},
                "bill": {"INVEST_NUM": "0", "INS_INVEST": "500.00", "TRANS_NUM": "0", "INS_TRANS": "600.00",
                         "PHOTO_NUM": "0", "INS_PHOTO": "50.00", "INS_TEL": "0.00", "INS_INSURE": "0.00",
                         "INS_CLAIM": "0.00", "INS_DAILY": "0.00", "INS_OTHER": "0.00"},
                "accident_summary": "ความเห็นหัวหน้า"},
            2: {"Accident": {"acc_date": "2026-08-22", "acc_time": "10:00", "acc_provinceID": "20", "acc_amphurID": "2006",
                             "acc_place": "บริษัท ทดสอบ จำกัด", "acc_detail": "รายละเอียด",
                             "acc_type_desc": "ชนวัสดุ/สิ่งของ เช่น เสา,กำแพง,ประตู ฯลฯ", "surveyor_comment": "ความเห็นช่าง"}},
            3: {"vehTID": "3", "plate_no": "9กจ6163", "plate_provinceID": "10", "car_brand": "FORD", "car_color": "เทา",
                "D_TOTAL_COST": "8000",
                "Driver": {"drv_name": "วิไลรัตน์ อินเทพ", "drv_gender": "F", "lic_typeID": "15", "relation": "ลูกจ้าง",
                           "age": "29", "birthdate": "1996-09-18", "IDcard_no": "1200500072660",
                           "drv_provinceID": "20", "drv_amphurID": "2006", "lic_issue_provinceID": "48"}},
            7: {"Policy": {"policy_no": "525013111407", "assured_name": "บริษัท โตโยต้า ลีสซิ่ง (ประเทศไทย) จำกัด",
                           "policy_TypeID": "ประเภท 1", "effective_date": "2025-10-12", "expiry_date": "2026-10-12"}},
            8: {"Accident": {"notified_date": "2026-08-28", "notified_time": "14:38"}},
        }
        self.parts = [
            {"partname": "บังโคลนหน้าขวา", "damaged_level": "A", "damage_type_detail": "บุบ,ครูด,", "LABOUR_COST": "3000"},
            {"partname": "ไฟหน้าซ้าย-ขวา", "damaged_level": "C", "damage_type_detail": "", "LABOUR_COST": ""},
            {"partname": "กันชนหน้า + คิ้ว", "damaged_level": "B", "damage_type_detail": "", "LABOUR_COST": ""},
            {"partname": "กระจกมองข้างซ้าย", "damaged_level": "D", "damage_type_detail": "", "LABOUR_COST": ""},
        ]
        self.records = {
            4: [("k4", {"vehTID": "3", "plate_provinceID": "10", "plate_no": "1กก1", "oth_insure_companyID": "136",
                        "oth_insure_company_name": None, "oth_insure_typeID": "52", "oth_policy_no": "P1",
                        "owner_name": None, "D_SPRP": "1000", "D_LABOUR": "500", "D_OTH": "",
                        "driver": {"drv_name": "นาย พาสกรณ์ (ทดสอบ)", "drv_gender": "M", "lic_typeID": "15",
                                   "relation": "เจ้าของรถ", "drv_provinceID": "33", "drv_amphurID": "3306",
                                   "IDcard_no": "21401661"}})],
            5: [("k5", {"patient": {"person_name": "น.ส. อุมาพร ทดสอบ", "injury_type": "I", "related_accidentID": "2",
                                    "gender": "F", "age": "30"}})],
            6: [("k6", {"property": {"prop_name": "กำแพง", "prop_damage_detail": "กำแพงปูน 2 แผ่น", "damage_cost": "20000",
                                     "owner_name": "น.ส. มติกา (เจ้าของ)", "owner_phone": "0629979153", "owner_address": "613 ม.1"}})],
        }
        self.masters = {
            "masterClaimVerdict": {"01": "รอคำตัดสิน", "02": "รถประกันเป็นฝ่ายถูก", "03": "รถประกันเป็นฝ่ายผิด",
                                   "04": "ประมาทร่วม", "05": "ไม่มีคู่กรณี", "06": "รถประกันเป็นฝ่ายถูกและผิด",
                                   "07": "ไปถึงแล้วไม่พบ", "08": "ยกเลิกการเคลม"},
            "masterClaimMType": {"01": "เคลมสด", "02": "เคลมแห้ง", "03": "ติดตาม", "04": "เจรจาสินไหม"},
            "masterDrvLicense": {"15": "ใบขับขี่รถยนต์ส่วนบุคคล", "99": "ไม่มีใบขับขี่"},
            "masterPolicyType": {"01": "ประเภท 1", "52": "ประเภท 2+"},
        }

    def get_tab(self, cid, t): return self.tabs.get(t, {})
    def get_parts(self, cid): return self.parts
    def list_records(self, cid, t): return [{"ikey": k} for k, _ in self.records.get(t, [])]
    def get_record(self, cid, t, ikey): return dict(self.records.get(t, []))[ikey]
    def master(self, name, k, v): return self.masters.get(name, {})
    def _company(self, code): return "บริษัท ทิพยประกันภัย จำกัด (มหาชน)" if code == "136" else ""
    def opponent_parts(self, cid, ikey):
        return [{"part": "ประตูหน้าซ้าย", "type": "บุบ,", "level": "B", "labour": "1500", "parts": "0", "memo": ""},
                {"part": "กระจกมองข้างซ้าย", "type": "แตก,", "level": "A", "labour": "", "parts": "800", "memo": ""}] if ikey == "k4" else []
    def _tumbon(self, c): return ""
    def _amphur(self, c): return ""
    def _prov(self, c): return ""


def _build():
    return conv.build_case(FakeAPI(), "case1", {"emp_phone": "0988639214"})


def test_driver_gender_is_mf_code():
    r = _build()["report"]
    assert r["driver_gender"] == "F"                    # ไม่ใช่ 'หญิง'


def test_damage_level_and_side_split():
    items = _build()["report"]["insured_damage"]
    assert [(i["part"], i["pos"], i["level"]) for i in items] == [
        ("บังโคลนหน้า", "R", "L"),        # ข้างท้ายชื่อ → pos · rank A → L
        ("ไฟหน้า", "A", "H"),             # ซ้าย-ขวา → ทั้งคู่ · C → H
        ("กันชนหน้า + คิ้ว", "A", "M"),   # ไม่มีข้าง → คงชื่อ · B → M
        ("กระจกมองข้าง", "L", "X"),        # ซ้าย → L · D → X
    ]


def test_side_split_keeps_checklist_names():
    assert conv.split_part_side("ครอบไฟตัดหมอกด้านซ้าย") == ("ครอบไฟตัดหมอก", "L")
    assert conv.split_part_side("กระจกมองข้างขวา") == ("กระจกมองข้าง", "R")      # 'ข้าง' เป็นส่วนของชื่อ
    assert conv.split_part_side("บันไดประตูข้างขวา") == ("บันไดประตูข้าง", "R")
    assert conv.split_part_side("กันชนหน้า(ใหญ่)") == ("กันชนหน้า(ใหญ่)", "A")


def test_photo_fee_unit_and_counts_inferred():
    ex = _build()["expenses"]
    assert (ex["photo_fee_count"], ex["photo_fee_price"]) == (10, 5)   # ยอดรวม 50 จำนวน 0 → 10 × 5
    assert ex["service_fee_count"] == 1 and ex["travel_fee_count"] == 1  # มียอดแต่จำนวน 0 → 1 ครั้ง
    assert ex["service_fee_price"] == 500 and ex["travel_fee_price"] == 600


def test_photo_split_edge_cases():
    assert conv._photo_split("50.00", "10") == (10, "5")
    assert conv._photo_split("0.00", "0") == (0, 0)
    assert conv._photo_split("75", "0") == (15, 5)
    assert conv._photo_split("37", "0") == (1, 37)


def test_relation_accepts_labels_and_codes():
    r = _build()["report"]
    assert r["driver_relation"] == "ลูกจ้าง"
    assert r["opposing_parties"][0]["relation"] == "เจ้าของรถ"
    assert conv._relation("04") == "ลูกจ้าง" and conv._relation("คนรู้จัก") == ""


def test_names_are_emcs_safe():
    p = _build()
    r = p["report"]
    assert r["assured_name"] == "บริษัท โตโยต้า ลีสซิ่ง ประเทศไทย จำกัด"   # วงเล็บหาย ช่องว่างยุบ
    assert " " not in r["acc_surveyor"] and r["acc_surveyor"] == "SEC343 นาย มี วงษ์สุวรรณ"
    assert r["opposing_parties"][0]["first_name"] == "พาสกรณ์"
    assert r["damaged_property"][0]["owner_name"] == "น.ส. มติกา เจ้าของ"


def test_opponent_insurer_resolved_from_company_code():
    o = _build()["report"]["opposing_parties"][0]
    assert o["insurer"]                                   # เดิม None → ว่างทุกเคส
    assert o["policy_type"] == "ประเภท 2+"
    assert o["license_type"] == "ใบขับขี่รถยนต์ส่วนบุคคล"
    assert o["gender"] == "ชาย"                           # คู่กรณีใช้คำไทย (คนละกติกากับผู้ขับขี่รถประกัน)
    assert o["estimated_cost"] == "1500"


def test_opponent_damage_pulled_from_parts_api():
    o = _build()["report"]["opposing_parties"][0]
    assert [(d["part"], d["pos"], d["level"]) for d in o["damage"]] == [("ประตูหน้า", "L", "M"), ("กระจกมองข้าง", "L", "L")]
    assert o["estimated_cost"] == "1500"     # ยอดรวมจาก record (D_SPRP+D_LABOUR) มีอยู่แล้ว ไม่ทับด้วยรายชิ้น


def test_opponent_cost_falls_back_to_parts_when_record_total_empty():
    api = FakeAPI()
    rec = dict(api.records[4][0][1]); rec.update({"D_SPRP": "", "D_LABOUR": "", "D_OTH": ""})
    api.records[4] = [("k4", rec)]
    o = conv.build_case(api, "case1", {})["report"]["opposing_parties"][0]
    assert o["estimated_cost"] == "2300"     # 1500 ค่าแรง + 800 อะไหล่


def test_property_record_is_unwrapped():
    a = _build()["report"]["damaged_property"][0]
    assert a["item"] == "กำแพง" and a["detail"] == "กำแพงปูน 2 แผ่น" and a["estimated_cost"] == "20000"


def test_injured_mapping():
    p = _build()["report"]["injured_persons"][0]
    assert p["person_type"] == "ผู้ขับขี่ - รถประกัน" and p["wound_level"] == "บาดเจ็บ - ปานกลาง" and p["gender"] == "หญิง"


def test_driver_id_type_and_titles():
    r = _build()["report"]
    assert r["driver_id_type"] == "thai" and r["driver_title"] == "คุณ"        # ไม่มีคำนำหน้า → คุณ
    assert _build()["report"]["opposing_parties"][0]["title"] == "นาย"


def test_dates_are_buddhist_and_timeline_format():
    r = _build()["report"]
    assert r["acc_date"] == "22/08/2569" and r["acc_time"] == "10:00"
    assert r["policy_start"] == "12/10/2568"
    assert r["acc_customer_report_date"] == "28/08/2569|14:38"
    assert r["driver_birthdate"] == "18/09/2539"


def test_verdict_claim_type_and_places():
    r = _build()["report"]
    assert r["acc_fault"] == "รถประกันเป็นฝ่ายผิด" and r["claim_type"] == "D"
    assert r["acc_province"] == "ชลบุรี" and r["acc_district"] == "อำเภอพนัสนิคม"
    assert r["car_province"] == "กรุงเทพ ฯ" and r["car_type"] == "T"
    assert r["driver_license_place"] == "นครพนม"


if __name__ == "__main__":
    import traceback
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception:
                fails += 1; print("FAIL", name); traceback.print_exc()
    sys.exit(1 if fails else 0)
