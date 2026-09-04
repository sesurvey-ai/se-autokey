# -*- coding: utf-8 -*-
"""โหมดสถานีนำเข้า EMCS — รับงานจาก "คิวนำเข้า EMCS" ของเว็บ se-survey ทีละเรื่อง (user ตัดสิน 04/09/69)

ทำไมต้องมี: ปุ่ม "นำเข้า EMCS" แบบเดิมต้องมีบอทติดตั้งบนเครื่องที่กด (127.0.0.1:8765) + ตั้ง token + Chrome
ขออนุญาตเครือข่ายภายใน ทุกเครื่อง · โหมดนี้ให้ **เครื่องสถานี** (Windows ที่จัดเตรียมไว้) รันบอทค้างไว้
หัวหน้ากด "ส่งเข้าคิว EMCS" จากเครื่องไหนก็ได้ สถานีมารับงานเองแล้วรายงานผลกลับขึ้นหน้าเว็บ

หลักการที่ user เคาะ
  - **ล็อกอิน EMCS ครั้งเดียวต่อกะ** (ไม่ใช่ 300 ครั้ง/วัน): เปิด Chrome หนึ่งหน้าต่าง ล็อกอินครั้งแรก แล้ววนรับงาน
    ในหน้าต่างเดิม · ล็อกอินซ้ำเฉพาะเมื่อ EMCS หมดเวลาเซสชัน/เตะออก (ตรวจเองก่อนทุกงาน)
  - ระหว่างเรื่องกลับไปหน้า MainPage ล้างสถานะ · รีสตาร์ต Chrome ทุก RESTART_EVERY เรื่องหรือเมื่องานพัง
  - ทีละเรื่องต่อบัญชี (EMCS ล็อกเรื่องต่อ username) · 2 สถานี = 2 บัญชี — คิวฝั่งเว็บกันชนด้วย SKIP LOCKED
  - draft-only เหมือนเดิม: บอทไม่กด "ส่งงานใหม่" · กันซ้ำ 3 ชั้น (emcs_imported_at / meta / ค้นเรื่องเดิม) ทำงานเหมือนเดิม
  - งานพัง → รายงานสาเหตุ + log ท้าย ๆ + ภาพหน้าจอ ขึ้นหน้าเว็บ ให้หัวหน้าส่งเข้าคิวอีกครั้งหรือแก้ต้นเหตุ
  - heartbeat ทุก HEARTBEAT_SEC ระหว่างทำ — เว็บถือว่าสถานีหายถ้าเงียบเกิน 40 นาที แล้วคืนงานเข้าคิว

ใช้: `python main.py --station [--station-name ชื่อ] [--poll วินาที] [--dry-run]` หรือ start-station.bat
`--dry-run` = รับงานจากคิวจริง แต่ตรวจ XML + โหลดรูปแล้วรายงาน "ผ่าน" โดยไม่แตะ EMCS (ไว้ทดสอบท่อ)
"""
from __future__ import annotations

import base64
import collections
import io
import os
import socket
import sys
import threading
import time
import traceback
from datetime import datetime

import requests
from selenium.webdriver.common.by import By

from autokey import emcs
from autokey.browser import log, log_plain, make_driver, save_debug_snapshot
from autokey.claim_data import ClaimData
from autokey.surv_xml import enrich_claim_from_xml, parse_surv_report

POLL_SEC = 10            # ถามคิวทุกกี่วินาทีเมื่อว่าง
HEARTBEAT_SEC = 60       # บอกเว็บว่ายังทำอยู่
RESTART_EVERY = 50       # รีสตาร์ต Chrome ทุก N เรื่อง (กันหน่วยความจำบวม/สถานะค้าง)
LOG_TAIL_LINES = 80      # log ท้าย ๆ ที่ส่งขึ้นเว็บเมื่องานจบ/พัง


def banner(text: str):
    log_plain(f"\n{'=' * 60}\n  {text}\n{'=' * 60}")


class _Tee(io.TextIOBase):
    """สำเนา stdout เก็บบรรทัดท้าย ๆ ไว้ส่งขึ้นเว็บ (log() ของบอทพิมพ์ลง stdout)"""

    def __init__(self, inner):
        self.inner = inner
        self.lines: collections.deque[str] = collections.deque(maxlen=LOG_TAIL_LINES)
        self._cur = ""

    def write(self, s):
        try:
            self.inner.write(s)
        except Exception:
            pass
        self._cur += s
        while "\n" in self._cur:
            line, self._cur = self._cur.split("\n", 1)
            if line.strip():
                self.lines.append(line.rstrip())
        return len(s)

    def flush(self):
        try:
            self.inner.flush()
        except Exception:
            pass

    def tail(self) -> str:
        return "\n".join(self.lines)

    def reset(self):
        self.lines.clear()


