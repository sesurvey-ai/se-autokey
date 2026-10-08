# -*- coding: utf-8 -*-
"""บัญชี ISURVEY กลาง (08/10/69) — ล็อกอินครั้งเดียวแล้วใช้ session เดิม · หลุดแล้วล็อกอินใหม่เอง · ไม่แย่งกับคนที่เปิดบัญชีกลางที่อื่น
· session ไม่ต่อเนื่องระหว่างงาน = อ่านใหม่ (ISURVEY บางหน้าตอบ "ว่าง" เงียบ ๆ ตอนหลุด) · service ถอยไปบัญชีหัวหน้าเมื่อบัญชีกลางใช้ไม่ได้

ISURVEY ปลอมในเครื่อง (ไม่แตะเครือข่าย): ล็อกอินใหม่ = session เก่าตาย (1 บัญชีใช้ได้ที่เดียว) · คำตอบตอน session หลุด
ใช้รูปแบบที่ยิงดูจริงแบบไม่ล็อกอิน 08/10/69 (listcases "Session lose!" · get-images ว่างเงียบ · รายงาน PHP Notice)
รัน:  python -m pytest tests/test_isurvey_central.py -q
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
from autokey import isurvey_central, pull_core  # noqa: E402
from autokey.isurvey_api import session_lost  # noqa: E402
from autokey.isurvey_central import CentralSession, CentralUnavailable  # noqa: E402


# ───────────────────────────────────────────── ISURVEY ปลอม
class _Resp:
    def __init__(self, obj=None, text=None, status=200):
        self.status_code = status
        self.text = text if text is not None else json.dumps(obj)

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeIsurvey:
    """เซิร์ฟเวอร์ ISURVEY จำลอง: บัญชีละ 1 session — ล็อกอินใหม่ = session เก่าตาย"""

    def __init__(self):
        self.current = None
        self.next_sid = 0
        self.logins = 0
        self.login_error = None          # None | "bad" (รหัสผิด) | "net" (ISURVEY ไม่ตอบ)
        self.hits: list[str] = []

    def kick(self):                      # มีคนล็อกอินบัญชีเดียวกันที่อื่น
        self.next_sid += 1
        self.current = f"other-{self.next_sid}"

    def valid(self, sess) -> bool:
        return sess.sid is not None and sess.sid == self.current


class FakeHttp:
    """แทน requests.Session ของ ISurveyAPI"""

    def __init__(self, server: FakeIsurvey):
        self.server = server
        self.sid = None

    def get(self, url, params=None, timeout=None):
        sv = self.server
        name = url.rsplit("/", 1)[-1]
        sv.hits.append(name)
        ok = sv.valid(self)
        if name == "getUserData.php":
            return _Resp({"success": 1, "message": "บัญชีกลาง ทดสอบ"} if ok else {"success": 0, "message": ""})
        if name == "listcases.php":
            return _Resp({"total": 1, "cases": [{"claim_no": "2026013000001"}]} if ok
                         else {"total": 0, "message": "Session lose!"})
        if name == "get-images.php":
            return _Resp({"images": [{"name": "a.jpg"}]} if ok else {"images": []})      # หลุด = ว่างเงียบ ๆ
        if name == "get_data_report.php":
            return _Resp({"arr_data": []}) if ok else _Resp(
                text="<br />\n<b>Notice</b>:  Undefined index: iSurvey-SE-6.2.0.981-ins_companyID in <b>x.php</b>")
        return _Resp({"ok": True})                                                       # หน้าแรก (ก่อน POST login)

    def post(self, url, data=None, timeout=None):
        sv = self.server
        if sv.login_error == "net":
            raise ConnectionError("ISURVEY ไม่ตอบ")
        if sv.login_error != "bad":
            sv.logins += 1
            sv.next_sid += 1
            self.sid = sv.current = f"sid-{sv.next_sid}"
        return _Resp({"success": sv.login_error != "bad"})


def _factory(server):
    def make():
        api = pull_core.new_client("central", "pw")
        api.s = FakeHttp(server)
        return api
    return make


def _central(server, now=None, **kw):
    clock = (lambda: now[0]) if now is not None else None
    extra = {"clock": clock} if clock else {}
    return CentralSession("central", "pw", _factory(server), log=lambda m: None, **extra, **kw)


# ───────────────────────────────────────────── สัญญาณ session หลุด
def test_session_lost_signatures_match_isurvey():
    assert session_lost(_Resp({"total": 0, "message": "Session lose!"}))                    # listcases
    assert session_lost(_Resp(text="<b>Notice</b>: Undefined index: iSurvey-SE-6.2.0.981-sys_branchID"))   # รายงาน enquiry
    assert session_lost(_Resp({"x": 1}, status=401))
    assert not session_lost(_Resp({"total": 1, "cases": [{"remark": "Session lose!"}]}))   # ข้อความในข้อมูล ≠ สัญญาณ
    assert not session_lost(_Resp({"images": []}))                                         # ว่างเงียบ ๆ จับจากคำตอบไม่ได้
    assert not session_lost(_Resp({"success": 0, "message": ""}))                          # getUserData — ผู้เรียกเช็คเอง
    assert not session_lost(_Resp([1, 2]))


# ───────────────────────────────────────────── ล็อกอินครั้งเดียว ใช้ร่วมกัน
def test_logs_in_once_and_shares_one_session_but_not_the_client():
    sv = FakeIsurvey()
    c = _central(sv)
    a, b = c.client(), c.client()
    assert sv.logins == 1
    assert a.s is b.s                       # cookie/session เดียวกัน
    assert a is not b                       # ⛔ client ต่องาน — last_case_id ไม่ปนกัน
    a.last_case_id = "111"
    assert b.last_case_id == ""
    assert a._get("supervisor/listcases.php")["cases"]
    assert c.intact(a) and c.status()["state"] == "ready" and c.status()["name"] == "บัญชีกลาง ทดสอบ"


def test_session_lost_on_first_read_relogs_in_and_retries_transparently():
    sv = FakeIsurvey()
    c = _central(sv, min_relogin_gap_sec=0)
    api = c.client()
    sv.kick()                                                   # session หลุดก่อนงานเริ่มอ่าน
    d = api._get("supervisor/listcases.php")
    assert d["cases"] and sv.logins == 2
    assert c.intact(api)                    # ยังไม่ได้อ่านอะไรด้วย session ที่หลุด → งานนี้ยังครบ


def test_session_lost_mid_job_marks_job_not_intact():
    sv = FakeIsurvey()
    c = _central(sv, min_relogin_gap_sec=0)
    api = c.client()
    assert api._get("supervisor/listcases.php")["cases"]
    sv.kick()
    assert api._get("supervisor/get-images.php")["images"] == []      # ว่างเงียบ ๆ — ตัวคำตอบไม่ฟ้อง
    assert not c.intact(api)                                           # แต่เช็คหลังงานจับได้
    api2 = c.client(verify=True)                                       # รอบอ่านใหม่: เช็คก่อน → ล็อกอินใหม่
    assert api2._get("supervisor/get-images.php")["images"] and c.intact(api2)


def test_concurrent_jobs_seeing_the_same_loss_login_only_once():
    sv = FakeIsurvey()
    c = _central(sv, min_relogin_gap_sec=0)
    a, b = c.client(), c.client()
    gen = c.generation
    sv.kick()
    c.recover(gen, "a")
    c.recover(gen, "b")                     # อีกงานเห็นการหลุดเดียวกัน — ไม่ล็อกอินซ้ำ
    assert sv.logins == 2
    assert b._get("supervisor/listcases.php")["cases"]


def test_lost_again_right_after_login_means_someone_else_uses_the_account():
    now = [1000.0]
    sv = FakeIsurvey()
    c = _central(sv, now=now)
    c.client()
    now[0] += 3600                          # ใช้ไปนานแล้วค่อยหลุด (เช่น ISURVEY ล้าง session) = ล็อกอินใหม่ปกติ
    sv.kick()
    c.recover(c.generation, "x")
    assert sv.logins == 2
    now[0] += 10
    sv.kick()                               # หลุดอีกภายใน 1 นาที = มีคนใช้บัญชีกลางที่อื่น
    with pytest.raises(CentralUnavailable):
        c.recover(c.generation, "y")
    assert sv.logins == 2                   # ไม่แย่งกลับ
    assert c.status()["state"] == "paused" and "ที่อื่น" in c.status()["last_error"]
    with pytest.raises(CentralUnavailable):
        c.client()
    now[0] += 301
    assert c.available()


def test_bad_password_pauses_long_and_network_error_pauses_short():
    now = [1000.0]
    sv = FakeIsurvey()
    sv.login_error = "bad"
    c = _central(sv, now=now)
    with pytest.raises(CentralUnavailable, match="ไม่ผ่าน"):
        c.client()
    now[0] += 600
    with pytest.raises(CentralUnavailable):                 # ยังพัก (15 นาที) — ไม่ลองรหัสผิดถี่ ๆ จนบัญชีโดนล็อก
        c.client()
    now[0] += 301
    sv.login_error = "net"
    with pytest.raises(CentralUnavailable, match="ไม่ตอบ"):
        c.client()
    now[0] += 121
    sv.login_error = None
    assert c.client() and sv.logins == 1


def test_keepalive_logs_in_once_then_only_touches_and_relogs_in_when_lost():
    sv = FakeIsurvey()
    c = _central(sv, min_relogin_gap_sec=0)
    c.ping()
    assert sv.logins == 1
    c.ping()
    assert sv.logins == 1 and sv.hits[-1] == "getUserData.php"        # ยังล็อกอินอยู่ = แค่แตะ
    sv.kick()
    c.ping()
    assert sv.logins == 2


def test_admin_test_clears_pause_and_logs_in_now():
    now = [1000.0]
    sv = FakeIsurvey()
    sv.login_error = "bad"
    c = _central(sv, now=now)
    with pytest.raises(CentralUnavailable):
        c.client()
    sv.login_error = None
    st = c.test()
    assert st["state"] == "ready" and st["last_error"] is None and sv.logins == 1


def test_not_configured_is_unavailable_and_says_so():
    c = CentralSession("", "", lambda: None, log=lambda m: None)
    assert not c.configured() and c.status()["state"] == "not_configured"
    with pytest.raises(CentralUnavailable, match="ISURVEY_CENTRAL_USERNAME"):
        c.client()


def test_report_notice_page_counts_as_session_lost_for_pending_list():
    sv = FakeIsurvey()
    c = _central(sv, min_relogin_gap_sec=0)
    api = c.client()
    sv.kick()
    assert pull_core.list_pending(api, "2026-10-01", "2026-10-08", status="") == []
    assert sv.logins == 2 and c.intact(api)


def test_own_account_client_reports_session_lose_instead_of_empty_result():
    sv = FakeIsurvey()
    api = pull_core.new_client("head", "pw")
    api.s = FakeHttp(sv)
    api.login()
    api.central = isurvey_central.DETECT_ONLY      # make_client ตั้งให้
    sv.kick()                                      # หัวหน้าเปิดหน้า ISURVEY ด้วยบัญชีเดียวกัน
    with pytest.raises(RuntimeError, match="session หลุด"):
        api._get("supervisor/listcases.php")


# ───────────────────────────────────────────── service: เลือกบัญชี
def _serve(monkeypatch, central):
    monkeypatch.setattr(pull_service, "TOKEN", "t")
    monkeypatch.setattr(pull_service, "CENTRAL", central)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), pull_service.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def post(path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode("utf-8"),
                                     headers={"X-Service-Token": "t", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))
    return srv, post


def test_service_reads_with_central_and_never_closes_with_it(monkeypatch):
    sv = FakeIsurvey()
    central = _central(sv)
    used = {}

    def fake_search(api, q):
        used["search"] = api.central is central
        return {"cases": [], "rounds": {}, "capped": False}

    def fake_make_client(username, password):
        used.setdefault("own", []).append(username)
        return object()

    monkeypatch.setattr(pull_core, "search_jobs", fake_search)
    monkeypatch.setattr(pull_core, "make_client", fake_make_client)
    monkeypatch.setattr(pull_service.isurvey_close, "close_case",
                        lambda api, claim, survey_no, **kw: {"dry_run": True})
    srv, post = _serve(monkeypatch, central)
    try:
        code, out = post("/search", {"use_central": True, "q": "2026013000001"})      # ไม่ส่งบัญชีหัวหน้าก็ได้
        assert code == 200 and out["account"] == "central" and used["search"] is True
        code, out = post("/close", {"use_central": True, "username": "head", "password": "x", "claim": "1"})
        assert code == 200 and out["account"] == "own" and used["own"] == ["head"]      # ปิดงาน = บัญชีหัวหน้าเสมอ
        code, out = post("/central/status", {})
        assert out["central"]["state"] == "ready" and "pw" not in json.dumps(out)
        code, out = post("/central/test", {})
        assert code == 200 and out["test"]["ok"] is True and out["central"]["state"] == "ready"
    finally:
        srv.shutdown()
        srv.server_close()


def test_service_falls_back_to_own_account_or_412(monkeypatch):
    sv = FakeIsurvey()
    sv.login_error = "bad"
    central = _central(sv)
    monkeypatch.setattr(pull_core, "search_jobs", lambda api, q: {"cases": [], "rounds": {}, "capped": False})
    monkeypatch.setattr(pull_core, "make_client", lambda u, p: object())
    srv, post = _serve(monkeypatch, central)
    try:
        code, out = post("/search", {"use_central": True, "username": "head", "password": "x", "q": "2026013000001"})
        assert code == 200 and out["account"] == "own"                                   # บัญชีกลางล็อกอินไม่ผ่าน → บัญชีหัวหน้า
        code, out = post("/search", {"use_central": True, "q": "2026013000001"})
        assert code == 412 and out["code"] == "no_account" and "บัญชี ISURVEY" in out["error"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_service_without_central_keeps_old_behaviour(monkeypatch):
    central = CentralSession("", "", lambda: None, log=lambda m: None)
    monkeypatch.setattr(pull_core, "search_jobs", lambda api, q: {"cases": [], "rounds": {}, "capped": False})
    monkeypatch.setattr(pull_core, "make_client", lambda u, p: object())
    srv, post = _serve(monkeypatch, central)
    try:
        code, out = post("/search", {"use_central": True, "username": "head", "password": "x", "q": "2026013000001"})
        assert code == 200 and out["account"] == "own"
        code, out = post("/search", {"use_central": True, "q": "2026013000001"})
        assert code == 412 and out["code"] == "no_account"
        code, out = post("/search", {"q": "2026013000001"})                             # backend เก่า: 400 เหมือนเดิม
        assert code == 400
    finally:
        srv.shutdown()
        srv.server_close()


def test_read_job_is_retried_when_session_broke_during_it(monkeypatch):
    sv = FakeIsurvey()
    central = _central(sv, min_relogin_gap_sec=0)
    monkeypatch.setattr(pull_service, "CENTRAL", central)
    calls = []

    def fn(api):
        calls.append(api)
        out = api._get("supervisor/get-images.php")
        if len(calls) == 1:
            sv.kick()                              # หลุดกลางงานรอบแรก → ผลรอบแรกเชื่อไม่ได้
        return 200, out

    code, out = pull_service._read(lambda verify=False: central.client(verify=verify), True, fn)
    assert len(calls) == 2 and code == 200 and out["images"]


def test_pull_rereads_before_creating_the_case_when_session_broke(monkeypatch):
    sv = FakeIsurvey()
    central = _central(sv, min_relogin_gap_sec=0)
    monkeypatch.setattr(pull_service, "CENTRAL", central)
    attempts = []

    def fake_pull_case(api, claim, survey_no, url, token, before_import=None, **kw):
        attempts.append(api)
        if len(attempts) == 1:
            sv.kick()                              # อ่านคู่กรณีระหว่าง session หลุด
        before_import()                            # รอบแรกต้องหยุดก่อนสร้างเคส
        return {"caseId": 77}, None

    monkeypatch.setattr(pull_core, "pull_case", fake_pull_case)
    result, err = pull_service._pull_central(lambda verify=False: central.client(verify=verify),
                                             "2026013000001", "SEABI-1", {"with_photos": False})
    assert err is None and result["caseId"] == 77 and len(attempts) == 2


def test_pull_tops_up_photos_when_session_broke_after_the_case_was_created(monkeypatch):
    sv = FakeIsurvey()
    central = _central(sv, min_relogin_gap_sec=0)
    monkeypatch.setattr(pull_service, "CENTRAL", central)

    def fake_pull_case(api, claim, survey_no, url, token, before_import=None, **kw):
        before_import()
        sv.kick()                                  # หลุดช่วงโหลดรูป (หลังสร้างเคส)
        return {"caseId": 78}, None

    topups = []
    monkeypatch.setattr(pull_core, "pull_case", fake_pull_case)
    monkeypatch.setattr(pull_core, "refetch_photos",
                        lambda api, claim, survey_no, case_id, url, token: topups.append(case_id) or {"added": 5})
    result, err = pull_service._pull_central(lambda verify=False: central.client(verify=verify),
                                             "2026013000001", "SEABI-1", {"with_photos": True})
    assert err is None and topups == [78] and result["photos_topup"]["added"] == 5
    assert any("เติมรูป" in w for w in result["warnings"])
