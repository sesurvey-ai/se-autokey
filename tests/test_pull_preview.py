# -*- coding: utf-8 -*-
"""หน้าต่าง "ดูอย่างเดียว" จากผลค้นหาบนเว็บ se-survey (user สั่ง 09/10/69)

งานสถานะอื่นที่ดึงเข้าไม่ได้ (ช่างยังทำงานอยู่ / ยกเลิก) เปิดดูข้อมูล+รูปจาก ISURVEY ตรง ๆ — ไม่สร้างเคส ไม่บันทึกอะไร
- /preview: ข้อมูลชุดเดียวกับ build_case + รายการรูป (กันซ้ำแบบ download_images) · url รูปไม่ออกจาก service (ได้ pid + ลำดับ)
- /preview-photo {pid, i}: รูปลำดับ i ด้วย session ที่อ่านงาน · หน้า HTML (session หลุด) ไม่ส่งต่อ · หมดอายุ 410 · ลำดับผิด 404

เปิด service จริงบนพอร์ตสุ่มในเครื่อง + ISURVEY ปลอม — ไม่แตะเครือข่ายภายนอก · รัน:  python -m pytest tests/test_pull_preview.py -q
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
from autokey.isurvey_api import ISurveyAPI  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0fakejpeg"
IMAGES = {   # หมวด t → รายการรูป (มีซ้ำข้ามแท็บ + คู่กรณี 2 คันชื่อไฟล์ชนกัน)
    1: [{"name": "_1_.jpg", "url": "/files/c9/PICTURES/OTHERS/_1_.jpg"}],
    3: [{"name": "a.jpg", "url": "/files/c9/PICTURES/INS/a.jpg"}, {"name": "b.jpg", "url": "files/c9/PICTURES/INS/b.jpg?x=1"}],
    4: [{"name": "_1_.jpg", "url": "/files/c9/PICTURES/TP_VEH//tp_car20260921180135/_1_.jpg"},
        {"name": "_1_.jpg", "url": "/files/c9/PICTURES/TP_VEH/1791202382/_1_.jpg"},
        {"name": "a.jpg", "url": "/files/c9/PICTURES/INS/a.jpg"}],          # ซ้ำกับแท็บ 3 — ต้องนับครั้งเดียว
    5: [{"name": "", "url": "/files/c9/PICTURES/INS/x.jpg"}],               # ไม่มีชื่อ = ข้าม
}


class _Resp:
    def __init__(self, status, ctype, content):
        self.status_code, self.headers, self.content = status, {"Content-Type": ctype}, content


class _Session:
    def __init__(self):
        self.got: list[str] = []
        self.html = False

    def get(self, url, timeout=0):
        self.got.append(url)
        if self.html:
            return _Resp(200, "text/html; charset=utf-8", b"<html>Session lose!</html>")
        return _Resp(200, "image/jpeg", JPEG)


class FakeAPI:
    _host = "https://isurvey.example"
    _img_category = staticmethod(ISurveyAPI._img_category)
    _img_group = staticmethod(ISurveyAPI._img_group)

    def __init__(self):
        self.s = _Session()
        self.saved = []          # ห้ามมีอะไรถูกบันทึก

    def find_case(self, claim, invoice=""):
        assert claim == "2026013199999" and invoice == "SEABI-110261000777"
        return {"caseID": "c9", "sttcase_ID": "30", "claim_no": claim}

    def master(self, name, key, val):
        return {"30": "รับงาน", "40": "รอตรวจข้อมูล", "100": "จบงาน"}

    def get_images_list(self, case_id, t):
        assert case_id == "c9"
        return [dict(x) for x in IMAGES.get(t, [])]


def test_list_photos_dedup_and_groups():
    out = pull_core.list_photos(FakeAPI(), "c9")
    assert [(p["category"], p["group"], p["name"]) for p in out] == [
        ("OTHERS", "", "_1_.jpg"), ("INS", "", "a.jpg"), ("INS", "", "b.jpg"),
        ("TP_VEH", "tp_car20260921180135", "_1_.jpg"), ("TP_VEH", "1791202382", "_1_.jpg")]


def test_preview_case_any_status_no_save(monkeypatch):
    monkeypatch.setattr(pull_core, "build_case", lambda api, cid, case: {"report": {"claim_no": case["claim_no"]}, "caseFields": {}})
    monkeypatch.setattr(pull_core, "sesurvey_post", lambda *a, **k: pytest.fail("preview ต้องไม่สร้างเคส"))
    out = pull_core.preview_case(FakeAPI(), "2026013199999", "SEABI-110261000777")
    assert out["status_id"] == "30" and out["status_name"] == "รับงาน"           # สถานะที่ "ดึงเข้า" ไม่ได้ ก็ดูได้
    assert out["data"]["report"]["claim_no"] == "2026013199999" and len(out["photos"]) == 5


@pytest.fixture()
def service(monkeypatch):
    api = FakeAPI()
    monkeypatch.setattr(pull_core, "make_client", lambda u, p: api)
    monkeypatch.setattr(pull_core, "build_case", lambda a, cid, case: {"report": {"claim_no": case["claim_no"]}})
    monkeypatch.setattr(pull_service, "TOKEN", "t")
    pull_service._PREVIEWS.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), pull_service.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def post(path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                     data=b"" if body is None else json.dumps(body).encode("utf-8"),
                                     headers={"X-Service-Token": "t", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()

    yield api, post
    srv.shutdown()


def test_preview_route_hides_urls_and_serves_photos(service):
    api, post = service
    code, ctype, body = post("/preview", {"username": "u", "password": "p", "claim": "2026013199999",
                                          "survey_no": "SEABI-110261000777"})
    assert code == 200
    j = json.loads(body)
    assert j["ok"] and j["account"] == "own" and j["preview"]["status_name"] == "รับงาน"
    assert j["pid"] and len(j["pid"]) >= 16
    assert "url" not in json.dumps(j["preview"]["photos"]) and "/files/" not in body.decode("utf-8")   # url ไม่ออกจาก service
    assert [p["category"] for p in j["preview"]["photos"]] == ["OTHERS", "INS", "INS", "TP_VEH", "TP_VEH"]

    code, ctype, body = post("/preview-photo", {"pid": j["pid"], "i": 3})
    assert code == 200 and ctype == "image/jpeg" and body == JPEG
    assert api.s.got[-1] == "https://isurvey.example/files/c9/PICTURES/TP_VEH//tp_car20260921180135/_1_.jpg"

    assert post("/preview-photo", {"pid": j["pid"], "i": 5})[0] == 404
    assert post("/preview-photo", {"pid": j["pid"], "i": "x"})[0] == 400
    assert post("/preview-photo", {"pid": "nope", "i": 0})[0] == 410
    api.s.html = True                                                   # session หลุด → หน้า HTML ไม่ใช่รูป
    code, _, body = post("/preview-photo", {"pid": j["pid"], "i": 0})
    assert code == 502 and "ISURVEY ไม่ได้ส่งรูปมา" in json.loads(body)["error"]


def test_preview_expires(service, monkeypatch):
    _, post = service
    j = json.loads(post("/preview", {"username": "u", "password": "p", "claim": "2026013199999",
                                     "survey_no": "SEABI-110261000777"})[2])
    pull_service._PREVIEWS[j["pid"]]["expires"] = 0
    assert post("/preview-photo", {"pid": j["pid"], "i": 0})[0] == 410


def test_preview_photo_needs_token(service, monkeypatch):
    _, post = service
    j = json.loads(post("/preview", {"username": "u", "password": "p", "claim": "2026013199999",
                                     "survey_no": "SEABI-110261000777"})[2])
    monkeypatch.setattr(pull_service, "TOKEN", "other")                 # token ไม่ตรง = 401 เหมือนเส้นอื่น
    # ส่ง body ว่าง — service ตอบ 401 ก่อนอ่าน body (ส่ง body ค้างไว้ = connection reset แบบสุ่มในเทส)
    assert post("/preview-photo", None)[0] == 401
    assert "/preview" in pull_service.CENTRAL_PATHS and "/preview-photo" not in pull_service.CENTRAL_PATHS
