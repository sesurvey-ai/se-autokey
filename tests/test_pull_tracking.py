# -*- coding: utf-8 -*-
"""งาน "ติดตาม" (user สั่ง 09/10/69) — ดึงงานที่ช่างยังทำอยู่บน ISURVEY เข้าเว็บแบบดูอย่างเดียว แล้วอัปเดตเองเมื่อสถานะเปลี่ยน

- status_kind: รอตรวจข้อมูล 40 = review · จบงาน 100 = closed · ยกเลิกเคลม 99/ไม่รับงาน 60 = cancelled · ที่เหลือ = working
- pull_case(tracking=True): ช่างยังทำ → payload.tracking (สถานะ ISURVEY) · ช่างส่งแล้ว (40) = ดึงปกติ · จบงาน/ยกเลิก = ไม่ดึง
- refresh_case: อ่านใหม่ทั้งใบ + สถานะ → POST /cases/:id/refresh แล้วเติมรูป · อ่านพลาด = ไม่ยิง backend (ห้ามทับด้วยของว่าง)
- job_statuses: 1 คำขอต่อเลขเคลม · เคลมที่ถามพลาดข้ามไป
- service: /status /refresh (ข้อมูลไม่ครบ = 400)

ไม่แตะเครือข่าย · รัน:  python -m pytest tests/test_pull_tracking.py -q
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pull_service  # noqa: E402
from autokey import pull_core  # noqa: E402

CLAIM = "2026013084159"
SNO = "SEABI-110261001580"
NAMES = {"30": "เสร็จงาน", "40": "รอตรวจข้อมูล", "100": "จบงาน", "99": "ยกเลิกเคลม", "60": "ไม่รับงาน", "20": "ถึงที่ตรวจสอบ"}


class FakeAPI:
    def __init__(self, status="30", fail=False):
        self.status, self.fail = status, fail
        self.claim_calls: list[str] = []

    def find_case(self, claim, invoice=""):
        if self.fail:
            raise RuntimeError("Session lose!")
        return {"caseID": "c1", "claim_no": claim, "survey_no": invoice, "sttcase_ID": self.status,
                "close_datetime": "2026-10-09 15:20" if self.status == "100" else ""}

    def master(self, name, key, val):
        return dict(NAMES)

    def list_claim_jobs(self, claim):
        self.claim_calls.append(claim)
        if claim == "bad":
            raise RuntimeError("ISURVEY ช้า")
        return [{"survey_no": f"{claim}-A", "sttcase_ID": "40", "status_name": "รอตรวจข้อมูล"},
                {"survey_no": f"{claim}-B", "sttcase_ID": "30", "status_name": "เสร็จงาน"}]


@pytest.fixture()
def posts(monkeypatch):
    out: list = []

    def fake_post(base, token, path, payload=None, body=None, content_type=None, timeout=120):
        out.append((path, payload))
        return {"success": True, "data": {"caseId": 77, "transition": "tracking"}}, None

    monkeypatch.setattr(pull_core, "sesurvey_post", fake_post)
    monkeypatch.setattr(pull_core, "build_case", lambda api, cid, case=None: {
        "report": {"survey_job_no": SNO}, "caseFields": {}, "warnings": [], "source_comments": {}})
    monkeypatch.setattr(pull_core, "pull_references", lambda *a, **k: ([], 1))
    monkeypatch.setattr(pull_core, "_push_photos", lambda *a, **k: {"added": 2, "skipped": 0})
    return out


def test_status_kind():
    k = pull_core.status_kind
    assert k("40") == "review" and k("", "รอตรวจข้อมูล") == "review"
    assert k("100") == "closed" and k("", "จบงาน") == "closed"
    assert k("99") == k("60") == k("", "ยกเลิกเคลม") == k("", "ไม่รับงาน") == "cancelled"
    assert k("30", "เสร็จงาน") == k("20") == k("", "ยืนยันรับงาน") == "working"


def test_tracking_pull_working_job(posts):
    res, err = pull_core.pull_case(FakeAPI("30"), CLAIM, SNO, "u", "t", created_by=5, tracking=True)
    assert err is None and res["tracking"] is True
    path, payload = posts[0]
    assert path == "/api/integrations/cases/import"
    assert payload["tracking"] == {"status_id": "30", "status_name": "เสร็จงาน"}
    assert "reference" not in payload


def test_tracking_pull_job_already_submitted_becomes_normal(posts):
    res, err = pull_core.pull_case(FakeAPI("40"), CLAIM, SNO, "u", "t", tracking=True)
    assert err is None and res["tracking"] is False and "tracking" not in posts[0][1]


@pytest.mark.parametrize("status,word", [("100", "ดึงเข้า (ดูอย่างเดียว)"), ("99", "ยกเลิก"), ("60", "ยกเลิก")])
def test_tracking_pull_refuses_closed_or_cancelled(posts, status, word):
    res, err = pull_core.pull_case(FakeAPI(status), CLAIM, SNO, "u", "t", tracking=True)
    assert res is None and word in err and posts == []


def test_normal_pull_of_working_job_points_to_tracking(posts):
    res, err = pull_core.pull_case(FakeAPI("30"), CLAIM, SNO, "u", "t")
    assert res is None and "ดึงเข้า (ติดตาม)" in err and posts == []


def test_refresh_case_posts_status_then_photos(posts):
    seen = []
    res, err = pull_core.refresh_case(FakeAPI("40"), CLAIM, SNO, 77, "u", "t", visit_no=1,
                                      before_post=lambda: seen.append("checked"))
    assert err is None and seen == ["checked"]
    path, payload = posts[0]
    assert path == "/api/integrations/cases/77/refresh"
    assert payload["isurvey_status"] == {"id": "40", "name": "รอตรวจข้อมูล", "closed_at": None}
    assert payload["insurance_company"] and "source_comments" not in payload      # apply_visit_rules ตัดออกแล้ว
    assert res["photos"] == {"added": 2, "skipped": 0}


def test_refresh_case_closed_sends_close_time(posts):
    pull_core.refresh_case(FakeAPI("100"), CLAIM, SNO, 77, "u", "t")
    assert posts[0][1]["isurvey_status"]["closed_at"] == "2026-10-09T15:20:00+07:00"


def test_refresh_case_read_failure_never_posts(posts):
    res, err = pull_core.refresh_case(FakeAPI(fail=True), CLAIM, SNO, 77, "u", "t")
    assert res is None and "อ่านงานจาก ISURVEY ไม่ได้" in err and posts == []
    res, err = pull_core.refresh_case(FakeAPI(), CLAIM, "XX-1", 77, "u", "t")
    assert res is None and "คำนำหน้า" in err and posts == []


def test_job_statuses_one_call_per_claim():
    api = FakeAPI()
    out = pull_core.job_statuses(api, [{"claim": "A1", "survey_no": "A1-A"}, {"claim": "A1", "survey_no": "A1-B"},
                                       {"claim": "bad", "survey_no": "x"}, {"claim": "", "survey_no": "y"}])
    assert api.claim_calls == ["A1", "bad"]
    assert out == {"A1-A": {"status_id": "40", "status_name": "รอตรวจข้อมูล"},
                   "A1-B": {"status_id": "30", "status_name": "เสร็จงาน"}}


@pytest.fixture()
def service(monkeypatch):
    api = FakeAPI()
    monkeypatch.setattr(pull_core, "make_client", lambda u, p: api)
    monkeypatch.setattr(pull_service, "TOKEN", "t")
    monkeypatch.setattr(pull_service, "SESURVEY_TOKEN", "x")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), pull_service.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def post(path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode("utf-8"),
                                     headers={"X-Service-Token": "t", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield post
    srv.shutdown()


def test_service_status_and_refresh(service, monkeypatch):
    code, j = service("/status", {"username": "u", "password": "p", "items": [{"claim": "C9", "survey_no": "C9-A"}]})
    assert code == 200 and j["statuses"]["C9-A"]["status_name"] == "รอตรวจข้อมูล" and j["account"] == "own"
    assert service("/refresh", {"username": "u", "password": "p", "claim": CLAIM, "survey_no": SNO})[0] == 400
    calls = []
    monkeypatch.setattr(pull_core, "refresh_case", lambda api, c, s, cid, url, tok, visit_no=None, before_post=None:
                        (calls.append((c, s, cid, visit_no)) or {"transition": "surveyed"}, None))
    code, j = service("/refresh", {"username": "u", "password": "p", "claim": CLAIM, "survey_no": SNO, "case_id": 77, "visit_no": 2})
    assert code == 200 and j["result"]["transition"] == "surveyed" and calls == [(CLAIM, SNO, 77, 2)]
    assert {"/refresh", "/status"} <= pull_service.CENTRAL_PATHS
