# -*- coding: utf-8 -*-
"""แกนดึงงาน ISURVEY → se-survey — รับ ISurveyAPI ที่ล็อกอินแล้ว ไม่ผูกกับ .env
(บัญชีหัวหน้าล็อกอินใหม่ต่อคำขอ ด้วย make_client · บัญชีกลางยืม session ที่ค้างไว้ ด้วย isurvey_central — 08/10/69)

ใช้โดย `pull_service.py` (service บนเซิร์ฟเวอร์ ให้เว็บ se-survey เรียก) — หัวหน้าแต่ละคนกรอกบัญชี ISURVEY
ของตัวเองไว้บนเว็บ แล้วเซิร์ฟเวอร์ใช้บัญชีนั้นดึงงาน "รอตรวจข้อมูล" ของคนนั้นเข้าเป็นเคส (user ตัดสิน 04/09/69)

หน้าที่เดียวกับ `webui.fetch_isurvey_cases` / `webui.pull_isurvey_case` แต่รับ `ISurveyAPI` ที่ล็อกอินแล้วเป็นพารามิเตอร์
(webui ยังใช้ของเดิมกับบัญชีใน .env บนเครื่องผู้ใช้บอท — สองทางใช้ตัวแปลง `build_case` ตัวเดียวกัน)

⚠️ อ่าน ISURVEY อย่างเดียว ไม่เขียนกลับ ไม่เปลี่ยนสถานะ ไม่แตะ EMCS
"""
from __future__ import annotations

import dataclasses
import io
import json
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .config import Config
from .isurvey_api import ISurveyAPI
from . import isurvey_central
from .isurvey_to_sesurvey import apply_visit_rules, build_case
from . import survey_order

ISURVEY_STATUS_PENDING = "รอตรวจข้อมูล"
ISURVEY_EMCS_SENT = "send"
REPORT_URL = "https://cloud.isurvey.mobi/web/php/report/get_data_report.php"
#: สถานะ ISURVEY (masterStatus.sttcase_ID) ที่กด "ดึงเข้า" ได้ — 40 รอตรวจข้อมูล · 100 จบงาน (user เคาะ 13/09/69)
PULLABLE_STATUS_IDS = {"40", "100"}
# 19/09/69 user: ครั้งก่อนหน้าของเคลมมาเป็น "เคสอ้างอิง" (ปิดตั้งแต่สร้าง ไม่เข้าคิวบอท) ได้เฉพาะที่ **จบงาน** (100) บน ISURVEY แล้ว
# ใบที่ยังรอตรวจข้อมูล (40) หรือยังไม่จบ ต้องถูกดึงเข้ามาตรวจเป็นงานปกติก่อน — ไม่งั้นจะกลายเป็น "ปิดแล้ว" ทั้งที่ยังไม่เคยเข้า EMCS
# แล้วบอทครั้งถัดไปติด "ต้องนำเข้าครั้งที่ 1 ก่อน" โดยแก้ผ่านเว็บไม่ได้
CLOSED_STATUS_ID = "100"
REVIEW_STATUS_ID = "40"


class OpenRoundError(RuntimeError):
    """ครั้งก่อนหน้ายังไม่จบงานบน ISURVEY และยังไม่มีในเว็บ — ต้องดึงใบนั้นเข้าตรวจก่อน (ข้อความอ่านได้ ส่งกลับหน้าเว็บตรง ๆ)"""

#: ต้องตรงกับ INSURER_BY_JOB_PREFIX ของหน้า import-xml บนเว็บ se-survey
#: ⛔ prefix ที่ไม่รู้จัก = หยุด ห้าม fallback (เข้าผิดบริษัทใน EMCS ลบไม่ได้)
INSURER_BY_PREFIX = {
    "SETP": "บริษัท ไทยไพบูลย์ประกันภัย จำกัด (มหาชน)",
    "SEABI": "ไอโออิกรุงเทพประกันภัย",
}


