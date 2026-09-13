# -*- coding: utf-8 -*-
"""เทสกติกาลำดับ "ครั้งที่" จากเลขเรื่องเซอร์เวย์ (autokey.survey_order) — user เคาะ 13/09/69

ข้อมูลอ้างอิงเป็นของจริงที่ตรวจกับ EMCS แล้ว (อ่านอย่างเดียว 13/09/69):
  2026013127658 → ครั้งที่ 1..4 = SEABI-110260400680 · 410260501454 · 410260502277 · 410260600413
  2026013020764 → ครั้งที่ 1..3 = SEABI-110260301484 · 410260400230 · 410260401463

รัน:  python -m pytest tests/test_survey_order.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import survey_order as so  # noqa: E402


def _rows(*items):
    """(survey_no, sttcase_ID, dispatch) → แถวโครงเดียวกับ listcases ของ ISURVEY"""
    return [{"survey_no": n, "sttcase_ID": st, "dispatch_datetime": dt, "caseID": f"c{i}"}
            for i, (n, st, dt) in enumerate(items)]


def test_parse_seabi_and_setp():
    p = so.parse_survey_no("SEABI-410260600413")
    assert p == {"prefix": "SEABI", "type": "4", "prov": "10", "yymm": "2606", "seq": 413}
    assert so.parse_survey_no("setp-69050083") == {"prefix": "SETP", "type": "", "prov": "", "yymm": "6905", "seq": 83}
    assert so.parse_survey_no("SEABI-12345") is None and so.parse_survey_no("") is None


def test_claim_2026013127658_matches_emcs_rounds():
    """ใบเดือนมิถุนายน ลำดับ 00413 น้อยกว่าทุกใบ แต่ต้องเป็นครั้งสุดท้าย — เทียบเดือนก่อนลำดับเรื่อง"""
    rows = _rows(("SEABI-410260600413", "100", "2026-06-04 04:42"),
                 ("SEABI-410260502277", "100", "2026-05-20 09:23"),
                 ("SEABI-110260400680", "100", "2026-04-06 14:07"),
                 ("SEABI-410260501454", "100", "2026-05-13 18:21"))
    ordered = so.order_claim_jobs(rows)
    assert [r["survey_no"] for r in ordered] == [
        "SEABI-110260400680", "SEABI-410260501454", "SEABI-410260502277", "SEABI-410260600413"]
    assert [r["round"] for r in ordered] == [1, 2, 3, 4]
    assert so.round_of(ordered, "SEABI-410260502277") == 3
    assert so.round_of(ordered, "seabi-410260600413") == 4
    assert so.first_type_ok(ordered)


def test_claim_2026013020764_matches_emcs_rounds():
    rows = _rows(("SEABI-410260401463", "100", "2026-04-15 09:25"),
                 ("SEABI-110260301484", "100", "2026-03-13 12:14"),
                 ("SEABI-410260400230", "100", "2026-04-02 17:47"))
    ordered = so.order_claim_jobs(rows)
    assert [r["survey_no"] for r in ordered] == [
        "SEABI-110260301484", "SEABI-410260400230", "SEABI-410260401463"]


def test_cancelled_and_declined_jobs_are_not_rounds():
    """99 ยกเลิกเคลม · 60 ไม่รับงาน ไม่นับเป็นครั้ง และ round_of ของใบพวกนั้น = None"""
    rows = _rows(("SEABI-110260400680", "100", "2026-04-06 14:07"),
                 ("SEABI-410260500001", "99", "2026-05-01 08:00"),
                 ("SEABI-410260500002", "60", "2026-05-02 08:00"),
                 ("SEABI-410260501454", "100", "2026-05-13 18:21"))
    ordered = so.order_claim_jobs(rows)
    assert [r["round"] for r in ordered] == [1, 2]
    assert so.round_of(ordered, "SEABI-410260501454") == 2
    assert so.round_of(ordered, "SEABI-410260500001") is None
    assert so.is_excluded({"sttcase_ID": "99"}) and not so.is_excluded({"sttcase_ID": "100"})


def test_same_month_different_province_uses_dispatch_time():
    """ลำดับเรื่องรันแยกตามจังหวัด — เดือนเดียวกันคนละจังหวัดเทียบลำดับกันไม่ได้ ใช้วันเวลาจ่ายงาน"""
    rows = _rows(("SEABI-110260400001", "100", "2026-04-01 09:00"),
                 ("SEABI-410260500015", "100", "2026-05-20 10:00"),   # จังหวัด 10 ลำดับน้อยแต่จ่ายงานทีหลัง
                 ("SEABI-420260500230", "100", "2026-05-02 09:00"))   # จังหวัด 20 ลำดับมากแต่จ่ายงานก่อน
    ordered = so.order_claim_jobs(rows)
    assert [r["survey_no"] for r in ordered] == [
        "SEABI-110260400001", "SEABI-420260500230", "SEABI-410260500015"]


def test_same_month_same_province_uses_sequence_even_if_dispatch_disagrees():
    """จังหวัดเดียวกัน = ใช้ลำดับเรื่องตามที่ user เคาะ (วันเวลาจ่ายงานไม่มีผล)"""
    rows = _rows(("SEABI-110260400001", "100", "2026-04-01 09:00"),
                 ("SEABI-410260500230", "100", "2026-05-20 10:00"),
                 ("SEABI-410260500015", "100", "2026-05-21 09:00"))
    ordered = so.order_claim_jobs(rows)
    assert [r["survey_no"] for r in ordered] == [
        "SEABI-110260400001", "SEABI-410260500015", "SEABI-410260500230"]


def test_first_round_type_check_and_describe():
    ordered = so.order_claim_jobs(_rows(("SEABI-410260400230", "100", "2026-04-02 17:47"),
                                        ("SEABI-410260401463", "100", "2026-04-15 09:25")))
    assert not so.first_type_ok(ordered)          # ครั้งที่ 1 เป็นประเภท 4 = ผิดปกติ (ครั้งที่ 1 ต้องเป็น 1/2)
    assert so.first_type_ok([])                   # ไม่มีงาน = ไม่มีอะไรให้ค้าน
    setp = so.order_claim_jobs(_rows(("SETP-69050083", "100", ""), ("SETP-69080003", "100", "")))
    assert so.first_type_ok(setp) and [r["round"] for r in setp] == [1, 2]   # ไทยไพบูลย์ไม่มีหลักประเภท
    text = so.describe(ordered, "SEABI-410260401463")
    assert "ครั้งที่ 1 = SEABI-410260400230" in text and "ครั้งที่ 2 = SEABI-410260401463 ←ใบนี้" in text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
