# -*- coding: utf-8 -*-
"""เวลา "24:MM" — EMCS ไม่รับชั่วโมง 24 (เคลม 2026013079700 · เคส #1232 · 09/10/69)

ISURVEY รับ "25/09/2569 24:18" → ไฟล์ XML ปัดตกทั้งไฟล์ ("…not supported in calendar GregorianCalendar")
และช่องชั่วโมงบนฟอร์ม EMCS ก็ไม่รับ → บอทปรับเป็น 00:MM ของวันถัดไป (ค่าเดียวกัน) ทั้งในไฟล์และก่อนพิมพ์
กติกาเดียวกับ se-survey backend (xmlExport parseSe/rollMidnight)

รัน:  python -m pytest tests/test_hour24.py -q
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import emcs  # noqa: E402
from autokey.claim_data import ClaimData, normalize_hour24, roll_hour24, roll_hour24_dt  # noqa: E402


@pytest.mark.parametrize("d,t,want", [
    ("25/09/2569", "24:18", ("26/09/2569", "00:18")),          # พ.ศ. (แบบที่ ISURVEY/เว็บใช้)
    ("31/12/2569", "24:05", ("01/01/2570", "00:05")),          # ข้ามปี
    ("28/02/2567", "24:00", ("29/02/2567", "00:00")),          # 2024 อธิกสุรทิน
    ("25/09/2026", "24:18", ("26/09/2026", "00:18")),          # ค.ศ. คงเป็น ค.ศ.
    ("2026-09-25", "24:18:00", ("2026-09-26", "00:18:00")),    # ISO
    ("25/09/2569", "23:59", ("25/09/2569", "23:59")),          # ปกติ ไม่แตะ
    ("25/09/2569", "24:60", ("25/09/2569", "24:60")),          # นาทีไม่จริง ไม่เดา
    ("", "24:10", ("", "24:10")),                              # ไม่รู้วัน ไม่เดา
    ("00/00/2569", "24:10", ("00/00/2569", "24:10")),          # วันไม่จริง ไม่เดา
])
def test_roll_hour24(d, t, want):
    assert roll_hour24(d, t) == want


def test_roll_hour24_combined_value():
    assert roll_hour24_dt("25/09/2569|24:30") == "26/09/2569|00:30"
    assert roll_hour24_dt("25/09/2569|21:10") == "25/09/2569|21:10"
    assert roll_hour24_dt("") == ""


def test_normalize_claim_data_all_moments():
    d = ClaimData()
    d.acc_date, d.acc_time = "25/09/2569", "20:02"
    d.arrive_date, d.arrive_time = "25/09/2569", "22:37"
    d.finish_date, d.finish_time = "25/09/2569", "24:18"
    d.noti_date, d.noti_time = "30/09/2569", "24:01"
    d.police_date = "25/09/2569|24:30"
    notes = normalize_hour24(d)
    assert (d.finish_date, d.finish_time) == ("26/09/2569", "00:18")
    assert (d.noti_date, d.noti_time) == ("01/10/2569", "00:01")
    assert d.police_date == "26/09/2569|00:30"
    assert (d.acc_date, d.acc_time, d.arrive_time) == ("25/09/2569", "20:02", "22:37")
    assert len(notes) == 3 and normalize_hour24(d) == []          # ทำซ้ำไม่เปลี่ยนอะไร


def test_xml_rewrite_only_hour24_values():
    t = ("<R><ACC_FINISH>2026-09-25 24:18:00</ACC_FINISH><ACC_REACH>2026-09-25 22:37:00</ACC_REACH>"
         "<ACC_DETAIL>นัด 24:00 น.</ACC_DETAIL></R>")
    new, changed = emcs.fit_hour24_xml(t)
    assert "<ACC_FINISH>2026-09-26 00:18:00</ACC_FINISH>" in new
    assert "<ACC_REACH>2026-09-25 22:37:00</ACC_REACH>" in new and "นัด 24:00 น." in new   # ข้อความอิสระไม่แตะ
    assert changed == [("2026-09-25 24:18:00", "2026-09-26 00:18:00")]
    assert emcs.fit_hour24_xml("<R><A>2026-09-25 10:00:00</A></R>")[1] == []


def test_wired_into_fill_accident_and_xml_import():
    fa = inspect.getsource(emcs.fill_accident)
    assert fa.index("normalize_hour24(data)") < fa.index('set_text(driver, "wuCale_Acc_Date_txtCalendar"')
    ix = inspect.getsource(emcs.import_xml_report)
    assert ix.index("fit_hour24_xml(_t)") < ix.index("send_keys(str(xml_path))")
