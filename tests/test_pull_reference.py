# -*- coding: utf-8 -*-
"""เทสตัวดึงงาน: งานครั้งถัดไปต้องดึงครั้งก่อนหน้าที่ยังไม่มีในเว็บมาเป็น "เคสอ้างอิง" ก่อน (user เคาะ 13/09/69:
อัตโนมัติ + ทุกใบก่อนหน้า + ข้ามรูป) แล้วใบที่ดึงได้ครั้งที่ (visit_no) ตามเลขเซอร์เวย์

ไม่แตะเครือข่าย: แทน build_case/sesurvey_post ด้วยตัวปลอมที่จดว่าถูกเรียกด้วยอะไร

รัน:  python -m pytest tests/test_pull_reference.py -q
"""
from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import pull_core  # noqa: E402

JOBS = [  # โครงเดียวกับ ISurveyAPI.list_claim_jobs (listcases + status_name)
    {"caseID": "c1", "survey_no": "SEABI-110260301484", "sttcase_ID": "100", "status_name": "จบงาน",
     "dispatch_datetime": "2026-03-13 12:14", "close_datetime": "2026-03-20 10:01", "claim_no": "2026013020764"},
    {"caseID": "c3", "survey_no": "SEABI-410260401463", "sttcase_ID": "40", "status_name": "รอตรวจข้อมูล",
     "dispatch_datetime": "2026-04-15 09:25", "close_datetime": "", "claim_no": "2026013020764"},
    {"caseID": "c2", "survey_no": "SEABI-410260400230", "sttcase_ID": "100", "status_name": "จบงาน",
     "dispatch_datetime": "2026-04-02 17:47", "close_datetime": "2026-04-03 10:05", "claim_no": "2026013020764"},
    {"caseID": "cx", "survey_no": "SEABI-410260400999", "sttcase_ID": "99", "status_name": "ยกเลิกเคลม",
     "dispatch_datetime": "2026-04-05 09:00", "close_datetime": "", "claim_no": "2026013020764"},
]


class FakeAPI:
    def list_claim_jobs(self, claim):
        return [dict(j) for j in JOBS]

    def find_case(self, claim, invoice=""):
        return next(j for j in JOBS if j["survey_no"] == invoice)


class _Posts(list):
    """list ของ (path, payload) ที่ยิงไปเว็บ + .lookups = เลขเซอร์เวย์ที่ถามผ่าน /cases/lookup"""
    lookups: list


def _harness(monkeypatch, dup_survey_nos=(), web_has=(), lookup_error=None):
    posts = _Posts()
    lookups = []

    def fake_get(base, token, path, timeout=60):
        no = urllib.parse.unquote(path.split("survey_no=")[-1])
        lookups.append(no)
        if lookup_error:
            return None, lookup_error
        return {"success": True, "data": ({"id": 55, "status": "surveyed"} if no in web_has else None)}, None

    monkeypatch.setattr(pull_core, "sesurvey_get", fake_get)
    posts.lookups = lookups

    def fake_build_case(api, case_id, listrow=None):
        return {"report": {"survey_job_no": listrow["survey_no"]}, "caseFields": {}, "warnings": []}

    def fake_post(base, token, path, payload=None, body=None, content_type=None, timeout=120):
        posts.append((path, payload))
        if payload and payload["report"]["survey_job_no"] in dup_survey_nos:
            return None, "se-survey ตอบ 409: เลขเซอร์เวย์นี้มีอยู่แล้ว"
        return {"success": True, "data": {"caseId": 100 + len(posts)}}, None

    monkeypatch.setattr(pull_core, "build_case", fake_build_case)
    monkeypatch.setattr(pull_core, "sesurvey_post", fake_post)
    return posts


def test_round3_pulls_rounds_1_and_2_as_reference_first(monkeypatch):
    posts = _harness(monkeypatch)
    result, err = pull_core.pull_case(FakeAPI(), "2026013020764", "SEABI-410260401463",
                                      "https://api.example", "tok", created_by=7, with_photos=False)
    assert err is None
    paths = [p for p, _ in posts]
    assert paths == ["/api/integrations/cases/import"] * 3
    nos = [pl["report"]["survey_job_no"] for _, pl in posts]
    # ครั้งก่อนหน้าไปก่อนตามลำดับ (1 แล้ว 2) แล้วค่อยใบที่ขอดึง (3) · ใบยกเลิกไม่ถูกดึง
    assert nos == ["SEABI-110260301484", "SEABI-410260400230", "SEABI-410260401463"]
    ref1, ref2, main = (pl for _, pl in posts)
    assert ref1["reference"] == {"closed_at": "2026-03-20T10:01:00+07:00", "round": 1, "status": "จบงาน"}
    assert ref1["visit_no"] == 1 and ref2["visit_no"] == 2 and ref2["reference"]["round"] == 2
    assert "reference" not in main and main["visit_no"] == 3
    assert all(pl["insurance_company"] == "ไอโออิกรุงเทพประกันภัย" and pl["created_by"] == 7 for _, pl in posts)
    assert result["visit_no"] == 3
    assert [(r["round"], r["caseId"], r["skipped"]) for r in result["references"]] == [(1, 101, None), (2, 102, None)]


