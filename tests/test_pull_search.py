# -*- coding: utf-8 -*-
"""เทสค้นงานบน ISURVEY จากเว็บ se-survey + ดึงงานที่จบแล้วเข้าเป็นเคสอ้างอิง ดูอย่างเดียว (user สั่ง 08/10/69)

- search_jobs: ช่องค้นหาเดียวกับหน้าตรวจงาน ISURVEY (เลขเคลม/เลขรับแจ้ง/เลขเซอร์เวย์) · ครั้งที่ตามกติกา survey_order ·
  ค้นด้วยเลขเคลมเต็มไม่ถามซ้ำ · ค้นด้วยเลขอื่นถามทุกใบของเคลมเพิ่ม (ไม่เกิน 3 เคลม) · ไม่ตัดแถวบริษัทนอก/ยังไม่จ่ายงานทิ้ง
- pull_case(as_reference=True): เฉพาะ "จบงาน" (100) → payload มี reference (เวลาปิด + ครั้งที่) · ครั้งก่อนหน้าที่ยังไม่จบ = ข้าม ไม่หยุด

ไม่แตะเครือข่าย · รัน:  python -m pytest tests/test_pull_search.py -q
"""
from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import pull_core  # noqa: E402

CLAIM = "2026013020764"
JOBS = [  # โครงเดียวกับ listcases + status_name (ครั้งที่ 1 จบ · ครั้งที่ 2 จบ · ครั้งที่ 3 รอตรวจ · ใบยกเลิกไม่นับ)
    {"caseID": "c1", "claim_no": CLAIM, "notify_no": "2026111111", "survey_no": "SEABI-110260301484", "sttcase_ID": "100",
     "status_name": "จบงาน", "dispatch_datetime": "2026-03-13 12:14", "close_datetime": "2026-03-20 10:01",
     "surveyor_name": "SE332\xa0นาย ทดสอบ", "acc_place": "หน้าห้าง", "acc_province": "สมุทรปราการ"},
    {"caseID": "c3", "claim_no": CLAIM, "notify_no": "2026111111", "survey_no": "SEABI-410260401463", "sttcase_ID": "40",
     "status_name": "รอตรวจข้อมูล", "dispatch_datetime": "2026-04-15 09:25", "close_datetime": ""},
    {"caseID": "c2", "claim_no": CLAIM, "notify_no": "2026111111", "survey_no": "SEABI-410260400230", "sttcase_ID": "100",
     "status_name": "จบงาน", "dispatch_datetime": "2026-04-02 17:47", "close_datetime": "2026-04-03 10:05"},
    {"caseID": "cx", "claim_no": CLAIM, "notify_no": "2026111111", "survey_no": "SEABI-410260400999", "sttcase_ID": "99",
     "status_name": "ยกเลิกเคลม", "dispatch_datetime": "2026-04-05 09:00", "close_datetime": ""},
]


class FakeAPI:
    def __init__(self, search_rows=None, jobs=None):
        self.search_rows = search_rows if search_rows is not None else JOBS
        self.jobs = jobs if jobs is not None else JOBS
        self.searched: list[str] = []
        self.claim_lookups: list[str] = []

    def search_cases(self, q, limit=50):
        self.searched.append(q)
        return [dict(r) for r in self.search_rows][:limit]

    def list_claim_jobs(self, claim):
        self.claim_lookups.append(claim)
        return [dict(j) for j in self.jobs if j["claim_no"] == claim]

    def find_case(self, claim, invoice=""):
        return next(dict(j) for j in self.jobs if j["survey_no"] == invoice)


# ── search_jobs ──
def test_search_by_full_claim_uses_found_rows_for_rounds():
    api = FakeAPI()
    out = pull_core.search_jobs(api, f" {CLAIM} ")
    assert api.searched == [CLAIM] and api.claim_lookups == []          # เลขเคลมเต็ม: ไม่ถามทุกใบซ้ำ
    assert [(r["survey_no"], r["round"]) for r in out["rounds"][CLAIM]] == [
        ("SEABI-110260301484", 1), ("SEABI-410260400230", 2), ("SEABI-410260401463", 3)]   # ใบยกเลิกไม่นับครั้ง
    assert len(out["cases"]) == 4 and out["capped"] is False
    first = out["cases"][0]
    assert first["status_id"] == "100" and first["status_name"] == "จบงาน" and first["notify_no"] == "2026111111"
    assert first["surveyor_name"] == "SE332 นาย ทดสอบ"                  # NBSP ถูกล้าง
    assert first["close_dt"] == "2026-03-20 10:01" and first["insurer_known"] is True