class StationError(RuntimeError):
    """ข้อผิดพลาดที่อธิบายได้เป็นภาษาคน (ขึ้นหน้าเว็บตรง ๆ)"""


class Station:
    def __init__(self, cfg, name: str, dry_run: bool = False, poll: int = POLL_SEC):
        self.cfg = cfg
        self.name = (name or socket.gethostname() or "station").strip()[:80]
        self.dry_run = dry_run
        self.poll = max(3, int(poll or POLL_SEC))
        self.hdrs = {"Authorization": f"Bearer {cfg.sesurvey_api_token}"}
        self.driver = None
        self.main_url = ""          # หน้า MainPage พร้อม session token ของ EMCS (ไว้กลับมาล้างสถานะ)
        self.jobs_since_restart = 0
        self.tee: _Tee | None = None
        self.stop = False

    # ───────────── se-survey API ─────────────
    def api(self, method: str, path: str, timeout: int = 30, **kw) -> dict:
        r = requests.request(method, f"{self.cfg.sesurvey_api_url}{path}", headers=self.hdrs, timeout=timeout, **kw)
        if r.status_code == 401:
            raise StationError("token ของ se-survey ไม่ถูกต้อง (⚙ ตั้งค่า → ระบบ se-survey)")
        r.raise_for_status()
        return r.json()

    # ───────────── Chrome / EMCS session ─────────────
    def ensure_driver(self):
        if self.driver is None:
            per_run_dl = self.cfg.download_dir / "_dl" / f"station_{os.getpid()}"
            self.driver = make_driver(detach=True, download_dir=per_run_dl)
            self.main_url = ""
            self.jobs_since_restart = 0
        return self.driver

    def restart_driver(self, why: str):
        if self.driver is not None:
            log(f"🔄 รีสตาร์ต Chrome ({why})")
            try:
                self.driver.quit()
            except Exception:
                pass
        self.driver = None
        self.main_url = ""
        self.jobs_since_restart = 0

    def ensure_login(self):
        """อยู่ในเซสชัน EMCS อยู่แล้ว = แค่กลับไปหน้า MainPage · หลุด/หมดเวลา = ล็อกอินใหม่ (ครั้งเดียวต่อกะตามปกติ)"""
        d = self.ensure_driver()
        if self.main_url:
            try:
                d.get(self.main_url)
                time.sleep(1.0)
                if not d.find_elements(By.ID, "txtUserName"):
                    return                              # ยังอยู่ในระบบ ไม่ต้องล็อกอินซ้ำ
                log("EMCS: เซสชันหมด/ถูกเตะออก — ล็อกอินใหม่")
            except Exception as e:
                log(f"EMCS: กลับหน้าหลักไม่ได้ ({type(e).__name__}) — ล็อกอินใหม่")
        emcs.login(d, self.cfg)
        self.main_url = d.current_url

    # ───────────── งานหนึ่งเรื่อง ─────────────
    def process(self, job: dict) -> dict:
        """เตรียมข้อมูลจาก se-survey → (dry-run หยุดตรงนี้) → กรอก EMCS จนถึง draft → mark กลับ"""
        import main as M   # helper ของ flow --sesurvey-case (ไม่ทำซ้ำสองที่)

        case_id = str(int(job["case_id"]))
        cfg, hdrs = self.cfg, self.hdrs
        meta = self.api("GET", f"/api/integrations/cases/{case_id}").get("data") or {}
        if meta.get("approved") is False:
            raise StationError(f"เคสยังไม่ได้อนุมัติ (สถานะ {meta.get('status')}) — ให้หัวหน้ากดอนุมัติก่อน")
        if meta.get("emcs_imported_at") and not self.dry_run:
            log(f"⛔ เคส #{case_id} นำเข้า EMCS ไปแล้วเมื่อ {meta['emcs_imported_at']} — ไม่ทำซ้ำ")
            return {"ok": True, "esurvey_no": meta.get("emcs_esurvey_no") or None, "note": "นำเข้าอยู่แล้ว"}

        banner(f"งาน #{job['id']} · เคส #{case_id} · เคลม {job.get('claim_no') or meta.get('claim_no') or '?'}"
               + (" [DRY-RUN]" if self.dry_run else ""))
        resp = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/export-xml",
                            headers=hdrs, timeout=60)
        if resp.status_code == 404:
            raise StationError("ไม่พบเคส หรือเคสยังไม่มีข้อมูลรายงานสำรวจ")
        resp.raise_for_status()
        xml_dir = cfg.runs_dir / "xml"
        xml_dir.mkdir(parents=True, exist_ok=True)
        xml_path = xml_dir / f"sesurvey_case_{case_id}.txt"     # EMCS รับเฉพาะ .txt
        xml_path.write_bytes(resp.content)
        parsed = parse_surv_report(xml_path)

        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
        rep = root.find("TXN_SURV_REPORT")
        get_tag = lambda t: (rep.findtext(t) or "").strip() if rep is not None else ""
        claim_no = get_tag("REF_CLAIM_NO") or f"sesurvey_{case_id}"
        log(f"✓ XML: เคลม {claim_no} · เซอร์เวย์ {get_tag('SURV_JOBNO')} · คู่กรณี {len(parsed['third_parties'])}"
            f" / ผู้บาดเจ็บ {len(parsed['injuries'])} / ทรัพย์สิน {len(parsed['assets'])}")

        from autokey.insurer_map import resolve_insurer_code
        company = meta.get("insurance_company") or ""
        ins_code = resolve_insurer_code(company)
        if not ins_code:
            raise StationError(f"ไม่รู้รหัสบริษัทประกันของ '{company or '(ว่าง)'}' ใน EMCS — เติมใน autokey/insurer_map.py ก่อน")
        log(f"✓ บริษัทประกัน: {company} → รหัส EMCS {ins_code}")

        img_folder = M._download_case_photos(cfg, case_id, hdrs, claim_no)

        data = ClaimData()
        data.claim_value = claim_no
        data.invoice_value = get_tag("SURV_JOBNO")
        data.xml_file = str(xml_path)
        enrich_claim_from_xml(data, xml_path, xml_bill_is_approved=True)
        loss_type, severity = "auto", "เบา"
        try:
            rr = requests.get(f"{cfg.sesurvey_api_url}/api/integrations/cases/{case_id}/report", headers=hdrs, timeout=20)
            rr.raise_for_status()
            report = rr.json().get("data") or {}
            loss_type = M._populate_claim_from_report(data, report)
            severity = str(report.get("damage_level") or "").strip() or "เบา"
        except Exception as e:
            log(f"⚠️ ดึง report มาเติมข้อมูลหน้าหลักไม่ได้ ({e}) — บางช่องอาจต้องกรอกมือ")

        if self.dry_run:
            log("🧪 DRY-RUN: ตรวจ XML + รูป + บริษัทครบ — ไม่แตะ EMCS")
            return {"ok": True, "esurvey_no": None}

        self.ensure_login()
        try:
            esurvey = emcs.fill_imported(self.driver, cfg, data, images_folder=img_folder,
                                         insurer_code=ins_code, full_billing=True, loss_type=loss_type,
                                         severity=severity, allow_continuation=False)
        except Exception:
            # draft อาจถูกสร้างไปแล้วก่อนพัง (ลบใน EMCS ไม่ได้) — mark ฝั่งเว็บให้ตรงความจริง กัน import ซ้ำ
            partial = getattr(emcs.fill_imported, "last_draft_esurvey", "")
            if partial:
                log(f"⚠️ draft {partial} ถูกสร้างใน EMCS แล้วก่อนงานจะพัง — mark ฝั่ง se-survey ไว้ (เติมส่วนที่ขาดด้วยโหมดกู้)")
                try:
                    M._mark_emcs_imported(cfg, case_id, hdrs, partial)
                except Exception as e:
                    log(f"   mark ไม่สำเร็จ: {e}")
            raise
        M._mark_emcs_imported(cfg, case_id, hdrs, esurvey)
        log(f"✅ สร้าง draft สำเร็จ{f' (e-Survey {esurvey})' if esurvey else ''} — รอคนตรวจแล้วกดส่งงานใหม่")
        return {"ok": True, "esurvey_no": esurvey or None}

    # ───────────── วงจรงาน ─────────────
    def _heartbeat_loop(self, job_id: int, stop: threading.Event):
        while not stop.wait(HEARTBEAT_SEC):
            try:
                self.api("POST", f"/api/integrations/emcs-queue/{job_id}/heartbeat", json={"station": self.name}, timeout=15)
            except Exception:
                pass

    def _screenshot_b64(self) -> str | None:
        if self.driver is None:
            return None
        try:
            png = self.driver.get_screenshot_as_png()
            return base64.b64encode(png).decode("ascii") if png else None
        except Exception:
            return None

    def _report(self, job_id: int, payload: dict):
        for attempt in range(3):
            try:
                self.api("POST", f"/api/integrations/emcs-queue/{job_id}/result", json=payload, timeout=60)
                return
            except Exception as e:
                log(f"⚠️ รายงานผลงาน #{job_id} ไม่สำเร็จ (ครั้งที่ {attempt + 1}): {e}")
                time.sleep(5)

    def handle(self, job: dict):
        job_id = int(job["id"])
        stop = threading.Event()
        hb = threading.Thread(target=self._heartbeat_loop, args=(job_id, stop), daemon=True)
        hb.start()
        if self.tee:
            self.tee.reset()
        failed = False
        try:
            res = self.process(job)
            self._report(job_id, {"ok": True, "esurvey_no": res.get("esurvey_no"),
                                  "log_tail": self.tee.tail() if self.tee else ""})
        except KeyboardInterrupt:
            self._report(job_id, {"ok": False, "error": "สถานีถูกหยุดกลางคัน (Ctrl+C) — ส่งเข้าคิวอีกครั้งได้",
                                  "log_tail": self.tee.tail() if self.tee else ""})
            raise
        except Exception as e:
            failed = True
            msg = str(e) if isinstance(e, StationError) else f"{type(e).__name__}: {e}"
            log(f"❌ งาน #{job_id} พัง: {msg}")
            log_plain(traceback.format_exc(limit=3))
            shot = self._screenshot_b64()
            if self.driver is not None:
                try:
                    save_debug_snapshot(self.driver, self.cfg.runs_dir / "logs", tag=f"station_job{job_id}")
                except Exception:
                    pass
            self._report(job_id, {"ok": False, "error": msg[:2000], "screenshot_b64": shot,
                                  "log_tail": self.tee.tail() if self.tee else ""})
        finally:
            stop.set()
        # ล้างสถานะระหว่างเรื่อง: พัง = รีสตาร์ต Chrome ทั้งตัว · ปกติ = นับรอบ รีสตาร์ตทุก N เรื่อง
        self.jobs_since_restart += 1
        if failed:
            self.restart_driver("หลังงานพัง — เริ่มเรื่องถัดไปด้วยหน้าต่างสะอาด")
        elif self.jobs_since_restart >= RESTART_EVERY:
            self.restart_driver(f"ครบ {RESTART_EVERY} เรื่อง")

    def run(self):
        self.tee = _Tee(sys.stdout)
        sys.stdout = self.tee
        banner(f"สถานีนำเข้า EMCS: {self.name} — รับงานจากคิว {self.cfg.sesurvey_api_url} ทุก {self.poll} วิ"
               + ("  [DRY-RUN: ไม่แตะ EMCS]" if self.dry_run else "  (ล็อกอิน EMCS ครั้งเดียว วนรับงานทีละเรื่อง)"))
        log_plain("  หยุด: กด Ctrl+C ที่หน้าต่างนี้ (งานที่ค้างจะถูกรายงานว่าถูกหยุด และส่งเข้าคิวใหม่ได้)\n")
        idle_logged = False
        try:
            while not self.stop:
                try:
                    job = (self.api("POST", "/api/integrations/emcs-queue/claim", json={"station": self.name}, timeout=30)
                           .get("data") or {}).get("job")
                except StationError as e:
                    log(f"⛔ {e}")
                    time.sleep(max(self.poll, 30))
                    continue
                except Exception as e:
                    log(f"⚠️ ติดต่อ se-survey ไม่ได้: {e} — ลองใหม่ใน {self.poll} วิ")
                    time.sleep(self.poll)
                    continue
                if not job:
                    if not idle_logged:
                        log(f"…ว่าง รอคิว (ถามทุก {self.poll} วิ) {datetime.now():%H:%M}")
                        idle_logged = True
                    time.sleep(self.poll)
                    continue
                idle_logged = False
                self.handle(job)
        except KeyboardInterrupt:
            log_plain("\nปิดสถานีแล้ว")
        finally:
            sys.stdout = self.tee.inner if self.tee else sys.stdout


def run_station(cfg, args):
    if not cfg.sesurvey_api_token:
        raise SystemExit("ไม่พบ SESURVEY_API_TOKEN — เปิด start-webui.bat → ⚙ ตั้งค่า → ระบบ se-survey แล้ววาง token ก่อน")
    Station(cfg, getattr(args, "station_name", "") or "", dry_run=bool(getattr(args, "dry_run", False)),
            poll=int(getattr(args, "poll", POLL_SEC) or POLL_SEC)).run()
