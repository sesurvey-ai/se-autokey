# -*- coding: utf-8 -*-
"""ผลกด "นำเข้าข้อมูล" XML บน EMCS — สำเร็จแล้วห้ามตีเป็น "ปัดตก" (เคส #1441 #1410 · 09/10/69)

อาการ: EMCS ตอบ "สำเร็จ! Import ข้อมูลเข้าระบบเรียบร้อยแล้ว [เลขที่ eSurvey : S68426102288]" แต่หน้าฟอร์มไม่ขึ้นใน 20 วิ
(ปกติขึ้นใน 1 วิ) → บอทเอาข้อความสำเร็จไปขึ้นกล่องแดง "EMCS ปัดตกไฟล์" + "ยังไม่มีเรื่องใน EMCS — นำเข้าใหม่ได้เลย"
ทั้งที่เรื่องอยู่ใน EMCS แล้ว (กดใหม่ = ด่านกันซ้ำจับได้ แต่เคสค้าง ไม่ได้ mark ฝั่ง se-survey)
แก้: ข้อความสำเร็จ = จดเลข draft ทันที · หน้าฟอร์มไม่ขึ้น = เปิดเรื่องจากหน้ารายการแล้วกรอกต่อ · เปิดไม่ได้ = ImportedFormError
+ ปัดตกจริงแบบ "รูปแบบไฟล์ไม่ถูกต้อง" (เคส #975) รายละเอียดอยู่ใน <textarea> ต้องอ่านมาด้วย

รัน:  python -m pytest tests/test_emcs_import_result.py -q
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import TimeoutException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import emcs  # noqa: E402

OK_TEXT = "สำเร็จ!\nImport ข้อมูลเข้าระบบเรียบร้อยแล้ว [เลขที่ eSurvey : S68426102288]\nOK"
FORMAT_TEXT = "ขออภัย!\nรูปแบบไฟล์นำเข้าไม่ถูกต้อง กรุณาตรวจสอบรายละเอียดดังนี้\nOK"
SIZE_TEXT = ("กรุณาตรวจสอบ!\nข้อมูลนำเข้ามีขนาดเกิน โปรดตรวจสอบรายละเอียดดังนี้\n"
             "1\tTXN_SURV_CAR\tDRI_CARDID\t13\t14000700231336\tรถประกัน\nOK")


def test_success_text_gives_esurvey_and_rejections_do_not():
    assert emcs.import_success_esurvey(OK_TEXT) == "S68426102288"
    assert emcs.import_success_esurvey("Import ข้อมูลเข้าระบบเรียบร้อยแล้ว [เลขที่ eSurvey : S68426102317]") == "S68426102317"
    assert emcs.import_success_esurvey(FORMAT_TEXT) == ""
    assert emcs.import_success_esurvey(SIZE_TEXT) == ""
    assert emcs.import_success_esurvey("") == ""


def test_imported_form_error_says_success_not_rejected():
    e = emcs.ImportedFormError("S68426102288", "TimeoutException: btnUpdate")
    assert e.esurvey == "S68426102288"
    assert "สำเร็จ" in str(e) and "S68426102288" in str(e) and "ปัดตก" not in str(e)


# ── import_xml_report กับหน้า EMCS จำลอง ──
class _El:
    text = ""

    def click(self):
        pass

    def send_keys(self, *a):
        pass

    def is_displayed(self):
        return True


class FakeDriver:
    def __init__(self):
        self.current_url = "https://emcs.example/frmMainPage.aspx?P1=x"

    def execute_script(self, js, *args):
        if "f.files.length" in js:
            return "sesurvey_case_1441.txt"       # ไฟล์ติดแล้ว
        if "btnImport" in js:
            self.current_url = "https://emcs.example/frmSurvey.aspx?id=1"   # กดนำเข้า → EMCS พาไปหน้าฟอร์ม
        return None

    def find_element(self, *a):
        return _El()

    def find_elements(self, *a):
        return []


@pytest.fixture
def page(monkeypatch, tmp_path):
    xml = tmp_path / "sesurvey_case_1441.txt"
    xml.write_text("<INSURERBRID> </INSURERBRID>", encoding="utf-8")
    data = SimpleNamespace(xml_file=str(xml), claim_value="2026013083925")
    cfg = SimpleNamespace(runs_dir=tmp_path)
    calls = {"reopen": []}
    monkeypatch.setattr(emcs.time, "sleep", lambda s: None)
    monkeypatch.setattr(emcs, "wait_clickable", lambda *a, **k: _El())
    monkeypatch.setattr(emcs, "wait_present", lambda *a, **k: _El())
    monkeypatch.setattr(emcs, "_set_selectpicker", lambda *a, **k: None)
    monkeypatch.setattr(emcs, "_import_branch_value", lambda d: "1778|x")

    def no_form(driver, by, value, timeout=10):
        if value == "btnUpdate":
            raise TimeoutException("btnUpdate ไม่ขึ้น")      # อาการเคส #1441: หน้าฟอร์มไม่ขึ้น
        return _El()

    monkeypatch.setattr(emcs, "wait_visible", no_form)
    monkeypatch.setattr(emcs, "_reopen_imported_draft",
                        lambda d, c, dt, no, url: calls["reopen"].append((no, url)))
    emcs.fill_imported.last_draft_esurvey = ""
    return SimpleNamespace(driver=FakeDriver(), cfg=cfg, data=data, calls=calls, mp=monkeypatch)


def test_success_without_form_reopens_the_draft_instead_of_rejecting(page):
    page.mp.setattr(emcs, "_wait_import_dialog", lambda d, timeout=60: (OK_TEXT, OK_TEXT))
    no = emcs.import_xml_report(page.driver, page.cfg, page.data, insurer_code="1059")
    assert no == "S68426102288"
    assert page.calls["reopen"] == [("S68426102288", "https://emcs.example/frmMainPage.aspx?P1=x")]
    # จดเลข draft ไว้ให้ผู้เรียก mark ฝั่ง se-survey ถ้าขั้นต่อไปพัง
    assert emcs.fill_imported.last_draft_esurvey == "S68426102288"


def test_real_rejection_still_raises_rejected(page):
    page.mp.setattr(emcs, "_wait_import_dialog", lambda d, timeout=60: (SIZE_TEXT, SIZE_TEXT))
    with pytest.raises(emcs.ImportRejectedError) as ei:
        emcs.import_xml_report(page.driver, page.cfg, page.data, insurer_code="1059")
    assert ei.value.rows and ei.value.rows[0]["field"] == "DRI_CARDID"
    assert page.calls["reopen"] == [] and emcs.fill_imported.last_draft_esurvey == ""


# ── เปิดเรื่องคืนจากหน้ารายการ ──
class ListDriver:
    def __init__(self):
        self.visited = []

    def get(self, url):
        self.visited.append(url)


def _reopen_env(monkeypatch, tmp_path, reports, form_ok=True):
    clicked = []
    monkeypatch.setattr(emcs, "save_debug_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(emcs, "login", lambda d, c: None)
    monkeypatch.setattr(emcs, "find_existing_reports", lambda d, claim: reports)

    def vis(driver, by, value, timeout=10):
        if value == "btnUpdate" and not form_ok:
            raise TimeoutException("btnUpdate ไม่ขึ้น")
        return _El()

    class _Link(_El):
        def __init__(self, xp):
            self.xp = xp

        def click(self):
            clicked.append(self.xp)

    monkeypatch.setattr(emcs, "wait_visible", vis)
    monkeypatch.setattr(emcs, "wait_clickable", lambda d, by, xp, t=10: _Link(xp))
    cfg = SimpleNamespace(runs_dir=tmp_path)
    data = SimpleNamespace(claim_value="2026013083925")
    return cfg, data, clicked


def test_reopen_opens_the_esurvey_link_from_the_list(monkeypatch, tmp_path):
    cfg, data, clicked = _reopen_env(monkeypatch, tmp_path, [{"esurvey": "S68426102288", "row": "รายงานสร้างใหม่"}])
    d = ListDriver()
    emcs._reopen_imported_draft(d, cfg, data, "S68426102288", "https://emcs.example/frmMainPage.aspx?P1=x")
    assert d.visited == ["https://emcs.example/frmMainPage.aspx?P1=x"]
    assert clicked and "S68426102288" in clicked[0]


@pytest.mark.parametrize("reports,form_ok", [([], True), ([{"esurvey": "S68426102288", "row": ""}], False)])
def test_reopen_failure_is_imported_form_error_not_rejection(monkeypatch, tmp_path, reports, form_ok):
    cfg, data, _ = _reopen_env(monkeypatch, tmp_path, reports, form_ok)
    with pytest.raises(emcs.ImportedFormError) as ei:
        emcs._reopen_imported_draft(ListDriver(), cfg, data, "S68426102288", "")
    assert ei.value.esurvey == "S68426102288"


# ── ส่วนที่ไม่ต้องเปิดเบราว์เซอร์ ──
def test_dialog_reader_includes_textarea_details():
    src = inspect.getsource(emcs._wait_import_dialog)
    assert "querySelectorAll('textarea')" in src and "a.value" in src


def test_fill_imported_resets_draft_number_per_job():
    src = inspect.getsource(emcs.fill_imported)
    assert src.index('fill_imported.last_draft_esurvey = ""') < src.index("import_xml_report(")


def test_main_marks_imported_and_never_says_rejected_for_imported_form_error():
    main = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    a = main.index("except emcs.ImportedFormError as e:")
    b = main.index("except emcs.ImportRejectedError as e:")
    assert a < b
    block = main[a:b]
    assert "_mark_emcs_imported(cfg, case_id, hdrs, e.esurvey)" in block
    assert "ยังไม่มีเรื่องใน EMCS" not in block and "ห้ามกดนำเข้าใหม่" in block