def test_search_by_notify_no_asks_whole_claim_for_rounds():
    api = FakeAPI(search_rows=[JOBS[1]])                                 # ค้นเลขรับแจ้ง/เลขเซอร์เวย์ ได้แถวเดียว
    out = pull_core.search_jobs(api, "SEABI-410260401463")
    assert api.claim_lookups == [CLAIM]                                  # ต้องถามทุกใบของเคลม ไม่งั้นครั้งที่ผิด (ได้ 1)
    assert {r["survey_no"]: r["round"] for r in out["rounds"][CLAIM]}["SEABI-410260401463"] == 3


def test_search_many_claims_skips_rounds_and_flags_cap():
    rows = [{**JOBS[0], "claim_no": f"20260131788{i:02d}", "survey_no": f"SEABI-1102603{i:05d}"} for i in range(50)]
    api = FakeAPI(search_rows=rows)
    out = pull_core.search_jobs(api, "20260131788")
    assert out["rounds"] == {} and api.claim_lookups == []               # เกิน 3 เคลม ไม่ไล่ถามทีละเคลม
    assert out["capped"] is True and len(out["cases"]) == 50


def test_search_keeps_unknown_insurer_and_undispatched_rows():
    rows = [{"claim_no": "2026013178806", "notify_no": "2026173347", "survey_no": "", "sttcase_ID": "", "status_name": ""},
            {"claim_no": "2026013178807", "notify_no": "2026173348", "survey_no": "SEMS-1", "sttcase_ID": "40", "status_name": "รอตรวจข้อมูล"}]
    out = pull_core.search_jobs(FakeAPI(search_rows=rows, jobs=[]), "2026013178")
    assert [r["insurer_known"] for r in out["cases"]] == [False, False]   # หน้าเว็บบอกเองว่าดึงไม่ได้ ไม่ตัดทิ้ง
    assert out["rounds"]["2026013178806"] == [] and out["rounds"]["2026013178807"] == []


def test_search_round_error_does_not_break_results():
    class Boom(FakeAPI):
        def list_claim_jobs(self, claim):
            raise RuntimeError("ISURVEY ช้า")
    out = pull_core.search_jobs(Boom(search_rows=[JOBS[1]]), "2026111111")
    assert len(out["cases"]) == 1 and "error" in out["rounds"][CLAIM]


def test_search_cases_retries_once_on_read_timeout():
    """08/10/69 หัวหน้าค้นบนเว็บแล้วได้ ReadTimeout 30 วิ (ISURVEY ค้างคำขอแรกหลังล็อกอิน) → รอ 45 วิ แล้วลองใหม่ 1 ครั้ง (75 วิ)"""
    import requests
    from autokey.isurvey_api import ISurveyAPI
    api = ISurveyAPI.__new__(ISurveyAPI)                 # ไม่ล็อกอิน ไม่แตะเครือข่าย
    api._masters = {"masterStatus": {"100": "จบงาน"}}
    calls = []

    def fake_get(path, _timeout=30, **params):
        calls.append((path, _timeout, params.get("claim_no")))
        if len(calls) == 1:
            raise requests.exceptions.ReadTimeout("slow")
        return {"cases": [{"claim_no": CLAIM, "sttcase_ID": "100"}]}

    api._get = fake_get
    rows = api.search_cases(CLAIM)
    assert [c[1] for c in calls] == [45, 75] and all(c[0] == "supervisor/listcases.php" and c[2] == CLAIM for c in calls)
    assert rows[0]["status_name"] == "จบงาน"


def test_search_jobs_timeout_becomes_readable_message():
    import pytest
    import requests

    class Slow(FakeAPI):
        def search_cases(self, q, limit=50):
            raise requests.exceptions.ReadTimeout("HTTPSConnectionPool(...): Read timed out.")
    with pytest.raises(RuntimeError, match="ISURVEY ตอบช้ามาก"):
        pull_core.search_jobs(Slow(), CLAIM)


