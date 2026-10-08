# -*- coding: utf-8 -*-
"""บัญชี ISURVEY กลางของระบบ — service ดึงงานล็อกอินครั้งเดียวแล้วใช้ session เดิมต่อไป (user เปิดบัญชีให้ 08/10/69)

ทำไม: ISURVEY ให้ 1 บัญชีล็อกอินได้ที่เดียว — เดิมทุกคำขอ (ค้น/โหลดรายการ/ครั้งที่/ดึง/ดึงรูป) ล็อกอินด้วยบัญชีหัวหน้าใหม่
หน้า ISURVEY ที่หัวหน้าเปิดค้างไว้จึงขึ้น "กรุณา login เข้าสู่ระบบอีกครั้ง : session lose!" · บัญชีกลางไม่มีคนใช้ ล็อกอินค้างไว้ได้
(ปิดงานหลังอนุมัติยังใช้บัญชีหัวหน้า — ISURVEY ลงชื่อผู้ตรวจถูกคน · user เลือก 08/10/69)

กติกา
 - ทุกงานใช้ requests.Session เดียวกัน (cookie เดียว) · แต่ละงานได้ ISurveyAPI ของตัวเอง
   (⛔ ห้ามแชร์ instance ข้ามงาน — last_case_id/ตาราง master เป็นของงานนั้น ใช้ร่วมกันแล้วรูปข้ามเคสได้)
 - **คำขอ JSON ของ session กลางวิ่งทีละคำขอ (req_lock)** — ISURVEY (PHP) ล็อก session ต่อคำขอ คำขอพร้อมกันบน session เดียว
   ไปต่อคิวที่ ISURVEY แล้วหมดเวลาเอง (เจอจริง 08/10/69 14:21–14:26: หัวหน้าหลายคนโหลดรายการ/ครั้งที่/ดึงพร้อมกัน เช็ค getUserData
   ค้างหลังคิว → นึกว่าหลุด → ล็อกอินใหม่บน session เดิมก็ค้าง → ตีว่ารหัสผิด พัก 15 นาที ทั้งที่บัญชีหัวหน้าล็อกอินได้ทันที)
   ต่อคิวฝั่งเราแทน = ไม่มีใครหมดเวลาเพราะรอคิว (รูปเป็นไฟล์นิ่ง โหลดนอกคิว)
 - ล็อกอิน (ครั้งแรก/ใหม่) ทำบน **session ใหม่** (PHPSESSID ใหม่ ไม่ต่อคิวกับ session เดิมที่อาจค้าง) แล้วย้าย cookie เข้า session ที่ใช้ร่วม
 - เช็ค "ยังล็อกอินอยู่ไหม" มี 3 ผล: ใช่ / ไม่ (ISURVEY ตอบ success:0) / **ไม่รู้ (ช้า/เน็ต) — ห้ามตีเป็นหลุด**
 - keep-alive ทุก keepalive_sec · ก่อนเริ่มงานเช็คถ้าเช็คล่าสุดเก่ากว่า verify_ttl_sec · หลุดจริง = ล็อกอินใหม่เอง
 - คำขอเจอ "Session lose!" → recover(): ล็อกอินใหม่ครั้งเดียว (งานอื่นที่เจอพร้อมกันรอ lock แล้วใช้ session ใหม่ — generation)
 - หลุดซ้ำภายใน min_relogin_gap_sec หลังล็อกอิน = มีคนใช้บัญชีกลางที่อื่น → ไม่แย่งกลับ พักไว้ (ระหว่างพักใช้บัญชีหัวหน้า)
 - ISURVEY **ไม่รับรหัส** (success:0 หลังล็อกอิน) = พัก fail_backoff_sec ใช้บัญชีหัวหน้า (กันบัญชีโดนล็อกเพราะลองรหัสผิดถี่ ๆ)
   ISURVEY ช้า/เน็ตล่ม = พักสั้น net_backoff_sec และ **ไม่ถอยไปบัญชีหัวหน้า** (fallback_ok=False — ถอยไปก็ช้าเหมือนกัน แถมเตะหัวหน้า)
 - ⚠️ session หลุด ISURVEY บางหน้า (รูป/คู่กรณี/ชิ้นส่วน) ตอบ "ว่าง" เงียบ ๆ ไม่ error → intact() เช็คหลังงานที่เขียนข้อมูล (ดึง/ดึงรูป)
   งานอ่านอย่างเดียว (ค้น/รายการ/ครั้งที่) ใช้แต่หน้าที่ฟ้อง "Session lose!" ได้ จึงไม่ต้องเช็คซ้ำ
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

NOT_CONFIGURED = "ยังไม่ได้ตั้งบัญชี ISURVEY กลาง (ISURVEY_CENTRAL_USERNAME / ISURVEY_CENTRAL_PASSWORD ของ service ดึงงาน)"


class CentralUnavailable(RuntimeError):
    """บัญชีกลางใช้ไม่ได้ตอนนี้ (ไม่ได้ตั้ง / ISURVEY ไม่รับรหัส / พักอยู่ / มีคนใช้ที่อื่น / ISURVEY ไม่ตอบ)
    fallback_ok=True ผู้เรียกถอยไปบัญชีหัวหน้าได้ · False (ISURVEY ช้า/เน็ต) ให้ตอบ "ลองใหม่" แทน"""

    def __init__(self, msg: str, fallback_ok: bool = True):
        super().__init__(msg)
        self.fallback_ok = fallback_ok


class _LoginRejected(Exception):
    """ISURVEY ตอบแล้วว่าไม่ได้ล็อกอิน (success:0) หลังส่งรหัส = รหัสผิด / บัญชีถูกปิด"""


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
                 min_relogin_gap_sec: int = 60, fail_backoff_sec: int = 900, net_backoff_sec: int = 60,
                 contended_backoff_sec: int = 300, check_timeout_sec: int = 30, clock=time.monotonic, log=print):
        self.username = str(username or "").strip()
        self._password = str(password or "")
        self._factory = factory
        self.keepalive_sec = keepalive_sec
        self.verify_ttl_sec = verify_ttl_sec
        self.min_relogin_gap_sec = min_relogin_gap_sec
        self.fail_backoff_sec = fail_backoff_sec
        self.net_backoff_sec = net_backoff_sec
        self.contended_backoff_sec = contended_backoff_sec
        self.check_timeout_sec = check_timeout_sec
        self._clock = clock
        self._log = log
        self._lock = threading.RLock()     # ล็อกอิน/เปลี่ยนสถานะ ทีละคน
        self.req_lock = threading.Lock()   # คำขอ JSON บน session กลาง ทีละคำขอ (ดูหัวไฟล์)
        self._holder = None               # ISurveyAPI ที่ถือ session กลาง (ใช้เช็ค/เก็บ cookie เท่านั้น ไม่ทำงานอ่าน)
        self.generation = 0               # +1 ทุกครั้งที่ล็อกอินสำเร็จ
        self._login_mono = -1e12
        self._verified_mono = -1e12
        self.backoff_until = -1e12        # เวลา (clock) ที่หยุดพัก
        self._fallback_ok = True          # ชนิดของการพักล่าสุด — ถอยไปบัญชีหัวหน้าได้ไหม
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
            # พักเพราะ ISURVEY ช้า/เน็ต (False) = ไม่ใช้บัญชีหัวหน้าแทน ให้ลองใหม่ · True = ใช้บัญชีหัวหน้าแทนระหว่างพัก
            "fallback": self._fallback_ok,
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
        if stale and self._check(holder) is False:     # เช็คนอก lock · None (ช้า/เน็ต) = ไม่รู้ → ใช้ต่อ ไม่ล็อกอินใหม่
            self.recover(gen, "ตรวจก่อนเริ่มงาน")
            holder, gen = self._holder, self.generation
        api = self._factory()
        api.s = holder.s                 # cookie/session เดียวกัน
        api.req_lock = self.req_lock     # คำขอ JSON ทีละคำขอ (PHP ล็อก session ต่อคำขอ)
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
                           self.contended_backoff_sec, fallback_ok=True)
                raise CentralUnavailable(self.last_error, fallback_ok=True)
            self._log(f"[central] session หลุด ({where}) — ล็อกอินใหม่")
            self._login_locked(f"session หลุด ({where})")

    def intact(self, api) -> bool:
        """งานที่ใช้ client นี้ได้ข้อมูลครบไหม: ไม่มีการล็อกอินใหม่ตั้งแต่เริ่มอ่าน + session ไม่ได้หลุดตอนจบ
        (session ที่หลุดแล้วกลับมาเองไม่ได้นอกจากล็อกอินใหม่ → generation เดิม + ไม่หลุดตอนจบ = ใช้ได้ตลอดงาน)
        เช็คตอนจบได้ "ไม่รู้" (ISURVEY ช้า) = ถือว่าครบ — หลุดจริง ISURVEY ตอบ success:0 ทันที ไม่ได้ช้า"""
        if getattr(api, "central", None) is not self or api.central_gen != self.generation:
            return False
        holder = self._holder
        if holder is None or self._check(holder) is False:
            return False
        return api.central_gen == self.generation

    def test(self) -> dict:
        """ปุ่ม "ทดสอบบัญชีกลาง" ของแอดมิน — ล้างการพัก แล้วเช็ค/ล็อกอินเดี๋ยวนี้ · ไม่ผ่าน = CentralUnavailable"""
        with self._lock:
            if not self.configured():
                raise CentralUnavailable(NOT_CONFIGURED)
            self.backoff_until = -1e12
            holder = self._holder
        if holder is not None and self._check(holder) is True:
            return self.status()
        with self._lock:
            self._login_locked("ทดสอบจากหน้าเว็บ")
        return self.status()

    # ------------------------------------------------------------ keep-alive
    def ping(self) -> None:
        """รอบ keep-alive: ยังไม่เคยล็อกอิน = ล็อกอิน · ยังล็อกอินอยู่ = แค่แตะ (ต่ออายุ session) · หลุดจริง = ล็อกอินใหม่ ·
        ไม่รู้ (ISURVEY ช้า) = ปล่อยไว้ รอบหน้าค่อยดู"""
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
        if self._check(self._holder) is not False:
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
            wait = f"อีก {int(left // 60) + 1} นาที" if left >= 60 else f"อีก {int(left) + 1} วินาที"
            raise CentralUnavailable(f"{self.last_error or 'บัญชี ISURVEY กลางพักอยู่'} (พัก{wait})",
                                     fallback_ok=self._fallback_ok)

    def _fail(self, msg: str, backoff_sec: int, fallback_ok: bool) -> None:
        self.last_error = msg
        self.backoff_until = self._clock() + backoff_sec
        self._fallback_ok = fallback_ok
        self._log(f"[central] {msg} — พักบัญชีกลาง {backoff_sec} วินาที")

    def _login_locked(self, reason: str) -> None:
        """ล็อกอินบน session ใหม่ (ไม่ต่อคิวกับคำขอที่ค้างบน session เดิม) แล้วย้าย cookie เข้า session ที่ใช้ร่วม
        งานที่กำลังวิ่งอยู่จึงใช้ session ใหม่ได้ทันทีในคำขอถัดไป"""
        fresh = self._factory()
        try:
            name = self._do_login(fresh)
        except _LoginRejected as e:
            self._fail("ISURVEY ไม่รับรหัสบัญชีกลาง — ตรวจ ISURVEY_CENTRAL_USERNAME / ISURVEY_CENTRAL_PASSWORD",
                       self.fail_backoff_sec, fallback_ok=True)
            raise CentralUnavailable(self.last_error, fallback_ok=True) from e
        except Exception as e:  # noqa: BLE001 — ช้า/เน็ต/ISURVEY ล่ม
            self._fail(f"ล็อกอินบัญชี ISURVEY กลางไม่ได้ — ISURVEY ไม่ตอบ ({type(e).__name__})",
                       self.net_backoff_sec, fallback_ok=False)
            raise CentralUnavailable(self.last_error, fallback_ok=False) from e
        if self._holder is None:
            fresh.req_lock = self.req_lock
            self._holder = fresh
        else:
            # ย้าย cookie ของ session ใหม่เข้า session ที่ใช้ร่วม — รอคำขอที่กำลังวิ่งจบก่อนถ้ารอได้ (กัน Set-Cookie ของคำขอเก่าทับ)
            got = self.req_lock.acquire(timeout=5)
            try:
                jar = self._holder.s.cookies
                jar.clear()
                jar.update(fresh.s.cookies)
            finally:
                if got:
                    self.req_lock.release()
        self.generation += 1
        self.logins += 1
        self._login_mono = self._verified_mono = self._clock()
        self.logged_in_at = self.last_ok_at = _now_iso()
        self.last_error = None
        self.backoff_until = -1e12
        self._fallback_ok = True
        self.name = name
        self._log(f"[central] ล็อกอินบัญชีกลาง {self.username} แล้ว ({reason}) · ครั้งที่ {self.logins} ตั้งแต่เปิด service")

    @staticmethod
    def _do_login(api) -> str:
        """ล็อกอินบน session ของ api (ใหม่ ยังไม่มี cookie) — คืนชื่อผู้ใช้ที่ ISURVEY บอก
        ISURVEY ตอบ success:0 = _LoginRejected · ช้า/เน็ต = exception อื่น (แยกให้ออก — ISurveyAPI.login รวมทั้งสองเป็น RuntimeError)"""
        cfg = api.cfg
        try:
            api.s.get(cfg.isurvey_url, timeout=30)       # เอา PHPSESSID เริ่มต้น (แบบ ISurveyAPI.login)
        except Exception:  # noqa: BLE001
            pass
        api.s.post(f"{api.base}/login.php",
                   data={"username": cfg.isurvey_username, "password": cfg.isurvey_password}, timeout=45)
        last: Exception | None = None
        for t in (30, 60):          # คำขอแรกหลังล็อกอินบางครั้งค้าง >30 วิ (เจอ 08/10/69) — รอนานขึ้นอีกรอบก่อนยอมแพ้
            try:
                who = api._get("getUserData.php", _timeout=t, _dc=0)
                break
            except Exception as e:  # noqa: BLE001
                last = e
        else:
            raise last if last else TimeoutError("getUserData ไม่ตอบ")
        if not (isinstance(who, dict) and who.get("success")):
            raise _LoginRejected()
        return str(who.get("message") or "")

    def _check(self, holder):
        """ยังล็อกอินอยู่ไหม: True · False (ISURVEY ตอบ success:0) · None (ช้า/เน็ต — ไม่รู้ ⛔ ห้ามตีเป็นหลุด)"""
        try:
            who = holder._get("getUserData.php", _timeout=self.check_timeout_sec, _dc=0)
        except Exception:  # noqa: BLE001
            return None
        if isinstance(who, dict) and who.get("success"):
            self._verified_mono = self._clock()
            self.last_ok_at = _now_iso()
            return True
        return False
