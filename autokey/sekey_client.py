"""บันทึก "งานที่ทำเสร็จแล้ว" ลงฐานข้อมูลกลางของ se-key (key.sesurvey.cloud)

se-key คือระบบทะเบียนงานคีย์ (Chrome extension + Express + SQLite) ที่เก็บว่า
เลขเซอร์เวย์ไหนถูกคีย์โดยใคร ส่ง iSurvey แล้วหรือยัง — โปรเจกต์นี้ (se-autokey)
ยิงเข้า REST API ตัวเดียวกัน เพื่อให้ "งานที่บอททำ" โผล่ในทะเบียน/รายงานเดียวกัน

ใช้ 2 จังหวะ:
  - ก่อนกรอก EMCS: check_survey() ตรวจว่าเลขเซอร์เวย์ซ้ำกับ DB ไหม (ซ้ำ = ข้าม)
  - หลังกดส่งงาน (live): save_record() บันทึกงาน + mark "ส่งแล้ว"

ปลอดภัย:
  - ไม่ได้ตั้ง SE_KEY_API_URL/SE_KEY_API_KEY ใน .env → enabled()=False (no-op ทั้งหมด)
  - ตรวจซ้ำพลาด (เน็ตล่ม/auth) → คืน ok=False ให้ผู้เรียก fail-open (ทำงานต่อ ไม่บล็อก)
"""
import requests

from .browser import log
from .isurvey_report import keyer_for

# work_type เริ่มต้นของงานที่บอททำ (se-autokey ทำเคลมแห้ง = งานต้นเป็นหลัก)
DEFAULT_WORK_TYPE = "งานต้น"
TIMEOUT = 20


def enabled(cfg) -> bool:
    """เปิดใช้บันทึก se-key เมื่อมีทั้ง url + api key (ไม่งั้นทุกฟังก์ชันเป็น no-op)"""
    return bool(getattr(cfg, "sekey_api_url", "")
                and getattr(cfg, "sekey_api_key", ""))


def _base(cfg) -> str:
    return str(getattr(cfg, "sekey_api_url", "") or "").rstrip("/")


def _headers(cfg) -> dict:
    return {"X-API-Key": cfg.sekey_api_key, "Content-Type": "application/json"}


def _parse_check(body) -> dict:
    """แปลงผล GET /api/records (filter survey_no) → สรุปว่าซ้ำไหม
    survey_count = จำนวนแถวของเลขนี้, survey_sent_count = ที่ส่ง iSurvey แล้ว"""
    if not isinstance(body, dict):
        return {"exists": False, "sent": False, "count": 0, "sent_count": 0}
    count = int(body.get("survey_count") or 0)
    sent_count = int(body.get("survey_sent_count") or 0)
    return {"exists": count > 0, "sent": sent_count > 0,
            "count": count, "sent_count": sent_count}


def check_survey(cfg, survey_no: str) -> dict:
    """ตรวจว่าเลขเซอร์เวย์มีใน se-key DB แล้วหรือยัง
    คืน {ok, exists, sent, count, sent_count, error}
    ok=False = ตรวจไม่ได้ (เน็ต/auth) — ผู้เรียกควร fail-open (ทำงานต่อ)"""
    survey_no = (survey_no or "").strip()
    base_empty = {"exists": False, "sent": False, "count": 0, "sent_count": 0}
    if not survey_no:
        return {"ok": True, "error": "", **base_empty}
    try:
        r = requests.get(f"{_base(cfg)}/api/records",
                         params={"survey_no": survey_no, "limit": 1},
                         headers=_headers(cfg), timeout=TIMEOUT)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:120]}",
                    **base_empty}
        return {"ok": True, "error": "", **_parse_check(r.json())}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", **base_empty}