def test_reference_already_in_web_is_skipped_not_fatal(monkeypatch):
    posts = _harness(monkeypatch, dup_survey_nos=("SEABI-110260301484",))
    result, err = pull_core.pull_case(FakeAPI(), "2026013020764", "SEABI-410260401463",
                                      "https://api.example", "tok", with_photos=False)
    assert err is None
    refs = result["references"]
    assert refs[0]["round"] == 1 and refs[0]["caseId"] is None and refs[0]["skipped"] == "มีในระบบแล้ว"
    assert refs[1]["round"] == 2 and refs[1]["caseId"] is not None
    assert posts[-1][1]["visit_no"] == 3          # ใบหลักยังถูกดึงตามปกติ


def test_first_round_pulls_nothing_extra(monkeypatch):
    posts = _harness(monkeypatch)
    result, err = pull_core.pull_case(FakeAPI(), "2026013020764", "SEABI-110260301484",
                                      "https://api.example", "tok", with_photos=False)
    assert err is None and len(posts) == 1
    assert posts[0][1]["visit_no"] == 1 and "reference" not in posts[0][1]
    assert result["references"] == [] and result["visit_no"] == 1


def test_references_get_their_own_photos(monkeypatch):
    """15/09/69 user เปลี่ยนกติกา: รูปเป็นของครั้งนั้น ๆ → เคสอ้างอิงต้องได้รูปของมันด้วย (เดิม 13/09 ข้ามรูป)
    ลำดับ: import ครั้งที่ 1 → รูปครั้งที่ 1 → import ครั้งที่ 2 → รูปครั้งที่ 2 → import ใบหลัก → รูปใบหลัก"""
    posts = _harness(monkeypatch)
    downloaded = []

    class PhotoAPI(FakeAPI):
        def download_images(self, case_id, out_dir):
            downloaded.append(case_id)
            return {"INS": 2}

    monkeypatch.setattr(pull_core, "zip_photos", lambda tmp: b"PK-fake-zip")
    result, err = pull_core.pull_case(PhotoAPI(), "2026013020764", "SEABI-410260401463",
                                      "https://api.example", "tok", with_photos=True)
    assert err is None
    paths = [p for p, _ in posts]
    assert paths == ["/api/integrations/cases/import", "/api/integrations/cases/101/photos-zip",
                     "/api/integrations/cases/import", "/api/integrations/cases/103/photos-zip",
                     "/api/integrations/cases/import", "/api/integrations/cases/105/photos-zip"]
    assert downloaded == ["c1", "c2", "c3"]            # โหลดรูปของแต่ละงานจาก ISURVEY ตามครั้ง
    refs = result["references"]
    assert [r["caseId"] for r in refs] == [101, 103] and all(r["photos"] is not None for r in refs)
    assert refs[0]["photos"]["isurvey_photo_counts"] == {"INS": 2}
    assert result["isurvey_photo_counts"] == {"INS": 2} and "isurvey_photo_counts" not in result["photos"]


def test_reference_skipped_gets_no_photo_push(monkeypatch):
    posts = _harness(monkeypatch, dup_survey_nos=("SEABI-110260301484",))
    monkeypatch.setattr(pull_core, "zip_photos", lambda tmp: b"PK-fake-zip")

    class PhotoAPI(FakeAPI):
        def download_images(self, case_id, out_dir):
            return {}

    result, err = pull_core.pull_case(PhotoAPI(), "2026013020764", "SEABI-410260401463",
                                      "https://api.example", "tok", with_photos=True)
    assert err is None
    photo_posts = [p for p, _ in posts if p.endswith("/photos-zip")]
    assert len(photo_posts) == 2                          # ครั้งที่ 2 + ใบหลัก (ครั้งที่ 1 มีในเว็บแล้ว ไม่ยิงรูป)
    assert result["references"][0]["photos"] is None and result["references"][1]["photos"] is not None


def test_iso_bkk_dt():
    assert pull_core._iso_bkk_dt("2026-06-04 22:48") == "2026-06-04T22:48:00+07:00"
    assert pull_core._iso_bkk_dt("2026-06-04 4:42:10") == "2026-06-04T04:42:00+07:00"
    assert pull_core._iso_bkk_dt("") is None and pull_core._iso_bkk_dt(None) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


