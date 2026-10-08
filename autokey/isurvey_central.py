# -*- coding: utf-8 -*-
"""บัญชี ISURVEY กลางของระบบ — service ดึงงานล็อกอินครั้งเดียวแล้วใช้ session เดิมต่อไป (user เปิดบัญชีให้ 08/10/69)

ทำไม: ISURVEY ให้ 1 บัญชีล็อกอินได้ที่เดียว — เดิมทุกคำขอ (ค้น/โหลดรายการ/ครั้งที่/ดึง/ดึงรูป) ล็อกอินด้วยบัญชีหัวหน้าใหม่
หน้า ISURVEY ที่หัวหน้าเปิดค้างไว้จึงขึ้น "กรุณา login เข้าสู่ระบบอีกครั้ง : session lose!" · บัญชีกลางไม่มีคนใช้ ล็อกอินค้างไว้ได้
(ปิดงานหลังอนุมัติยังใช้บัญชีหัวหน้า — ISURVEY ลงชื่อผู้ตรวจถูกคน · user เลือก 08/10/69)

กติกา
 - ล็อกอินทีละครั้ง (lock) · ทุกงานใช้ requests.Session เดียวกัน (cookie เดียว) ทำพร้อมกันได้ · แต่ละงานได้ ISurveyAPI ของตัวเอง
   (⛔ ห้ามแชร์ instance ข้ามงาน — last_case_id/ตาราง master เป็นของงานนั้น ใช้ร่วมกันแล้วรูปข้ามเคสได้)
 - keep-alive ทุก keepalive_sec (getUserData) · ก่อนเริ่มงานเช็คซ้ำถ้าเช็คล่าสุดเก่ากว่า verify_ttl_sec · หลุด = ล็อกอินใหม่เอง
 - คำขอเจอ "Session lose!" → recover(): ล็อกอินใหม่ครั้งเดียว (งานอื่นที่เจอพร้อมกันรอ lock แล้วใช้ session ใหม่ — generation)
 - หลุดซ้ำภายใน min_relogin_gap_sec หลังล็อกอิน = มีคนใช้บัญชีกลางที่อื่น → ไม่แย่งกลับ (จะเตะกันไปมา) พักไว้ ใช้บัญชีหัวหน้าแทน
 - ล็อกอินไม่ผ่าน = พัก fail_backoff_sec (กันบัญชีโดนล็อกเพราะลองรหัสผิดถี่ ๆ) · เน็ต/ISURVEY ล่ม = พักสั้น net_backoff_sec
 - ⚠️ session หลุด ISURVEY บางหน้า (รูป/คู่กรณี/ชิ้นส่วน) ตอบ "ว่าง" เงียบ ๆ ไม่ error → intact() เช็คหลังงาน ผู้เรียกอ่านใหม่
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

NOT_CONFIGURED = "ยังไม่ได้ตั้งบัญชี ISURVEY กลาง (ISURVEY_CENTRAL_USERNAME / ISURVEY_CENTRAL_PASSWORD ของ service ดึงงาน)"


class CentralUnavailable(RuntimeError):
    """บัญชีกลางใช้ไม่ได้ตอนนี้ (ไม่ได้ตั้ง / ล็อกอินไม่ผ่าน / พักอยู่ / มีคนใช้ที่อื่น) — ผู้เรียกถอยไปบัญชีหัวหน้า"""


class _DetectOnly:
    """client บัญชีหัวหน้า (ล็อกอินใหม่ทุกคำขอ): เจอ "Session lose!" กลางงาน = บอกตรง ๆ ไม่ล็อกอินแย่งกลับ ไม่ปล่อยผลว่างเงียบ ๆ"""
    generation = 0

    def recover(self, gen_seen: int, where: str = "") -> None:
        raise RuntimeError("ISURVEY แจ้ง session หลุดระหว่างทำงาน — มีการล็อกอินบัญชี ISURVEY นี้ที่อื่น "
                           "(เช่นเปิดหน้า ISURVEY ด้วยบัญชีเดียวกัน) ลองใหม่อีกครั้ง")


DETECT_ONLY = _DetectOnly()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class CentralSession:
    """session ISURVEY ของบัญชีกลาง 1 อัน ใช้ร่วมทั้ง service — factory() = ISurveyAPI ของบัญชีกลางที่ยังไม่ล็อกอิน"""

    def __init__(self, username: str, password: str, factory, *, keepalive_sec: int = 600, verify_ttl_sec: int = 90,
                 min_relogin_gap_sec: int = 60, fail_backoff_sec: int = 900, net_backoff_sec: int = 120,
                 contended_backoff_sec: int = 300, clock=time.monotonic, log=print):
        self.username = str(username or "").strip()
        self._password = str(password or "")
        self._factory = factory
        self.keepalive_sec = keepalive_sec
        self.verify_ttl_sec = verify_ttl_sec
        self.min_relogin_gap_sec = min_relogin_gap_sec
        self.fail_backoff_sec = fail_backoff_sec
        self.net_backoff_sec = net_backoff_sec
        self.contended_backoff_sec = contended_backoff_sec
        self._clock = clock
        self._log = log
        self._lock = threading.RLock()
        self._holder = None               # ISurveyAPI ที่ถือ session กลาง (ใช้ล็อกอิน/เช็ค เท่านั้น ไม่ทำงานอ่าน)
        self.generation = 0               # +1 ทุกครั้งที่ล็อกอินสำเร็จ
        self._login_mono = -1e12
        self._verified_mono = -1e12
        self.backoff_until = -1e12        # เวลา (clock) ที่หยุดพัก
        self.logins = 0
        self.name = ""
        self.logged_in_at: str | None = None
        self.last_ok_at: str | None = None
        self.last_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ สถานะ
    def configured(self) -> bool:
        return bool(self.username and self._password)

    def available(self) -> bool:
        return self.configured() and self._clock() >= self.backoff_until

    def status(self) -> dict:
        """สถานะให้หน้าเว็บ (ไม่มีรหัสผ่าน) — state: not_configured | paused | ready | idle"""
        left = max(0.0, self.backoff_until - self._clock())
        if not self.configured():
            state = "not_configured"
        elif left > 0:
            state = "paused"
        elif self._holder is not None:
            state = "ready"
        else:
            state = "idle"
        return {
            "configured": self.configured(), "username": self.username, "state": state, "name": self.name,
            "logged_in_at": self.logged_in_at, "last_ok_at": self.last_ok_at, "last_error": self.last_error,
            "paused_until": ((datetime.now().astimezone() + timedelta(seconds=left)).isoformat(timespec="seconds")
                             if left > 0 else None),
            "logins": self.logins,
        }

    # ------------------------------------------------------------ ใช้งาน
    def client(self, verify: bool = False):
        """ISurveyAPI สำหรับ 1 งาน — ยืม session กลาง (ล็อกอินให้ถ้ายังไม่เคย/หลุด) · ใช้ไม่ได้ = CentralUnavailable
        verify=True บังคับเช็คว่ายังล็อกอินอยู่ก่อนเริ่ม (รอบอ่านใหม่หลังพบว่า session ไม่ต่อเนื่อง)"""
        with self._lock:
            self._usable_locked()
            if self._holder is None:
                self._login_locked("ใช้งานครั้งแรก")
            holder, gen = self._holder, self.generation
            stale = verify or self._clock() - self._verified_mono > self.verify_ttl_sec
        if stale and not self._alive(holder):          # เช็คนอก lock — ISURVEY ช้าไม่ขวางงานอื่น
            self.recover(gen, "ตรวจก่อนเริ่มงาน")
            holder, gen = self._holder, self.generation
        api = self._factory()
        api.s = holder.s                 # cookie/session เดียวกัน (requests.Session ใช้ข้ามเธรดได้สำหรับ GET — แบบ claim_rounds)
        api.central = self
        api.central_gen = gen
        api.central_reads = 0
        return api

    def recover(self, gen_seen: int, where: str = "") -> None:
        """งานหนึ่งเจอ session หลุด (ตอนส่งคำขอ generation = gen_seen) — ล็อกอินใหม่ครั้งเดียวต่อการหลุด แล้วผู้เรียกลองซ้ำ"""
        with self._lock:
            if self.generation != gen_seen:
                return                                   # งานอื่นล็อกอินใหม่ให้แล้วระหว่างรอ lock
            self._usable_locked()
            if self._clock() - self._login_mono < self.min_relogin_gap_sec:
                self._fail("บัญชี ISURVEY กลางหลุดซ้ำทันทีหลังล็อกอิน — น่าจะมีคนเปิดบัญชีกลางที่อื่น (หน้า ISURVEY / บอท)",
                           self.contended_backoff_sec)
                raise CentralUnavailable(self.last_error)
            self._log(f"[central] session หลุด ({where}) — ล็อกอินใหม่")
            self._login_locked(f"session หลุด ({where})")

    def intact(self, api) -> bool:
        """งานที่ใช้ client นี้ได้ข้อมูลครบไหม: ไม่มีการล็อกอินใหม่ตั้งแต่เริ่มอ่าน + session ยังใช้ได้ตอนจบ
        (session ที่หลุดแล้วกลับมาเองไม่ได้นอกจากล็อกอินใหม่ → generation เดิม + ยังใช้ได้ตอนจบ = ใช้ได้ตลอดงาน)"""
        if getattr(api, "central", None) is not self or api.central_gen != self.generation:
            return False
        holder = self._holder
        if holder is None or not self._alive(holder):
            return False
        return api.central_gen == self.generation

    def test(self) -> dict:
        """ปุ่ม "ทดสอบบัญชีกลาง" ของแอดมิน — ล้างการพัก แล้วเช็ค/ล็อกอินเดี๋ยวนี้ · ไม่ผ่าน = CentralUnavailable"""
        with self._lock:
            if not self.configured():
                raise CentralUnavailable(NOT_CONFIGURED)
            self.backoff_until = -1e12
            holder = self._holder
        if holder is not None and self._alive(holder):
            return self.status()
        with self._lock:
            self._login_locked("ทดสอบจากหน้าเว็บ")
        return self.status()

    # ------------------------------------------------------------ keep-alive
    def ping(self) -> None:
        """รอบ keep-alive: ยังไม่เคยล็อกอิน = ล็อกอิน · ยังล็อกอินอยู่ = แค่แตะ (ต่ออายุ session) · หลุด = ล็อกอินใหม่"""
        if not self.available():
            return
        if self._holder is None:
            with self._lock:
                if self._holder is None and self.available():
                    try:
                        self._login_locked("เปิด service")
                    except CentralUnavailable:
                        pass                              # จดไว้ใน last_error แล้ว
            return
        gen = self.generation
        if self._alive(self._holder):
            return
        try:
            self.recover(gen, "keep-alive")
        except CentralUnavailable:
            pass

    def start_keepalive(self) -> None:
        if not self.configured() or self._thread is not None:
            return

        def loop():
            self._stop.wait(3)                           # ให้ service เปิดพอร์ตก่อน แล้วค่อยล็อกอินรอบแรก
            while not self._stop.is_set():
                try:
                    self.ping()
                except Exception as e:  # noqa: BLE001 — keep-alive ห้ามตาย
                    self._log(f"[central] keep-alive พัง: {type(e).__name__}: {e}")
                self._stop.wait(self.keepalive_sec)

        self._thread = threading.Thread(target=loop, name="isurvey-central-keepalive", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ ภายใน
    def _usable_locked(self) -> None:
        if not self.configured():
            raise CentralUnavailable(NOT_CONFIGURED)
        left = self.backoff_until - self._clock()
        if left > 0:
            raise CentralUnavailable(f"{self.last_error or 'บัญชี ISURVEY กลางพักอยู่'} (พักอีก {int(left // 60) + 1} นาที)")

    def _fail(self, msg: str, backoff_sec: int) -> None:
        self.last_error = msg
        self.backoff_until = self._clock() + backoff_sec
        self._log(f"[central] {msg} — พักบัญชีกลาง {max(1, backoff_sec // 60)} นาที")

    def _login_locked(self, reason: str) -> None:
        holder = self._holder or self._factory()
        try:
            holder.login()
        except RuntimeError as e:        # ISURVEY ตอบแล้วแต่ล็อกอินไม่ผ่าน (รหัสผิด / บัญชีถูกปิด)
            self._fail("ล็อกอินบัญชี ISURVEY กลางไม่ผ่าน — ตรวจ ISURVEY_CENTRAL_USERNAME / ISURVEY_CENTRAL_PASSWORD",
                       self.fail_backoff_sec)
            raise CentralUnavailable(self.last_error) from e
        except Exception as e:           # เน็ต / ISURVEY ไม่ตอบ
            self._fail(f"ล็อกอินบัญชี ISURVEY กลางไม่ได้ — ISURVEY ไม่ตอบ ({type(e).__name__})", self.net_backoff_sec)
            raise CentralUnavailable(self.last_error) from e
        self._holder = holder
        self.generation += 1
        self.logins += 1
        self._login_mono = self._verified_mono = self._clock()
        self.logged_in_at = self.last_ok_at = _now_iso()
        self.last_error = None
        self.backoff_until = -1e12
        self.name = self._whoami(holder)
        self._log(f"[central] ล็อกอินบัญชีกลาง {self.username} แล้ว ({reason}) · ครั้งที่ {self.logins} ตั้งแต่เปิด service")

    def _alive(self, holder) -> bool:
        try:
            ok = bool(holder._get("getUserData.php", _timeout=20, _dc=0).get("success"))
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            self._verified_mono = self._clock()
            self.last_ok_at = _now_iso()
        return ok

    @staticmethod
    def _whoami(holder) -> str:
        try:
            who = holder._get("getUserData.php", _dc=0)
            return str(who.get("message") or "") if who.get("success") else ""
        except Exception:  # noqa: BLE001
            return ""
