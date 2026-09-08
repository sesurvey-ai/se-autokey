# -*- coding: utf-8 -*-
"""เทสสัญญาของตัวปิดงาน ISURVEY (isurvey_close.build_payload / check_can_close)

ล็อกให้ payload ตรงกับคำสั่งที่ดักได้จากหน้าเว็บจริง 08/09/69 (เคลม 2026013169905, 87 ช่อง — ดู memory
isurvey-writeback-plan) เพื่อว่าถ้าใครแก้ชื่อช่อง/รูปแบบเงิน/กติกา close_case แล้วเทสจะฟ้องก่อนไปยิงของจริง
ข้อมูลในเทสเป็นของสมมติที่ **โครงคีย์ตรงกับ getcaseinfo tab-1 จริง**

รัน:  python -m pytest tests/test_isurvey_close.py -q   หรือ   python tests/test_isurvey_close.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import isurvey_close as ic   # noqa: E402

# 87 ช่องที่หน้าเว็บส่งจริง (ลำดับตามฟอร์ม)
CAPTURED_KEYS = """caseID dispID button_event notify_no policy_no claim_no survey_no claim_MtypeID claim_typeID follow_num
tab1_thirdParty_type-inputEl surveyorID surveyor_name empcode sys_branch useOSS OSS_companyID OSS_company OSS_SurveyorName
OSS_phone acc_provinceID acc_amphurID dispatch_date dispatch_time confirm_date confirm_time arrive_date arrive_time
finish_date finish_time sendReportDate sendReportTime closeDate cmp_arrive acc_zone tab1_rd-in_out tab1_chk_co_area
survey_provinceID survey_amphurID service_type accident_summary tab1_PC_SUR_VAT-inputEl tab1_PC_INS_VAT-inputEl
tab1_INVEST_NUM-inputEl tab1_SUR_INVEST-inputEl tab1_INS_INVEST-inputEl tab1_DIST_NUM-inputEl tab1_SUR_DIST-inputEl
tab1_INS_DIST-inputEl tab1_TRANS_NUM-inputEl tab1_SUR_TRANS-inputEl tab1_INS_TRANS-inputEl tab1_FUL_OTHER-inputEl
tab1_SUR_OTHER-inputEl tab1_INS_OTHER-inputEl tab1_PHOTO_NUM-inputEl tab1_SUR_PHOTO-inputEl tab1_INS_PHOTO-inputEl
tab1_SUR_TEL-inputEl tab1_INS_TEL-inputEl tab1_FUL_INSURE-inputEl tab1_SUR_INSURE-inputEl tab1_INS_INSURE-inputEl
tab1_DAILY_NUM-inputEl tab1_SUR_DAILY-inputEl tab1_INS_DAILY-inputEl tab1_RECV_CLAIM-inputEl tab1_SUR_CLAIM-inputEl
tab1_INS_CLAIM-inputEl tab1_FUL_CARTOW-inputEl tab1_SUR_CARTOW-inputEl tab1_INS_CARTOW-inputEl tab1_SUR_TOTAL-inputEl
tab1_INS_TOTAL-inputEl tab1_SUR_VAT-inputEl tab1_INS_VAT-inputEl tab1_rd_vat tab1_SUR_TOTAL_NET-inputEl
tab1_INS_TOTAL_NET-inputEl memo chk_claimform chk_chassisNo chk_drvLic chk_prtDoc chk_other supervisor_summary
tab1_deduct_amount""".split()


def t1_fixture() -> dict:
    """โครงเดียวกับ getcaseinfo.php tab-1_clone ของจริง (ค่าเลียนแบบเคลม 2026013169905)"""
    return {
        "status": "40", "rpt_flag": "Y", "accident_summary": "ความเห็นเดิมใน ISURVEY",
        "chk_claimform": "Y", "chk_chassisNo": "Y", "chk_drvLic": "Y", "chk_prtDoc": "D", "chk_other": None,
        "Claim": {
            "caseID": "00000962033", "claim_no": "2026013169905", "notify_no": "2026153692",
            "survey_no": "SEABI-120260900190", "claim_MtypeID": "1", "claim_typeID": "01", "follow_num": "1",
            "surveyorID": "000945", "surveyor_name": "SEC343 นาย มี วงษ์สุวรรณ", "empcode": "SEC343",
            "sys_branch": "00002", "sys_branchName": "เมืองชลบุรี", "useOSS": "N",
            "OSS_companyID": None, "OSS_company": None, "OSS_SurveyorName": None, "OSS_phone": None,
            "acc_provinceID": "21", "acc_amphurID": "2106", "survey_provinceID": "20", "survey_amphurID": "2003",
            "service_type": None, "acc_zone": "ตจว", "cmp_arrive": "เร็วกว่า", "wrkTime": "ใน", "COArea": "N",
            "claim_TP": None,
        },
        "Policy": {"policy_no": "1250131105754"},
        "Dispatch": {
            "dispID": "967316", "dispatch_date": "2026-09-07", "dispatch_time": "13:18:00",
            "confirm_date": "2026-09-07", "confirm_time": "14:05:00", "arrive_date": "2026-09-07", "arrive_time": "14:05:00",
            "finish_date": "2026-09-07", "finish_time": "14:45:00", "sendReportDate": "2026-09-08", "sendReportTime": "09:27:00",
        },
        "bill": {
            "SUR_INVEST": "700.00", "INS_INVEST": "500.00", "INVEST_NUM": "0",
            "SUR_TRANS": "0.00", "INS_TRANS": "800.00", "TRANS_NUM": "0", "DISTANCE": "0",
            "SUR_DIST": "0.00", "INS_DIST": "0.00", "SUR_OTHER": "0.00", "INS_OTHER": "0.00", "OTHER_DESC": None,
            "PHOTO_NUM": "0", "SUR_PHOTO": "0.00", "INS_PHOTO": "50.00",
            "SUR_TEL": None, "INS_TEL": None, "SUR_INSURE": None, "INS_INSURE": None,
            "DAILY_NUM": "0", "SUR_DAILY": "0.00", "INS_DAILY": "0.00", "RECV_CLAIM": "0",
            "SUR_CLAIM": None, "INS_CLAIM": None, "SUR_TOWCAR": None, "INS_TOWCAR": None,
            "INC_VAT": "Y", "memo": None,
        },
    }


def test_keys_match_captured_form():
    p = ic.build_payload(t1_fixture())
    assert list(p.keys()) == CAPTURED_KEYS, (set(p) ^ set(CAPTURED_KEYS))


def test_values_from_tab1_without_overrides():
    p = ic.build_payload(t1_fixture())
    assert p["caseID"] == "00000962033" and p["dispID"] == "967316"
    assert p["dispatch_date"] == "07/09/2026" and p["dispatch_time"] == "13:18"   # ISO → dd/mm/yyyy, ตัดวินาที
    assert p["sys_branch"] == "เมืองชลบุรี" and p["tab1_thirdParty_type-inputEl"] == "กรุณาเลือก"
    assert p["tab1_rd-in_out"] == "ใน" and p["tab1_chk_co_area"] == "N" and p["closeDate"] == ""
    assert p["accident_summary"] == "ความเห็นเดิมใน ISURVEY"          # ไม่ส่งความเห็น = คงของเดิม
    assert p["supervisor_summary"] == "close_case"                      # "ปิดการตรวจสอบ" เสมอ
    assert p["chk_other"] == "" and p["memo"] == ""                     # None → ''
    # ตารางเดิม: ยอด/รวม/VAT ตรงกับที่หน้าเว็บคำนวณ (ไม่มีคอมมา)
    assert p["tab1_SUR_INVEST-inputEl"] == "700.00" and p["tab1_INS_TRANS-inputEl"] == "800.00"
    assert p["tab1_SUR_TEL-inputEl"] == "" and p["tab1_INS_CLAIM-inputEl"] == ""   # ช่องที่หน้าเว็บส่งว่าง
    assert p["tab1_SUR_TOTAL-inputEl"] == "700.00" and p["tab1_INS_TOTAL-inputEl"] == "1350.00"
    assert p["tab1_INS_VAT-inputEl"] == "94.50" and p["tab1_INS_TOTAL_NET-inputEl"] == "1444.50"
    assert p["tab1_SUR_VAT-inputEl"] == "0.00" and p["tab1_SUR_TOTAL_NET-inputEl"] == "700.00"
    assert p["tab1_rd_vat"] == "Y" and p["tab1_deduct_amount"] == "0"


def test_comment_overrides_but_blank_keeps_original():
    p = ic.build_payload(t1_fixture(), comment="ผลการดำเนินงานจากเว็บเรา\nบรรทัด 2")
    assert p["accident_summary"] == "ผลการดำเนินงานจากเว็บเรา\nบรรทัด 2"
    p2 = ic.build_payload(t1_fixture(), comment="   ")
    assert p2["accident_summary"] == "ความเห็นเดิมใน ISURVEY"


def test_rates_replace_table_and_recompute_totals():
    rates = {
        "sur": {"invest": 700, "trans": 150, "photo": 50, "other": 100, "deduct": 20},
        "ins": {"invest": 500, "invest_num": 1, "trans": 800, "trans_num": 1, "photo": 50, "photo_num": 10,
                "tel": 30, "other": 0, "other_desc": "ค่าจอดรถ"},
    }
    p = ic.build_payload(t1_fixture(), rates=rates)
    assert p["tab1_SUR_INVEST-inputEl"] == "700.00" and p["tab1_SUR_TRANS-inputEl"] == "150.00"
    assert p["tab1_SUR_OTHER-inputEl"] == "100.00" and p["tab1_SUR_DIST-inputEl"] == "0.00"  # ไม่ส่ง = 0.00 เมื่อใช้เรทของเรา
    assert p["tab1_INS_PHOTO-inputEl"] == "50.00" and p["tab1_PHOTO_NUM-inputEl"] == "10"
    assert p["tab1_INS_TEL-inputEl"] == "30.00" and p["tab1_FUL_OTHER-inputEl"] == "ค่าจอดรถ"
    assert p["tab1_SUR_TOTAL-inputEl"] == "1000.00"                       # 700+150+50+100
    assert p["tab1_INS_TOTAL-inputEl"] == "1380.00"                       # 500+800+50+30
    assert p["tab1_INS_VAT-inputEl"] == "96.60" and p["tab1_INS_TOTAL_NET-inputEl"] == "1476.60"
    assert p["tab1_deduct_amount"] == "20"


def test_one_side_only_keeps_other_side_from_isurvey():
    p = ic.build_payload(t1_fixture(), rates={"sur": {"invest": 650}})    # มีแต่ฝั่งพนักงาน
    assert p["tab1_SUR_INVEST-inputEl"] == "650.00" and p["tab1_SUR_TRANS-inputEl"] == "0.00"
    assert p["tab1_INS_INVEST-inputEl"] == "500.00" and p["tab1_INS_TRANS-inputEl"] == "800.00"   # ฝั่งประกันคงของเดิม
    assert p["tab1_INS_TEL-inputEl"] == "" and p["tab1_INS_TOTAL-inputEl"] == "1350.00"
    assert p["tab1_deduct_amount"] == "0"


def test_no_vat_when_inc_vat_n():
    t1 = t1_fixture()
    t1["bill"]["INC_VAT"] = "N"
    p = ic.build_payload(t1)
    assert p["tab1_rd_vat"] == "N" and p["tab1_INS_VAT-inputEl"] == "0.00"
    assert p["tab1_INS_TOTAL_NET-inputEl"] == "1350.00"


def test_check_can_close_rules():
    assert ic.check_can_close({"sttcase_ID": "40", "close_datetime": ""}) is None
    assert ic.check_can_close({"sttcase_ID": "99", "close_datetime": None}) is None
    assert "ปิดไปแล้ว" in ic.check_can_close({"sttcase_ID": "100", "close_datetime": "2026-09-08 10:17"})
    assert "จบงาน" in ic.check_can_close({"sttcase_ID": "100", "close_datetime": ""})
    assert "ไม่ใช่" in ic.check_can_close({"sttcase_ID": "20", "close_datetime": ""})   # สถานะแปลก = ไม่ปิด


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  [PASS] {name}")
            except AssertionError as e:
                fails += 1
                print(f"  [FAIL] {name}: {e}")
    sys.exit(1 if fails else 0)
