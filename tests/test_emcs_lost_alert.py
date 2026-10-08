# -*- coding: utf-8 -*-
"""alert ที่ "หายก่อนบอทเห็น" ตอนกดบันทึกหน้าหลัก (เคลม 2026013176757 · เคส #1578 · 08/10/69)

อาการ: บอทกด 'แก้ไข' 9 รอบ log ว่า "หน้าโหลดใหม่โดยไม่มี alert" / "EMCS เงียบ" แต่หน้าที่บอทเก็บไว้ทั้ง 3 ใบมี
<script>alert('บันทึกแก้ไขรายละเอียด เซอร์เวย์ เรียบร้อยแล้ว');</script> = EMCS บันทึกสำเร็จทุกรอบ (คนเห็นป๊อปอัพทุกครั้ง)
สาเหตุ: ChromeDriver ค่าเริ่มต้น "dismiss and notify" ปิด alert ให้ทันทีที่บอทยิงคำสั่งอื่น แล้วแนบข้อความมากับ exception —
โค้ดเดิมทิ้งข้อความแล้วไปรอ alert ใหม่ที่ไม่มีแล้ว

รัน:  python -m pytest tests/test_emcs_lost_alert.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from selenium.common.exceptions import NoAlertPresentException, UnexpectedAlertPresentException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import browser, emcs  # noqa: E402

OK_TEXT = "บันทึกแก้ไขรายละเอียด เซอร์เวย์ เรียบร้อยแล้ว"


class _Switch:
    @property
    def alert(self):
        raise NoAlertPresentException("no alert")      # alert ถูก ChromeDriver ปิดไปแล้ว


class FakeDriver:
    """หน้า EMCS จำลอง: ตอบสคริปต์ 2 แบบที่ _wait_alert_or_refresh ใช้ — ตัวดักคลิกหาย? / สคริปต์ alert ในหน้าใหม่"""

    def __init__(self, gone=False, startup=None, alert_exc_text=None):
        self.switch_to = _Switch()
        self.gone = gone
        self.startup = startup or []
        self.alert_exc_text = alert_exc_text

    def execute_script(self, js, *args):
        if "getElementsByTagName('script')" in js:
            return list(self.startup)
        if "__seClick" in js:
            if self.alert_exc_text is not None:
                raise UnexpectedAlertPresentException("unexpected alert open", alert_text=self.alert_exc_text)
            return self.gone
        return None


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(browser, "harvest_rule", lambda *a, **k: None)
    monkeypatch.setattr(emcs, "log", lambda *a, **k: None)


def test_alert_closed_by_chromedriver_keeps_its_text():
    # เดิม: accept_alert รอ alert ใหม่ 2 วิ → TimeoutException → "EMCS เงียบ" → กดซ้ำ
    d = FakeDriver(alert_exc_text=OK_TEXT)
    assert emcs._wait_alert_or_refresh(d, timeout=3) == ("alert", OK_TEXT)


def test_new_page_with_embedded_success_alert_is_not_a_plain_refresh():
    # เดิม: "หน้าโหลดใหม่โดยไม่มี alert" → กดซ้ำ ทั้งที่หน้าใหม่ฝัง alert สำเร็จมา
    d = FakeDriver(gone=True, startup=[OK_TEXT])
    assert emcs._wait_alert_or_refresh(d, timeout=3) == ("alert", OK_TEXT)


def test_plain_refresh_without_alert_still_detected():
    d = FakeDriver(gone=True, startup=[])
    assert emcs._wait_alert_or_refresh(d, timeout=3, grace=0.1) == ("refresh", "")


def test_alert_caught_during_click_is_handed_to_the_waiter():
    d = FakeDriver(gone=False)
    emcs._stash_alert(d, OK_TEXT)
    assert emcs._wait_alert_or_refresh(d, timeout=3) == ("alert", OK_TEXT)
    assert emcs._take_pending_alert(d) == ""          # ใช้แล้วหมด ไม่ค้างไปรอบหน้า


def test_alert_text_parsed_from_message_when_attribute_missing():
    e = UnexpectedAlertPresentException(
        "unexpected alert open: {Alert text : " + OK_TEXT + "}\n  (Session info: chrome=140)")
    assert emcs._exc_alert_text(e) == OK_TEXT


def test_validation_alert_text_is_kept_as_validation():
    # alert ฟ้อง validation ที่ถูกปิดไปก่อน — ข้อความต้องมาครบ ให้ save_main_form ตกเส้น "หยุดรอคนกรอก" ตามเดิม
    msg = "กรุณาระบุ : เลขที่รับแจ้ง"
    d = FakeDriver(alert_exc_text=msg)
    kind, text = emcs._wait_alert_or_refresh(d, timeout=3)
    assert kind == "alert" and "กรุณา" in text


def test_startup_alert_script_regex_matches_only_standalone_alert_scripts():
    js = emcs._JS_STARTUP_ALERTS
    assert "getElementsByTagName('script')" in js and "if (ss[i].src) continue;" in js
    # ทั้งก้อนต้องเป็น alert('...') อย่างเดียว — alert ในฟังก์ชัน (เช่น dialogCancel_Check ของ EMCS) ไม่นับ
    assert r"/^alert\(" in js and r"\s*;?$/" in js


def test_red_messages_skip_text_hidden_by_closed_dialog():
    # ป้าย "กรุณาระบุเหตุผลไม่น้อยกว่า 5 ตัวอักษร" อยู่ในหน้าต่าง "ยกเลิกรายงาน" ที่ปิดอยู่ — เคยถูกรายงานเป็น error ของ EMCS
    js = emcs._JS_RED_MESSAGES
    assert "if (!el.getClientRects().length) continue;" in js
    assert "ใส่ได้มากกว่าหนึ่งชื่อ" in js
