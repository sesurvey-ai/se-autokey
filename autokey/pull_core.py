# -*- coding: utf-8 -*-
"""แกนดึงงาน ISURVEY → se-survey แบบ "บัญชีต่อครั้ง" — ไม่ผูกกับ .env / ไม่มี client กลาง

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
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .isurvey_api import ISurveyAPI
from .isurvey_to_sesurvey import build_case
from . import survey_order

ISURVEY_STATUS_PENDING = "รอตรวจข้อมูล"
ISURVEY_EMCS_SENT = "send"
REPORT_URL = "https://cloud.isurvey.mobi/web/php/report/get_data_report.php"
#: สถานะ ISURVEY (masterStatus.sttcase_ID) ที่กด "ดึงเข้า" ได้ — 40 รอตรวจข้อมูล · 100 จบงาน (user เคาะ 13/09/69)
PULLABLE_STATUS_IDS = {"40", "100"}

#: ต้องตรงกับ INSURER_BY_JOB_PREFIX ของหน้า import-xml บนเว็บ se-survey
#: ⛔ prefix ที่ไม่รู้จัก = หยุด ห้าม fallback (เข้าผิดบริษัทใน EMCS ลบไม่ได้)
INSURER_BY_PREFIX = {
    "SETP": "บริษัท ไทยไพบูลย์ประกันภัย จำกัด (มหาชน)",
    "SEABI": "ไอโออิกรุงเทพประกันภัย",
}


def make_client(username: str, password: str) -> ISurveyAPI:
    """ISurveyAPI ที่ล็อกอินด้วยบัญชีที่ส่งมา — ไม่อ่าน .env (ช่องบังคับอื่นของ Config ใส่ว่าง)"""
    kw = {}
    for f in dataclasses.fields(Config):
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            kw[f.name] = ""
    kw.update(isurvey_username=username, isurvey_password=password)
    api = ISurveyAPI(Config(**kw))
    api.login()
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
    r = api.s.get(REPORT_URL, timeout=120, params={
        "con_date": 2, "date_from": date_from, "date_to": date_to,
        "report_type": "enquiry", "page": 1, "start": 0, "limit": 5000})
    r.raise_for_status()
    d = r.json()
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


def _push_photos(api: ISurveyAPI, isurvey_case_id: str, case_id, sesurvey_url: str, token: str) -> dict:
    """โหลดรูปของงานจาก ISURVEY แล้วอัปเข้าเคสบนเว็บ (zip → /photos-zip) — คืนผลสรุป ไม่ raise
    (รูปพลาดไม่ควรล้มงาน — เคสสร้างแล้ว ดึงรูปซ้ำทีหลังได้) · ใช้ทั้งใบหลักและเคสอ้างอิง (15/09/69)"""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            counts = api.download_images(isurvey_case_id, tmp)
            blob = zip_photos(tmp)
            if not blob:
                return {"added": 0, "note": "ต้นทางยังไม่มีรูป", "isurvey_photo_counts": counts}
            boundary = "----sepull"
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"zip\"; "
                    f"filename=\"photos.zip\"\r\nContent-Type: application/zip\r\n\r\n"
                    ).encode("utf-8") + blob + f"\r\n--{boundary}--\r\n".encode("utf-8")
            pdata, perr = sesurvey_post(
                sesurvey_url, token, f"/api/integrations/cases/{case_id}/photos-zip", body=body,
                content_type=f"multipart/form-data; boundary={boundary}", timeout=300)
            out = dict(((pdata or {}).get("data") or {}) if not perr else {"error": perr})
            out["isurvey_photo_counts"] = counts
            return out
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def pull_references(api: ISurveyAPI, claim: str, survey_no: str, insurer: str, sesurvey_url: str, token: str,
                    created_by: int | None = None, with_photos: bool = True) -> tuple[list[dict], int | None]:
    """งานครั้งถัดไป (user เคาะ 13/09/69: อัตโนมัติ + ทุกใบก่อนหน้า · 15/09/69 เปลี่ยน: เอารูปของทุกครั้งด้วย):
    หาครั้งที่ของใบนี้จากเลขเซอร์เวย์ทุกใบของเคลม (survey_order) แล้วดึง "ครั้งก่อนหน้า" ทุกใบเข้าเว็บเป็น
    เคสอ้างอิง (reference → อนุมัติ/ปิดแล้วตั้งแต่สร้าง) เรียงตามครั้ง เพื่อให้เว็บมีประวัติครบเหมือน EMCS
    รูปเป็นของครั้งนั้น ๆ (ไม่ใช่ของครั้งที่ 1) จึงต้องอัปเข้าเคสอ้างอิงด้วย — ข้อมูลหลักที่ใบครั้งถัดไปไม่มี
    ฝั่งเว็บเติมจากครั้งที่ 1 ให้เองตอนนำเข้า (visitInherit)
    ใบที่มีในเว็บอยู่แล้ว (409 เลขเซอร์เวย์ซ้ำ) = ข้าม · ใบไหนพลาดก็ข้ามใบนั้น ไม่ล้มงานหลัก
    คืน (รายการผลรายใบ, ครั้งที่ของใบที่กำลังดึง หรือ None ถ้าหาไม่เจอ)"""
    ordered = survey_order.order_claim_jobs(api.list_claim_jobs(claim))
    k = survey_order.round_of(ordered, survey_no)
    refs: list[dict] = []
    if not k or k <= 1:
        return refs, k
    for it in ordered[: k - 1]:
        entry = {"survey_no": str(it.get("survey_no") or ""), "round": int(it["round"]), "caseId": None,
                 "skipped": None, "photos": None}
        try:
            payload = build_case(api, it["caseID"], it)
            payload["insurance_company"] = insurer
            if created_by:
                payload["created_by"] = int(created_by)
            payload["visit_no"] = int(it["round"])
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
              created_by: int | None = None, with_photos: bool = True) -> tuple[dict | None, str | None]:
    """ดึงงาน 1 เรื่อง → สร้างเคสบน se-survey (+รูป) — คืน (result, error)
    งานครั้งถัดไป: ดึงครั้งก่อนหน้าที่ยังไม่มีในเว็บมาเป็นเคสอ้างอิงก่อน แล้วใบนี้ได้ visit_no ตามเลขเซอร์เวย์ (13/09/69)"""
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
                                         with_photos=with_photos)
    except Exception as e:
        refs = [{"survey_no": "", "round": 0, "caseId": None, "skipped": f"หาลำดับครั้งของเคลมไม่ได้: {type(e).__name__}"}]
    if visit_no:
        payload["visit_no"] = int(visit_no)

    data, err = sesurvey_post(sesurvey_url, token, "/api/integrations/cases/import", payload=payload)
    if err:
        return None, err
    result = (data or {}).get("data") or {}
    case_id = result.get("caseId")
    result["visit_no"] = visit_no
    result["references"] = refs

    if with_photos and case_id:
        ph = _push_photos(api, cid, case_id, sesurvey_url, token)
        counts = ph.pop("isurvey_photo_counts", None)
        if counts is not None:
            result["isurvey_photo_counts"] = counts
        result["photos"] = ph
    return result, None
