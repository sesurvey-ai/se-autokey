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
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .isurvey_api import ISurveyAPI
from .isurvey_to_sesurvey import build_case

ISURVEY_STATUS_PENDING = "รอตรวจข้อมูล"
ISURVEY_EMCS_SENT = "send"
REPORT_URL = "https://cloud.isurvey.mobi/web/php/report/get_data_report.php"

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
            "finish_dt": x.get("finish_dt") or "",
            "status": x.get("stt_desc") or "",
            "emcs_sent": str(x.get("EMCSstatus") or "") == ISURVEY_EMCS_SENT,
        })
    rows.sort(key=lambda r: str(r.get("finish_dt") or ""), reverse=True)
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


def pull_case(api: ISurveyAPI, claim: str, survey_no: str, sesurvey_url: str, token: str,
              created_by: int | None = None, with_photos: bool = True) -> tuple[dict | None, str | None]:
    """ดึงงาน 1 เรื่อง → สร้างเคสบน se-survey (+รูป) — คืน (result, error)"""
    prefix = str(survey_no or "").split("-")[0].strip().upper()
    insurer = INSURER_BY_PREFIX.get(prefix)
    if not insurer:
        return None, (f"ไม่รู้จักคำนำหน้าเลขเซอร์เวย์ {prefix or '(ว่าง)'} — "
                      "บอกไม่ได้ว่างานของบริษัทไหน จึงไม่ดึงเข้าระบบ")
    try:
        case = api.find_case(claim, survey_no)
        cid = case["caseID"]
        payload = build_case(api, cid, case)
    except Exception as e:
        return None, f"อ่านงานจาก ISURVEY ไม่ได้: {type(e).__name__}: {e}"

    payload["insurance_company"] = insurer
    if created_by:
        payload["created_by"] = int(created_by)      # เจ้าของเคส = คนที่กดดึง (backend ตรวจสิทธิ์อีกชั้น)
    data, err = sesurvey_post(sesurvey_url, token, "/api/integrations/cases/import", payload=payload)
    if err:
        return None, err
    result = (data or {}).get("data") or {}
    case_id = result.get("caseId")

    if with_photos and case_id:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                counts = api.download_images(cid, tmp)
                blob = zip_photos(tmp)
                if blob:
                    boundary = "----sepull"
                    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"zip\"; "
                            f"filename=\"photos.zip\"\r\nContent-Type: application/zip\r\n\r\n"
                            ).encode("utf-8") + blob + f"\r\n--{boundary}--\r\n".encode("utf-8")
                    pdata, perr = sesurvey_post(
                        sesurvey_url, token, f"/api/integrations/cases/{case_id}/photos-zip", body=body,
                        content_type=f"multipart/form-data; boundary={boundary}", timeout=300)
                    result["photos"] = (pdata or {}).get("data") if not perr else {"error": perr}
                else:
                    result["photos"] = {"added": 0, "note": "ต้นทางยังไม่มีรูป"}
                result["isurvey_photo_counts"] = counts
        except Exception as e:
            # รูปพลาดไม่ควรล้มทั้งงาน — เคสสร้างแล้ว ดึงรูปซ้ำทีหลังได้
            result["photos"] = {"error": f"{type(e).__name__}: {e}"}
    return result, None