def new_client(username: str, password: str) -> ISurveyAPI:
    """ISurveyAPI ของบัญชีที่ส่งมา **ยังไม่ล็อกอิน** — ไม่อ่าน .env (ช่องบังคับอื่นของ Config ใส่ว่าง)
    (บัญชีกลางใช้ตัวนี้ทำ client ต่องานที่ยืม session กลาง — isurvey_central.CentralSession)"""
    kw = {}
    for f in dataclasses.fields(Config):
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            kw[f.name] = ""
    kw.update(isurvey_username=username, isurvey_password=password)
    return ISurveyAPI(Config(**kw))


def make_client(username: str, password: str) -> ISurveyAPI:
    """ISurveyAPI ที่ล็อกอินด้วยบัญชีที่ส่งมา (บัญชีหัวหน้า — ล็อกอินใหม่ทุกคำขอ)
    08/10/69: ISURVEY ตอบ "Session lose!" กลางงาน (หัวหน้าเปิดหน้า ISURVEY ด้วยบัญชีเดียวกัน) = error อ่านได้ ไม่ใช่ผลว่างเงียบ ๆ"""
    api = new_client(username, password)
    api.login()
    api.central = isurvey_central.DETECT_ONLY
    return api


def whoami(api: ISurveyAPI) -> str:
    """ชื่อผู้ใช้ที่ ISURVEY บอกหลังล็อกอิน ('' ถ้าอ่านไม่ได้)"""
    try:
        who = api._get("getUserData.php", _dc=0)
        return str(who.get("message") or "") if who.get("success") else ""
    except Exception:
        return ""


def list_pending(api: ISurveyAPI, date_from: str = "", date_to: str = "",
                 status: str = ISURVEY_STATUS_PENDING) -> list[dict]:
    """งานตามสถานะในช่วงวันที่ (ค่าเริ่มต้น 14 วันหลัง) — ใช้รายงาน enquiry เหมือน webui
    (listcases.php ตัน 50 แถว/paging ใช้ไม่ได้ — probe 2026-08-04) · กรองเฉพาะบริษัทที่รับงานจริง
    status="" = ทุกสถานะ (เว็บ se-survey ขอทั้งหมดแล้วให้ผู้ใช้เลือกสถานะเอง — user ขอ 04/09/69)"""
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    # ผ่าน _get_url (ไม่ใช่ api.s.get ตรง) — session หลุด รายงานนี้ตอบ PHP Notice แทน JSON ให้ตัวคุม session จับได้ (08/10/69)
    d = api._get_url(REPORT_URL, 120, {
        "con_date": 2, "date_from": date_from, "date_to": date_to,
        "report_type": "enquiry", "page": 1, "start": 0, "limit": 5000})
    rows = []
    for x in (d.get("arr_data") or d.get("data") or []):
        if status and str(x.get("stt_desc") or "").strip() != status:
            continue
        survey_no = str(x.get("survey_no") or "")
        if survey_no.split("-")[0].upper() not in INSURER_BY_PREFIX:
            continue
        rows.append({
            "claim_no": x.get("claim_no") or "",
            "survey_no": survey_no,
            "surveyor_name": x.get("empcode") or "",
            "acc_province": x.get("acc_province") or "",
            # จังหวัด/อำเภอที่ออกตรวจสอบ (รายงาน enquiry มีแยกจากที่เกิดเหตุ — เช็คช่องจริง 25/09/69) · หน้ารอตรวจโชว์ต่อจากจังหวัดที่เกิดเหตุ
            "survey_province": x.get("survey_province") or "",
            "survey_amphur": x.get("survey_amphur") or "",
            "plate_no": x.get("plate_no") or "",
            # เวลา 3 จุดของงาน (user ขอ 07/09/69): จ่ายงาน → สำรวจเสร็จ → ส่งรายงาน — หน้าเว็บโชว์ จ่ายงาน + ส่งรายงาน
            "dispatch_dt": x.get("dispatch_dt") or "",
            "finish_dt": x.get("finish_dt") or "",
            "send_report_dt": x.get("sendReport_dt") or "",
            "status": x.get("stt_desc") or "",
            "emcs_sent": str(x.get("EMCSstatus") or "") == ISURVEY_EMCS_SENT,
        })
    # เรียงตามเวลาส่งรายงานล่าสุด (งานที่ยังไม่ส่งรายงานใช้เวลาสำรวจเสร็จแทน)
    rows.sort(key=lambda r: str(r.get("send_report_dt") or r.get("finish_dt") or ""), reverse=True)
    return rows