# ── pull_case(as_reference=True) ──
def _harness(monkeypatch, web_has=()):
    posts, lookups = [], []

    def fake_get(base, token, path, timeout=60):
        no = urllib.parse.unquote(path.split("survey_no=")[-1])
        lookups.append(no)
        return {"success": True, "data": ({"id": 55} if no in web_has else None)}, None

    def fake_build_case(api, case_id, listrow=None):
        return {"report": {"survey_job_no": listrow["survey_no"]}, "caseFields": {}, "warnings": []}

    def fake_post(base, token, path, payload=None, body=None, content_type=None, timeout=120):
        posts.append((path, payload))
        return {"success": True, "data": {"caseId": 200 + len(posts)}}, None

    monkeypatch.setattr(pull_core, "sesurvey_get", fake_get)
    monkeypatch.setattr(pull_core, "build_case", fake_build_case)
    monkeypatch.setattr(pull_core, "sesurvey_post", fake_post)
    return posts, lookups


def test_reference_pull_of_closed_round2(monkeypatch):
    posts, _ = _harness(monkeypatch)
    result, err = pull_core.pull_case(FakeAPI(), CLAIM, "SEABI-410260400230", "https://api.example", "tok",
                                      created_by=7, with_photos=False, as_reference=True)
    assert err is None and result["as_reference"] is True and result["visit_no"] == 2
    nos = [pl["report"]["survey_job_no"] for _, pl in posts]
    assert nos == ["SEABI-110260301484", "SEABI-410260400230"]           # ครั้งที่ 1 (จบแล้ว) ตามมาเป็นอ้างอิงก่อน
    main = posts[-1][1]
    assert main["reference"] == {"closed_at": "2026-04-03T10:05:00+07:00", "round": 2, "status": "จบงาน"}
    assert main["visit_no"] == 2 and main["created_by"] == 7


def test_reference_pull_refuses_job_not_closed(monkeypatch):
    posts, _ = _harness(monkeypatch)
    result, err = pull_core.pull_case(FakeAPI(), CLAIM, "SEABI-410260401463", "https://api.example", "tok",
                                      with_photos=False, as_reference=True)
    assert result is None and "เฉพาะงานที่ \"จบงาน\"" in err and posts == []


def test_reference_pull_skips_open_earlier_round_instead_of_blocking(monkeypatch):
    jobs = [dict(j) for j in JOBS]
    # ครั้งที่ 3 จบแล้ว แต่ครั้งที่ 2 ยังรอตรวจข้อมูลและยังไม่มีในเว็บ — ดึงแบบปกติจะหยุด (OpenRoundError) · แบบดูอย่างเดียวข้ามใบนั้น
    next(j for j in jobs if j["caseID"] == "c2").update({"sttcase_ID": "40", "status_name": "รอตรวจข้อมูล", "close_datetime": ""})
    next(j for j in jobs if j["caseID"] == "c3").update({"sttcase_ID": "100", "status_name": "จบงาน", "close_datetime": "2026-04-20 08:00"})
    posts, lookups = _harness(monkeypatch)
    api = FakeAPI(jobs=jobs)
    result, err = pull_core.pull_case(api, CLAIM, "SEABI-410260401463", "https://api.example", "tok",
                                      with_photos=False, as_reference=True)
    assert err is None and result["visit_no"] == 3
    assert [pl["report"]["survey_job_no"] for _, pl in posts] == ["SEABI-110260301484", "SEABI-410260401463"]
    skipped = {r["survey_no"]: r["skipped"] for r in result["references"]}
    assert "ยังไม่จบงานบน ISURVEY" in skipped["SEABI-410260400230"]
    # แบบปกติยังหยุดเหมือนเดิม (กติกา 19/09/69 ไม่เปลี่ยน)
    posts.clear()
    result2, err2 = pull_core.pull_case(api, CLAIM, "SEABI-410260401463", "https://api.example", "tok", with_photos=False)
    assert result2 is None and "ยังดึงครั้งที่ 3 ไม่ได้" in err2 and posts == []


def test_normal_pull_has_no_reference_flag(monkeypatch):
    posts, _ = _harness(monkeypatch)
    result, err = pull_core.pull_case(FakeAPI(), CLAIM, "SEABI-110260301484", "https://api.example", "tok", with_photos=False)
    assert err is None and result["as_reference"] is False and "reference" not in posts[-1][1]


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
