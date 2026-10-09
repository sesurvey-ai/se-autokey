# -*- coding: utf-8 -*-
"""service ดึงงาน ISURVEY → se-survey (รันบนเซิร์ฟเวอร์ ให้ backend se-survey เรียก)

backend ส่งบัญชี ISURVEY ของหัวหน้าแต่ละคนมาต่อคำขอ (เก็บเข้ารหัสอยู่ฝั่ง se-survey) — ที่นี่ไม่เก็บบัญชีหัวหน้า ไม่เปิด Chrome ไม่แตะ EMCS

บัญชีกลาง (08/10/69): ถ้าตั้ง ISURVEY_CENTRAL_* งานอ่าน (/pending /rounds /search /pull /photos) ที่ backend ส่ง use_central=true
ใช้ session ของบัญชีกลางที่ล็อกอินค้างไว้ (autokey/isurvey_central.py) — หน้า ISURVEY ที่หัวหน้าเปิดอยู่ไม่โดนเตะ "session lose"
บัญชีกลางใช้ไม่ได้ = ใช้บัญชีหัวหน้าที่ส่งมาด้วยแทน (ไม่มี = 412 code no_account) · /close + /login-test ใช้บัญชีหัวหน้าเสมอ
ทุกคำตอบบอก account: "central" | "own" (backend จดผลล็อกอินให้บัญชีหัวหน้าเฉพาะ "own")

env:
  PULL_SERVICE_TOKEN   token ที่ backend ต้องส่งใน header X-Service-Token (บังคับ)
  SESURVEY_API_URL     backend se-survey (default https://api.sesurvey.cloud)
  SESURVEY_API_TOKEN   INTEGRATION_TOKEN ของ backend (บังคับ — ใช้สร้างเคส/อัปรูป)
  ISURVEY_CENTRAL_USERNAME / ISURVEY_CENTRAL_PASSWORD   บัญชี ISURVEY กลาง (ไม่บังคับ · ⛔ ห้ามใช้บัญชีนี้ที่อื่น — เตะกันเอง)
  ISURVEY_CENTRAL_KEEPALIVE_SEC   เช็ค/ต่ออายุ session กลางทุกกี่วินาที (default 600)
  PORT                 default 8790

POST (JSON) — ทุกอันต้องมี X-Service-Token:
  /login-test  {username, password}                          → {ok, name}
  /pending     {username, password, date_from?, date_to?, status?}  → {ok, cases: [...]}   (status "" = ทุกสถานะ · ไม่ส่ง = รอตรวจข้อมูล)
  /pull        {username, password, claim, survey_no, created_by?, with_photos?, as_reference?} → {ok, result}
               as_reference=true = งานที่จบงานแล้วเข้ามาเป็นเคสอ้างอิง ดูอย่างเดียว (ปุ่มจากผลค้นหา 08/10/69)
  /search      {username, password, q} → {ok, cases: [...], rounds: {...}, capped}
               ค้นงานด้วยเลขเคลม/เลขรับแจ้ง/เลขเซอร์เวย์ ช่องเดียวกับหน้าตรวจงาน ISURVEY (08/10/69) · อ่านอย่างเดียว
  /rounds      {username, password, claims: [...]} → {ok, rounds: {claim: [{survey_no, round, status_name}]}}  (ครั้งที่ของทุกใบในเคลม)
  /preview     {username, password, claim, survey_no} → {ok, preview: {status_id, status_name, data, photos: [{category, group, name}]}, pid}
               หน้าต่าง "ดูอย่างเดียว" จากผลค้นหา (09/10/69): อ่านงาน+รายการรูปทุกสถานะ ไม่สร้างเคส ไม่บันทึกอะไร · url รูปเก็บไว้ที่นี่ 30 นาที
  /preview-photo {pid, i} → ไฟล์รูปลำดับ i ของหน้าต่างนั้น (image/*) · หมดอายุ = 410 · ไม่ต้องส่งบัญชี (session ผูกกับ pid)
  /close       {username, password, claim, survey_no, comment?, rates?, checklist?, dry_run?} → {ok, result}
               = กด "ยืนยันการตรวจสอบ" (ปิดงาน → จบงาน) แทนหัวหน้า หลังอนุมัติบนเว็บ (08/09/69) · dry_run ไม่ส่ง = True
  /central/status {} → {ok, central: {state, username, name, logged_in_at, last_ok_at, last_error, paused_until, logins}}
  /central/test   {} → ล้างการพัก แล้วเช็ค/ล็อกอินบัญชีกลางเดี๋ยวนี้ (ปุ่มแอดมิน) → {ok, test: {ok, error?}, central}
  (ทุกเส้นข้างบนรับ use_central: true — username/password ของหัวหน้ากลายเป็นตัวสำรอง ไม่ส่งก็ได้)
GET /healthz → {ok: true}
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from autokey import isurvey_central, isurvey_close, pull_core  # noqa: E402
from autokey import __version__ as BOT_VERSION  # noqa: E402

TOKEN = os.environ.get("PULL_SERVICE_TOKEN", "")
SESURVEY_URL = os.environ.get("SESURVEY_API_URL", "https://api.sesurvey.cloud").rstrip("/")
SESURVEY_TOKEN = os.environ.get("SESURVEY_API_TOKEN", "")
PORT = int(os.environ.get("PORT", "8790"))
# บัญชี ISURVEY กลางของระบบ (user เปิดให้ 08/10/69) — ไม่ตั้ง = ใช้บัญชีหัวหน้าทุกงานแบบเดิม
CENTRAL_USERNAME = os.environ.get("ISURVEY_CENTRAL_USERNAME", "").strip()
_CENTRAL_PASSWORD = os.environ.get("ISURVEY_CENTRAL_PASSWORD", "")
CENTRAL_KEEPALIVE_SEC = int(os.environ.get("ISURVEY_CENTRAL_KEEPALIVE_SEC", "600") or 600)
#: เส้นที่อ่าน ISURVEY อย่างเดียว ใช้บัญชีกลางได้ (backend ส่ง use_central) · /close (ปิดงาน — ISURVEY ต้องลงชื่อหัวหน้าที่อนุมัติ)
#: กับ /login-test (ทดสอบบัญชีของหัวหน้าเอง) ใช้บัญชีหัวหน้าเสมอ
CENTRAL_PATHS = {"/pending", "/rounds", "/search", "/pull", "/photos", "/preview"}

# ── หน้าต่าง "ดูอย่างเดียว" (09/10/69): url รูปของงานที่เปิดดูอยู่ที่นี่ — backend/หน้าเว็บได้แค่ pid + ลำดับรูป (กันขอ url อื่นผ่าน service)
#    เก็บ client ที่อ่านงานไว้ด้วย (บัญชีกลาง = session กลางตัวเดียวกัน · บัญชีหัวหน้า = session ที่ล็อกอินไว้แล้ว ไม่ล็อกอินซ้ำต่อรูป)
PREVIEW_TTL_SEC = 1800
_PREVIEWS: dict[str, dict] = {}
_PREVIEWS_GUARD = threading.Lock()


def _preview_put(api, photos: list) -> str:
    pid = secrets.token_urlsafe(16)
    now = time.time()
    with _PREVIEWS_GUARD:
        for k in [k for k, v in _PREVIEWS.items() if v["expires"] < now]:
            del _PREVIEWS[k]
        _PREVIEWS[pid] = {"api": api, "urls": [p["url"] for p in photos], "expires": now + PREVIEW_TTL_SEC}
    return pid


def _preview_photo(pid: str, i) -> tuple[int, dict | None, bytes | None, str]:
    """รูปลำดับ i ของหน้าต่าง pid → (code, error-json|None, bytes|None, content-type)"""
    try:
        i = int(i)
    except (TypeError, ValueError):
        return 400, {"ok": False, "error": "ต้องมีลำดับรูป"}, None, ""
    with _PREVIEWS_GUARD:
        pv = _PREVIEWS.get(str(pid or ""))
    if not pv or pv["expires"] < time.time():
        return 410, {"ok": False, "error": "หน้าต่างดูอย่างเดียวหมดอายุ — ปิดแล้วเปิดใหม่"}, None, ""
    if not 0 <= i < len(pv["urls"]):
        return 404, {"ok": False, "error": "ไม่มีรูปลำดับนี้"}, None, ""
    api = pv["api"]
    try:
        r = api.s.get(f"{api._host}/{pv['urls'][i].lstrip('/')}", timeout=60)
    except Exception as e:
        return 502, {"ok": False, "error": f"โหลดรูปจาก ISURVEY ไม่ได้: {type(e).__name__}"}, None, ""
    ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if r.status_code != 200 or not r.content or not ctype.startswith("image/"):
        # session หลุด = ISURVEY ตอบหน้า HTML แทนรูป — ไม่ส่งต่อ
        return 502, {"ok": False, "error": f"ISURVEY ไม่ได้ส่งรูปมา ({r.status_code}) — ปิดหน้าต่างแล้วเปิดใหม่"}, None, ""
    return 200, None, r.content, ctype


def _log(msg: str) -> None:
    print(msg, flush=True)


# ── คิวต่อบัญชี ISURVEY (08/10/69) ──
# ISURVEY ให้ 1 บัญชีล็อกอินได้ที่เดียว และทุกคำขอที่นี่ล็อกอินใหม่ → 2 คำขอของบัญชีเดียวกันพร้อมกัน (กด "ดึงเข้า" หลายแถวติดกัน /
# หลายแท็บ / หลายเครื่อง — เจอจริง: เคส #1517–#1520 ถูกสร้างในวินาทีเดียวกัน) จะเตะ session กันเองกลางทาง เสี่ยงรูปไม่ครบ
# → ให้คำขอของบัญชีเดียวกันรอคิวทีละงาน · คนละบัญชียังทำพร้อมกันได้ตามเดิม
_ACCOUNT_LOCKS: dict[str, threading.Lock] = {}
_ACCOUNT_LOCKS_GUARD = threading.Lock()
#: รอคิวบัญชีเดียวกันได้นานสุด (วินาที) — ต่ำกว่าที่ backend รอเส้นนั้น ให้ตอบข้อความชัด ๆ ก่อนฝั่งนั้นตัดเอง
#: (backend: /pull /photos 300 · /close 240 · /pending /rounds /search 150 · /login-test 30)
ACCOUNT_WAIT_SEC = 240
ACCOUNT_WAIT_BY_PATH = {"/pending": 100, "/rounds": 100, "/search": 100, "/preview": 100, "/close": 200, "/login-test": 20}


CENTRAL = isurvey_central.CentralSession(
    CENTRAL_USERNAME, _CENTRAL_PASSWORD, lambda: pull_core.new_client(CENTRAL_USERNAME, _CENTRAL_PASSWORD),
    keepalive_sec=CENTRAL_KEEPALIVE_SEC, log=_log)


def _account_lock(username: str) -> threading.Lock:
    key = username.strip().lower()
    with _ACCOUNT_LOCKS_GUARD:
        lk = _ACCOUNT_LOCKS.get(key)
        if lk is None:
            lk = _ACCOUNT_LOCKS[key] = threading.Lock()
        return lk


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):   # กัน log ของ http.server ที่มี query/รหัสผ่านโผล่
        _log(f"[pull] {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}")

    def _send(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # backend ตัดสายไปก่อน (รอเกินเวลาของมัน) — ไม่ต้องพ่น traceback (08/10/69)
            _log(f"[pull] {self.path.split('?')[0]} ตอบไม่ทัน — backend ตัดสายไปก่อน ({code})")

    def _send_bytes(self, data: bytes, ctype: str) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            _log("[pull] /preview-photo ตอบไม่ทัน — backend ตัดสายไปก่อน")

    def _authed(self) -> bool:
        got = self.headers.get("X-Service-Token", "")
        return bool(TOKEN) and hmac.compare_digest(got, TOKEN)

    def do_GET(self):
        if self.path.split("?")[0] == "/healthz":
            return self._send(200, {"ok": True, "sesurvey": SESURVEY_URL, "version": BOT_VERSION})
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
        if path == "/central/status":
            return self._send(200, {"ok": True, "central": CENTRAL.status()})
        if path == "/central/test":
            # ok ของคำตอบ = service ทำงานได้ · ผลทดสอบจริงอยู่ใน test (ไม่ผ่านก็ยังคืนสถานะให้หน้าเว็บโชว์)
            try:
                return self._send(200, {"ok": True, "test": {"ok": True}, "central": CENTRAL.test()})
            except isurvey_central.CentralUnavailable as e:
                return self._send(200, {"ok": True, "test": {"ok": False, "error": str(e)}, "central": CENTRAL.status()})
        if path == "/preview-photo":
            # รูปของหน้าต่างดูอย่างเดียว — session ผูกกับ pid ตั้งแต่ /preview (ไม่ต้องใช้บัญชี ไม่เข้าคิวบัญชี)
            code, err, data, ctype = _preview_photo(body.get("pid"), body.get("i"))
            return self._send(code, err) if err else self._send_bytes(data, ctype)
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        # งานอ่าน ISURVEY → บัญชีกลาง (ไม่เตะ session หัวหน้า · 08/10/69) · ใช้ไม่ได้ = ถอยไปบัญชีหัวหน้าที่ส่งมาด้วย (ถ้ามี)
        if body.get("use_central") and path in CENTRAL_PATHS and CENTRAL.configured():
            try:
                code, obj = self._run(path, body, central=True)
                return self._send(code, {**obj, "account": "central"})
            except isurvey_central.CentralUnavailable as e:
                if not getattr(e, "fallback_ok", True):
                    # ISURVEY ช้า/เน็ตล่ม — ถอยไปบัญชีหัวหน้าก็ช้าเหมือนกัน แถมเตะหน้า ISURVEY ของหัวหน้า → ให้ลองใหม่แทน (08/10/69)
                    return self._send(503, {"ok": False, "account": "central",
                                            "error": f"{e} — ลองใหม่อีกครั้งในอีกสักครู่"})
                if not username or not password:
                    return self._send(412, {"ok": False, "code": "no_account", "account": "central",
                                            "error": f"{e} — และยังไม่ได้ตั้งบัญชี ISURVEY ของคุณไว้สำรอง (เมนู \"บัญชี ISURVEY\")"})
                _log(f"[central] ใช้ไม่ได้ — {path} ใช้บัญชี {username} แทน: {e}")
        if not username or not password:
            if body.get("use_central"):
                return self._send(412, {"ok": False, "code": "no_account",
                                        "error": "ยังไม่ได้ตั้งบัญชี ISURVEY — ไปที่เมนู \"บัญชี ISURVEY\" ก่อน (บัญชีกลางของระบบยังไม่ได้ตั้ง)"})
            return self._send(400, {"ok": False, "error": "ต้องมี username และ password ของ ISURVEY"})
        lock = _account_lock(username)
        wait = ACCOUNT_WAIT_BY_PATH.get(path, ACCOUNT_WAIT_SEC)
        if not lock.acquire(timeout=wait):
            _log(f"[queue] {username}: รอคิวบัญชีเกิน {wait} วิ — {path}")
            return self._send(503, {"ok": False, "account": "own",
                                    "error": "บัญชี ISURVEY นี้กำลังทำงานอื่นค้างอยู่นาน — รอสักครู่แล้วลองใหม่"})
        try:
            code, obj = self._run(path, body, central=False, username=username, password=password)
            return self._send(code, {**obj, "account": "own"})
        finally:
            lock.release()

    def _run(self, path: str, body: dict, central: bool, username: str = "", password: str = "") -> tuple[int, dict]:
        """งานจริงของแต่ละเส้น → (HTTP code, JSON)
        central=False: บัญชีหัวหน้า ล็อกอินใหม่ทุกคำขอ (เรียกตอนถือคิวของบัญชีนั้นอยู่ — _account_lock)
        central=True: ยืม session บัญชีกลาง · session ไม่ต่อเนื่องระหว่างงาน (ISURVEY บางหน้าตอบว่างเงียบ ๆ ตอนหลุด) = อ่านใหม่"""
        if central:
            def make(verify: bool = False):
                return CENTRAL.client(verify=verify)
        else:
            def make(verify: bool = False):
                return pull_core.make_client(username, password)
        who = "บัญชีกลาง" if central else username
        try:
            if path == "/login-test":
                api = make()
                return 200, {"ok": True, "name": pull_core.whoami(api)}
            if path == "/pending":
                status = str(body.get("status", pull_core.ISURVEY_STATUS_PENDING) or "")   # "" = ทุกสถานะ
                return _read(make, central, lambda api: (200, {"ok": True, "cases": pull_core.list_pending(
                    api, str(body.get("date_from") or ""), str(body.get("date_to") or ""), status=status)}))
            if path == "/rounds":
                # "ครั้งที่" ของงานบนหน้างานรอตรวจ (22/09/69) — อ่าน ISURVEY อย่างเดียว 1 คำขอ/เคลม สูงสุด 200 เคลม/ครั้ง
                claims = body.get("claims") if isinstance(body.get("claims"), list) else []
                return _read(make, central, lambda api: (200, {"ok": True, "rounds": pull_core.claim_rounds(
                    api, [str(c) for c in claims[:200]])}))
            if path == "/search":
                # ค้นงานบน ISURVEY จากเว็บ se-survey (user สั่ง 08/10/69) — ช่องเดียวรับเลขเคลม/เลขรับแจ้ง/เลขเซอร์เวย์ (ค้นแบบขึ้นต้นได้)
                # อ่านอย่างเดียว: 1 คำขอ + ถามทุกใบของเคลมเพิ่มไว้หา "ครั้งที่" (สูงสุด 3 เคลม)
                q = str(body.get("q") or "").strip()
                if len(q) < 6:
                    return 400, {"ok": False, "error": "พิมพ์เลขเคลม / เลขรับแจ้ง / เลขเซอร์เวย์ อย่างน้อย 6 ตัว"}
                code, out = _read(make, central, lambda api: (200, {"ok": True, **pull_core.search_jobs(api, q)}))
                _log(f"[search] {who}: {len(out.get('cases') or [])} แถว{' (ครบ 50 — ตัด)' if out.get('capped') else ''}")
                return code, out
            if path == "/preview":
                # หน้าต่าง "ดูอย่างเดียว" จากผลค้นหา (user สั่ง 09/10/69): ข้อมูล+รายการรูปจาก ISURVEY ทุกสถานะ — ไม่สร้างเคส ไม่บันทึกอะไร
                # หน้ารูป/คู่กรณีตอบ "ว่าง" เงียบ ๆ ตอน session หลุด → เช็คความต่อเนื่องแบบงานดึงรูป (check_intact)
                claim = str(body.get("claim") or "").strip()
                if not claim:
                    return 400, {"ok": False, "error": "ต้องมีเลขเคลม"}
                survey_no = str(body.get("survey_no") or "").strip()
                used = {}

                def read_preview(api):
                    used["api"] = api
                    return 200, pull_core.preview_case(api, claim, survey_no)

                _, out = _read(make, central, read_preview, check_intact=True)
                photos = out.pop("photos", [])
                pid = _preview_put(used["api"], photos)
                out["photos"] = [{"category": p["category"], "group": p["group"], "name": p["name"]} for p in photos]
                _log(f"[preview] {who}: เคลม {claim} {survey_no} สถานะ {out.get('status_name')} · รูป {len(photos)}")
                return 200, {"ok": True, "preview": out, "pid": pid}
            if path == "/pull":
                if not SESURVEY_TOKEN:
                    return 503, {"ok": False, "error": "service ยังไม่ได้ตั้ง SESURVEY_API_TOKEN"}
                claim = str(body.get("claim") or "").strip()
                survey_no = str(body.get("survey_no") or "").strip()
                if not claim:
                    return 400, {"ok": False, "error": "ต้องมีเลขเคลม"}
                created_by = body.get("created_by")
                kw = dict(created_by=int(created_by) if created_by else None,
                          with_photos=bool(body.get("with_photos", True)),
                          as_reference=bool(body.get("as_reference", False)))
                result, err = (_pull_central(make, claim, survey_no, kw) if central else
                               pull_core.pull_case(make(), claim, survey_no, SESURVEY_URL, SESURVEY_TOKEN, **kw))
                if err:
                    return 502, {"ok": False, "error": err}
                _log(f"[pull] {who}: เคลม {claim} → เคส #{(result or {}).get('caseId')}"
                     + (" (อ้างอิง ดูอย่างเดียว)" if body.get("as_reference") else ""))
                return 200, {"ok": True, "result": result}
            if path == "/photos":
                # "ดึงรูปเพิ่มจาก ISURVEY" ให้เคสเดิม (22/09/69) · backend เป็นคนตัดสินว่าเคสยังรับรูปได้ไหม (ยังไม่เข้า EMCS)
                if not SESURVEY_TOKEN:
                    return 503, {"ok": False, "error": "service ยังไม่ได้ตั้ง SESURVEY_API_TOKEN"}
                claim = str(body.get("claim") or "").strip()
                case_id = body.get("case_id")
                if not claim or not case_id:
                    return 400, {"ok": False, "error": "ต้องมีเลขเคลมและเลขเคส"}
                survey_no = str(body.get("survey_no") or "").strip()
                # เติมรูปซ้ำได้ไม่ซ้อน (backend เทียบเนื้อไฟล์) → session ไม่ต่อเนื่อง = ทำอีกรอบเหมือนงานอ่าน
                code, result = _read(make, central, lambda api: (200, pull_core.refetch_photos(
                    api, claim, survey_no, int(case_id), SESURVEY_URL, SESURVEY_TOKEN)), check_intact=True)
                if result.get("error"):
                    return 502, {"ok": False, "error": str(result["error"])}
                _log(f"[photos] {who}: เคลม {claim} → เคส #{case_id} +{result.get('added')} ข้าม {result.get('skipped')} (ISURVEY มี {result.get('isurvey_photo_listed')})")
                return 200, {"ok": True, "result": result}
            if path == "/close":
                # เขียนกลับ ISURVEY: ความเห็นหัวหน้า + ตารางค่าสำรวจ + "ปิดการตรวจสอบ" — ด้วยบัญชีของหัวหน้าที่อนุมัติเสมอ (ไม่ใช่บัญชีกลาง)
                # dry_run เป็นค่าเริ่มต้น (ไม่ส่ง = ไม่ยิง) — ฝั่ง backend เป็นคนตัดสินว่าเปิดยิงจริงหรือยัง
                claim = str(body.get("claim") or "").strip()
                if not claim:
                    return 400, {"ok": False, "error": "ต้องมีเลขเคลม"}
                api = make()
                result = isurvey_close.close_case(
                    api, claim, str(body.get("survey_no") or "").strip(),
                    comment=body.get("comment"), rates=body.get("rates"),
                    dry_run=bool(body.get("dry_run", True)), checklist=body.get("checklist"))
                _log(f"[close] {who}: เคลม {claim} → {'dry-run' if result.get('dry_run') else 'ปิดงานแล้ว'}")
                return 200, {"ok": True, "result": result}
            return 404, {"ok": False, "error": "not found"}
        except isurvey_central.CentralUnavailable:
            raise                          # do_POST ถอยไปบัญชีหัวหน้า (ถ้ามี)
        except RuntimeError as e:          # login ไม่ผ่าน / หาเคลมไม่เจอ / session หลุด — ข้อความอ่านได้ ส่งกลับตรง ๆ
            msg = str(e)
            if "login" in msg and "ไม่สำเร็จ" in msg:   # ข้อความเดิมพูดถึง .env ของบอท — คนใช้เว็บไม่รู้จัก
                msg = "ล็อกอิน ISURVEY ไม่สำเร็จ — ตรวจ username/password ของบัญชี ISURVEY"
            return 502, {"ok": False, "error": msg}
        except Exception as e:
            traceback.print_exc()
            return 500, {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _read(make, central: bool, fn, check_intact: bool = False) -> tuple[int, dict]:
    """งาน fn(api) → (code, JSON)
    check_intact=True (ดึงรูป — หน้ารูปตอบ "ว่าง" เงียบ ๆ ตอน session หลุด): บัญชีกลาง session ไม่ต่อเนื่องระหว่างงาน = ทำใหม่ 1 รอบ
    งานอ่านอย่างเดียว (รายการ/ครั้งที่/ค้น) ใช้แต่หน้าที่ฟ้อง "Session lose!" ได้ (จัดการใน _get_url แล้ว) — ไม่ต้องเช็คซ้ำ
    (08/10/69: เช็คซ้ำทุกงานตอนคนใช้พร้อมกันหลายคน = getUserData ต่อคิวยาวจนหมดเวลา → นึกว่าหลุด วนล็อกอินใหม่)"""
    if not central or not check_intact:
        return fn(make())
    for attempt in (1, 2):
        api = make(verify=attempt > 1)
        try:
            out = fn(api)
        except isurvey_central.CentralUnavailable:
            raise
        except Exception:
            if attempt == 1 and not CENTRAL.intact(api):
                _log("[central] งานพังระหว่าง session หลุด — อ่านใหม่อีกรอบ")
                continue
            raise
        if CENTRAL.intact(api):
            return out
        _log("[central] session ไม่ต่อเนื่องระหว่างอ่าน (ISURVEY อาจตอบว่างเงียบ ๆ) — อ่านใหม่อีกรอบ")
    raise RuntimeError("ISURVEY session หลุดระหว่างอ่าน 2 ครั้งติด — ลองใหม่อีกครั้งในอีกสักครู่")


class _SessionBroke(Exception):
    """session บัญชีกลางไม่ต่อเนื่องระหว่างอ่านงาน (ก่อนสร้างเคส) — อ่านใหม่ทั้งงาน"""


def _pull_central(make, claim: str, survey_no: str, kw: dict) -> tuple[dict | None, str | None]:
    """ดึง 1 งานด้วยบัญชีกลาง — เช็คก่อนสร้างเคสว่าอ่านด้วย session เดียวตลอด (หลุดกลางทาง ISURVEY ตอบคู่กรณี/ชิ้นส่วนว่างเงียบ ๆ)
    ไม่ต่อเนื่อง = อ่านใหม่ทั้งงานก่อนมีเคส (เคสอ้างอิงที่สร้างไปแล้วรอบแรก รอบสองเจอ 409 = ข้าม) · หลุดหลังสร้างเคส (ช่วงโหลดรูป)
    = เติมรูปอีกรอบด้วย session ใหม่ (เติมซ้ำไม่ซ้อน)"""
    for attempt in (1, 2):
        api = make(verify=attempt > 1)

        def before_import(api=api):
            if not CENTRAL.intact(api):
                raise _SessionBroke()

        try:
            result, err = pull_core.pull_case(api, claim, survey_no, SESURVEY_URL, SESURVEY_TOKEN,
                                              before_import=before_import, **kw)
        except _SessionBroke:
            _log(f"[central] session ไม่ต่อเนื่องระหว่างอ่านเคลม {claim} — อ่านใหม่ก่อนสร้างเคส")
            continue
        if err and attempt == 1 and not CENTRAL.intact(api):
            # pull_case คืน err เฉพาะตอนยังไม่ได้สร้างเคสใบหลัก → อ่านใหม่ได้ปลอดภัย (เช่น ISURVEY ค้างเพราะ session หลุด)
            _log(f"[central] ดึงเคลม {claim} พังระหว่าง session หลุด — อ่านใหม่อีกรอบ")
            continue
        if err or not result:
            return result, err
        if kw.get("with_photos") and result.get("caseId") and not CENTRAL.intact(api):
            _log(f"[central] session ไม่ต่อเนื่องระหว่างโหลดรูปเคลม {claim} — เติมรูปอีกรอบ")
            extra = pull_core.refetch_photos(make(verify=True), claim, survey_no, result["caseId"], SESURVEY_URL, SESURVEY_TOKEN)
            result["photos_topup"] = extra
            result.setdefault("warnings", []).append(
                "ISURVEY session หลุดระหว่างโหลดรูป — เติมรูปให้อีกรอบแล้ว"
                + (f" (+{extra.get('added')})" if not extra.get("error") else f" แต่ไม่สำเร็จ: {extra.get('error')}"))
        return result, None
    return None, "ISURVEY session หลุดระหว่างอ่านงาน 2 ครั้งติด — ยังไม่ได้สร้างเคส ลองใหม่อีกครั้งในอีกสักครู่"


def main() -> None:
    if not TOKEN:
        print("PULL_SERVICE_TOKEN ยังไม่ได้ตั้ง — ไม่เปิด service", file=sys.stderr)
        sys.exit(2)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    _log(f"[pull] ISURVEY pull service :{PORT} → se-survey {SESURVEY_URL}")
    if CENTRAL.configured():
        _log(f"[central] บัญชี ISURVEY กลาง {CENTRAL_USERNAME} — keep-alive ทุก {CENTRAL_KEEPALIVE_SEC} วิ")
        CENTRAL.start_keepalive()
    else:
        _log("[central] ไม่ได้ตั้งบัญชี ISURVEY กลาง — ทุกงานใช้บัญชีของหัวหน้าที่กด (แบบเดิม)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