def save_record(cfg, claim_no: str, survey_no: str, keyer: str = "",
                work_type: str = DEFAULT_WORK_TYPE, invoice_mix: str = "",
                mark_sent: bool = True, dry_run: bool = False) -> dict:
    """บันทึกงานที่ทำเสร็จลง se-key DB (idempotent upsert) + mark "ส่งแล้ว" ถ้า mark_sent
    คืน {ok, status, text, record_id, upserted, sent, payload}

    mark_sent=True (se-autokey แจ้ง iSurvey เองในจังหวะเดียวกัน) → PATCH isurvey_sent=1
    POST บังคับ survey_no ตรง format (SEABI-12หลัก ฯลฯ) — ผิด format จะได้ ok=False
    """
    keyer = keyer or keyer_for(claim_no)
    payload = {
        "claim_no": str(claim_no).strip(),
        "survey_no": str(survey_no).strip(),
        "keyer": keyer,
        "work_type": work_type,
        "invoice_mix": invoice_mix,
        "upsert_pending": True,
    }
    fail = {"record_id": None, "upserted": "", "sent": False, "payload": payload}
    if not enabled(cfg):
        return {"ok": False, "status": 0,
                "text": "ยังไม่ได้ตั้ง SE_KEY_API_URL/SE_KEY_API_KEY ใน .env", **fail}
    if not payload["claim_no"]:
        return {"ok": False, "status": 0, "text": "ไม่มีเลขเคลม", **fail}
    if dry_run:
        log(f"   [dry-run] se-key save payload: {payload} (mark_sent={mark_sent})")
        return {"ok": True, "status": 0, "text": "(dry-run ไม่ยิงจริง)",
                "record_id": None, "upserted": "dry", "sent": mark_sent,
                "payload": payload}
    try:
        r = requests.post(f"{_base(cfg)}/api/records", json=payload,
                          headers=_headers(cfg), timeout=TIMEOUT)
        if r.status_code not in (200, 201):
            return {"ok": False, "status": r.status_code, "text": r.text[:200], **fail}
        body = r.json() if r.text else {}
        rec = body.get("record") or {}
        rid = rec.get("id")
        upserted = body.get("upserted", "")
        sent = bool(rec.get("isurvey_sent"))
        # mark "ส่งแล้ว" — PATCH เฉพาะตอนยังไม่ใช่ 1 (กรณี insert/updated ใหม่)
        if mark_sent and rid and not sent:
            pr = requests.patch(f"{_base(cfg)}/api/records/{rid}",
                                json={"isurvey_sent": 1},
                                headers=_headers(cfg), timeout=TIMEOUT)
            if pr.status_code == 200:
                sent = True
            else:
                log(f"   ⚠️ บันทึก se-key แล้ว (id {rid}) แต่ mark 'ส่งแล้ว' ไม่ได้: "
                    f"HTTP {pr.status_code}")
        return {"ok": True, "status": r.status_code, "text": f"id={rid} {upserted}".strip(),
                "record_id": rid, "upserted": upserted, "sent": sent, "payload": payload}
    except Exception as e:
        return {"ok": False, "status": 0, "text": f"{type(e).__name__}: {e}", **fail}


def derive_base_type(survey_no) -> str:
    """default ประเภทงานจากเลขเซอร์เวย์ (เหมือน se-key extension):
    ขึ้นต้น 'SESV' → 'SESV', อื่น ๆ → 'งานต้น'
    (งานต้น vs งานตาม จริง ๆ แยกด้วย ddlAdd_No บนหน้า eClaim3 ซึ่ง webui มองไม่เห็น
    → ปล่อยให้ผู้ใช้เลือกเองในแผง โดย default เป็น 'งานต้น')"""
    return "SESV" if str(survey_no or "").strip().upper().startswith("SESV") else "งานต้น"


def build_payloads(claim_no, survey_no, keyer="", base_type="งานต้น",
                   batch=False, mix_values=None) -> list:
    """สร้างรายการ row สำหรับบันทึก se-key — ลอกตรรกะ payload จาก extension (content.js):
      - ไม่ batch         → 1 row (work_type=base_type, invoice_mix='')
      - งานรวม (batch)    → primary(work_type=base_type, mix='')
                            + followup ต่อ 1 invoice (work_type='งานรวม', mix=survey_no)
      - SESV (ล็อก batch) → primary(work_type='SESV', mix=mixValues[0])  ← SEABI ตัวแรก
                            + followup จาก mixValues[1:] (work_type='งานรวม', mix=survey_no)
    คืน list ของ dict {claim_no, survey_no, keyer, work_type, invoice_mix}"""
    keyer = keyer or keyer_for(claim_no)
    survey_no = str(survey_no or "").strip()
    mix = [str(m).strip() for m in (mix_values or []) if str(m).strip()]
    is_sesv = (base_type == "SESV")
    if is_sesv:
        batch = True   # SESV เคลมเงินเองไม่ได้ ต้องอิง SEABI invoice เสมอ
    out = []
    if batch:
        primary_mix = (mix[0] if mix else "") if is_sesv else ""
        followups = mix[1:] if is_sesv else mix
        out.append({"claim_no": claim_no, "survey_no": survey_no, "keyer": keyer,
                    "work_type": base_type, "invoice_mix": primary_mix})
        for v in followups:
            out.append({"claim_no": claim_no, "survey_no": v, "keyer": keyer,
                        "work_type": "งานรวม", "invoice_mix": survey_no})
    else:
        out.append({"claim_no": claim_no, "survey_no": survey_no, "keyer": keyer,
                    "work_type": base_type, "invoice_mix": ""})
    return out


def save_many(cfg, payloads, mark_sent=True, dry_run=False) -> list:
    """บันทึกหลาย row (จาก build_payloads) ลง se-key — คืน list ผลของ save_record แต่ละตัว"""
    results = []
    for p in payloads:
        results.append(save_record(
            cfg, p["claim_no"], p["survey_no"], keyer=p.get("keyer", ""),
            work_type=p.get("work_type", DEFAULT_WORK_TYPE),
            invoice_mix=p.get("invoice_mix", ""), mark_sent=mark_sent, dry_run=dry_run))
    return results
