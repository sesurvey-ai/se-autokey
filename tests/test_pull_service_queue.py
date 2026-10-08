# -*- coding: utf-8 -*-
"""เทสคิวต่อบัญชี ISURVEY ของ service ดึงงาน (08/10/69)

ISURVEY ให้ 1 บัญชีล็อกอินได้ที่เดียว และทุกคำขอของ service ล็อกอินใหม่ — คำขอของบัญชีเดียวกันพร้อมกัน
(กด "ดึงเข้า" หลายแถวติดกัน / หลายแท็บ / หลายเครื่อง — เจอจริง: เคส #1517–#1520 สร้างในวินาทีเดียวกัน) เตะ session กันเองกลางทาง
→ บัญชีเดียวกันต้องรอคิวทีละงาน (ไม่สนตัวพิมพ์) · คนละบัญชียังทำพร้อมกันได้

เปิด service จริงบนพอร์ตสุ่มในเครื่อง แทนตัวล็อกอิน ISURVEY ด้วยตัวปลอมที่นับว่ามีกี่งานทำพร้อมกัน — ไม่แตะเครือข่ายภายนอก
รัน:  python -m pytest tests/test_pull_service_queue.py -q
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pull_service  # noqa: E402
from autokey import pull_core  # noqa: E402


def _run(monkeypatch, usernames):
    active, peak, guard = [0], [0], threading.Lock()

    def fake_client(username, password):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.3)                     # ทำงานกับ ISURVEY อยู่
        with guard:
            active[0] -= 1
        return object()

    monkeypatch.setattr(pull_core, "make_client", fake_client)
    monkeypatch.setattr(pull_core, "whoami", lambda api: "ทดสอบ")
    monkeypatch.setattr(pull_service, "TOKEN", "t")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), pull_service.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    results: list[dict] = []

    def post(u):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/login-test",
                                     data=json.dumps({"username": u, "password": "x"}).encode("utf-8"),
                                     headers={"X-Service-Token": "t", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            results.append(json.loads(r.read().decode("utf-8")))

    threads = [threading.Thread(target=post, args=(u,)) for u in usernames]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    srv.shutdown()
    srv.server_close()
    return peak[0], results


def test_same_account_requests_wait_their_turn(monkeypatch):
    peak, results = _run(monkeypatch, ["Head1", "head1", " HEAD1 "])
    assert len(results) == 3 and all(r["ok"] for r in results)
    assert peak == 1                       # ไม่มี 2 งานของบัญชีเดียวกันทำพร้อมกันเลย


def test_different_accounts_still_run_together(monkeypatch):
    peak, results = _run(monkeypatch, ["head1", "head2"])
    assert len(results) == 2 and all(r["ok"] for r in results)
    assert peak == 2                       # คนละบัญชีไม่ต้องรอกัน


def test_lock_is_per_account_case_insensitive():
    assert pull_service._account_lock("Head1") is pull_service._account_lock(" head1 ")
    assert pull_service._account_lock("head1") is not pull_service._account_lock("head2")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
