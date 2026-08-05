"""webui.py — หน้าเว็บสำหรับสั่งรัน se-autokey แบบกดปุ่ม

เปิดหน้าเว็บที่มีช่องใส่เลขเคลม + ปุ่มรัน แล้วโชว์ log การทำงานสดๆ
ตัวมันเองเป็นแค่ "ตัวเปิดโปรแกรม" — เบื้องหลังเรียก main.py ตัวเดิมผ่าน
subprocess ทุกอย่างจึงทำงานเหมือนรันใน terminal เป๊ะ (Chrome เปิดให้เห็น,
บันทึกเป็น draft, ไม่กดปุ่ม "ส่งงานใหม่" ให้)

รองรับ "รันหลายงานพร้อมกัน" — แต่ละงานเป็น subprocess + หน้าต่าง Chrome แยกกัน
(ISURVEY บัญชีเดียวเปิดได้หลาย session) มีการ์ด log + ปุ่มหยุด/ดำเนินการต่อ
แยกของแต่ละงาน จำกัดจำนวนงานพร้อมกันด้วย SE_MAX_CONCURRENT (default 4)

วิธีใช้:
    python webui.py            # เปิดที่ http://127.0.0.1:8765
    python webui.py --port 9000
    python webui.py --no-open  # ไม่เปิดเบราว์เซอร์ให้อัตโนมัติ

ใช้ไลบรารีมาตรฐานของ Python ล้วน — ไม่ต้องติดตั้งอะไรเพิ่ม
"""
from datetime import datetime, timedelta
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from autokey.config import _load_env_file

BASE = Path(__file__).resolve().parent


def _sesurvey_cfg():
    """อ่าน URL + token ของ se-survey จาก env/.env (ไม่ต้องพึ่ง load_config เต็ม
    ที่บังคับมี creds ISURVEY/EMCS) — token อยู่ฝั่ง server ไม่ส่งให้เบราว์เซอร์"""
    envf = _load_env_file(BASE / ".env")

    def get(k, default=""):
        return os.environ.get(k, envf.get(k, default))
    return (get("SESURVEY_API_URL", "https://api.sesurvey.cloud").rstrip("/"),
            get("SESURVEY_API_TOKEN"))