# ── 19/09/69 user: ครั้งก่อนหน้าเป็นเคสอ้างอิงเฉพาะที่จบงาน (100) · ใบที่ยังรอตรวจ/ยังไม่จบ ต้องมีในเว็บแล้ว ไม่งั้นหยุดและบอกให้ดึงใบนั้นก่อน ──
def _jobs_round2(status_id: str, status_name: str):
    jobs = [dict(j) for j in JOBS]
    r2 = next(j for j in jobs if j["survey_no"] == "SEABI-410260400230")
    r2.update({"sttcase_ID": status_id, "status_name": status_name, "close_datetime": ""})
    return jobs


class _OpenAPI(FakeAPI):
    def __init__(self, jobs):
        self._jobs = jobs

    def list_claim_jobs(self, claim):
        return [dict(j) for j in self._jobs]

    def find_case(self, claim, invoice=""):
        return next(j for j in self._jobs if j["survey_no"] == invoice)


def test_open_earlier_round_not_on_web_blocks_pull(monkeypatch):
    posts = _harness(monkeypatch)
    api = _OpenAPI(_jobs_round2("40", "รอตรวจข้อมูล"))
    result, err = pull_core.pull_case(api, "2026013020764", "SEABI-410260401463",
                                      "https://api.example", "tok", with_photos=False)
    assert result is None and err
    assert "ยังดึงครั้งที่ 3 ไม่ได้" in err and "ครั้งที่ 2 (SEABI-410260400230)" in err
    assert 'ยังเป็น "รอตรวจข้อมูล"' in err and "ดึงใบนั้นเข้ามาตรวจก่อน" in err
    assert posts == []                                   # ไม่มีอะไรถูกสร้างบนเว็บ (ไม่มีอ้างอิง ไม่มีใบหลัก)
    assert posts.lookups == ["SEABI-410260400230"]       # ถามเว็บเฉพาะใบที่ยังไม่จบ (ครั้งที่ 1 จบงานไม่ต้องถาม)


def test_open_earlier_round_already_on_web_passes_without_repull(monkeypatch):
    posts = _harness(monkeypatch, web_has=("SEABI-410260400230",))
    api = _OpenAPI(_jobs_round2("40", "รอตรวจข้อมูล"))
    result, err = pull_core.pull_case(api, "2026013020764", "SEABI-410260401463",
                                      "https://api.example", "tok", with_photos=False)
    assert err is None
    nos = [pl["report"]["survey_job_no"] for _, pl in posts]
    assert nos == ["SEABI-110260301484", "SEABI-410260401463"]   # ครั้งที่ 1 อ้างอิง · ครั้งที่ 2 ไม่ดึงซ้ำ · ใบหลัก
    refs = result["references"]
    assert refs[0]["round"] == 1 and refs[0]["caseId"] is not None
    assert refs[1]["round"] == 2 and refs[1]["caseId"] is None and "งานปกติ" in refs[1]["skipped"]
    assert posts[-1][1]["visit_no"] == 3 and result["visit_no"] == 3


def test_unfinished_earlier_round_has_wait_message(monkeypatch):
    posts = _harness(monkeypatch)
    api = _OpenAPI(_jobs_round2("20", "กำลังสำรวจ"))
    result, err = pull_core.pull_case(api, "2026013020764", "SEABI-410260401463",
                                      "https://api.example", "tok", with_photos=False)
    assert result is None and "ยังไม่จบงานบน ISURVEY" in err and 'สถานะ "กำลังสำรวจ"' in err and posts == []


def test_lookup_failure_is_reported_in_message(monkeypatch):
    posts = _harness(monkeypatch, lookup_error="se-survey ตอบ 503")
    api = _OpenAPI(_jobs_round2("40", "รอตรวจข้อมูล"))
    result, err = pull_core.pull_case(api, "2026013020764", "SEABI-410260401463",
                                      "https://api.example", "tok", with_photos=False)
    assert result is None and "ตรวจกับเว็บไม่สำเร็จ: se-survey ตอบ 503" in err and posts == []


def test_closed_earlier_rounds_never_ask_web(monkeypatch):
    posts = _harness(monkeypatch)
    result, err = pull_core.pull_case(FakeAPI(), "2026013020764", "SEABI-410260401463",
                                      "https://api.example", "tok", with_photos=False)
    assert err is None and posts.lookups == []            # ครั้งที่ 1–2 จบงาน → อ้างอิงตามเดิม ไม่ต้องถามเว็บ