def claim_rounds(api: ISurveyAPI, claims: list[str]) -> dict:
    """ทุกใบของแต่ละเคลมเรียงเป็น "ครั้งที่" (survey_order — กติกาเดียวกับตอนดึงงาน) ไว้โชว์บนหน้างานรอตรวจ (user ขอ 22/09/69)
    คืน {claim: [{survey_no, round, status_name}]} · เคลมที่ถามไม่ได้ = {"error": ...} (ไม่ล้มทั้งชุด)
    ⚠️ 1 คำขอ ISURVEY ต่อเคลม → เรียกแยกจาก list_pending เฉพาะแถวที่หน้าเว็บมองเห็น ไม่ใช่ทั้ง 14 วัน
    · ยิงขนานทีละ 4 (requests.Session ใช้ข้ามเธรดได้สำหรับ GET ธรรมดา) · อุ่นตาราง masterStatus ก่อน กันเธรดแย่งโหลด"""
    uniq = []
    for c in claims or []:
        c = str(c or "").strip()
        if c and c not in uniq:
            uniq.append(c)
    if not uniq:
        return {}
    api.master("masterStatus", "sttcase_ID", "stt_desc")

    def one(claim: str):
        try:
            ordered = survey_order.order_claim_jobs(api.list_claim_jobs(claim))
            return claim, [{"survey_no": str(it.get("survey_no") or ""), "round": int(it["round"]),
                            "status_name": str(it.get("status_name") or "")} for it in ordered]
        except Exception as e:  # noqa: BLE001
            return claim, {"error": f"{type(e).__name__}: {e}"}

    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(ex.map(one, uniq))


#: ISURVEY listcases ตันที่ 50 แถวอยู่แล้ว (probe 2026-08-04 · ค้น 11 หลักแรกของเลขเคลมได้ 50 พอดี 08/10/69)
SEARCH_LIMIT = 50
#: หา "ครั้งที่" ให้เฉพาะเมื่อผลค้นมีไม่เกินกี่เคลม — 1 คำขอ ISURVEY ต่อเคลม (ค้นด้วยเลขเคลมเต็มใช้แถวที่ค้นได้เลย ไม่ถามซ้ำ)
SEARCH_ROUND_CLAIMS = 3


def _txt(v) -> str:
    return str(v or "").replace("\xa0", " ").strip()


