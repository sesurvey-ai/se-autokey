# -*- coding: utf-8 -*-
"""เทสตัวดึงงาน: งานครั้งถัดไปต้องดึงครั้งก่อนหน้าที่ยังไม่มีในเว็บมาเป็น "เคสอ้างอิง" ก่อน (user เคาะ 13/09/69:
อัตโนมัติ + ทุกใบก่อนหน้า + ข้ามรูป) แล้วใบที่ดึงได้ครั้งที่ (visit_no) ตามเลขเซอร์เวย์

ไม่แตะเครือข่าย: แทน build_case/sesurvey_post ด้วยตัวปลอมที่จดว่าถูกเรียกด้วยอะไร

รัน:  python -m pytest tests/test_pull_reference.py -q
"""
from __future__ import annotations

import sys
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


def _harness(monkeypatch, dup_survey_nos=()):
    posts = []

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


def test_iso_bkk_dt():
    assert pull_core._iso_bkk_dt("2026-06-04 22:48") == "2026-06-04T22:48:00+07:00"
    assert pull_core._iso_bkk_dt("2026-06-04 4:42:10") == "2026-06-04T04:42:00+07:00"
    assert pull_core._iso_bkk_dt("") is None and pull_core._iso_bkk_dt(None) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
