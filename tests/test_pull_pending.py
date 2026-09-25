# -*- coding: utf-8 -*-
"""list_pending — แถวหน้า "รอตรวจ ISURVEY" บนเว็บ se-survey (25/09/69)

ล็อกกติกา:
  - ส่งจังหวัดที่เกิดเหตุ (acc_province) และจังหวัด/อำเภอที่ออกตรวจสอบ (survey_province/survey_amphur) แยกกัน
    — รายงาน enquiry มีทั้ง 2 ชุด (เช็คช่องจริง 25/09/69) และบางงานไม่ตรงกัน (เกิดเหตุ กทม. ออกตรวจ ชลบุรี)
  - ช่องที่รายงานไม่ส่งมา = "" (ไม่ใช่ None) · งานบริษัทที่ไม่ได้รับทำถูกตัดทิ้งเหมือนเดิม
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from autokey import pull_core  # noqa: E402


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    def get(self, url, timeout=None, params=None):
        self.params = params
        return _Resp({"arr_data": self.rows})


class FakeAPI:
    def __init__(self, rows):
        self.s = _Session(rows)


def test_pending_rows_carry_accident_and_survey_province():
    api = FakeAPI([
        {"claim_no": "2026013072661", "survey_no": "SEABI-220260900177", "empcode": "SE225",
         "acc_province": "กรุงเทพฯ", "survey_province": "ชลบุรี", "survey_amphur": "บางละมุง",
         "stt_desc": "รอตรวจข้อมูล", "sendReport_dt": "2026-09-25 12:00"},
        {"claim_no": "2026013000001", "survey_no": "SETP-110260900001", "empcode": "SE1",
         "acc_province": "กรุงเทพฯ", "stt_desc": "รอตรวจข้อมูล"},          # รายงานไม่มีช่องที่ออกตรวจ
        {"claim_no": "X", "survey_no": "MSIG-110260900001", "acc_province": "กรุงเทพฯ", "stt_desc": "รอตรวจข้อมูล"},
    ])
    rows = pull_core.list_pending(api, "2026-09-24", "2026-09-25", status="")
    assert api.s.params["report_type"] == "enquiry"
    assert [r["survey_no"] for r in rows] == ["SEABI-220260900177", "SETP-110260900001"]  # MSIG ตัดทิ้ง
    first = rows[0]
    assert first["acc_province"] == "กรุงเทพฯ"
    assert first["survey_province"] == "ชลบุรี" and first["survey_amphur"] == "บางละมุง"
    assert rows[1]["survey_province"] == "" and rows[1]["survey_amphur"] == ""