def fetch_sesurvey_cases():
    """ดึงรายการเคสสำรวจแล้วจาก se-survey — คืน (cases, error)
    proxy ฝั่ง server: เบราว์เซอร์เรียก webui (same-origin) ไม่ต้องรู้ token/ไม่ติด CORS"""
    url, token = _sesurvey_cfg()
    if not token:
        return None, "ยังไม่ได้ตั้ง SESURVEY_API_TOKEN ใน .env"
    try:
        req = urllib.request.Request(
            f"{url}/api/integrations/cases",
            headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return (body.get("data") or {}).get("cases") or [], None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None, "token ไม่ถูกต้อง หรือ integration ยังไม่เปิดบน server"
        return None, f"server ตอบ {e.code}"
    except Exception as e:
        return None, f"เชื่อมต่อ se-survey ไม่ได้: {e}"


_isv_client = None      # cache ไว้ทั้ง process — login ใหม่ทุก request ช้าและโดน rate


# สถานะงานที่พร้อมนำเข้า EMCS (ป้ายไทยจากรายงาน — คอลัมน์ stt_desc)
ISURVEY_STATUS_DONE = "จบงาน"
# ค่าใน EMCSstatus เมื่อเรื่องนั้นถูกนำเข้า EMCS แล้ว (ISURVEY เซ็ตเอง เราไม่เขียนกลับ)
ISURVEY_EMCS_SENT = "send"
_ISURVEY_REPORT_URL = "https://cloud.isurvey.mobi/web/php/report/get_data_report.php"


def fetch_isurvey_cases(date_from: str = "", date_to: str = "",
                        status: str = ISURVEY_STATUS_DONE):
    """รายการงาน ISURVEY ตามสถานะ (ค่าเริ่มต้น = "จบงาน") — คืน (rows, error)

    ใช้ **รายงาน** `report/get_data_report.php` (report_type=enquiry) ไม่ใช่ listcases.php
    เพราะ listcases มีข้อจำกัดหนัก (probe แล้ว 2026-08-04): ตัน 50 แถว · paging ใช้ไม่ได้
    (start/page เท่าไหร่ก็ชุดเดิม) · sort ถูกเมิน · total = limit เสมอ · **ไม่มี EMCSstatus**
    รายงานให้ครบทุกข้อ: ช่วงวันที่ from–to · คืนครบทุกแถว (1,094 แถว/4 วัน) ·
    สถานะเป็นป้ายไทย `stt_desc` · และมี **EMCSstatus/EMCSby/EMCSdate**
    (แหล่งที่มาของสูตรนี้: โปรเจกต์ se-report ซึ่งอ่านรายงานชุดเดียวกันอยู่แล้ว)

    EMCSstatus == 'send' = เรื่องนั้นนำเข้า EMCS ไปแล้ว — **ISURVEY เซ็ตเอง**
    บอทไม่ต้องยิงกลับไปอัปเดต (user ยืนยัน 2026-08-04)
    """
    global _isv_client
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        from autokey.config import load_config
        from autokey.isurvey_api import ISurveyAPI
        if _isv_client is None:
            _isv_client = ISurveyAPI(load_config())
            _isv_client.login()

        def _report():
            r = _isv_client.s.get(_ISURVEY_REPORT_URL, timeout=120, params={
                "con_date": 2, "date_from": date_from, "date_to": date_to,
                "report_type": "enquiry", "page": 1, "start": 0, "limit": 5000})
            r.raise_for_status()
            return r.json()
        try:
            d = _report()
        except Exception:
            _isv_client.login()          # session หมดอายุ → login ใหม่แล้วลองอีกรอบ
            d = _report()

        rows = []
        for x in (d.get("arr_data") or d.get("data") or []):
            if status and str(x.get("stt_desc") or "").strip() != status:
                continue
            rows.append({
                "claim_no": x.get("claim_no") or "",
                "survey_no": x.get("survey_no") or "",
                "surveyor_name": x.get("empcode") or "",
                "acc_province": x.get("acc_province") or "",
                "plate_no": x.get("plate_no") or "",
                "finish_dt": x.get("finish_dt") or "",
                "status": x.get("stt_desc") or "",
                # นำเข้า EMCS แล้วหรือยัง (ISURVEY เซ็ตเอง)
                "emcs_sent": str(x.get("EMCSstatus") or "") == ISURVEY_EMCS_SENT,
                "emcs_by": x.get("EMCSby") or "",
                "emcs_date": x.get("EMCSdate") or "",
            })
        rows.sort(key=lambda r: str(r.get("finish_dt") or ""), reverse=True)
        return rows, None
    except Exception as e:
        _isv_client = None
        return None, f"อ่านรายการจาก ISURVEY ไม่ได้: {type(e).__name__}: {e}"


def check_sesurvey_case(case_id: str):
    """ตรวจก่อนนำเข้าฝั่ง se-survey — คืน (ผลตรวจ, error)

    ต่างจากฝั่ง ISURVEY: ที่นี่ข้อมูลมาจากแอปมือถือซึ่งฟิลด์ตรง EMCS เกือบหมด
    (ลักษณะความเสียหาย/คำนำหน้ามาครบ) จึงไม่ต้องให้เลือกค่าบนหน้าเว็บ —
    สิ่งที่พลาดได้คือ "ดึงของไม่ครบ" (XML/report ดึงไม่ได้, ไม่มีความเสียหาย ฯลฯ)

    ตรวจจาก XML + report ของจริง **ไม่เปิด Chrome ไม่แตะ EMCS** — เร็วกว่ารัน dry-run
    แล้วไล่อ่าน log เอง (ซึ่งเป็นวิธีเดิม)
    """
    import importlib
    xml_bytes, err = fetch_sesurvey_xml(case_id)
    if err:
        return None, f"ดึง XML ไม่ได้: {err}"

    warnings, blockers = [], []
    counts = {"opponents": 0, "injuries": 0, "assets": 0, "damage": 0}
    try:
        import tempfile
        from autokey.surv_xml import parse_surv_report
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as f:
            f.write(xml_bytes)
            tmp = f.name
        parsed = parse_surv_report(tmp)
        Path(tmp).unlink(missing_ok=True)
        counts["opponents"] = len(parsed.get("third_parties") or [])
        counts["injuries"] = len(parsed.get("injuries") or [])
        counts["assets"] = len(parsed.get("assets") or [])
    except Exception as e:
        blockers.append(f"อ่านไฟล์ XML ไม่ได้ ({type(e).__name__}) — เคสนี้ยังนำเข้าไม่ได้")

    # report = ค่าไทยที่ fill_* ต้องใช้เลือก dropdown บังคับของ EMCS
    # (ประเภทรถ/จังหวัด/ยี่ห้อ/คำนำหน้า/ลักษณะความเสียหาย) — ขาดแล้วบอทจะหยุดรอกลางทาง
    info = {}
    try:
        url, token = _sesurvey_cfg()
        req = urllib.request.Request(f"{url}/api/integrations/cases/{case_id}/report",
                                     headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            rep = (json.loads(resp.read().decode("utf-8")).get("data") or {})
        _main = importlib.import_module("main")
        from autokey.claim_data import ClaimData
        d = ClaimData()
        loss_type = _main._populate_claim_from_report(d, rep)
        counts["damage"] = len(d.damage or [])
        info = {"loss_type": loss_type, "severity": str(rep.get("damage_level") or "").strip(),
                "car_type": d.prb_car_type, "acc_province": d.acc_province,
                "driver_title": d.driver_title, "claim_no": d.claim_value or rep.get("claim_no", ""),
                "survey_no": d.invoice_value or rep.get("survey_job_no", "")}
        for label, val in (("ลักษณะความเสียหาย", loss_type), ("ประเภทรถ", d.prb_car_type),
                           ("จังหวัดที่เกิดเหตุ", d.acc_province)):
            if not str(val or "").strip() or str(val).strip() == "auto":
                blockers.append(f"{label} ว่าง — EMCS บังคับ บอทจะหยุดรอกรอกเองกลางทาง")
        if not str(d.driver_title or "").strip():
            warnings.append("ไม่มีคำนำหน้าผู้ขับขี่ (บอทจะลองอนุมานจากชื่อผู้เอาประกัน)")
    except Exception as e:
        blockers.append(f"ดึง report ของเคสไม่ได้ ({type(e).__name__}) — "
                        "ค่าไทยของ dropdown บังคับจะขาด")

    if counts["damage"] == 0:
        warnings.append("ไม่มีรายการความเสียหาย")
    return {"case_id": str(case_id), "counts": counts, "info": info,
            "blockers": blockers, "warnings": warnings,
            "ready": not blockers}, None


def _spec_options(dropdown_id: str) -> list:
    """ตัวเลือกจริงของ dropdown EMCS จาก runs/emcs_spec.json — [] ถ้ายังไม่ได้สกัดสเปก"""
    try:
        spec = json.loads(Path("runs/emcs_spec.json").read_text(encoding="utf-8"))
        for f in spec:
            opts = (f.get("dropdowns") or {}).get(dropdown_id)
            if opts:
                return [o["label"] for o in opts
                        if o.get("label") and not o["label"].startswith("-")]
    except Exception:
        pass
    return []


def check_isurvey_case(claim: str, invoice: str = ""):
    """ตรวจก่อนนำเข้า: เรื่องนี้บอทกรอกจนจบเองได้ไหม — คืน (ผลตรวจ, error)

    แยกเป็น 2 ระดับ (ตั้งใจให้ต่างกัน — ปนกันแล้วคนจะเลิกอ่าน):
    - **blockers** = บอทกรอกต่อไม่ได้ ต้องให้คนเลือกก่อน (ส่งตัวเลือกไปให้เลือกบนหน้าเว็บ
      แล้วส่งต่อเป็น --loss-type / --driver-title) ไม่ใช่ "ข้อมูลไม่ครบ" แต่คือ
      "ต้นทางไม่มีช่องนี้เลย" เช่นลักษณะความเสียหายของเคลมสด — ทุกใบจะติดเหมือนกันหมด
    - **warnings** = ช่องสำคัญที่ว่าง ให้คนดูก่อนกด แต่บอทยังเดินต่อได้
    """
    global _isv_client
    try:
        from autokey.config import load_config
        from autokey import emcs
        from autokey.isurvey_api import ISurveyAPI
        if _isv_client is None:
            _isv_client = ISurveyAPI(load_config())
            _isv_client.login()
        try:
            data = _isv_client.read_claim(claim, invoice, expect_claim=claim)
        except Exception:
            _isv_client.login()
            data = _isv_client.read_claim(claim, invoice, expect_claim=claim)
    except Exception as e:
        _isv_client = None
        return None, f"อ่านเคลม {claim} ไม่ได้: {type(e).__name__}: {e}"

    blockers = []
    # 1) ลักษณะความเสียหาย — ISURVEY ไม่มีช่องนี้ตรง ๆ แต่แปลงจาก 'ลักษณะการเกิดเหตุ'
    #    ได้ 34/58 รายการ; เคลมแห้ง (ไม่มีคู่กรณี) รู้จากโครงสร้างอยู่แล้ว
    _loss = emcs.resolve_loss_type(data, "auto")
    if not _loss:
        blockers.append({
            "field": "losstype", "label": "ลักษณะความเสียหาย",
            "why": f"แปลงจากลักษณะการเกิดเหตุ '{data.acc_type_desc or '(ว่าง)'}' ไม่ได้ "
                   "— คำนี้คละสาเหตุ หรือ EMCS ไม่มีตัวเลือกที่ตรงกัน จึงต้องเลือกเอง",
            "options": _spec_options("ddlLoss_ID"),
        })
    # 2) คำนำหน้าผู้ขับขี่ — ไล่หาจากคำนำหน้าในชื่อ/ผู้เอาประกัน/เพศ+อายุ
    #    บล็อกเฉพาะตอนหาไม่ได้เลย (ไม่มีคำนำหน้า + ไม่รู้เพศ) ซึ่งแทบไม่เกิด
    title, src = emcs._derive_insured_title(data)
    if not title:
        blockers.append({
            "field": "drivertitle", "label": "คำนำหน้าผู้ขับขี่",
            "why": "ISURVEY ไม่มีคำนำหน้าในชื่อผู้ขับขี่ ชื่อไม่ตรงผู้เอาประกัน "
                   "และไม่มีเพศให้อนุมาน",
            "options": _spec_options("ddlDri_Title_ID"),
        })

    v = data.validate()
    warnings = list(v.get("critical", []) + v.get("warnings", []))

    # ยอดค่าสำรวจเป็น 0 — งานสถานะ "จบงาน" ปกติต้องมียอดแล้ว (กติกา user)
    # ถ้าเป็น 0 แปลว่ายอดยังตามมาทีหลัง → นำเข้าตอนนี้บอทจะกรอกตารางราคาเป็น 0
    # ให้เตือน ไม่บล็อก (บางงานอาจไม่มีค่าสำรวจจริง ๆ — คนตัดสิน)
    # เจอจริง: เคลม 2026013160275 จบงานแล้วแต่ INS_*/SUR_* เป็น 0 ทั้งชุด
    _bill = data.bill or {}
    try:
        _net = float(str(_bill.get("total_net") or 0).replace(",", "").strip() or 0)
    except ValueError:
        _net = 0.0
    if _net <= 0:
        warnings.insert(0, "ยอดค่าสำรวจเป็น 0 — ถ้านำเข้าตอนนี้ตารางราคาใน EMCS "
                           "จะเป็น 0 ด้วย (รอยอดก่อน หรือกรอกเองภายหลัง)")

    # คำนำหน้าที่ได้จากการอนุมาน — กรอกให้ได้ แต่ต้องบอกคนตรวจว่าไม่ใช่ค่าจริง
    if title == "คุณ":
        warnings.insert(0, f"คำนำหน้าผู้ขับขี่ใช้ 'คุณ' เป็นค่ากลาง ({src}) — "
                           "ถ้ารู้ว่าเป็น นาง/นางสาว แก้ใน EMCS ตอนตรวจ")

    return {
        "claim": data.claim_value, "invoice": data.invoice_value,
        "plate": data.insure_plate, "car": f"{data.car_brand} {data.insure_model}".strip(),
        "insured": data.insure_name, "driver": f"{data.driver_name} {data.driver_surname}".strip(),
        "acc_result": data.acc_result,
        "counts": {"opponents": len(data.third_parties or []),
                   "injuries": len(data.injuries or []),
                   "assets": len(data.assets or []),
                   "damage": len(data.damage or [])},
        "bill_net": (data.bill or {}).get("total_net", ""),
        "bill_zero": _net <= 0,
        "blockers": blockers,
        "warnings": warnings,
        "ready": not blockers,
        "title_from": src,
    }, None


def fetch_sesurvey_xml(case_id: str):
    """ดึงไฟล์ XML (INSERT_SURV_REPORT_XML) ของเคสจาก se-survey — คืน (bytes, error)
    proxy ฝั่ง server แนบ token; เบราว์เซอร์ดาวน์โหลดผ่าน webui (same-origin) ไม่ต้องรู้ token
    ใช้เป็น 'ไฟล์สำรอง' ไป import EMCS เองตอนบอทใช้ไม่ได้"""
    url, token = _sesurvey_cfg()
    if not token:
        return None, "ยังไม่ได้ตั้ง SESURVEY_API_TOKEN ใน .env"
    try:
        req = urllib.request.Request(
            f"{url}/api/integrations/cases/{case_id}/export-xml",
            headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(), None
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return None, "token ไม่ถูกต้อง หรือ integration ยังไม่เปิดบน server"
        if e.code == 404:
            return None, "ไม่พบเคส หรือยังไม่มีรายงานสำรวจ"
        return None, f"server ตอบ {e.code}"
    except Exception as e:
        return None, f"เชื่อมต่อ se-survey ไม่ได้: {e}"


def resolve_case_ref(ref):
    """คืน (case_id, error): ตัวเลขล้วน = case id; อื่น = เลขเซอร์เวย์ (survey_job_no เช่น SETP-...) → หาใน list.
    ใช้กับ proxy XML ของ webui (ฝั่ง import ส่ง raw ให้ main.py resolve เอง)"""
    ref = str(ref or "").strip()
    if not ref:
        return None, "ไม่มีเลขเคส/เลขเซอร์เวย์"
    if ref.isdigit():
        return ref, None
    cases, err = fetch_sesurvey_cases()
    if err:
        return None, f"ค้นเลขเซอร์เวย์ไม่ได้: {err}"
    hits = [c for c in cases if str(c.get("survey_job_no") or "").strip().lower() == ref.lower()]
    if not hits:
        return None, f"ไม่พบเลขเซอร์เวย์ '{ref}' (ในเคสล่าสุด {len(cases)} รายการ)"
    if len(hits) > 1:
        return None, f"เลขเซอร์เวย์ '{ref}' ซ้ำหลายเคส — ใช้ case id แทน"
    return str(hits[0].get("id")), None


# marker ที่ main.py (ผ่าน browser.wait_for_manual_fill) พิมพ์ออก stdout
# เมื่อต้องการให้คนกรอกข้อมูลเอง — ต้องตรงกับค่าใน autokey/browser.py
MANUAL_MARKER = "@@MANUAL_FILL@@"
SUBMIT_MARKER = "@@READY_SUBMIT@@"   # ต้องตรงกับ autokey/browser.py (พร้อมส่งงาน)
SELECT_MARKER = "@@SELECT_IMAGES@@"  # ต้องตรงกับ autokey/browser.py (เลือกรูปอัปโหลด)
INJURY_MARKER = "@@INJURY_INPUTS@@"  # ต้องตรงกับ autokey/browser.py (กรอกข้อมูลผู้บาดเจ็บ)
SENT_MARKER = "@@JOB_SENT@@"         # ต้องตรงกับ autokey/browser.py (ส่งงาน+verify แล้ว)

# จำนวนงานที่รันพร้อมกันได้สูงสุด (กันเปิด Chrome เยอะเกินจนเครื่องค้าง)
MAX_CONCURRENT = int(os.environ.get("SE_MAX_CONCURRENT", "4") or "4")

# ---------------------------------------------------------------------------
# สถานะการรัน — เก็บได้หลายงานพร้อมกัน (keyed by run_id)
# run dict: {id, proc, lines[], status, returncode, cmd, title, pause}
#   status: running | waiting | done | error | stopped
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_runs = {}
_next_id = 0


def _parse_claims(text: str) -> list:
    """แยกเลขเคลมจากข้อความในช่อง — รองรับขึ้นบรรทัดใหม่/comma/เว้นวรรค
    ข้ามบรรทัดว่างและบรรทัดที่ขึ้นต้นด้วย # กันเลขซ้ำโดยรักษาลำดับ"""
    claims = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for tok in re.split(r"[,\s]+", line):
            tok = tok.strip()
            if tok:
                claims.append(tok)
    seen, out = set(), []
    for c in claims:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _build_cmd(params: dict):
    """แปลงค่าจากหน้าเว็บ → คำสั่ง python main.py ... (คืน (cmd, error))"""
    claims = _parse_claims(params.get("claims", ""))
    if not claims:
        return None, "ยังไม่ได้ใส่เลขเคลม"

    cmd = [sys.executable, "-u", "main.py"]

    if len(claims) == 1:
        cmd += ["--claim", claims[0]]
        invoice = (params.get("invoice") or "").strip()
        if invoice:
            cmd += ["--invoice", invoice]
    else:
        cmd += ["--claims", ",".join(claims)]

    severity = params.get("severity") or "เบา"
    if severity in ("เบา", "หนัก"):
        cmd += ["--severity", severity]

    if params.get("readonly"):
        cmd += ["--read-only"]
    if params.get("skipimages"):
        if params.get("imagesonly"):
            return None, "ติ๊ก 'อัปรูปอย่างเดียว' คู่กับ 'ไม่ยุ่งกับรูปภาพ' พร้อมกันไม่ได้"
        cmd += ["--skip-images"]
    if params.get("nosaveprice"):
        cmd += ["--no-save-price"]
    if params.get("forcenew"):
        cmd += ["--force-new"]
    # เรื่องมีอยู่แล้วบน EMCS (หน้าหลักบันทึกไปแล้ว ส่วนที่เหลือยังว่าง) → กรอกต่อบนเรื่องเดิม
    if params.get("fillexisting"):
        cmd += ["--fill-existing"]
    # เติม "เฉพาะรูป" เข้าเรื่องเดิม — ไม่แตะข้อมูลหน้าอื่น (ใช้ตอนกรอกครบแล้วแต่รูปยังไม่ขึ้น)
    if params.get("imagesonly"):
        cmd += ["--images-only"]
        if params.get("includemain"):
            cmd += ["--include-main-images"]
    # เลข e-Survey ใช้ร่วมกันทั้ง --fill-existing และ --images-only (เจาะจงเรื่องที่จะแก้)
    _es = (params.get("esurvey") or "").strip()
    if _es and (params.get("fillexisting") or params.get("imagesonly")):
        cmd += ["--esurvey", _es]
    if params.get("checklicense"):
        cmd += ["--check-license"]

    # ค่าที่ผู้ใช้เลือกจากแผง "🔍 ตรวจ" — ช่องที่ ISURVEY ไม่มีข้อมูลให้บอทเดา
    # ส่งมาแล้วบอทกรอกต่อได้จนจบ ไม่ต้องหยุดรอบนหน้า EMCS กลางทาง
    losstype = (params.get("losstype") or "").strip()
    if losstype:
        cmd += ["--loss-type", losstype]
    drivertitle = (params.get("drivertitle") or "").strip()
    if drivertitle:
        cmd += ["--driver-title", drivertitle]

    # โหมดนำเข้า XML: ให้ EMCS import ฟอร์มหลักจาก SURV_REPORT XML แทนกรอกเอง
    # (run_import_xml อ่านเคลมด้วย scrape เองเพื่อโหลด XML + คู่กรณีครบ) — ทำได้ทีละเคลม
    if params.get("importxml"):
        cmd += ["--import-xml"]

    # โหมดเคลมสด/นัดหมาย/ติดตาม
    # ⚠️ เหตุผลเดิมของ --scrape ("API อ่าน tab-4/5/6 ไม่ได้") ไม่จริงแล้วตั้งแต่ 2026-08-03
    # — API อ่านคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินครบทุกประเภทเคลม และอ่านได้ครบทุกรายการ
    # (scrape อ่านได้แค่รายการที่แสดงอยู่จนถึง 2026-08-04) จึงไม่ต้องบังคับ --scrape แล้ว
    # --allow-fresh กลายเป็น no-op (ถอดด่านเคลมแห้งแล้ว) คงไว้ให้เข้ากันได้กับของเดิม
    if params.get("claimmode") == "fresh":
        cmd += ["--allow-fresh"]

    # ไม่มี console ให้กด Enter — ต้องข้ามการหยุดถามเสมอ
    # (ปลอดภัย: การกรอก EMCS เป็นแค่บันทึก draft สคริปต์ไม่กด "ส่งงานใหม่")
    cmd += ["-y"]
    return cmd, None


def _title_from(params: dict) -> str:
    """ป้ายสั้นๆ ของงาน (โชว์บนหัวการ์ด) — เลขเคลมแรก + จำนวนที่เหลือ"""
    claims = _parse_claims(params.get("claims", ""))
    if not claims:
        return "(ไม่มีเลขเคลม)"
    if len(claims) == 1:
        return claims[0]
    return f"{claims[0]} +{len(claims) - 1} เคลม"


def _active_count() -> int:
    return sum(1 for r in _runs.values() if r["status"] in ("running", "waiting"))


def start_run(params: dict):
    """เริ่มงานใหม่จากฟอร์มหน้าเว็บ — คืน (run_id, error). เต็มขีดจำกัดจะคืน error"""
    cmd, err = _build_cmd(params)
    if err:
        return None, err
    title = _title_from(params)
    kind = "report" if params.get("mode") == "report" else "fill"
    claims = _parse_claims(params.get("claims", ""))
    return _spawn(cmd, title, kind, claims)


# โหมดงาน se-survey → (flag ของ main.py, ป้ายชื่อ) — import ต่อ --sesurvey-live ตาม live
_SESURVEY_MODES = {
    "import":        (None,                       "นำเข้า"),
    "fill-existing": ("--sesurvey-fill-existing",  "เติมส่วนที่ขาด"),
    "images-only":   ("--sesurvey-images-only",    "อัปรูปใหม่"),
    "injured-only":  ("--sesurvey-injured-only",   "กู้ผู้บาดเจ็บ"),
}


def start_sesurvey_run(params: dict):
    """เริ่มงานจากหน้า SE Survey — คืน (run_id, error)

    mode: import (ค่าเริ่มต้น) / fill-existing / images-only / injured-only
      - import + live=True → --sesurvey-live = นำเข้า EMCS จริง (สร้าง draft); live=False = dry-run
        (ดึง+ตรวจ XML+รูป แล้วหยุดก่อนแตะ EMCS)
      - โหมดกู้/ซ่อม (fill-existing/images-only/injured-only): เปิด draft เดิม (เคสต้อง import แล้ว)
        แตะ EMCS จริงเสมอ — ไม่มี dry-run
    ทุกโหมดยัง draft-only: บอทไม่กดส่งงาน.
    ⚠️ route บังคับ import+dry ถ้ามาจาก cross-origin (ปุ่ม inspector) — live/กู้ ได้เฉพาะหน้า operator ท้องถิ่น"""
    case_id = str(params.get("case_id", "")).strip()   # รับทั้ง case id + เลขเซอร์เวย์ (main.py resolve เอง)
    if not case_id:
        return None, "ไม่มีเลขเคส/เลขเซอร์เวย์"
    mode = str(params.get("mode", "import")).strip() or "import"
    if mode not in _SESURVEY_MODES:
        return None, f"mode ไม่ถูกต้อง: {mode}"
    claim_no = str(params.get("claim_no", "")).strip()
    live = bool(params.get("live"))
    cmd = [sys.executable, "-u", "main.py", "--sesurvey-case", case_id, "-y"]
    flag, label = _SESURVEY_MODES[mode]
    if flag:
        cmd.append(flag)
        tag = label
    else:  # import
        if live:
            cmd.append("--sesurvey-live")
        tag = "นำเข้าจริง" if live else "dry-run"
    title = f"SE-Survey #{case_id}" + (f" · {claim_no}" if claim_no else "") + f" ({tag})"
    return _spawn(cmd, title, "sesurvey", [claim_no] if claim_no else [])


def _spawn(cmd, title: str, kind: str, claims: list):
    """spawn subprocess ของ main.py + ลงทะเบียนการ์ดงาน (แกนร่วมของทุกทางเข้า)"""
    with _lock:
        if _active_count() >= MAX_CONCURRENT:
            return None, (f"มีงานกำลังรันอยู่ {MAX_CONCURRENT} งาน (เต็มขีดจำกัด) — "
                          f"รอให้บางงานเสร็จก่อน หรือเพิ่ม SE_MAX_CONCURRENT")
        global _next_id
        _next_id += 1
        run_id = _next_id
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["SE_WEBUI"] = "1"   # บอก main.py ว่ารันผ่านหน้าเว็บ (เปิดโหมดหยุด-รอ)
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(BASE),
                stdin=subprocess.PIPE,   # ใช้ส่งสัญญาณ "ดำเนินการต่อ" ให้ input() ใน main.py
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
            )
        except Exception as e:
            return None, f"เปิดโปรแกรมไม่สำเร็จ: {e}"

        _runs[run_id] = {
            "id": run_id, "proc": proc, "lines": [], "status": "running",
            "returncode": None, "cmd": " ".join(cmd), "title": title, "pause": None,
            "kind": kind, "claims": claims,
        }

    threading.Thread(target=_reader, args=(proc, run_id), daemon=True).start()
    return run_id, None


def _reader(proc, run_id: int):
    """อ่าน stdout ของ subprocess ทีละบรรทัดเก็บเข้า run['lines']
    ถ้าเจอบรรทัด marker = main.py ขอให้คนกรอกข้อมูลเอง → ตั้ง status=waiting"""
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith(MANUAL_MARKER):
                marker, kind = MANUAL_MARKER, "fill"
            elif line.startswith(SUBMIT_MARKER):
                marker, kind = SUBMIT_MARKER, "submit"
            elif line.startswith(SELECT_MARKER):
                marker, kind = SELECT_MARKER, "images"
            elif line.startswith(INJURY_MARKER):
                marker, kind = INJURY_MARKER, "injury"
            elif line.startswith(SENT_MARKER):
                # ส่งงานสำเร็จ + verify สถานะบน EMCS แล้ว → ให้การ์ดปิดตัวเองได้
                # (ไม่ใช่จุดหยุด จึงไม่แตะ status/pause) ข้อมูลอยู่ในสมุดงานแล้ว
                try:
                    info = json.loads(line[len(SENT_MARKER):])
                except Exception:
                    info = {}
                with _lock:
                    r = _runs.get(run_id)
                    if r is None:
                        break
                    r["sent"] = info or {"claim": ""}
                continue
            else:
                marker = None
            if marker:
                try:
                    info = json.loads(line[len(marker):])
                except Exception:
                    info = {}
                # kind=fill หยุดรอกรอกข้อมูล / submit พร้อมส่งงาน / images เลือกรูป
                info["kind"] = kind
                with _lock:
                    r = _runs.get(run_id)
                    if r is None:
                        break
                    r["status"] = "waiting"
                    r["pause"] = info
                continue  # ไม่ต้องโชว์บรรทัด marker ดิบใน log
            with _lock:
                r = _runs.get(run_id)
                if r is None:
                    break
                r["lines"].append(line)
    except Exception as e:
        with _lock:
            r = _runs.get(run_id)
            if r is not None:
                r["lines"].append(f"[webui] อ่าน log ผิดพลาด: {e}")
    finally:
        proc.wait()
        with _lock:
            r = _runs.get(run_id)
            if r is not None:
                r["returncode"] = proc.returncode
                if r["status"] in ("running", "waiting"):
                    r["status"] = "done" if proc.returncode == 0 else "error"
                r["pause"] = None


def stop_run(run_id: int):
    """สั่งหยุดงานที่ระบุ (Chrome ที่เปิดค้าง detach ไว้จะยังอยู่)"""
    with _lock:
        r = _runs.get(run_id)
        if r is None or r["status"] not in ("running", "waiting"):
            return False
        r["status"] = "stopped"
        r["pause"] = None
        proc = r["proc"]
    try:
        proc.terminate()
    except Exception:
        pass
    with _lock:
        r = _runs.get(run_id)
        if r is not None:
            r["lines"].append("[webui] ⏹ ผู้ใช้สั่งหยุดงาน")
    return True


def continue_run(run_id: int, payload=None):
    """ผู้ใช้สั่งให้ main.py ของงานนี้ทำงานต่อ (ปลด readline ที่ค้างอยู่)

    payload=None → ส่ง newline ธรรมดา (จุดหยุดกรอกข้อมูล)
    payload=dict → ส่ง JSON เข้า stdin: เลือกรูป {"selected":[...]} /
                   ส่งงาน {"submit":true,"base_type":..,"batch":..,"mix":[..]}"""
    with _lock:
        r = _runs.get(run_id)
        if r is None or r["status"] != "waiting":
            return False
        r["status"] = "running"
        r["pause"] = None
        proc = r["proc"]
    try:
        proc.stdin.write((json.dumps(payload, ensure_ascii=False)
                          if payload is not None else "") + "\n")
        proc.stdin.flush()
    except Exception:
        pass
    if isinstance(payload, dict) and "selected" in payload:
        msg = f"[webui] ⬆️ เลือกอัปโหลด {len(payload['selected'])} รูป"
    elif isinstance(payload, dict) and payload.get("submit"):
        wt = (payload.get("base_type") or "") + (" +งานรวม" if payload.get("batch") else "")
        msg = f"[webui] ✅ สั่งส่งงาน (ประเภทงาน: {wt})"
    else:
        msg = "[webui] ▶️ ผู้ใช้กดดำเนินการต่อ"
    with _lock:
        r = _runs.get(run_id)
        if r is not None:
            r["lines"].append(msg)
    return True


def forget_run(run_id: int):
    """ลบงานที่จบแล้วออกจากรายการ (ปิดการ์ด) — ห้ามลบงานที่ยังรันอยู่"""
    with _lock:
        r = _runs.get(run_id)
        if r is None:
            return False
        if r["status"] in ("running", "waiting"):
            return False
        del _runs[run_id]
    return True


def poll_state(offsets: dict) -> dict:
    """คืนสถานะทุกงาน + log บรรทัดใหม่ตั้งแต่ offset ที่ client รู้แล้วของแต่ละงาน
    offsets = {run_id(str): next_offset(int)}"""
    offsets = offsets or {}
    with _lock:
        runs_out = []
        for run_id in sorted(_runs.keys()):
            r = _runs[run_id]
            try:
                off = int(offsets.get(str(run_id), 0))
            except (TypeError, ValueError):
                off = 0
            lines = r["lines"]
            new = lines[off:] if 0 <= off <= len(lines) else lines
            runs_out.append({
                "id": run_id, "status": r["status"], "returncode": r["returncode"],
                "cmd": r["cmd"], "title": r["title"], "pause": r["pause"],
                "kind": r.get("kind", "fill"), "claims": r.get("claims", []),
                "sent": r.get("sent"),   # มีค่า = ส่งงาน+verify แล้ว (การ์ดปิดตัวเองได้)
                "lines": new, "next_offset": len(lines),
            })
        return {"runs": runs_out, "active": _active_count(), "max": MAX_CONCURRENT}


_IMG_CTYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def _img_ctype(name: str) -> str:
    return _IMG_CTYPES.get(Path(name).suffix.lower(), "application/octet-stream")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
# origin ของหน้าเว็บ se-survey ที่อนุญาตให้ยิงงานเข้ามา (ปุ่ม "นำเข้า EMCS" หน้า inspector)
_ALLOWED_ORIGINS = {
    "https://survey.sesurvey.cloud",
    "http://localhost:3000",      # dev
    "http://127.0.0.1:3000",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # เงียบ — ไม่ต้อง log ทุก request ออก console

    def _cors_origin(self):
        origin = self.headers.get("Origin", "")
        return origin if origin in _ALLOWED_ORIGINS else None

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")  # กัน browser ค้างหน้าเก่า
        origin = self._cors_origin()
        if origin:  # ให้หน้า se-survey (คนละ origin) อ่านคำตอบได้
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        """CORS preflight — เฉพาะ origin ในรายการอนุญาต"""
        origin = self._cors_origin()
        self.send_response(204 if origin else 403)
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0) or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def _id(self, params) -> int:
        try:
            return int(params.get("id"))
        except (TypeError, ValueError):
            return -1

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif u.path == "/image":
            self._serve_image(parse_qs(u.query))
        elif u.path == "/sesurvey-cases":
            cases, err = fetch_sesurvey_cases()
            if err:
                self._send(502, {"error": err})
            else:
                self._send(200, {"cases": cases})
        elif u.path == "/sesurvey-check":
            cid = ((parse_qs(u.query).get("case_id") or [""])[0]).strip()
            case_id, rerr = resolve_case_ref(cid)
            if rerr:
                self._send(400, {"error": rerr})
            else:
                res, err = check_sesurvey_case(case_id)
                self._send(502 if err else 200, {"error": err} if err else res)
        elif u.path == "/isurvey-check":
            q = parse_qs(u.query)
            claim = ((q.get("claim") or [""])[0]).strip()
            inv = ((q.get("invoice") or [""])[0]).strip()
            if not claim:
                self._send(400, {"error": "ไม่ได้ระบุเลขเคลม"})
            else:
                res, err = check_isurvey_case(claim, inv)
                self._send(502 if err else 200, {"error": err} if err else res)
        elif u.path == "/jobs":
            # สมุดงาน: เลขเคลม/เลขเซอร์เวย์ที่ทำไปแล้ว (ค้นด้วย q)
            q = parse_qs(u.query)
            from autokey import joblog
            try:
                limit = int((q.get("limit") or ["300"])[0])
            except ValueError:
                limit = 300
            self._send(200, {"jobs": joblog.read_jobs(
                limit=max(1, min(limit, 2000)), q=((q.get("q") or [""])[0]))})
        elif u.path == "/settings":
            from autokey import isurvey_report as _ir
            self._send(200, {"keyers": _ir.load_keyers(),
                             "file": str(_ir.KEYERS_FILE)})
        elif u.path == "/isurvey-cases":
            q = parse_qs(u.query)
            rows, err = fetch_isurvey_cases(
                date_from=((q.get("from") or [""])[0]).strip(),
                date_to=((q.get("to") or [""])[0]).strip())
            if err:
                self._send(502, {"error": err})
            else:
                self._send(200, {"cases": rows})
        elif u.path == "/sesurvey-xml":
            # ดาวน์โหลดไฟล์ XML สำรอง (proxy แนบ token) — ไป import EMCS เอง
            ref = ((parse_qs(u.query).get("case_id") or [""])[0]).strip()
            case_id, rerr = resolve_case_ref(ref)   # รับ case id / เลขเซอร์เวย์
            if rerr:
                self._send(400, {"error": rerr})
            else:
                data, err = fetch_sesurvey_xml(case_id)
                if err:
                    self._send(502, {"error": err})
                else:
                    self._send(200, data, "application/octet-stream")
        else:
            self._send(404, {"error": "not found"})

    def _serve_image(self, q):
        """ส่งไฟล์รูปของงานที่กำลังหยุดรอเลือกรูป (อ่านจากโฟลเดอร์ใน pause)
        ปลอดภัย: ยอมเฉพาะชื่อไฟล์ที่อยู่ในรายการ images ของ pause เท่านั้น"""
        try:
            run_id = int((q.get("id") or [""])[0])
        except (TypeError, ValueError):
            return self._send(400, {"error": "bad id"})
        name = (q.get("name") or [""])[0]
        with _lock:
            r = _runs.get(run_id)
            pause = dict(r["pause"]) if (r and r.get("pause")) else None
        if not pause or pause.get("kind") != "images":
            return self._send(404, {"error": "no image pause"})
        images = pause.get("images", [])
        names = {im.get("name") if isinstance(im, dict) else im for im in images}
        if (name not in names or "/" in name or "\\" in name or ".." in name):
            return self._send(404, {"error": "not allowed"})
        try:
            data = (Path(pause.get("folder", "")) / name).read_bytes()
        except Exception:
            return self._send(404, {"error": "not found"})
        self._send(200, data, _img_ctype(name))

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/poll":
            params = self._read_json()
            self._send(200, poll_state(params.get("offsets", {})))
        elif u.path == "/run":
            params = self._read_json()
            run_id, err = start_run(params)
            if err:
                self._send(409, {"error": err})
            else:
                self._send(200, {"run_id": run_id})
        elif u.path == "/stop":
            self._send(200, {"stopped": stop_run(self._id(self._read_json()))})
        elif u.path == "/continue":
            p = self._read_json()
            self._send(200,
                       {"continued": continue_run(self._id(p), p.get("payload"))})
        elif u.path == "/forget":
            self._send(200, {"forgot": forget_run(self._id(self._read_json()))})
        elif u.path == "/settings":
            # แก้ตารางคนคีย์จากหน้าเว็บ — เฉพาะหน้า operator ท้องถิ่นเท่านั้น
            if self._cors_origin() is not None:
                self._send(403, {"error": "แก้ตั้งค่าได้จากหน้า operator ในเครื่องเท่านั้น"})
                return
            from autokey import isurvey_report as _ir
            body = self._read_json() or {}
            table = body.get("keyers")
            if not isinstance(table, dict) or not table:
                self._send(400, {"error": "ไม่มีข้อมูลตารางคนคีย์ที่จะบันทึก"})
                return
            missing = [d for d in "0123456789" if not str(table.get(d, "")).strip()]
            if missing:
                self._send(400, {"error": "ต้องมีชื่อคนคีย์ครบทุกเลขท้าย — ยังว่าง: "
                                          + ", ".join(missing)})
                return
            try:
                _ir.save_keyers(table)
            except OSError as e:
                self._send(500, {"error": f"เขียนไฟล์ไม่ได้: {e}"})
                return
            self._send(200, {"keyers": _ir.load_keyers()})
        elif u.path == "/api/import-sesurvey":
            # ปุ่ม "นำเข้า EMCS": หน้า operator ท้องถิ่น (same-origin) หรือ inspector (cross-origin)
            params = self._read_json()
            # 🔒 live import + โหมดกู้/ซ่อม อนุญาตเฉพาะหน้า operator ท้องถิ่น — ถ้ามาจาก origin
            # ภายนอก (ปุ่ม inspector ที่ยังพักไว้) บังคับ import แบบ dry-run เสมอ กันเผลอแตะ EMCS
            if self._cors_origin() is not None:
                params["live"] = False
                params["mode"] = "import"
            run_id, err = start_sesurvey_run(params)
            if err:
                self._send(409, {"error": err})
            else:
                self._send(200, {"run_id": run_id})
        else:
            self._send(404, {"error": "not found"})


# ---------------------------------------------------------------------------
# หน้าเว็บ (HTML + CSS + JS ในไฟล์เดียว)
# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>se-autokey · นำเข้า EMCS</title>
<style>
  :root{
    --bg:#0f172a; --card:#ffffff; --ink:#0f172a; --muted:#64748b;
    --line:#e2e8f0; --brand:#4f46e5; --brand2:#6366f1;
    --ok:#16a34a; --warn:#d97706; --err:#dc2626; --skip:#0891b2;
  }
  *{box-sizing:border-box}
  body{
    margin:0; font-family:Tahoma,"Segoe UI",sans-serif; color:var(--ink);
    background:linear-gradient(160deg,#eef2ff,#f8fafc 40%); min-height:100vh;
  }
  .wrap{max-width:1240px; margin:0 auto; padding:24px 18px 48px}
  header{display:flex; align-items:center; gap:12px; margin-bottom:18px}
  .logo{width:42px;height:42px;border-radius:12px;flex:none;
    background:linear-gradient(135deg,var(--brand),var(--brand2));
    display:grid;place-items:center;color:#fff;font-weight:700;font-size:20px;
    box-shadow:0 6px 16px rgba(79,70,229,.35)}
  h1{font-size:20px;margin:0;line-height:1.2}
  .sub{color:var(--muted);font-size:13px;margin-top:2px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;
    padding:18px;box-shadow:0 8px 30px rgba(2,6,23,.06);margin-bottom:16px}
  label.fld{display:block;font-weight:600;font-size:13px;margin:0 0 6px}
  textarea,input[type=text],select{width:100%;border:1px solid var(--line);
    border-radius:10px;padding:11px 12px;font-size:15px;font-family:inherit;
    background:#fff;color:var(--ink);outline:none;transition:.15s}
  textarea{min-height:96px;resize:vertical;line-height:1.6;
    font-variant-numeric:tabular-nums;letter-spacing:.3px}
  textarea:focus,input:focus,select:focus{border-color:var(--brand2);
    box-shadow:0 0 0 3px rgba(99,102,241,.15)}
  .grid{display:grid;grid-template-columns:1fr 160px;gap:12px;margin-top:12px}
  .checks{display:flex;flex-wrap:wrap;gap:18px;margin-top:14px}
  .checks label{display:flex;align-items:center;gap:8px;font-size:14px;
    color:#334155;cursor:pointer;user-select:none}
  .checks input{width:17px;height:17px;accent-color:var(--brand)}
  .checks label.warn{color:#b45309;font-weight:600}
  .checks label.warn input{accent-color:#d97706}
  .actions{display:flex;align-items:center;gap:12px;margin-top:18px;flex-wrap:wrap}
  button{font-family:inherit;font-size:15px;font-weight:600;border:0;
    border-radius:10px;padding:11px 20px;cursor:pointer;transition:.15s}
  .run{background:var(--brand);color:#fff;box-shadow:0 6px 16px rgba(79,70,229,.3)}
  .run:hover{background:#4338ca}
  .run:disabled{background:#c7d2fe;box-shadow:none;cursor:not-allowed}
  .ghost{background:transparent;color:var(--muted);padding:8px 12px;font-size:13px}
  .ghost:hover{color:var(--ink)}
  .badge{font-size:13px;font-weight:600;padding:5px 12px;border-radius:999px;
    display:inline-flex;align-items:center;gap:7px}
  .badge.idle{background:#f1f5f9;color:#475569}
  .badge.running{background:#eef2ff;color:var(--brand)}
  .badge.done{background:#dcfce7;color:var(--ok)}
  .badge.error{background:#fee2e2;color:var(--err)}
  .badge.stopped{background:#fef3c7;color:var(--warn)}
  .badge.waiting{background:#fef3c7;color:var(--warn)}
  .dot{width:8px;height:8px;border-radius:50%;background:currentColor}
  .badge.running .dot,.badge.waiting .dot{animation:pulse 1s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
  .note{font-size:12.5px;color:var(--muted);margin-top:14px;line-height:1.7;
    border-top:1px dashed var(--line);padding-top:12px}
  .note b{color:#b45309}
  /* การ์ดงานแต่ละงาน */
  .run-card{background:#0b1020;border-radius:16px;overflow:hidden;margin-bottom:16px;
    box-shadow:0 8px 30px rgba(2,6,23,.12);animation:popin .25s ease}
  @keyframes popin{from{transform:translateY(-6px);opacity:0}to{transform:none;opacity:1}}
  .loghead{display:flex;align-items:center;justify-content:space-between;gap:10px;
    padding:10px 14px;background:#111834;color:#cbd5e1;font-size:13px;
    border-bottom:1px solid #1e293b}
  .run-title{display:flex;align-items:baseline;gap:8px;min-width:0}
  .run-title b{color:#e2e8f0;font-size:14px}
  .run-cmd{color:#64748b;font-size:11px;white-space:nowrap;overflow:hidden;
    text-overflow:ellipsis;max-width:280px}
  .loghead .right{display:flex;align-items:center;gap:8px;flex:none}
  .stopone{color:#fca5a5}
  .stopone:hover{color:#fee2e2}
  .closeone{color:#94a3b8}
  .closeone:hover{color:#fff}
  .continue.submitbtn{background:var(--ok)}
  .continue.submitbtn:hover{background:#15803d}
  .pausebox{display:flex;gap:14px;align-items:flex-start;background:#fffbeb;
    border:2px solid var(--warn);margin:12px 14px;border-radius:14px;padding:14px 16px;
    box-shadow:0 8px 24px rgba(217,119,6,.18);animation:popin .25s ease}
  .pause-ic{font-size:24px;line-height:1;animation:pulse 1.2s infinite}
  .pause-title{font-weight:700;font-size:15px;color:#92400e}
  .pause-title span{color:#b45309}
  .pause-reason{font-size:13px;color:#a16207;margin-top:3px;white-space:pre-wrap}
  .pause-hint{font-size:12.5px;color:#713f12;margin-top:8px;line-height:1.6}
  .continue{margin-top:11px;background:var(--warn);color:#fff;
    box-shadow:0 6px 16px rgba(217,119,6,.35)}
  .continue:hover{background:#b45309}
  /* แกลเลอรีเลือกรูปอัปโหลด */
  .pause-gallery{margin-top:10px;display:none}
  .gal-bar{display:flex;gap:10px;align-items:center;margin-bottom:8px;
    font-size:12.5px;color:#92400e;flex-wrap:wrap}
  .gal-bar .gal-count{font-weight:700}
  .gal-bar button{padding:4px 10px;font-size:12px;font-weight:600;border-radius:8px;
    background:#fde68a;color:#92400e;box-shadow:none}
  .gal-bar button:hover{background:#fcd34d}
  .gal-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(108px,1fr));
    gap:8px;max-height:340px;overflow:auto;padding:8px;background:#fff;
    border:1px solid #fde68a;border-radius:10px}
  .gal-head{grid-column:1/-1;display:flex;justify-content:space-between;align-items:center;
    gap:10px;margin-top:6px;padding:5px 9px;background:#fef3c7;border-radius:7px;
    font-size:12.5px;font-weight:700;color:#92400e}
  .gal-head:first-child{margin-top:0}
  .gal-headchk{font-weight:600;font-size:11.5px;display:flex;align-items:center;
    gap:5px;cursor:pointer;color:#a16207}
  .gal-headchk input{width:15px;height:15px;accent-color:var(--brand)}
  .gal-item{position:relative;display:block;cursor:pointer;border-radius:8px;
    overflow:hidden;border:1px solid var(--line);background:#f8fafc}
  .gal-item img{width:100%;height:84px;object-fit:cover;display:block;
    transition:.15s;background:#eef2f7}
  .gal-item input{position:absolute;top:5px;left:5px;width:19px;height:19px;
    accent-color:var(--brand);z-index:2;cursor:pointer}
  .gal-item input:not(:checked)~img{opacity:.3;filter:grayscale(1)}
  .gal-item input:checked~img{outline:2px solid var(--ok);outline-offset:-2px}
  .gal-name{display:block;font-size:10px;color:#475569;padding:3px 5px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  /* แผงเลือกประเภทงานตอนส่งงาน (ลอกจาก se-key extension) */
  .pause-worktype{margin-top:10px;padding:10px 12px;background:#fff;
    border:1px solid #fde68a;border-radius:10px}
  .pause-injury{margin-top:10px;display:flex;flex-direction:column;gap:8px}
  .pause-pick{margin-top:10px}
  .pause-pick .pick-sel{width:100%;max-width:380px;padding:6px 8px;font:inherit}
  .inj-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:8px 12px;
    background:#fff;border:1px solid #fde68a;border-radius:10px}
  .inj-name{font-weight:600;color:#334155;font-size:13.5px;min-width:160px}
  .inj-f{display:flex;align-items:center;gap:6px;font-size:13px;color:#475569}
  .inj-f select,.inj-f input{padding:5px 8px;border:1px solid #cbd5e1;border-radius:7px;
    font-size:13px}
  .inj-f input.inj-plate{width:130px}
  .wt-radios{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px}
  .wt-radios label,.wt-batch-lbl{display:flex;align-items:center;gap:6px;
    font-size:13.5px;color:#334155;cursor:pointer}
  .wt-radios input,.wt-batch-lbl input{width:16px;height:16px;accent-color:var(--brand)}
  .wt-batch-lbl{font-weight:600}
  .wt-batch-lbl input:disabled{cursor:not-allowed}
  .wt-mix{margin-top:8px;padding-top:8px;border-top:1px dashed #fde68a}
  .wt-mix-cap{font-size:12px;color:#92400e;font-weight:600;margin-bottom:6px}
  .wt-mix-list{display:flex;flex-direction:column;gap:6px;margin-bottom:6px}
  .wt-mix-input{width:100%;border:1px solid var(--line);border-radius:8px;
    padding:7px 10px;font-size:13px;font-family:inherit}
  .wt-mix-add{background:#fde68a;color:#92400e;padding:5px 12px;font-size:12.5px;
    border-radius:8px;box-shadow:none}
  [hidden]{display:none !important}
  .log{margin:0;padding:12px 16px;height:260px;overflow:auto;
    font-family:"Cascadia Mono","Consolas",monospace;font-size:13px;
    line-height:1.65;color:#cbd5e1;white-space:pre-wrap;word-break:break-word}
  .log .l-ok{color:#4ade80}
  .log .l-err{color:#f87171}
  .log .l-skip{color:#38bdf8}
  .log .l-warn{color:#fbbf24}
  .log .l-bar{color:#a5b4fc;font-weight:700}
  .log .l-time{color:#64748b}
  .emptyruns{text-align:center;color:#94a3b8;font-size:14px;padding:30px 10px;
    border:1px dashed var(--line);border-radius:16px;background:#fff}
  /* แท็บ + แดชบอร์ด 2 คอลัมน์ */
  .tabs{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
  .tab{background:#fff;border:1px solid var(--line);color:var(--muted);
    padding:10px 18px;border-radius:12px;font-size:14px;font-weight:600}
  .tab.active{background:var(--brand);color:#fff;border-color:var(--brand);
    box-shadow:0 6px 16px rgba(79,70,229,.25)}
  .tab:hover:not(.active){color:var(--ink);border-color:var(--brand2)}
  .dash{display:grid;grid-template-columns:minmax(360px,440px) 1fr;gap:16px;align-items:start}
  /* การ์ดเคสในรายการซ้าย */
  .case-item{border:1px solid var(--line);border-radius:12px;padding:11px 13px;
    margin-bottom:10px;background:#fff;transition:.15s}
  .case-item:hover{border-color:var(--brand2);box-shadow:0 4px 14px rgba(2,6,23,.06)}
  .case-sv{font-size:15px;font-weight:700;color:var(--ink);
    font-variant-numeric:tabular-nums;letter-spacing:.3px}
  .case-claim{font-size:12px;color:var(--muted);margin-top:1px}
  .case-meta{font-size:11.5px;color:#94a3b8;margin-top:2px}
  .case-btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
  .case-btns button{padding:6px 11px;font-size:12.5px;border-radius:8px;box-shadow:none}
  .caselist{max-height:70vh;overflow:auto}
  /* สมุดงาน + ตั้งค่า */
  .jobtbl{width:100%;border-collapse:collapse;font-size:12.5px}
  .jobtbl th{text-align:left;color:var(--muted);font-weight:600;padding:6px 8px;
    border-bottom:1px solid var(--line);position:sticky;top:0;background:#fff}
  .jobtbl td{padding:6px 8px;border-bottom:1px solid #f1f5f9;
    font-variant-numeric:tabular-nums}
  .jobtbl tr:hover td{background:#f8fafc}
  .ev{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700}
  .ev-sent{background:#dcfce7;color:#166534}
  .ev-draft{background:#fef3c7;color:#92400e}
  .keyrow{display:flex;align-items:center;gap:10px;margin-bottom:7px}
  .keyrow .dg{width:34px;height:34px;flex:none;border-radius:9px;background:#eef2ff;
    color:#3730a3;font-weight:800;display:flex;align-items:center;justify-content:center}
  .keyrow input{flex:1;max-width:320px}
  @media(max-width:900px){.dash{grid-template-columns:1fr}}
  @media(max-width:560px){.grid{grid-template-columns:1fr}.run-cmd{display:none}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">SE</div>
    <div>
      <h1>se-autokey · นำเข้า EMCS</h1>
      <div class="sub">รายการงานสำรวจจากแอปมือถือ → นำเข้า EMCS · ดูรายละเอียดการนำเข้าแบบเรียลไทม์</div>
    </div>
  </header>

  <div class="tabs">
    <button class="tab active" data-pane="sesurvey">📥 นำเข้า SE Survey</button>
    <button class="tab" data-pane="isurvey">🖊 กรอกเคลม ISURVEY</button>
    <button class="tab" data-pane="jobs">📚 สมุดงาน</button>
    <button class="tab" data-pane="settings">⚙ ตั้งค่า</button>
  </div>

  <div class="dash">
   <div class="col-left">
    <div class="tabpane" id="pane-sesurvey">
     <div class="card">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:4px">
        <h2 style="font-size:16px;margin:0">📥 งานสำรวจ (SE Survey)</h2>
        <button class="run" id="loadcasesbtn" style="margin-left:auto;padding:7px 12px;font-size:13px">↻ โหลดรายการ</button>
      </div>
      <div style="display:flex;gap:8px;align-items:flex-end;margin-top:10px;flex-wrap:wrap">
        <div style="flex:1;min-width:150px">
          <label class="fld" for="secase">เลขเคส / เลขเซอร์เวย์</label>
          <input type="text" id="secase" placeholder="เช่น 73 หรือ SETP-69060062">
        </div>
        <button class="run" id="serunbtn" style="padding:11px 14px">⚡ นำเข้า</button>
        <button class="run" id="sedrybtn" style="padding:11px 14px;background:#64748b" title="ดึง+ตรวจ XML+รูป แล้วหยุด ไม่แตะ EMCS">🧪 ทดสอบ</button>
      </div>
      <div id="setoolbar" hidden style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0 2px">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer">
          <input type="checkbox" id="seall"> เลือกทั้งหมด
        </label>
        <span id="secount" style="color:var(--muted);font-size:13px"></span>
        <button class="run" id="sechkall" style="margin-left:auto;padding:7px 12px;font-size:13px;background:#64748b">🔍 ตรวจที่เลือก</button>
        <button class="run" id="serunall" style="padding:7px 12px;font-size:13px">⚡ นำเข้าที่เลือก</button>
      </div>
      <div id="sequeue" hidden style="margin:8px 0;padding:8px 10px;border-radius:8px;background:#0f172a11;font-size:13px"></div>
      <div id="secasesbox" class="caselist" style="margin-top:12px"></div>
      <div class="note" style="margin-top:12px">
        • ต้องตั้ง <b>SESURVEY_API_TOKEN</b> ใน .env ให้ตรงกับ INTEGRATION_TOKEN ของ server<br>
        • <b>⚡ นำเข้า</b> = กรอกฟอร์ม + อัปรูป + บันทึก draft (บอท<b>ไม่กดส่งงาน</b>)<br>
        • <b>🧪 ทดสอบ</b> = dry-run: ดึง XML + โหลดรูป แล้วหยุด — ไม่แตะ EMCS
      </div>
     </div>
    </div>

    <div class="tabpane" id="pane-isurvey" hidden>
     <div class="card">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px">
        <h2 style="font-size:16px;margin:0">✅ งานจบแล้ว (ISURVEY)</h2>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;margin-left:auto">
          <input type="checkbox" id="isvhidesent" checked> ซ่อนที่นำเข้าแล้ว
        </label>
        <label class="fld" for="isvfrom" style="margin:0;font-weight:400">วันที่</label>
        <input type="date" id="isvfrom" style="width:145px;padding:6px 8px">
        <span style="color:var(--muted)">–</span>
        <input type="date" id="isvto" style="width:145px;padding:6px 8px">
        <button class="run" id="loadisvbtn" style="padding:7px 12px;font-size:13px">↻ โหลดรายการ</button>
      </div>
      <div id="isvtoolbar" hidden style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0 2px">
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer">
          <input type="checkbox" id="isvall"> เลือกทั้งหมด
        </label>
        <span id="isvcount" style="color:var(--muted);font-size:13px"></span>
        <button class="run" id="isvchkall" style="margin-left:auto;padding:7px 12px;font-size:13px;background:#64748b">🔍 ตรวจที่เลือก</button>
        <button class="run" id="isvrunall" style="padding:7px 12px;font-size:13px">⚡ นำเข้าที่เลือก</button>
      </div>
      <div id="isvsummary" style="color:var(--muted);font-size:12.5px;margin:2px 0 6px"></div>
      <div id="isvqueue" hidden style="margin:8px 0;padding:8px 10px;border-radius:8px;background:#0f172a11;font-size:13px"></div>
      <div id="isvcasesbox" class="caselist"></div>
      <div class="note" style="margin:10px 0 18px">
        • แสดงเฉพาะสถานะ <b>“จบงาน”</b> — เรียงงานที่เสร็จล่าสุดขึ้นก่อน<br>
        • <b>“✓ นำเข้าแล้ว”</b> อ่านจาก <code>EMCSstatus</code> ของ ISURVEY เอง (ใครนำเข้า/เมื่อไหร่) — บอทไม่ได้เขียนค่านี้<br>
        • กด <b>⚡ นำเข้า</b> = เติมเลขเคลม+เลขเซอร์เวย์ลงฟอร์มด้านล่างแล้วรันด้วยตัวเลือกที่ตั้งไว้
      </div>

      <h2 style="font-size:16px;margin:0 0 12px">🖊 กรอกเคลมอัตโนมัติ (ISURVEY)</h2>
      <label class="fld" for="claims">เลขเคลม <span style="color:var(--muted);font-weight:400">(หลายเคลมได้ — บรรทัดละเลข)</span></label>
      <textarea id="claims"></textarea>

      <div class="grid">
        <div>
          <label class="fld" for="invoice">เลขเซอร์เวย์ <span style="color:var(--muted);font-weight:400">(ใส่เมื่อค้นเจอหลายแถว — เฉพาะกรณีเคลมเดียว)</span></label>
          <input type="text" id="invoice">
        </div>
        <div>
          <label class="fld" for="severity">ความเสียหาย</label>
          <select id="severity">
            <option value="เบา">เบา</option>
            <option value="หนัก">หนัก</option>
          </select>
        </div>
      </div>

      <div style="margin-top:12px">
        <label class="fld" for="claimmode">ประเภทเคลมที่จะกรอก</label>
        <select id="claimmode">
          <option value="dry">เคลมแห้งเท่านั้น (ปลอดภัย — ค่าเริ่มต้น)</option>
          <option value="fresh">รวมเคลมสด / นัดหมาย / ติดตาม</option>
        </select>
        <div id="cmnote" hidden style="margin-top:8px;padding:8px 10px;background:#fff7ed;
             border:1px solid #fdba74;border-radius:8px;font-size:12.5px;color:#9a3412;line-height:1.55">
          ⚠️ <b>โหมดเคลมสด</b>: อ่านด้วย scrape (ช้ากว่า API) เพื่อดึงคู่กรณีจาก XML — ระบบกรอก
          <b>หน้าหลัก + คู่กรณี + ราคา</b> ให้ แต่ <b>ผู้บาดเจ็บ และ ทรัพย์สิน ต้องกรอกเอง</b>
          บน EMCS ก่อนส่ง (ตรวจให้ครบ)
        </div>
      </div>

      <div class="checks">
        <label><input type="checkbox" id="readonly"> อ่านอย่างเดียว (ไม่กรอก EMCS)</label>
        <label><input type="checkbox" id="skipimages"> ไม่ยุ่งกับรูปภาพ</label>
        <label><input type="checkbox" id="nosaveprice"> ไม่บันทึกราคา (ทดสอบ — กรอกถึงหน้าค่าใช้จ่ายแต่ไม่กดเซฟราคา)</label>
        <label class="warn"><input type="checkbox" id="forcenew"> ⚠️ สร้างเรื่องใหม่แม้มีเรื่องเดิม (--force-new) — draft ลบไม่ได้ ยกเลิกได้อย่างเดียว</label>
        <label><input type="checkbox" id="importxml"> นำเข้าด้วย XML (import) — ให้ EMCS เติมฟอร์มหลักจากไฟล์ แล้วบอทอุดช่องว่าง (ความเสียหายลงได้ 20 ช่อง เหมาะกับ >8 ชิ้น) · ทำทีละเคลม</label>
        <label><input type="checkbox" id="checklicense"> ตรวจใบขับขี่ผู้เอาประกัน — OCR หา+อ่านรูปใบขับขี่ในชุดรูป (เลขที่/เลขบัตร/ชื่อ) แล้วเทียบกับข้อมูลเคลม · ช้าลงเล็กน้อย</label>
      </div>

      <div class="checks" style="margin-top:2px">
        <label><input type="checkbox" id="fillexisting"> กรอกต่อบน "เรื่องเดิม" — เปิด draft ที่มีอยู่แล้ว กด "แก้ไข" แล้วกรอกส่วนที่ยังว่าง (ไม่สร้างเรื่องใหม่)</label>
        <label><input type="checkbox" id="imagesonly"> อัปเฉพาะ "รูป" เข้าเรื่องเดิม — ไม่แตะข้อมูลหน้าอื่น (ใช้ตอนกรอกครบแล้วแต่รูปยังไม่ขึ้น) · มีหน้าให้ติ๊กเลือกรูปก่อนอัป</label>
        <label><input type="checkbox" id="includemain"> ↳ รวมรูปรถประกันด้วย (ไม่ติ๊ก = อัปเฉพาะรูปรถคู่กรณี กันอัปซ้ำที่อัปไปแล้ว)</label>
        <div style="margin-top:6px">
          <label class="fld" for="esurvey">เลข e-Survey ของเรื่องเดิม (เว้นว่าง = เลือก draft ให้อัตโนมัติ)</label>
          <input id="esurvey" placeholder="S68426080794" style="max-width:260px">
        </div>
      </div>

      <div class="actions">
        <button class="run" id="runbtn">▶ รันโปรแกรม</button>
      </div>

      <div class="note">
        • รันพร้อมกันได้ — แต่ละงานเปิดหน้าต่าง Chrome แยกกัน (ปรับเพดานด้วย SE_MAX_CONCURRENT)<br>
        • หน้าต่าง Chrome จะเปิดขึ้นเองให้เห็นการทำงาน — กรอกเสร็จระบบ <b>บันทึกเป็น draft</b> แล้ว <b>หยุดรอให้ตรวจ</b><br>
        • ก่อนอัปโหลดรูป ระบบจะโชว์รูปให้ <b>เลือกเฉพาะรูปที่จะนำเข้า EMCS</b> (ติ๊กเฉพาะที่ต้องการ)<br>
        • ตรวจ draft บน Chrome แล้วกดปุ่ม <b>"✅ ส่งงาน + แจ้ง ISURVEY"</b> — ระบบจะกด "ส่งงานใหม่" ให้ + แจ้งกลับ ISURVEY<br>
        • ระบบ <b>ไม่กดส่งงานเอง</b> จนกว่าคุณจะสั่งผ่านปุ่ม (ถ้าไม่กด = เก็บเป็น draft)<br>
        • เคลมที่ไม่ใช่เคลมแห้ง หรือมีเรื่องใน EMCS อยู่แล้ว จะถูกข้ามพร้อมบอกเหตุผล
      </div>
     </div>
    </div>

    <div class="tabpane" id="pane-jobs" hidden>
     <div class="card">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">
       <b style="font-size:15px">📚 สมุดงาน</b>
       <span style="color:var(--muted);font-size:12.5px">เลขเคลม/เลขเซอร์เวย์ที่ทำไปแล้ว</span>
       <button id="jobsreload" class="ghost" style="margin-left:auto">↻ โหลดใหม่</button>
      </div>
      <input id="jobsq" placeholder="ค้นเลขเคลม / เลขเซอร์เวย์ / e-Survey / ชื่อคนคีย์">
      <div id="jobsbox" class="caselist" style="margin-top:10px">
        <div style="color:var(--muted);font-size:13px;padding:8px 0">กำลังโหลด…</div>
      </div>
      <div class="note" style="margin-top:10px">
       • บันทึกอัตโนมัติ 2 จังหวะ: <b>draft</b> = กรอกครบแล้ว · <b>ส่งแล้ว</b> = กด "ส่งงานใหม่" + ตรวจสถานะบน EMCS ผ่าน<br>
       • เก็บถาวรที่ <code>runs/jobs.jsonl</code> — ไม่หายตอนปิดการ์ด/รีสตาร์ตโปรแกรม
      </div>
     </div>
    </div>

    <div class="tabpane" id="pane-settings" hidden>
     <div class="card">
      <b style="font-size:15px">⚙ คนคีย์ตามเลขท้ายเลขเคลม</b>
      <div style="color:var(--muted);font-size:12.5px;margin:4px 0 12px">
       ชื่อนี้ถูกส่งไปกับการแจ้ง ISURVEY (ช่อง EMCSby) ตอนกด "ส่งงาน" — บอทดูเลขท้ายของเลขเคลมอย่างเดียว ไม่ได้ดูว่าใครนั่งกด
      </div>
      <div id="keyersbox"><div style="color:var(--muted);font-size:13px">กำลังโหลด…</div></div>
      <div class="actions" style="margin-top:6px">
       <button class="run" id="savekeyers">💾 บันทึกตั้งค่า</button>
       <button class="ghost" id="reloadkeyers">↻ ยกเลิกการแก้</button>
      </div>
      <div id="keyersmsg" style="font-size:12.5px;margin-top:8px"></div>
      <div class="note" style="margin-top:10px">
       • บันทึกแล้วมีผลกับงานถัดไปทันที ไม่ต้องรีสตาร์ตโปรแกรม<br>
       • เก็บที่ <code id="keyersfile">settings/keyers.json</code> — ไฟล์หาย/พัง ระบบถอยไปใช้ค่าเดิมในโค้ดเอง งานไม่ล้ม<br>
       • ต้องมีชื่อครบทั้ง 10 เลขท้าย ไม่งั้นบันทึกไม่ผ่าน (บอทจะไม่ยิงแจ้ง ISURVEY ถ้าหาคนคีย์ไม่ได้)
      </div>
     </div>
    </div>
   </div>

   <div class="col-main">
     <h2 style="font-size:16px;margin:0 0 12px">📋 รายละเอียดการนำเข้า EMCS <span class="badge idle" id="capbadge" style="margin-left:8px;vertical-align:middle">กำลังรัน 0/4</span></h2>
     <div id="runs"></div>
     <div class="emptyruns" id="emptyruns">ยังไม่มีงาน — เลือกงานจากรายการซ้าย แล้วกด "นำเข้า"</div>
   </div>
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const runBtn = $("#runbtn"), runsEl = $("#runs"), emptyEl = $("#emptyruns");
const capBadge = $("#capbadge");
const offsets = {};   // id -> next_offset ที่ client รู้แล้ว
const cards = {};     // id -> refs ของการ์ด

const STATUS = {
  running: ["running","กำลังทำงาน…"],
  waiting: ["waiting","รอกรอกข้อมูล"],
  done:    ["done","เสร็จแล้ว ✅"],
  error:   ["error","ผิดพลาด ❌"],
  stopped: ["stopped","หยุดแล้ว"],
  idle:    ["idle","-"],
};
function classify(line){
  if (line.includes("===")) return "l-bar";
  if (line.includes("❌") || /ล้มเหลว|ผิดพลาด|error|Error|Traceback/.test(line)) return "l-err";
  if (line.includes("✅")) return "l-ok";
  if (line.includes("⏭")) return "l-skip";
  if (line.includes("⚠")) return "l-warn";
  return "";
}
function appendLines(c, lines){
  if (!lines || !lines.length) return;
  const nearBottom = c.logEl.scrollHeight - c.logEl.scrollTop - c.logEl.clientHeight < 60;
  const frag = document.createDocumentFragment();
  for (const ln of lines){
    const div = document.createElement("div");
    const cls = classify(ln);
    if (cls) div.className = cls;
    const m = ln.match(/^(\[\d\d:\d\d:\d\d\]\s)([\s\S]*)$/);
    if (m && !cls){
      const t = document.createElement("span"); t.className="l-time"; t.textContent=m[1];
      div.appendChild(t); div.appendChild(document.createTextNode(m[2]));
    } else {
      div.textContent = ln || " ";
    }
    frag.appendChild(div);
  }
  c.logEl.appendChild(frag);
  if (nearBottom) c.logEl.scrollTop = c.logEl.scrollHeight;
}
async function postJSON(url, body){
  const r = await fetch(url, {method:"POST",
    headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
  return {ok:r.ok, data: await r.json()};
}
function escHtml(s){return String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function escAttr(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
const CAT_LABELS = {INS:"🚗 รูปรถประกัน", REPORTS:"📄 เอกสาร/ใบรับงาน",
  OTHERS:"📎 อื่นๆ", TP_VEH:"🚙 รูปคู่กรณี"};
function imgsSig(imgs){ return imgs.map(x => (x.name||x)+":"+(x.cat||"")).join("|"); }
function updateGalCount(c){
  if (!c.galEl) return;
  const items = [...c.galEl.querySelectorAll("input[data-name]")];
  const n = items.filter(x => x.checked).length;
  if (c.galCount) c.galCount.textContent = "เลือก " + n + "/" + items.length + " รูป";
  if (c.contBtn.dataset.kind === "images")
    c.contBtn.textContent = "⬆️ อัปโหลดรูปที่เลือก (" + n + ")";
  c.galEl.querySelectorAll(".gal-cat-all").forEach(h => {
    const box = [...c.galEl.querySelectorAll('input[data-name][data-cat="' + h.dataset.cat + '"]')];
    h.checked = box.length > 0 && box.every(x => x.checked);
  });
}
function setAllChecks(c, on){
  c.galEl.querySelectorAll("input[type=checkbox]").forEach(x => x.checked = on);
  updateGalCount(c);
}
function buildGallery(c, r){
  const imgs = (r.pause && r.pause.images) || [];
  const order = ["INS","REPORTS","OTHERS","TP_VEH"];
  const groups = {};
  for (const it of imgs){
    const name = (it && it.name) || it;
    const cat = (it && it.cat) || "OTHERS";
    (groups[cat] = groups[cat] || []).push(name);
  }
  const cats = Object.keys(groups).sort((a,b) =>
    (order.indexOf(a)<0?99:order.indexOf(a)) - (order.indexOf(b)<0?99:order.indexOf(b)));
  let html = "";
  for (const cat of cats){
    const names = groups[cat];
    html += '<div class="gal-head"><span>' + escHtml(CAT_LABELS[cat] || cat)
      + ' (' + names.length + ')</span>'
      + '<label class="gal-headchk"><input type="checkbox" class="gal-cat-all" checked data-cat="'
      + escAttr(cat) + '"> เลือกทั้งหมวด</label></div>';
    for (const name of names){
      html += '<label class="gal-item"><input type="checkbox" checked data-name="'
        + escAttr(name) + '" data-cat="' + escAttr(cat) + '">'
        + '<img loading="lazy" src="/image?id=' + r.id + '&name='
        + encodeURIComponent(name) + '" alt="">'
        + '<span class="gal-name" title="' + escAttr(name) + '">' + escHtml(name)
        + '</span></label>';
    }
  }
  c.galEl.innerHTML = html;
  c.galSig = imgsSig(imgs);
  updateGalCount(c);
}
function selectedBase(c){
  for (const rd of c.wtRadios) if (rd.checked) return rd.value;
  return "งานต้น";
}
function applyWtState(c){
  const sesv = (selectedBase(c) === "SESV");
  if (sesv) c.wtBatch.checked = true;      // SESV ล็อกคู่ "งานรวม" เสมอ
  c.wtBatch.disabled = sesv;
  c.wtBatch.title = sesv ? "SESV ต้องใช้คู่กับงานรวมเสมอ" : "";
  c.wtMix.hidden = !c.wtBatch.checked;
}
function buildWorkType(c, r){
  const base = (r.pause && r.pause.base_type) || "งานต้น";
  c.wtRadios.forEach(rd => { rd.name = "wt-base-" + r.id; rd.checked = (rd.value === base); });
  c.wtMixList.innerHTML =
    '<input type="text" class="wt-mix-input" placeholder="SEABI-... (เลข invoice)">';
  applyWtState(c);
}
function buildPicker(c, options){
  // ตัวเลือกมาจาก dropdown จริงบนหน้า EMCS ตอนนั้น (ไม่ใช่จากสเปกที่ดัมป์ไว้)
  if (!options.length){ c.pickWrap.hidden = true; c.pickWrap.innerHTML = ""; return; }
  c.pickWrap.innerHTML = '<select class="pick-sel"><option value="">— เลือกค่า —</option>'
    + options.map(o => '<option>' + escHtml(o) + '</option>').join("") + '</select>';
  c.pickWrap.hidden = false;
}
function buildInjuryForm(c, r){
  const persons = (r.pause && r.pause.persons) || [];
  const opts = (r.pause && r.pause.person_type_options) || [];
  let html = "";
  persons.forEach((p, i) => {
    const sel = opts.map(o => '<option value="' + escAttr(o.value) + '"'
      + (o.value === p.person_type_value ? ' selected' : '') + '>'
      + escHtml(o.label) + '</option>').join("");
    html += '<div class="inj-row">'
      + '<span class="inj-name">' + (i+1) + '. ' + escHtml(p.name || "ผู้บาดเจ็บ") + '</span>'
      + '<label class="inj-f">ประเภท: <select class="inj-type">' + sel + '</select></label>'
      + '<label class="inj-f">เลขทะเบียน: <input type="text" class="inj-plate" '
      + 'placeholder="เว้นว่าง = เติมอัตโนมัติ" value="' + escAttr(p.car_regno || "") + '"></label>'
      + '</div>';
  });
  c.injWrap.innerHTML = html;
  c.injSig = JSON.stringify(persons.map(p => p.name));
}
function makeCard(r){
  const root = document.createElement("div");
  root.className = "run-card"; root.dataset.id = r.id;
  root.innerHTML =
    '<div class="loghead">'
    + '<span class="run-title">📋 <b></b> <span class="run-cmd"></span></span>'
    + '<span class="right">'
    +   '<span class="badge running"><span class="dot"></span><span class="st"></span></span>'
    +   '<button class="ghost stopone">■ หยุด</button>'
    +   '<button class="ghost closeone" hidden>✕ ปิด</button>'
    + '</span></div>'
    + '<div class="pausebox" hidden>'
    +   '<div class="pause-ic">⏸️</div>'
    +   '<div class="pause-body" style="flex:1;min-width:0">'
    +     '<div class="pause-title"></div>'
    +     '<div class="pause-reason" hidden></div>'
    +     '<div class="pause-hint"></div>'
    +     '<div class="pause-worktype" hidden>'
    +       '<div class="wt-radios">'
    +         '<label><input type="radio" class="wt-base" value="งานต้น"> งานต้น</label>'
    +         '<label><input type="radio" class="wt-base" value="งานตาม"> งานตาม</label>'
    +         '<label><input type="radio" class="wt-base" value="SESV"> SESV</label>'
    +       '</div>'
    +       '<label class="wt-batch-lbl"><input type="checkbox" class="wt-batch"> งานรวม (หลาย invoice)</label>'
    +       '<div class="wt-mix" hidden>'
    +         '<div class="wt-mix-cap">เลข invoice (SEABI) ของงานรวม:</div>'
    +         '<div class="wt-mix-list"></div>'
    +         '<button type="button" class="wt-mix-add">+ เพิ่มเลข invoice</button>'
    +       '</div>'
    +     '</div>'
    +     '<div class="pause-gallery">'
    +       '<div class="gal-bar"><span class="gal-count"></span>'
    +         '<button type="button" class="gal-all">เลือกทั้งหมด</button>'
    +         '<button type="button" class="gal-none">ไม่เลือกเลย</button></div>'
    +       '<div class="gal-grid"></div>'
    +     '</div>'
    +     '<div class="pause-injury" hidden></div>'
    +     '<div class="pause-pick" hidden></div>'
    +     '<button class="continue"></button>'
    +   '</div>'
    + '</div>'
    + '<div class="log"></div>';
  root.querySelector(".run-title b").textContent = r.title || ("งาน #" + r.id);
  root.querySelector(".run-cmd").textContent = r.cmd || "";
  const c = {
    root, logEl: root.querySelector(".log"),
    badgeEl: root.querySelector(".badge"), stEl: root.querySelector(".st"),
    pauseEl: root.querySelector(".pausebox"),
    ptitle: root.querySelector(".pause-title"), phint: root.querySelector(".pause-hint"),
    preason: root.querySelector(".pause-reason"),
    stopBtn: root.querySelector(".stopone"), closeBtn: root.querySelector(".closeone"),
    contBtn: root.querySelector(".continue"),
    galWrap: root.querySelector(".pause-gallery"), galEl: root.querySelector(".gal-grid"),
    galCount: root.querySelector(".gal-count"),
    galAll: root.querySelector(".gal-all"), galNone: root.querySelector(".gal-none"),
    galSig: null,
    wtWrap: root.querySelector(".pause-worktype"),
    wtRadios: root.querySelectorAll(".wt-base"), wtBatch: root.querySelector(".wt-batch"),
    wtMix: root.querySelector(".wt-mix"), wtMixList: root.querySelector(".wt-mix-list"),
    wtMixAdd: root.querySelector(".wt-mix-add"), wtSig: null,
    injWrap: root.querySelector(".pause-injury"), injSig: null,
    pickWrap: root.querySelector(".pause-pick"), pickSig: null,
  };
  c.stopBtn.addEventListener("click", async () => {
    if (!confirm("ต้องการหยุดงาน " + (r.title || ("#"+r.id)) + " ?")) return;
    c.stopBtn.disabled = true;
    try{ await postJSON("/stop", {id:r.id}); }catch(e){}
  });
  c.closeBtn.addEventListener("click", async () => {
    try{ await postJSON("/forget", {id:r.id}); }catch(e){}
    removeCard(r.id);
  });
  c.contBtn.addEventListener("click", async () => {
    const kind = c.contBtn.dataset.kind;
    const body = {id:r.id};
    if (kind === "images"){
      body.payload = {selected: [...c.galEl.querySelectorAll("input[data-name]:checked")]
        .map(x => x.dataset.name)};
    } else if (kind === "submit"){
      const base = selectedBase(c);
      const batch = c.wtBatch.checked;
      const mix = [...c.wtMixList.querySelectorAll(".wt-mix-input")]
        .map(x => x.value.trim()).filter(Boolean);
      if ((batch || base === "SESV") && mix.length === 0){
        alert("งานรวม/SESV ต้องกรอกเลข invoice (SEABI) อย่างน้อย 1 เลข"); return;
      }
      if (!confirm("ตรวจ draft + ประเภทงานเรียบร้อยแล้วใช่ไหม?\n\nระบบจะกดส่งงานใน EMCS "
                   + "(ส่งงานใหม่ หรือ ส่งผลงานต่อเนื่อง ตามสถานะเรื่อง — ส่งงานจริง) "
                   + "+ แจ้ง ISURVEY + บันทึก se-key — ย้อนกลับไม่ได้")) return;
      body.payload = {submit:true, base_type:base, batch:batch, mix:mix};
    } else if (kind === "injury"){
      // เลขทะเบียนไม่บังคับ — EMCS เติมจากประเภทอัตโนมัติ (รถประกัน/คู่กรณี);
      // กรอกเองเฉพาะ 'บุคคลภายนอกรถ' หรือต้องการ override
      const rows = [...c.injWrap.querySelectorAll(".inj-row")];
      body.payload = {persons: rows.map(row => ({
        person_type: row.querySelector(".inj-type").value,
        car_regno: row.querySelector(".inj-plate").value.trim()}))};
    } else {
      // เลือกค่าจาก dropdown ในหน้าเว็บ → ส่งให้บอทกรอกลงช่องเอง
      // ไม่ได้เลือก = คนไปกรอกบนหน้า EMCS เองแล้ว (พฤติกรรมเดิม)
      const sel = c.pickWrap.querySelector(".pick-sel");
      const val = sel ? sel.value.trim() : "";
      if (val) body.payload = {choice: val};
    }
    c.contBtn.disabled = true;
    try{ await postJSON("/continue", body); }catch(e){}
    c.pauseEl.hidden = true;
    c.galWrap.style.display = "none"; c.galSig = null;
    c.wtWrap.hidden = true; c.wtSig = null;
    c.injWrap.hidden = true; c.injSig = null;
    c.pickWrap.hidden = true; c.pickSig = null;
  });
  c.galAll.addEventListener("click", () => setAllChecks(c, true));
  c.galNone.addEventListener("click", () => setAllChecks(c, false));
  c.galEl.addEventListener("change", (e) => {
    const t = e.target;
    if (t && t.classList.contains("gal-cat-all")){
      c.galEl.querySelectorAll('input[data-name][data-cat="' + t.dataset.cat + '"]')
        .forEach(x => x.checked = t.checked);
    }
    updateGalCount(c);
  });
  c.wtRadios.forEach(rd => rd.addEventListener("change", () => applyWtState(c)));
  c.wtBatch.addEventListener("change", () => { c.wtMix.hidden = !c.wtBatch.checked; });
  c.wtMixAdd.addEventListener("click", () => {
    const i = document.createElement("input");
    i.type = "text"; i.className = "wt-mix-input"; i.placeholder = "SEABI-...";
    c.wtMixList.appendChild(i); i.focus();
  });
  runsEl.prepend(root);   // งานใหม่อยู่บนสุด
  cards[r.id] = c;
  return c;
}
function removeCard(id){
  const c = cards[id];
  if (c){ c.root.remove(); delete cards[id]; }
  delete offsets[id];
  updateEmpty();
}
function updateEmpty(){ emptyEl.hidden = Object.keys(cards).length > 0; }
function renderRun(r){
  const c = cards[r.id] || makeCard(r);
  appendLines(c, r.lines);
  offsets[r.id] = r.next_offset;
  // กำลังนับถอยหลังปิดตัวเองอยู่ — ห้าม poll วาดสถานะทับ (ไม่งั้นข้อความกะพริบ
  // สลับ "ปิดใน N วิ" กับ "เสร็จแล้ว ✅" ทุกรอบ poll)
  if (c.autoClose) return;
  const [cls,txt] = STATUS[r.status] || STATUS.idle;
  c.badgeEl.className = "badge " + cls;
  c.stEl.textContent = txt;
  const active = (r.status === "running" || r.status === "waiting");
  c.stopBtn.hidden = !active;
  c.closeBtn.hidden = active;
  // ส่งงานสำเร็จ + บอท verify สถานะบน EMCS แล้ว → ปิดการ์ดให้เอง ไม่ต้องมากดทีละใบ
  // (ไม่รีบปิดทันที เผื่ออ่าน 3 บรรทัดสุดท้าย; ข้อมูลอยู่ใน 📚 สมุดงาน ถาวรอยู่แล้ว)
  if (r.sent && !active && !c.autoClose){
    c.autoClose = true;
    let left = 8;
    c.stEl.textContent = "ส่งแล้ว ✓ · ปิดใน " + left + " วิ";
    const tick = setInterval(async () => {
      left -= 1;
      if (!cards[r.id]){ clearInterval(tick); return; }
      if (left > 0){ c.stEl.textContent = "ส่งแล้ว ✓ · ปิดใน " + left + " วิ"; return; }
      clearInterval(tick);
      try{ await postJSON("/forget", {id:r.id}); }catch(e){}
      removeCard(r.id);
      if (!$("#pane-jobs").hidden) loadJobs();   // เปิดสมุดงานอยู่ → ให้เห็นแถวใหม่เลย
    }, 1000);
    c.closeBtn.addEventListener("click", () => clearInterval(tick), {once: true});
    return;    // ไม่ต้องวาดแผงหยุดรอต่อ งานจบแล้ว
  }
  if (r.status === "waiting" && r.pause){
    const k = r.pause.kind || "fill";
    const rs = r.pause.reason || "";
    c.preason.textContent = rs; c.preason.hidden = !rs;
    c.contBtn.dataset.kind = k;
    c.injWrap.hidden = (k !== "injury");
    if (k === "injury"){
      c.galWrap.style.display = "none"; c.galSig = null;
      c.wtWrap.hidden = true; c.wtSig = null;
      const isig = JSON.stringify((r.pause.persons || []).map(p => p.name));
      if (c.injSig !== isig){ buildInjuryForm(c, r); }
      c.ptitle.textContent = "ยืนยันประเภทผู้บาดเจ็บ (EMCS เติมเลขทะเบียนจากประเภทอัตโนมัติ)";
      c.phint.innerHTML = "เลือก <b>ประเภทผู้บาดเจ็บ</b> ให้ถูก — เลขทะเบียนเติมเอง"
        + "อัตโนมัติ (รถประกัน/รถคู่กรณี ตามประเภท; บุคคลภายนอกรถ = ใส่ 'บุคคลภายนอก') "
        + "<b>เลขทะเบียนเว้นว่างได้</b> กรอกเองเฉพาะตอนต้องการ override แล้วกดปุ่ม";
      c.contBtn.textContent = "✓ บันทึกข้อมูลผู้บาดเจ็บ — ดำเนินการต่อ";
      c.contBtn.className = "continue submitbtn";
    } else if (k === "images"){
      c.ptitle.textContent = "เลือกรูปที่จะอัปโหลดเข้า EMCS";
      c.phint.innerHTML = "ติ๊กเฉพาะรูปที่ต้องการนำเข้า EMCS แล้วกดปุ่มด้านล่าง — "
        + "รูปที่ <b>ไม่ติ๊ก</b> จะไม่ถูกอัปโหลด";
      const sig = imgsSig(r.pause.images || []);
      if (c.galSig !== sig) buildGallery(c, r);
      c.galWrap.style.display = "block";
      c.wtWrap.hidden = true;
      c.contBtn.className = "continue submitbtn";
      updateGalCount(c);
    } else if (k === "submit"){
      c.galWrap.style.display = "none"; c.galSig = null;
      const wsig = (r.pause.claim || "") + ":" + (r.pause.base_type || "");
      if (c.wtSig !== wsig){ buildWorkType(c, r); c.wtSig = wsig; }
      c.wtWrap.hidden = false;
      c.ptitle.textContent = "✅ พร้อมส่งงาน — ตรวจ draft + เลือกประเภทงาน";
      c.phint.innerHTML = "ตรวจในหน้าต่าง EMCS (Chrome) <b>ของงานนี้</b> "
        + "(ถ้าความเสียหาย >8 รายการ เติมให้ครบก่อน) + เลือกประเภทงานด้านล่าง แล้วกดปุ่ม "
        + "— ระบบจะกด 'ส่งงานใหม่' + แจ้ง ISURVEY + บันทึก se-key";
      c.contBtn.textContent = "✅ ส่งงาน + แจ้ง ISURVEY";
      c.contBtn.className = "continue submitbtn";
    } else {
      c.galWrap.style.display = "none"; c.galSig = null;
      c.wtWrap.hidden = true; c.wtSig = null;
      c.ptitle.textContent = "ต้องกรอกข้อมูลเอง: " + (r.pause.label || "ข้อมูลที่ขาด");
      // มีตัวเลือกมาด้วย = เลือกในหน้าเว็บได้เลย ไม่ต้องสลับไปหน้าต่าง EMCS
      const popts = r.pause.options || [];
      const psig = (r.pause.label || "") + "|" + popts.length;
      if (c.pickSig !== psig){ buildPicker(c, popts); c.pickSig = psig; }
      if (popts.length){
        c.phint.innerHTML = "ISURVEY ไม่มีข้อมูลช่องนี้ — <b>เลือกค่าด้านล่างได้เลย</b> "
          + "บอทจะกรอกลงหน้า EMCS ให้เอง (ไม่ต้องสลับหน้าต่าง)";
        c.contBtn.textContent = "✓ กรอกให้เลย";
      } else {
        c.phint.innerHTML = "ข้อมูลจาก ISURVEY ไม่ครบหรือกรอกอัตโนมัติไม่ได้ — "
          + "กรอก/เลือกช่องนี้ในหน้าต่าง EMCS (Chrome) <b>ของงานนี้</b> ให้เรียบร้อย แล้วกดปุ่ม";
        c.contBtn.textContent = "✓ กรอกเสร็จแล้ว — ดำเนินการต่อ";
      }
      c.contBtn.className = "continue";
    }
    c.contBtn.disabled = false;
    c.pauseEl.hidden = false;
  } else {
    c.pauseEl.hidden = true;
    c.galWrap.style.display = "none"; c.galSig = null;
    c.wtWrap.hidden = true; c.wtSig = null;
    c.injWrap.hidden = true; c.injSig = null;
    c.pickWrap.hidden = true; c.pickSig = null;
  }
  updateEmpty();
}
async function poll(){
  try{
    const {data} = await postJSON("/poll", {offsets});
    const seen = new Set();
    for (const r of data.runs){ seen.add(String(r.id)); renderRun(r); }
    for (const id of Object.keys(cards)){ if (!seen.has(String(id))) removeCard(id); }
    runBtn.disabled = data.active >= data.max;
    capBadge.textContent = "กำลังรัน " + data.active + "/" + data.max;
    capBadge.className = "badge " + (data.active > 0 ? "running" : "idle");
  }catch(e){ /* เซิร์ฟเวอร์อาจกำลังปิด — เงียบไว้ */ }
}
runBtn.addEventListener("click", async () => {
  const claims = $("#claims").value.trim();
  if (!claims){ $("#claims").focus(); return; }
  if ($("#forcenew").checked &&
      !confirm("สร้างเรื่องใหม่แม้เคลมนี้มีเรื่องเดิมใน EMCS แล้ว?\n\n"
               + "draft ที่สร้างจะลบไม่ได้ (ยกเลิกได้อย่างเดียว) — ใช้เฉพาะตอนทดสอบ")){
    return;
  }
  runBtn.disabled = true;
  const body = {
    claims,
    invoice: $("#invoice").value.trim(),
    severity: $("#severity").value,
    claimmode: $("#claimmode").value,
    readonly: $("#readonly").checked,
    skipimages: $("#skipimages").checked,
    nosaveprice: $("#nosaveprice").checked,
    forcenew: $("#forcenew").checked,
    importxml: $("#importxml").checked,
    checklicense: $("#checklicense").checked,
    fillexisting: $("#fillexisting").checked,
    imagesonly: $("#imagesonly").checked,
    includemain: $("#includemain").checked,
    esurvey: $("#esurvey").value.trim(),
    // ค่าที่ผู้ใช้เลือกจากแผง 🔍 ตรวจ (ช่องที่ ISURVEY ไม่มีข้อมูลให้)
    ...(window.__isvPick || {}),
  };
  window.__isvPick = null;   // ใช้ครั้งเดียว กันติดไปกับการรันรอบถัดไป
  try{
    const {ok,data} = await postJSON("/run", body);
    if (!ok){ alert(data.error || "เริ่มงานไม่สำเร็จ"); runBtn.disabled=false; return; }
    poll();   // ดึงงานใหม่มาแสดงทันที
  }catch(e){ alert("ติดต่อเซิร์ฟเวอร์ไม่ได้: " + e); runBtn.disabled=false; }
});
$("#claimmode").addEventListener("change", e => {
  $("#cmnote").hidden = (e.target.value !== "fresh");
});

// ── นำเข้าจาก SE Survey (ดึง XML+รูป → EMCS) ──
const seRunBtn = $("#serunbtn"), seDryBtn = $("#sedrybtn"), seCaseInput = $("#secase");
const seCasesBox = $("#secasesbox"), loadCasesBtn = $("#loadcasesbtn");
const seSent = new Set();   // case id ที่กดส่งเข้า AutoKey แล้วในรอบนี้ (กันกดซ้ำ)

async function startSesurvey(caseId, claimNo, mode, live){
  caseId = String(caseId||"").trim();
  mode = mode || "import";
  if (!caseId){ alert("ใส่เลขเคส (case id) หรือเลขเซอร์เวย์"); return; }
  const CONFIRM = {
    "import-live": "นำเข้า EMCS จริง (สร้าง draft) เคส #"+caseId+" ?\n\n"
      + "• กรอกฟอร์ม + อัปรูป + บันทึกเป็น draft — ไม่กดส่งงาน (หัวหน้าตรวจแล้วส่งเอง)\n"
      + "• draft ที่สร้างลบไม่ได้ (ยกเลิกได้อย่างเดียว) — เคสที่นำเข้าแล้วระบบกันซ้ำให้",
    "fill-existing": "เติมส่วนที่ขาดบน draft เดิม เคส #"+caseId+" ?\n\n"
      + "• เปิด draft เดิม เติมหน้าหลัก/คู่กรณี/รูป/ค่าใช้จ่าย แล้วบันทึก — ไม่กดส่ง\n"
      + "⚠️ เหมาะกับ draft ที่ยังไม่ได้เติมคู่กรณี/รูป (ไม่งั้นอาจเพิ่มซ้ำ)",
    "images-only": "อัปรูปใหม่บน draft เดิม เคส #"+caseId+" ?\n\n"
      + "• อัปรูปแยกหมวด — ไม่แตะฟอร์ม ไม่กดส่ง\n"
      + "⚠️ ควรลบรูปเก่าใน EMCS ก่อน (ไม่งั้นรูปซ้ำ)",
    "injured-only": "กู้บล็อกผู้บาดเจ็บบน draft เดิม เคส #"+caseId+" ?\n\n"
      + "• เติมเฉพาะผู้บาดเจ็บ (รพ.ว่าง → '-') แล้วบันทึก — ไม่แตะส่วนอื่น ไม่กดส่ง",
  };
  const key = (mode === "import") ? (live ? "import-live" : null) : mode;
  if (key && CONFIRM[key] && !confirm(CONFIRM[key])) return;
  try{
    const {ok,data} = await postJSON("/api/import-sesurvey",
      {case_id: caseId, claim_no: claimNo||"", mode: mode, live: !!live});
    if (!ok){ alert(data.error || "เริ่มงานไม่สำเร็จ"); return; }
    if (mode === "import") seSent.add(caseId);
    renderSeCasesFromCache();
    poll();
  }catch(e){ alert("ติดต่อเซิร์ฟเวอร์ไม่ได้: " + e); }
}
seRunBtn.addEventListener("click", () => startSesurvey(seCaseInput.value, "", "import", true));
seDryBtn.addEventListener("click", () => startSesurvey(seCaseInput.value, "", "import", false));
seCaseInput.addEventListener("keydown", e => { if (e.key === "Enter") startSesurvey(seCaseInput.value, "", "import", true); });

// ดาวน์โหลดไฟล์ XML สำรอง (.txt) ผ่าน proxy webui → เอาไป import EMCS เอง
async function downloadXml(caseId){
  caseId = String(caseId||"").trim();
  if (!caseId) return;
  try{
    const r = await fetch("/sesurvey-xml?case_id="+encodeURIComponent(caseId));
    if (!r.ok){ let e={}; try{ e = await r.json(); }catch(_){}; alert(e.error || ("โหลด XML ไม่สำเร็จ ("+r.status+")")); return; }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "sesurvey_case_"+caseId+".txt";   // EMCS รับเฉพาะ .txt
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }catch(e){ alert("ติดต่อเซิร์ฟเวอร์ไม่ได้: " + e); }
}

let seCasesCache = [];
function renderSeCasesFromCache(){
  if (!seCasesCache.length){
    seCasesBox.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">— ไม่มีเคสสำรวจแล้ว (กด ↻ โหลดรายการ) —</div>';
    return;
  }
  seCasesBox.innerHTML = seCasesCache.map(c => {
    const id = String(c.id);
    const claim = escAttr(c.claim_no||"");
    const who = c.surveyor_name && c.surveyor_name.trim() ? c.surveyor_name : "-";
    const imported = !!c.emcs_imported_at;
    const statusBadge = imported
      ? '<span style="color:var(--ok);font-weight:600;font-size:11.5px;white-space:nowrap">✓ นำเข้าแล้ว '+escHtml(c.emcs_imported_at)+'</span>'
      : '<span style="color:var(--muted);font-size:11.5px;white-space:nowrap">— ยังไม่นำเข้า</span>';
    let act;
    if (imported){
      // นำเข้าแล้ว → กู้/ซ่อม draft เดิม (ห้าม import ซ้ำ — EMCS สร้างเรื่องซ้ำที่เลขเคลมเดิม)
      const rb = (m,label) => '<button class="run seact" data-id="'+id+'" data-claim="'+claim+'" data-mode="'+m+'" style="background:#64748b">'+label+'</button>';
      act = rb("fill-existing","เติมส่วนที่ขาด")+rb("images-only","อัปรูปใหม่")+rb("injured-only","กู้ผู้บาดเจ็บ");
    } else if (seSent.has(id)){
      act = '<span style="color:var(--ok);font-weight:600;font-size:12.5px">✓ ส่งเข้า AutoKey แล้ว</span>';
    } else {
      act = '<button class="run seact" data-id="'+id+'" data-claim="'+claim+'" data-mode="import" data-live="1">⚡ นำเข้า EMCS</button>'
          + '<button class="run seact" data-id="'+id+'" data-claim="'+claim+'" data-mode="import" style="background:#64748b" title="ดึง+ตรวจ ไม่แตะ EMCS">🧪 ทดสอบ</button>';
    }
    if (!imported) act = '<button class="run sechk" data-id="'+id+'" style="background:#64748b">🔍 ตรวจ</button>' + act;
    act += '<button class="xmlbtn" data-id="'+id+'" style="background:transparent;color:var(--muted);border:1px solid var(--line)" title="ดาวน์โหลด XML (.txt) ไป import EMCS เอง — สำรอง">📄 XML</button>';
    return '<div class="case-item">'
      + '<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px">'
      +   '<span style="display:flex;align-items:center;gap:8px;min-width:0">'
      +     '<input type="checkbox" class="sesel"' + (imported ? ' disabled' : '')
      +       ' data-id="'+id+'" data-claim="'+claim+'">'
      +     '<span class="case-sv">'+escHtml(c.survey_job_no||"(ไม่มีเลขเซอร์เวย์)")+'</span>'
      +   '</span>'+statusBadge
      + '</div>'
      + '<div class="case-claim">'+escHtml(c.claim_no||"-")+'</div>'
      + '<div class="case-meta">'+escHtml(c.insurance_company||"-")+' · '+escHtml(who)+'</div>'
      + '<div class="case-btns">'+act+'</div>'
      + '<div class="sepanel" data-for="'+id+'" hidden style="margin-top:8px;padding:8px 10px;border-radius:8px;background:#0f172a11;font-size:12.5px"></div>'
      + '</div>';
  }).join("");
  seCasesBox.querySelectorAll(".seact").forEach(b => {
    b.addEventListener("click", () => startSesurvey(b.dataset.id, b.dataset.claim, b.dataset.mode, b.dataset.live === "1"));
  });
  seCasesBox.querySelectorAll(".xmlbtn").forEach(b => {
    b.addEventListener("click", () => downloadXml(b.dataset.id));
  });
  seCasesBox.querySelectorAll(".sechk").forEach(b => {
    b.addEventListener("click", () => checkSeCase(b.dataset.id));
  });
  seCasesBox.querySelectorAll(".sesel").forEach(c => c.addEventListener("change", updateSeCount));
  $("#setoolbar").hidden = !seCasesCache.some(c => !c.emcs_imported_at);
  $("#seall").checked = false;
  updateSeCount();
}

const seSelected = () => [...seCasesBox.querySelectorAll(".sesel:checked")];
function updateSeCount(){
  const n = seSelected().length;
  $("#secount").textContent = n ? ("เลือกไว้ " + n + " เคส") : "";
  $("#serunall").textContent = n ? ("⚡ นำเข้าที่เลือก (" + n + ")") : "⚡ นำเข้าที่เลือก";
}
$("#seall").addEventListener("change", e => {
  seCasesBox.querySelectorAll(".sesel:not([disabled])").forEach(c => { c.checked = e.target.checked; });
  updateSeCount();
});

// ตรวจก่อนนำเข้า — อ่าน XML + report ของจริง ไม่เปิด Chrome ไม่แตะ EMCS
async function checkSeCase(caseId){
  const panel = seCasesBox.querySelector('.sepanel[data-for="' + caseId + '"]');
  if (!panel) return;
  panel.hidden = false;
  panel.innerHTML = 'กำลังตรวจ…';
  try{
    const r = await fetch("/sesurvey-check?case_id=" + encodeURIComponent(caseId));
    const d = await r.json();
    if (!r.ok){ panel.innerHTML = '<span style="color:var(--err)">' + escHtml(d.error||"ตรวจไม่สำเร็จ") + '</span>'; return d; }
    const c = d.counts || {}, i = d.info || {};
    let h = '<div style="color:var(--muted)">คู่กรณี ' + c.opponents + ' · ผู้บาดเจ็บ ' + c.injuries
          + ' · ทรัพย์สิน ' + c.assets + ' · ความเสียหาย ' + c.damage + ' รายการ</div>'
          + '<div style="color:var(--muted);margin-top:2px">' + escHtml(i.car_type||"-") + ' · '
          + escHtml(i.acc_province||"-") + ' · ' + escHtml(i.loss_type||"-") + '</div>';
    h += d.ready ? '<div style="color:#16a34a;font-weight:600;margin-top:6px">✅ พร้อมนำเข้า</div>'
                 : d.blockers.map(b => '<div style="color:var(--err);margin-top:6px">⛔ ' + escHtml(b) + '</div>').join("");
    if ((d.warnings||[]).length)
      h += '<div style="color:#d97706;margin-top:6px">⚠️ ' + escHtml(d.warnings.join(" · ")) + '</div>';
    panel.innerHTML = h;
    return d;
  }catch(e){ panel.innerHTML = '<span style="color:var(--err)">ติดต่อเซิร์ฟเวอร์ไม่ได้</span>'; }
}

$("#sechkall").addEventListener("click", async () => {
  const sel = seSelected();
  if (!sel.length){ alert("ยังไม่ได้เลือกเคส"); return; }
  for (const c of sel) await checkSeCase(c.dataset.id);
});

// คิวนำเข้า — รันทีละเคสเสมอ (EMCS ล็อกเรื่องรายตัว + โควตารูปเป็นของเคลม)
$("#serunall").addEventListener("click", async () => {
  const sel = seSelected();
  if (!sel.length){ alert("ยังไม่ได้เลือกเคส"); return; }
  if (!confirm("นำเข้า EMCS จริง " + sel.length + " เคส (สร้าง draft) ?\n\n"
      + "• รันทีละเคส กรอกฟอร์ม + อัปรูป + บันทึก draft — ไม่กดส่งงาน\n"
      + "• draft ที่สร้างลบไม่ได้ (ยกเลิกได้อย่างเดียว)")) return;
  const qBox = $("#sequeue");
  qBox.hidden = false;
  $("#serunall").disabled = true; $("#sechkall").disabled = true;
  let done = 0;
  for (const c of sel){
    const id = c.dataset.id;
    qBox.innerHTML = 'ตรวจเคส #' + escHtml(id) + ' (' + (done + 1) + '/' + sel.length + ')…';
    const chk = await checkSeCase(id);              // ตรวจก่อนทุกเคส — ไม่พร้อมก็ข้าม
    if (chk && !chk.ready){
      qBox.innerHTML = '<span style="color:var(--err)">⛔ เคส #' + escHtml(id)
        + ' ยังไม่พร้อม (ดูรายละเอียดใต้เคส) — หยุดคิว</span>';
      break;
    }
    qBox.innerHTML = 'กำลังนำเข้า #' + escHtml(id) + ' (' + (done + 1) + '/' + sel.length + ')…'
      + '<div style="color:var(--muted);margin-top:4px">รันทีละเคส — EMCS ล็อกเรื่องรายตัว</div>';
    let runId = null;
    try{
      const {ok, data} = await postJSON("/api/import-sesurvey",
                                        {case_id: id, mode: "import", live: true});
      if (!ok){ qBox.innerHTML = '<span style="color:var(--err)">#' + escHtml(id) + ': '
                + escHtml(data.error || "เริ่มงานไม่สำเร็จ") + ' — หยุดคิว</span>'; break; }
      runId = data.run_id;
    }catch(e){ qBox.innerHTML = '<span style="color:var(--err)">ติดต่อเซิร์ฟเวอร์ไม่ได้ — หยุดคิว</span>'; break; }
    while (true){
      await new Promise(r => setTimeout(r, 1500));
      let st = null;
      try{
        const {data} = await postJSON("/poll", {});
        st = (data.runs || []).find(x => x.id === runId);
      }catch(e){ /* เน็ตสะดุด — วนรอต่อ */ }
      if (st && st.status !== "running") break;
    }
    done++;
    c.checked = false;
    seSent.add(String(id));
  }
  updateSeCount();
  if (done === sel.length) qBox.innerHTML = '✅ นำเข้าครบ ' + done + '/' + sel.length
    + ' เคส — ตรวจ draft บน EMCS แล้วกดส่งงานเอง';
  $("#serunall").disabled = false; $("#sechkall").disabled = false;
});
loadCasesBtn.addEventListener("click", async () => {
  loadCasesBtn.disabled = true;
  seCasesBox.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">กำลังโหลด…</div>';
  try{
    const r = await fetch("/sesurvey-cases");
    const data = await r.json();
    if (!r.ok){ seCasesBox.innerHTML = '<div style="color:var(--err);font-size:13px;padding:8px 0">'+escHtml(data.error||"โหลดไม่สำเร็จ")+'</div>'; return; }
    seCasesCache = data.cases || [];
    renderSeCasesFromCache();
  }catch(e){ seCasesBox.innerHTML = '<div style="color:var(--err);font-size:13px;padding:8px 0">ติดต่อเซิร์ฟเวอร์ไม่ได้</div>'; }
  finally{ loadCasesBtn.disabled = false; }
});

// ---- รายการงานจบแล้วฝั่ง ISURVEY ----
const isvBox = $("#isvcasesbox"), loadIsvBtn = $("#loadisvbtn");
let isvCache = [];
$("#isvto").value = new Date().toISOString().slice(0,10);
$("#isvfrom").value = new Date(Date.now() - 7*86400000).toISOString().slice(0,10);

function renderIsvCases(){
  const hideSent = $("#isvhidesent").checked;
  const rows = isvCache.filter(r => !(hideSent && r.emcs_sent));
  if (!rows.length){
    isvBox.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">'
      + (isvCache.length ? 'ทุกเรื่องในช่วงนี้นำเข้า EMCS ไปแล้ว (เอาติ๊ก “ซ่อนที่นำเข้าแล้ว” ออกเพื่อดู)'
                         : 'ไม่พบงานสถานะ “จบงาน” ในช่วงวันที่นี้') + '</div>';
    $("#isvtoolbar").hidden = true;
    return;
  }
  isvBox.innerHTML = rows.map(r => {
    const badge = r.emcs_sent
      ? '<span style="color:var(--ok);font-size:11.5px;white-space:nowrap">✓ นำเข้าแล้ว'
        + (r.emcs_by ? ' · ' + escHtml(r.emcs_by) : '')
        + (r.emcs_date ? ' · ' + escHtml(String(r.emcs_date).slice(0,16)) : '') + '</span>'
      : '';
    return '<div class="caseitem" style="display:flex;gap:10px;align-items:center;padding:8px 0;border-bottom:1px solid var(--line)">'
      + '<input type="checkbox" class="isvsel" style="flex:none"' + (r.emcs_sent ? ' disabled' : '')
      +   ' data-claim="' + escHtml(r.claim_no || "") + '" data-inv="' + escHtml(r.survey_no || "") + '">'
      + '<div style="flex:1;min-width:0">'
      +   '<div style="font-weight:600;font-size:13px">' + escHtml(r.claim_no || "") + ' ' + badge + '</div>'
      +   '<div style="color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
      +     escHtml(r.survey_no || "") + ' · ' + escHtml(r.plate_no || "-") + ' · ' + escHtml(r.surveyor_name || "-")
      +     ' · เสร็จงาน ' + escHtml(r.finish_dt || "-")
      +     (r.acc_province ? ' · ' + escHtml(r.acc_province) : '')
      +   '</div>'
      + '</div>'
      + '<button class="run isvchk" style="padding:7px 12px;font-size:13px;white-space:nowrap;background:#64748b" '
      +   'data-claim="' + escHtml(r.claim_no || "") + '" data-inv="' + escHtml(r.survey_no || "") + '">🔍 ตรวจ</button>'
      + '<button class="run isvact" style="padding:7px 12px;font-size:13px;white-space:nowrap" '
      +   'data-claim="' + escHtml(r.claim_no || "") + '" data-inv="' + escHtml(r.survey_no || "") + '">⚡ นำเข้า</button>'
      + '</div><div class="isvpanel" data-for="' + escHtml(r.claim_no || "") + '" hidden '
      + 'style="padding:10px 12px;margin:0 0 8px;background:var(--bg2,#0f172a11);border-radius:8px;font-size:13px"></div>';
  }).join("");
  isvBox.querySelectorAll(".isvchk").forEach(b => {
    b.addEventListener("click", () => checkIsvCase(b));
  });
  isvBox.querySelectorAll(".isvact").forEach(b => {
    b.addEventListener("click", () => runIsvCase(b.dataset.claim, b.dataset.inv));
  });
  isvBox.querySelectorAll(".isvsel").forEach(c => c.addEventListener("change", updateIsvCount));
  $("#isvtoolbar").hidden = !rows.length;
  $("#isvall").checked = false;
  updateIsvCount();
}

const isvSelected = () => [...isvBox.querySelectorAll(".isvsel:checked")];
function updateIsvCount(){
  const n = isvSelected().length;
  $("#isvcount").textContent = n ? ("เลือกไว้ " + n + " เรื่อง") : "";
  $("#isvrunall").textContent = n ? ("⚡ นำเข้าที่เลือก (" + n + ")") : "⚡ นำเข้าที่เลือก";
}
$("#isvhidesent").addEventListener("change", () => renderIsvCases());
$("#isvall").addEventListener("change", e => {
  isvBox.querySelectorAll(".isvsel").forEach(c => { c.checked = e.target.checked; });
  updateIsvCount();
});
$("#isvchkall").addEventListener("click", async () => {
  const sel = isvSelected();
  if (!sel.length){ alert("ยังไม่ได้เลือกเรื่อง"); return; }
  for (const c of sel){
    const btn = isvBox.querySelector('.isvchk[data-claim="' + c.dataset.claim + '"]');
    if (btn) await checkIsvCase(btn);      // ทีละเรื่อง — ISURVEY ยิงรัวแล้ว timeout บ่อย
  }
});

// เก็บค่าที่เลือกในแผงตรวจของเรื่องหนึ่ง — คืน null ถ้ายังเลือกไม่ครบ
function isvPickOf(claim){
  const panel = isvBox.querySelector('.isvpanel[data-for="' + claim + '"]');
  if (!panel || panel.hidden) return {};        // ยังไม่ได้ตรวจ = ไม่มีค่าให้ส่ง
  const pick = {};
  let missing = false;
  panel.querySelectorAll(".isvpick").forEach(s => {
    if (!s.value){ missing = true; s.style.borderColor = "var(--err)"; }
    else pick[s.dataset.field] = s.value;
  });
  return missing ? null : pick;
}

// คิวนำเข้า: รัน "ทีละเรื่อง" เสมอ — EMCS ล็อกเรื่องรายตัว + โควตารูปเป็นของเคลม
// รันขนานจะชนกันเอง ไม่ใช่แค่ช้า
$("#isvrunall").addEventListener("click", async () => {
  const sel = isvSelected();
  if (!sel.length){ alert("ยังไม่ได้เลือกเรื่อง"); return; }
  const qBox = $("#isvqueue");
  qBox.hidden = false;

  // ตรวจก่อนว่าทุกเรื่องเลือกค่าที่จำเป็นครบแล้ว — ไม่งั้นคิวจะไปค้างกลางทาง
  const jobs = [], needPick = [];
  for (const c of sel){
    const pick = isvPickOf(c.dataset.claim);
    if (pick === null) needPick.push(c.dataset.claim);
    else jobs.push({claim: c.dataset.claim, inv: c.dataset.inv, pick});
  }
  if (needPick.length){
    qBox.innerHTML = '<span style="color:var(--err)">⛔ ยังเลือกค่าไม่ครบ ' + needPick.length
      + ' เรื่อง: ' + escHtml(needPick.join(", ")) + ' — เลือกในแผงตรวจให้ครบก่อน</span>';
    return;
  }

  $("#isvrunall").disabled = true; $("#isvchkall").disabled = true;
  let done = 0;
  for (const j of jobs){
    qBox.innerHTML = 'กำลังนำเข้า ' + escHtml(j.claim) + ' (' + (done + 1) + '/' + jobs.length + ')…'
      + '<div style="color:var(--muted);margin-top:4px">รันทีละเรื่อง — EMCS ล็อกเรื่องรายตัว</div>';
    const body = {claims: j.claim, invoice: j.inv, severity: $("#severity").value,
                  claimmode: $("#claimmode").value, readonly: $("#readonly").checked,
                  skipimages: $("#skipimages").checked, nosaveprice: $("#nosaveprice").checked,
                  forcenew: $("#forcenew").checked, importxml: $("#importxml").checked,
                  checklicense: $("#checklicense").checked, ...j.pick};
    let runId = null;
    try{
      const {ok, data} = await postJSON("/run", body);
      if (!ok){ qBox.innerHTML = '<span style="color:var(--err)">' + escHtml(j.claim) + ': '
                + escHtml(data.error || "เริ่มงานไม่สำเร็จ") + ' — หยุดคิว</span>'; break; }
      runId = data.run_id;      // /run คืนคีย์ run_id (ไม่ใช่ id — poll ถึงใช้ x.id)
    }catch(e){ qBox.innerHTML = '<span style="color:var(--err)">ติดต่อเซิร์ฟเวอร์ไม่ได้ — หยุดคิว</span>'; break; }

    // รอเรื่องนี้จบก่อนค่อยเริ่มเรื่องถัดไป
    while (true){
      await new Promise(r => setTimeout(r, 1500));
      let st = null;
      try{
        const {data} = await postJSON("/poll", {});
        st = (data.runs || []).find(x => x.id === runId);
      }catch(e){ /* เน็ตสะดุด — วนรอต่อ */ }
      if (st && st.status !== "running") break;
    }
    done++;
    sel.find(c => c.dataset.claim === j.claim).checked = false;
  }
  updateIsvCount();
  if (done === jobs.length) qBox.innerHTML = '✅ นำเข้าครบ ' + done + '/' + jobs.length
    + ' เรื่อง — ตรวจ draft บน EMCS แล้วกดส่งงานเอง';
  $("#isvrunall").disabled = false; $("#isvchkall").disabled = false;
});

// รันเรื่องหนึ่ง — เติมลงฟอร์มด้านล่างแล้วกดรัน (ตัวเลือกที่ตั้งไว้ยังมีผล)
// pick = ค่าที่ผู้ใช้เลือกจากแผงตรวจ (ลักษณะความเสียหาย / คำนำหน้า) ส่งต่อเป็น flag
function runIsvCase(claim, inv, pick){
  $("#claims").value = claim;
  $("#invoice").value = inv || "";
  window.__isvPick = pick || null;
  runBtn.click();
}

async function checkIsvCase(btn){
  const panel = isvBox.querySelector('.isvpanel[data-for="' + btn.dataset.claim + '"]');
  panel.hidden = false;
  panel.innerHTML = 'กำลังอ่านเคลมจาก ISURVEY…';
  btn.disabled = true;
  try{
    const r = await fetch("/isurvey-check?claim=" + encodeURIComponent(btn.dataset.claim)
                          + "&invoice=" + encodeURIComponent(btn.dataset.inv || ""));
    const d = await r.json();
    if (!r.ok){ panel.innerHTML = '<span style="color:var(--err)">' + escHtml(d.error||"ตรวจไม่สำเร็จ") + '</span>'; return; }
    const c = d.counts || {};
    const netTxt = d.bill_zero
      ? '<b style="color:#d97706">สุทธิ ' + escHtml(d.bill_net || "0.00") + ' ⚠️</b>'
      : 'สุทธิ ' + escHtml(d.bill_net || "-");
    let h = '<div style="margin-bottom:6px">' + escHtml(d.car || "") + ' · ' + escHtml(d.plate || "")
          + ' · ' + escHtml(d.acc_result || "") + '</div>'
          + '<div style="color:var(--muted);margin-bottom:8px">คู่กรณี ' + c.opponents
          + ' · ผู้บาดเจ็บ ' + c.injuries + ' · ทรัพย์สิน ' + c.assets
          + ' · ความเสียหาย ' + c.damage + ' รายการ · ' + netTxt + '</div>';
    if (d.ready){
      h += '<div style="color:#16a34a;font-weight:600">✅ ข้อมูลครบ นำเข้าได้เลย</div>';
    } else {
      h += d.blockers.map(b =>
        '<div style="margin:8px 0">'
        + '<div style="font-weight:600">⛔ ต้องเลือกก่อน: ' + escHtml(b.label) + '</div>'
        + '<div style="color:var(--muted);margin:2px 0 4px">' + escHtml(b.why) + '</div>'
        + '<select class="isvpick" data-field="' + b.field + '" style="width:100%;padding:6px 8px">'
        + '<option value="">— เลือก —</option>'
        + b.options.map(o => '<option>' + escHtml(o) + '</option>').join("")
        + '</select></div>').join("");
    }
    if ((d.warnings || []).length){
      h += '<div style="color:#d97706;margin-top:8px">⚠️ ตรวจด้วย: ' + escHtml(d.warnings.join(" · ")) + '</div>';
    }
    h += '<div style="margin-top:10px"><button class="run isvgo" style="padding:7px 14px;font-size:13px">⚡ นำเข้า EMCS</button></div>';
    panel.innerHTML = h;
    panel.querySelector(".isvgo").addEventListener("click", () => {
      const pick = {};
      let missing = false;
      panel.querySelectorAll(".isvpick").forEach(s => {
        if (!s.value){ missing = true; s.style.borderColor = "var(--err)"; }
        else pick[s.dataset.field] = s.value;
      });
      if (missing){ alert("ยังเลือกไม่ครบ — ช่องที่ขอบแดงต้องเลือกก่อน\\n\\n(ไม่เลือก บอทจะไปหยุดรอกลางทางบนหน้า EMCS)"); return; }
      runIsvCase(btn.dataset.claim, btn.dataset.inv, pick);
    });
  }catch(e){ panel.innerHTML = '<span style="color:var(--err)">ติดต่อเซิร์ฟเวอร์ไม่ได้</span>'; }
  finally{ btn.disabled = false; }
}

loadIsvBtn.addEventListener("click", async () => {
  loadIsvBtn.disabled = true;
  isvBox.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">กำลังโหลด…</div>';
  try{
    const r = await fetch("/isurvey-cases?from=" + encodeURIComponent($("#isvfrom").value)
                          + "&to=" + encodeURIComponent($("#isvto").value));
    const data = await r.json();
    if (!r.ok){ isvBox.innerHTML = '<div style="color:var(--err);font-size:13px;padding:8px 0">'+escHtml(data.error||"โหลดไม่สำเร็จ")+'</div>'; return; }
    isvCache = data.cases || [];
    const sent = isvCache.filter(x => x.emcs_sent).length;
    $("#isvsummary").textContent = 'จบงาน ' + isvCache.length + ' เรื่อง · นำเข้าแล้ว ' + sent
                                 + ' · รอนำเข้า ' + (isvCache.length - sent);
    renderIsvCases();
  }catch(e){ isvBox.innerHTML = '<div style="color:var(--err);font-size:13px;padding:8px 0">ติดต่อเซิร์ฟเวอร์ไม่ได้</div>'; }
  finally{ loadIsvBtn.disabled = false; }
});

// ---------------- 📚 สมุดงาน: เลขเคลม/เลขเซอร์เวย์ที่ทำไปแล้ว ----------------
const jobsBox = $("#jobsbox"), jobsQ = $("#jobsq");
const EV_LABEL = {sent: "ส่งแล้ว", draft: "draft"};

async function loadJobs(){
  jobsBox.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">กำลังโหลด…</div>';
  try{
    const r = await fetch("/jobs?q=" + encodeURIComponent(jobsQ.value.trim()));
    const d = await r.json();
    const rows = d.jobs || [];
    if (!rows.length){
      jobsBox.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0">'
        + (jobsQ.value.trim() ? "ไม่พบงานที่ตรงกับที่ค้น" : "ยังไม่มีงานในสมุด") + '</div>';
      return;
    }
    jobsBox.innerHTML = '<table class="jobtbl"><thead><tr>'
      + '<th>เวลา</th><th>สถานะ</th><th>เลขเคลม</th><th>เลขเซอร์เวย์</th>'
      + '<th>e-Survey</th><th>คนคีย์</th><th>ประเภทงาน</th></tr></thead><tbody>'
      + rows.map(j => '<tr>'
        + '<td style="white-space:nowrap">' + escHtml(j.ts || "") + '</td>'
        + '<td><span class="ev ev-' + (j.event === "sent" ? "sent" : "draft") + '">'
        + escHtml(EV_LABEL[j.event] || j.event || "") + '</span></td>'
        + '<td>' + escHtml(j.claim || "") + '</td>'
        + '<td>' + escHtml(j.invoice || "") + '</td>'
        + '<td>' + escHtml(j.esurvey || "") + '</td>'
        + '<td>' + escHtml(j.keyer || "") + '</td>'
        + '<td>' + escHtml(j.work_type || "") + '</td>'
        + '</tr>').join("") + '</tbody></table>';
  }catch(e){
    jobsBox.innerHTML = '<div style="color:var(--err);font-size:13px">โหลดสมุดงานไม่ได้: ' + escHtml(String(e)) + '</div>';
  }
}
$("#jobsreload").addEventListener("click", loadJobs);
let jobsTimer = null;
jobsQ.addEventListener("input", () => { clearTimeout(jobsTimer); jobsTimer = setTimeout(loadJobs, 300); });

// ---------------- ⚙ ตั้งค่า: คนคีย์ตามเลขท้ายเลขเคลม ----------------
const keyersBox = $("#keyersbox"), keyersMsg = $("#keyersmsg");

async function loadKeyers(){
  keyersMsg.textContent = "";
  try{
    const r = await fetch("/settings");
    const d = await r.json();
    const k = d.keyers || {};
    if (d.file) $("#keyersfile").textContent = d.file;
    keyersBox.innerHTML = "0123456789".split("").map(dg =>
      '<div class="keyrow"><div class="dg">' + dg + '</div>'
      + '<input class="keyin" data-dg="' + dg + '" value="' + escHtml(k[dg] || "") + '"'
      + ' placeholder="ชื่อ-นามสกุล คนคีย์"></div>').join("");
  }catch(e){
    keyersBox.innerHTML = '<div style="color:var(--err);font-size:13px">โหลดตั้งค่าไม่ได้: ' + escHtml(String(e)) + '</div>';
  }
}
$("#reloadkeyers").addEventListener("click", loadKeyers);
$("#savekeyers").addEventListener("click", async () => {
  const table = {};
  keyersBox.querySelectorAll(".keyin").forEach(i => { table[i.dataset.dg] = i.value.trim(); });
  keyersMsg.textContent = "กำลังบันทึก…"; keyersMsg.style.color = "var(--muted)";
  try{
    const {ok, data} = await postJSON("/settings", {keyers: table});
    if (!ok){ keyersMsg.textContent = "❌ " + (data.error || "บันทึกไม่สำเร็จ"); keyersMsg.style.color = "var(--err)"; return; }
    keyersMsg.textContent = "✅ บันทึกแล้ว — มีผลกับงานถัดไปทันที"; keyersMsg.style.color = "var(--ok)";
  }catch(e){ keyersMsg.textContent = "❌ ติดต่อเซิร์ฟเวอร์ไม่ได้: " + e; keyersMsg.style.color = "var(--err)"; }
});

// แท็บสลับ (client-side toggle หน้าเดียว)
const PANES = ["sesurvey", "isurvey", "jobs", "settings"];
document.querySelectorAll(".tab").forEach(t => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(x => x.classList.toggle("active", x === t));
    const p = t.dataset.pane;
    PANES.forEach(n => { $("#pane-" + n).hidden = (n !== p); });
    if (p === "jobs") loadJobs();
    if (p === "settings") loadKeyers();
  });
});
loadCasesBtn.click();   // auto-load รายการเคสตอนเปิดหน้า

setInterval(poll, 1200);
poll();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="หน้าเว็บสั่งรัน se-autokey")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-open", action="store_true",
                    help="ไม่ต้องเปิดเบราว์เซอร์ให้อัตโนมัติ")
    a = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    url = f"http://{a.host}:{a.port}"
    print("=" * 56)
    print("  se-autokey web UI พร้อมใช้งาน")
    print(f"  เปิดเบราว์เซอร์ที่:  {url}")
    print(f"  รันพร้อมกันได้สูงสุด {MAX_CONCURRENT} งาน (SE_MAX_CONCURRENT)")
    print("  ปิดเซิร์ฟเวอร์: กด Ctrl+C ที่หน้าต่างนี้")
    print("=" * 56)
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nปิดเซิร์ฟเวอร์แล้ว")
        srv.shutdown()


if __name__ == "__main__":
    main()