def search_jobs(api: ISurveyAPI, q: str) -> dict:
    """ค้นงานบน ISURVEY ด้วยช่องค้นหาเดียวกับหน้าตรวจงานของ ISURVEY (user สั่ง 08/10/69) — **อ่านอย่างเดียว**
    q = เลขเคลม / เลขรับแจ้ง / เลขเซอร์เวย์ (พิมพ์ไม่ครบ = ค้นแบบขึ้นต้น) · ได้ทุกสถานะ ไม่ต้องเลือกช่วงวันที่
    คืน {"cases": [...], "rounds": {เลขเคลม: [{survey_no, round, status_name}] | {"error"}}, "capped": ครบ 50 แถวไหม}
    "ครั้งที่" ใช้ survey_order ตัวเดียวกับตอนดึงงาน — เคลมที่ค้นด้วยเลขเต็มเรียงจากแถวที่ได้เลย · ค้นด้วยเลขรับแจ้ง/เลขเซอร์เวย์
    ได้แถวเดียว ต้องถามทุกใบของเคลมนั้นเพิ่ม (สูงสุด SEARCH_ROUND_CLAIMS เคลม) · ไม่ตัดบริษัทนอก/งานที่ยังไม่จ่ายงานทิ้ง (หน้าเว็บบอกเองว่าดึงไม่ได้)"""
    q = _txt(q)
    try:
        rows = api.search_cases(q, limit=SEARCH_LIMIT)
    except requests.exceptions.Timeout as e:
        raise RuntimeError("ISURVEY ตอบช้ามาก (รอแล้ว 2 รอบ เกิน 2 นาที) — ลองค้นใหม่อีกครั้งในอีกสักครู่") from e
    claims: list[str] = []
    for r in rows:
        c = _txt(r.get("claim_no"))
        if c and c not in claims:
            claims.append(c)
    rounds: dict = {}
    if 0 < len(claims) <= SEARCH_ROUND_CLAIMS:
        for c in claims:
            try:
                jobs = [r for r in rows if _txt(r.get("claim_no")) == c] if c == q else api.list_claim_jobs(c)
                rounds[c] = [{"survey_no": _txt(it.get("survey_no")), "round": int(it["round"]),
                              "status_name": _txt(it.get("status_name"))} for it in survey_order.order_claim_jobs(jobs)]
            except Exception as e:  # noqa: BLE001 — หาครั้งที่ไม่ได้ไม่ควรล้มผลค้น
                rounds[c] = {"error": f"{type(e).__name__}: {e}"}
    cases = [{
        "claim_no": _txt(r.get("claim_no")),
        "notify_no": _txt(r.get("notify_no")),
        "survey_no": _txt(r.get("survey_no")),
        "status_id": _txt(r.get("sttcase_ID")),
        "status_name": _txt(r.get("status_name")),
        "surveyor_name": _txt(r.get("surveyor_name")),
        "acc_place": _txt(r.get("acc_place")),
        "acc_province": _txt(r.get("acc_province")),
        "claim_type": _txt(r.get("claim_type")),
        "accident_dt": _txt(r.get("accident_datetime")),
        "notify_dt": _txt(r.get("notify_datetime")),
        "dispatch_dt": _txt(r.get("dispatch_datetime")),
        "close_dt": _txt(r.get("close_datetime")),
        "insurer_known": _txt(r.get("survey_no")).split("-")[0].upper() in INSURER_BY_PREFIX,
    } for r in rows]
    return {"cases": cases, "rounds": rounds, "capped": len(rows) >= SEARCH_LIMIT}


def sesurvey_post(base: str, token: str, path: str, payload=None, body: bytes | None = None,
                  content_type: str | None = None, timeout: int = 120):
    """POST ไป backend se-survey ด้วย INTEGRATION_TOKEN — คืน (data, error)"""
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    elif content_type:
        headers["Content-Type"] = content_type
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}{path}", data=body or b"",
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (json.loads(e.read().decode("utf-8")) or {}).get("message") or ""
        except Exception:
            pass
        return None, f"se-survey ตอบ {e.code}" + (f": {detail}" if detail else "")
    except Exception as e:
        return None, f"เชื่อมต่อ se-survey ไม่ได้: {e}"


