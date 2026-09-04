# -*- coding: utf-8 -*-
"""service ดึงงาน ISURVEY → se-survey (รันบนเซิร์ฟเวอร์ ให้ backend se-survey เรียก)

backend ส่งบัญชี ISURVEY ของหัวหน้าแต่ละคนมาต่อคำขอ (เก็บเข้ารหัสอยู่ฝั่ง se-survey) — ที่นี่ **ไม่เก็บอะไร**
ไม่มี state ข้ามคำขอ ไม่เปิด Chrome ไม่แตะ EMCS

env:
  PULL_SERVICE_TOKEN   token ที่ backend ต้องส่งใน header X-Service-Token (บังคับ)
  SESURVEY_API_URL     backend se-survey (default https://api.sesurvey.cloud)
  SESURVEY_API_TOKEN   INTEGRATION_TOKEN ของ backend (บังคับ — ใช้สร้างเคส/อัปรูป)
  PORT                 default 8790

POST (JSON) — ทุกอันต้องมี X-Service-Token:
  /login-test  {username, password}                          → {ok, name}
  /pending     {username, password, date_from?, date_to?, status?}  → {ok, cases: [...]}   (status "" = ทุกสถานะ · ไม่ส่ง = รอตรวจข้อมูล)
  /pull        {username, password, claim, survey_no, created_by?, with_photos?} → {ok, result}
GET /healthz → {ok: true}
"""
from __future__ import annotations

import hmac
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from autokey import pull_core  # noqa: E402

TOKEN = os.environ.get("PULL_SERVICE_TOKEN", "")
SESURVEY_URL = os.environ.get("SESURVEY_API_URL", "https://api.sesurvey.cloud").rstrip("/")
SESURVEY_TOKEN = os.environ.get("SESURVEY_API_TOKEN", "")
PORT = int(os.environ.get("PORT", "8790"))


def _log(msg: str) -> None:
    print(msg, flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):   # กัน log ของ http.server ที่มี query/รหัสผ่านโผล่
        _log(f"[pull] {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}")

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        got = self.headers.get("X-Service-Token", "")
        return bool(TOKEN) and hmac.compare_digest(got, TOKEN)

    def do_GET(self):
        if self.path.split("?")[0] == "/healthz":
            return self._send(200, {"ok": True, "sesurvey": SESURVEY_URL})
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._authed():
            return self._send(401, {"ok": False, "error": "token ไม่ถูกต้อง"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
        except Exception:
            return self._send(400, {"ok": False, "error": "body ต้องเป็น JSON"})
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        if not username or not password:
            return self._send(400, {"ok": False, "error": "ต้องมี username และ password ของ ISURVEY"})
        try:
            if path == "/login-test":
                api = pull_core.make_client(username, password)
                return self._send(200, {"ok": True, "name": pull_core.whoami(api)})
            if path == "/pending":
                api = pull_core.make_client(username, password)
                status = body.get("status", pull_core.ISURVEY_STATUS_PENDING)   # "" = ทุกสถานะ
                rows = pull_core.list_pending(api, str(body.get("date_from") or ""), str(body.get("date_to") or ""),
                                              status=str(status or ""))
                return self._send(200, {"ok": True, "cases": rows})
            if path == "/pull":
                if not SESURVEY_TOKEN:
                    return self._send(503, {"ok": False, "error": "service ยังไม่ได้ตั้ง SESURVEY_API_TOKEN"})
                claim = str(body.get("claim") or "").strip()
                survey_no = str(body.get("survey_no") or "").strip()
                if not claim:
                    return self._send(400, {"ok": False, "error": "ต้องมีเลขเคลม"})
                api = pull_core.make_client(username, password)
                created_by = body.get("created_by")
                result, err = pull_core.pull_case(
                    api, claim, survey_no, SESURVEY_URL, SESURVEY_TOKEN,
                    created_by=int(created_by) if created_by else None,
                    with_photos=bool(body.get("with_photos", True)))
                if err:
                    return self._send(502, {"ok": False, "error": err})
                _log(f"[pull] {username}: เคลม {claim} → เคส #{(result or {}).get('caseId')}")
                return self._send(200, {"ok": True, "result": result})
            return self._send(404, {"ok": False, "error": "not found"})
        except RuntimeError as e:          # login ไม่ผ่าน / หาเคลมไม่เจอ — ข้อความอ่านได้ ส่งกลับตรง ๆ
            msg = str(e)
            if "login" in msg and "ไม่สำเร็จ" in msg:   # ข้อความเดิมพูดถึง .env ของบอท — คนใช้เว็บไม่รู้จัก
                msg = "ล็อกอิน ISURVEY ไม่สำเร็จ — ตรวจ username/password ของบัญชี ISURVEY"
            return self._send(502, {"ok": False, "error": msg})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})


def main() -> None:
    if not TOKEN:
        print("PULL_SERVICE_TOKEN ยังไม่ได้ตั้ง — ไม่เปิด service", file=sys.stderr)
        sys.exit(2)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    _log(f"[pull] ISURVEY pull service :{PORT} → se-survey {SESURVEY_URL}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