def sesurvey_get(base: str, token: str, path: str, timeout: int = 60):
    """GET ไป backend se-survey ด้วย INTEGRATION_TOKEN — คืน (data, error)"""
    try:
        req = urllib.request.Request(f"{base.rstrip('/')}{path}", headers={"Authorization": f"Bearer {token}"}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (json.loads(e.read().decode("utf-8")) or {}).get("message") or ""
        except Exception:
            pass
        return None, f"se-survey ตอบ {e.code}" + (f": {detail}" if detail else "")
    except Exception as e:
        return None, f"เชื่อมต่อ se-survey ไม่ได้: {e}"


def case_exists_on_web(base: str, token: str, survey_no: str) -> tuple[bool, str | None]:
    """เลขเซอร์เวย์นี้มีเคสในเว็บแล้วไหม (ทุกสถานะ ทุกต้นทาง) — คืน (มี, ข้อผิดพลาดถ้าถามไม่ได้)"""
    data, err = sesurvey_get(base, token, "/api/integrations/cases/lookup?survey_no=" + urllib.parse.quote(str(survey_no or "")))
    if err:
        return False, err
    return bool((data or {}).get("data")), None


def zip_photos(folder) -> bytes:
    """แพ็กรูปที่โหลดมาเป็น zip โครง `case/<หมวด>/<ไฟล์>` ที่ importPhotoZip ของ se-survey อ่านหมวดออก"""
    folder = Path(folder)
    cats = {}
    try:
        cats = json.loads((folder / "_categories.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in folder.rglob("*"):
            if not p.is_file() or p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"):
                continue
            cat = p.parent.name.upper() if p.parent != folder else cats.get(p.name, "OTHERS")
            z.write(p, f"case/{cat}/{p.name}")
    return buf.getvalue()


def _iso_bkk_dt(s) -> str | None:
    """'2026-06-04 22:48' (เวลาไทยของ ISURVEY listcases) → '2026-06-04T22:48:00+07:00' · อ่านไม่ออก = None"""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{1,2}):(\d{2})", str(s or "").strip())
    return f"{m.group(1)}T{int(m.group(2)):02d}:{m.group(3)}:00+07:00" if m else None


def _push_photos(api: ISurveyAPI, isurvey_case_id: str, case_id, sesurvey_url: str, token: str,
                 topup: bool = False, exclude_docs: bool = False) -> dict:
    """โหลดรูปของงานจาก ISURVEY แล้วอัปเข้าเคสบนเว็บ (zip → /photos-zip) — คืนผลสรุป ไม่ raise
    (รูปพลาดไม่ควรล้มงาน — เคสสร้างแล้ว ดึงรูปซ้ำทีหลังได้) · ใช้ทั้งใบหลักและเคสอ้างอิง (15/09/69)
    22/09/69: คืน isurvey_photo_listed = ไฟล์จริงที่ ISURVEY มี (ไม่นับซ้ำ) ให้หน้าเว็บเทียบกับที่ได้ (added+skipped) แล้วเตือนถ้าไม่ครบ ·
    topup=True → ?topup=1 (backend ยอมเติมรูปเคสที่อนุมัติแล้วถ้ายังไม่เข้า EMCS) · exclude_docs=True → ไม่เอาเอกสาร DOC_* ของ ISURVEY"""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            counts = api.download_images(isurvey_case_id, tmp, exclude_docs=exclude_docs)
            stats = getattr(api, "last_image_stats", None) or {}
            extra = {"isurvey_photo_counts": counts, "isurvey_photo_listed": stats.get("listed", sum(counts.values())),
                     "isurvey_photo_failed": stats.get("failed", 0)}
            blob = zip_photos(tmp)
            if not blob:
                return {"added": 0, "skipped": 0, "note": "ต้นทางยังไม่มีรูป", **extra}
            boundary = "----sepull"
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"zip\"; "
                    f"filename=\"photos.zip\"\r\nContent-Type: application/zip\r\n\r\n"
                    ).encode("utf-8") + blob + f"\r\n--{boundary}--\r\n".encode("utf-8")
            pdata, perr = sesurvey_post(
                sesurvey_url, token, f"/api/integrations/cases/{case_id}/photos-zip" + ("?topup=1" if topup else ""), body=body,
                content_type=f"multipart/form-data; boundary={boundary}", timeout=300)
            out = dict(((pdata or {}).get("data") or {}) if not perr else {"error": perr})
            out.update(extra)
            return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def refetch_photos(api: ISurveyAPI, claim: str, survey_no: str, case_id, sesurvey_url: str, token: str) -> dict:
    """ปุ่ม "ดึงรูปเพิ่มจาก ISURVEY" บนหน้าเคส (user สั่ง 22/09/69): เอาเฉพาะรูปที่ยังไม่มี (backend เทียบเนื้อไฟล์) ให้เคสที่มีอยู่แล้ว
    ใช้ได้จนกว่าเคสจะเข้า EMCS (backend กัน 423) · ไม่เอาเอกสาร DOC_* ที่ ISURVEY สร้างตอนปิดงาน · ไม่ raise (คืน {"error"})"""
    try:
        case = api.find_case(str(claim), str(survey_no or ""))
    except Exception as e:
        return {"error": f"หางานบน ISURVEY ไม่พบ: {type(e).__name__}: {e}"}
    return _push_photos(api, case["caseID"], case_id, sesurvey_url, token, topup=True, exclude_docs=True)


def pull_references(api: ISurveyAPI, claim: str, survey_no: str, insurer: str, sesurvey_url: str, token: str,
                    created_by: int | None = None, with_photos: bool = True,
                    strict: bool = True) -> tuple[list[dict], int | None]:
    """งานครั้งถัดไป (user เคาะ 13/09/69: อัตโนมัติ + ทุกใบก่อนหน้า · 15/09/69 เปลี่ยน: เอารูปของทุกครั้งด้วย):
    หาครั้งที่ของใบนี้จากเลขเซอร์เวย์ทุกใบของเคลม (survey_order) แล้วดึง "ครั้งก่อนหน้า" ทุกใบเข้าเว็บเป็น
    เคสอ้างอิง (reference → อนุมัติ/ปิดแล้วตั้งแต่สร้าง) เรียงตามครั้ง เพื่อให้เว็บมีประวัติครบเหมือน EMCS
    รูปเป็นของครั้งนั้น ๆ (ไม่ใช่ของครั้งที่ 1) จึงต้องอัปเข้าเคสอ้างอิงด้วย — ข้อมูลหลักที่ใบครั้งถัดไปไม่มี
    ฝั่งเว็บเติมจากครั้งที่ 1 ให้เองตอนนำเข้า (visitInherit)
    ใบที่มีในเว็บอยู่แล้ว (409 เลขเซอร์เวย์ซ้ำ) = ข้าม · ใบไหนพลาดก็ข้ามใบนั้น ไม่ล้มงานหลัก
    คืน (รายการผลรายใบ, ครั้งที่ของใบที่กำลังดึง หรือ None ถ้าหาไม่เจอ)
    strict=False (ใบหลักเป็นเคสอ้างอิงเอง — ดึงจากผลค้นหา 08/10/69): ครั้งก่อนหน้าที่ยังไม่จบและยังไม่มีในเว็บ **ข้าม** ไม่หยุด
    (ดูอย่างเดียว ไม่มีอะไรต้องตรวจ/เข้า EMCS ต่อ จึงไม่ต้องบังคับลำดับ) — ใบที่ข้ามยังไม่ถูกปิดผิด ๆ ดึงเข้าตรวจทีหลังได้ตามปกติ"""
    ordered = survey_order.order_claim_jobs(api.list_claim_jobs(claim))
    k = survey_order.round_of(ordered, survey_no)
    refs: list[dict] = []
    if not k or k <= 1:
        return refs, k
    # 19/09/69 user: ครั้งก่อนหน้าเป็นเคสอ้างอิงได้เฉพาะที่จบงาน (100) — ใบที่ยังไม่จบต้องมีในเว็บเป็นงานปกติแล้ว (ดึงไปก่อนหน้า)
    # ไม่งั้นหยุดทั้งการดึง แล้วบอกให้ดึงใบนั้นก่อน · ที่มีในเว็บแล้วไม่ดึงซ้ำ
    open_on_web: set[str] = set()
    open_skipped: dict[str, str] = {}
    blockers: list[str] = []
    for it in ordered[: k - 1]:
        st = str(it.get("sttcase_ID") or "").strip()
        if st == CLOSED_STATUS_ID:
            continue
        no = str(it.get("survey_no") or "")
        found, lerr = case_exists_on_web(sesurvey_url, token, no)
        if found:
            open_on_web.add(no)
            continue
        name = str(it.get("status_name") or st or "?")
        if not strict:
            open_skipped[no] = f'ยังไม่จบงานบน ISURVEY (สถานะ "{name}") — ไม่ดึงเป็นอ้างอิง'
            continue
        if st == REVIEW_STATUS_ID:
            msg = f'ครั้งที่ {it["round"]} ({no}) ยังเป็น "{name}" บน ISURVEY — ดึงใบนั้นเข้ามาตรวจก่อน'
        else:
            msg = f'ครั้งที่ {it["round"]} ({no}) ยังไม่จบงานบน ISURVEY (สถานะ "{name}") — รอให้ถึง "รอตรวจข้อมูล" แล้วดึงใบนั้นเข้ามาตรวจก่อน'
        if lerr:
            msg += f" (ตรวจกับเว็บไม่สำเร็จ: {lerr})"
        blockers.append(msg)
    if blockers:
        raise OpenRoundError(f"ยังดึงครั้งที่ {k} ไม่ได้ — " + " · ".join(blockers))
    for it in ordered[: k - 1]:
        entry = {"survey_no": str(it.get("survey_no") or ""), "round": int(it["round"]), "caseId": None,
                 "skipped": None, "photos": None}
        if entry["survey_no"] in open_on_web:
            entry["skipped"] = "มีในระบบแล้ว (งานปกติ ยังไม่จบบน ISURVEY)"
            refs.append(entry)
            continue
        if entry["survey_no"] in open_skipped:
            entry["skipped"] = open_skipped[entry["survey_no"]]
            refs.append(entry)
            continue
        try:
            payload = build_case(api, it["caseID"], it)
            payload["insurance_company"] = insurer
            if created_by:
                payload["created_by"] = int(created_by)
            payload["visit_no"] = int(it["round"])
            apply_visit_rules(payload, it["round"])     # ครั้งที่ 2+: ความคิดเห็นพนักงาน → ผลการดำเนินงาน (22/09/69)
            payload["reference"] = {"closed_at": _iso_bkk_dt(it.get("close_datetime")), "round": int(it["round"]),
                                    "status": str(it.get("status_name") or "")}
            data, err = sesurvey_post(sesurvey_url, token, "/api/integrations/cases/import", payload=payload)
            if err:
                entry["skipped"] = "มีในระบบแล้ว" if "ตอบ 409" in err else err
            else:
                entry["caseId"] = ((data or {}).get("data") or {}).get("caseId")
                if with_photos and entry["caseId"]:
                    entry["photos"] = _push_photos(api, it["caseID"], entry["caseId"], sesurvey_url, token)
        except Exception as e:
            entry["skipped"] = f"{type(e).__name__}: {e}"
        refs.append(entry)
    return refs, k


def pull_case(api: ISurveyAPI, claim: str, survey_no: str, sesurvey_url: str, token: str,
              created_by: int | None = None, with_photos: bool = True,
              as_reference: bool = False, before_import=None) -> tuple[dict | None, str | None]:
    """ดึงงาน 1 เรื่อง → สร้างเคสบน se-survey (+รูป) — คืน (result, error)
    งานครั้งถัดไป: ดึงครั้งก่อนหน้าที่ยังไม่มีในเว็บมาเป็นเคสอ้างอิงก่อน แล้วใบนี้ได้ visit_no ตามเลขเซอร์เวย์ (13/09/69)
    as_reference=True (ปุ่ม "ดึงเข้า (ดูอย่างเดียว)" จากผลค้นหา — user เคาะ 08/10/69): **เฉพาะงานที่จบงานแล้ว** เข้ามาเป็นเคสอ้างอิง
    พร้อมรูป (อนุมัติแล้ว/ถือว่าเข้า EMCS แล้วตั้งแต่สร้าง — ไม่เข้าคิวตรวจ ไม่เข้ารายการบอท) กันหัวหน้าอนุมัติซ้ำ/บอทเข้า EMCS ซ้ำ
    before_import(): เรียกก่อนสร้างเคสใบหลัก (อ่าน ISURVEY ครบแล้ว) — บัญชีกลางใช้เช็คว่า session ไม่หลุดระหว่างอ่าน
    (หลุด = ISURVEY ตอบคู่กรณี/ชิ้นส่วน "ว่าง" เงียบ ๆ) แล้ว raise ให้ผู้เรียกอ่านใหม่ก่อนมีเคส (08/10/69)"""
    prefix = str(survey_no or "").split("-")[0].strip().upper()
    insurer = INSURER_BY_PREFIX.get(prefix)
    if not insurer:
        return None, (f"ไม่รู้จักคำนำหน้าเลขเซอร์เวย์ {prefix or '(ว่าง)'} — "
                      "บอกไม่ได้ว่างานของบริษัทไหน จึงไม่ดึงเข้าระบบ")
    try:
        case = api.find_case(claim, survey_no)
        cid = case["caseID"]
    except Exception as e:
        return None, f"อ่านงานจาก ISURVEY ไม่ได้: {type(e).__name__}: {e}"
    # กติกา user 13/09/69: ดึงได้เฉพาะสถานะ "รอตรวจข้อมูล" (40) / "จบงาน" (100) — สถานะอื่นยังทำงานอยู่/ถูกยกเลิก ไม่ดึง
    # (หน้าเว็บซ่อนปุ่มอยู่แล้ว ที่นี่กันอีกชั้นเผื่อเรียกตรง) · ครั้งก่อนหน้าที่ระบบดึงตามเป็นอ้างอิงใช้กติกาของ survey_order แทน
    st_id = str(case.get("sttcase_ID") or "").strip()
    if as_reference and st_id != CLOSED_STATUS_ID:
        return None, 'ดึงแบบดูอย่างเดียวได้เฉพาะงานที่ "จบงาน" บน ISURVEY แล้ว — งานที่ยังไม่จบใช้ปุ่ม "ดึงเข้า" ตามปกติ'
    if st_id and st_id not in PULLABLE_STATUS_IDS:
        try:
            st_name = api.master("masterStatus", "sttcase_ID", "stt_desc").get(st_id, st_id)
        except Exception:
            st_name = st_id
        return None, f'งานนี้สถานะ "{st_name}" บน ISURVEY — ดึงได้เฉพาะ "รอตรวจข้อมูล" หรือ "จบงาน"'
    try:
        payload = build_case(api, cid, case)
    except Exception as e:
        return None, f"อ่านงานจาก ISURVEY ไม่ได้: {type(e).__name__}: {e}"

    payload["insurance_company"] = insurer
    if created_by:
        payload["created_by"] = int(created_by)      # เจ้าของเคส = คนที่กดดึง (backend ตรวจสิทธิ์อีกชั้น)

    # ครั้งก่อนหน้าไปก่อน (ลำดับสร้างในเว็บจะตรงครั้ง) — หาลำดับไม่ได้ก็ดึงใบนี้ตามปกติ แค่ไม่มีครั้งที่
    refs: list[dict] = []
    visit_no = None
    try:
        refs, visit_no = pull_references(api, claim, survey_no, insurer, sesurvey_url, token, created_by,
                                         with_photos=with_photos, strict=not as_reference)
    except OpenRoundError as e:
        return None, str(e)     # 19/09/69: ครั้งก่อนหน้ายังไม่จบและยังไม่มีในเว็บ → ไม่ดึงใบนี้ ให้หัวหน้าดึงใบนั้นก่อน
    except Exception as e:
        refs = [{"survey_no": "", "round": 0, "caseId": None, "skipped": f"หาลำดับครั้งของเคลมไม่ได้: {type(e).__name__}"}]
    if visit_no:
        payload["visit_no"] = int(visit_no)
    apply_visit_rules(payload, visit_no)     # ครั้งที่ 2+: ความคิดเห็นพนักงาน → ผลการดำเนินงาน · ไม่รู้ครั้ง = ครั้งที่ 1 (22/09/69)
    if as_reference:
        # เวลาปิดบน ISURVEY = เวลาที่ถือว่า "ตรวจแล้ว/เข้า EMCS แล้ว" ของเคสอ้างอิง (backend ใช้ closed_at + round เท่านั้น)
        payload["reference"] = {"closed_at": _iso_bkk_dt(case.get("close_datetime")), "round": int(visit_no or 1),
                                "status": "จบงาน"}

    if before_import is not None:
        before_import()
    data, err = sesurvey_post(sesurvey_url, token, "/api/integrations/cases/import", payload=payload)
    if err:
        return None, err
    result = (data or {}).get("data") or {}
    case_id = result.get("caseId")
    result["visit_no"] = visit_no
    result["references"] = refs
    result["as_reference"] = bool(as_reference)

    if with_photos and case_id:
        ph = _push_photos(api, cid, case_id, sesurvey_url, token)
        counts = ph.pop("isurvey_photo_counts", None)
        if counts is not None:
            result["isurvey_photo_counts"] = counts
        result["photos"] = ph
    return result, None
