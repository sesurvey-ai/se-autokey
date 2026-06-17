"""แจ้งสถานะ "ส่งงานแล้ว" กลับ ISURVEY (คนละ host/auth กับฝั่งอ่าน cloud)

flow ปลายทาง: บอทกรอก EMCS draft → user ตรวจ + กด "ส่งงานใหม่" ใน EMCS เอง →
เรียก report_sent() แจ้ง ISURVEY ว่าเคลมนี้คีย์เสร็จแล้ว (โดยใคร เมื่อไหร่)

ความปลอดภัย: ผู้เรียกต้อง "ยืนยันว่าส่งงานใน EMCS แล้วจริง" ก่อน (emcs.is_report_submitted)
— report_sent ตัวนี้แค่ยิง POST ไม่ได้ตรวจสถานะเอง
"""
import requests

from .browser import log

# คนคีย์รับผิดชอบตามเลขท้ายของเลขเคลม (คนละ 2 เลข)
KEYER_BY_LAST_DIGIT = {
    "0": "วรนุช น้ำพุ", "1": "วรนุช น้ำพุ",
    "2": "กัญญารัตน์ เสนคำ", "3": "กัญญารัตน์ เสนคำ",
    "4": "วิสุดา ดอนหมัน", "5": "วิสุดา ดอนหมัน",
    "6": "นิสากร เปรมปรีดิ์", "7": "นิสากร เปรมปรีดิ์",
    "8": "สุทิษา พงษ์แขก", "9": "สุทิษา พงษ์แขก",
}


def keyer_for(claim: str) -> str:
    """คืนชื่อคนคีย์ตามเลขท้ายของเลขเคลม ('' ถ้าหาเลขท้ายไม่ได้)"""
    digits = "".join(ch for ch in str(claim) if ch.isdigit())
    return KEYER_BY_LAST_DIGIT.get(digits[-1], "") if digits else ""


def report_sent(cfg, claim: str, invoice: str, keyer: str = "",
                when: str = "", dry_run: bool = False) -> dict:
    """POST แจ้ง ISURVEY ว่าเคลมนี้ส่งงานแล้ว — คืน {ok, status, text, payload}

    *ไม่ตรวจสถานะ EMCS เอง* — ผู้เรียกต้องยืนยันว่าส่งงานจริงก่อน (gate)
    dry_run=True = ประกอบ payload แต่ไม่ยิงจริง (ไว้ตรวจก่อน)
    """
    keyer = keyer or keyer_for(claim)
    payload = {
        "survey_no": invoice,
        "claim_no": claim,
        "EMCSstatus": "send",      # ISURVEY กำหนดมา มีค่าเดียว
        "EMCSby": keyer,
        "EMCSdate": when,          # 'YYYY-MM-DD HH:MM:SS' (ผู้เรียกใส่เวลาจริงมา)
        "user_id": cfg.isurvey_report_user,
        "password": cfg.isurvey_report_pass,
    }
    if not (cfg.isurvey_report_user and cfg.isurvey_report_pass):
        return {"ok": False, "status": 0,
                "text": "ยังไม่ได้ตั้ง ISURVEY_REPORT_USER/PASS ใน .env", "payload": payload}
    if not keyer:
        return {"ok": False, "status": 0,
                "text": f"หาคนคีย์จากเลขท้ายเคลม {claim} ไม่ได้", "payload": payload}
    if dry_run:
        safe = dict(payload, password="***")
        log(f"   [dry-run] ISURVEY report payload: {safe}")
        return {"ok": True, "status": 0, "text": "(dry-run ไม่ยิงจริง)", "payload": payload}

    log(f"   ยิงแจ้ง ISURVEY: เคลม {claim} ส่งโดย {keyer}")
    try:
        r = requests.post(cfg.isurvey_report_url, json=payload, timeout=30)
        ok = (r.status_code == 200)
        try:                                  # 200 แต่ success:false ก็ถือว่าไม่ผ่าน
            j = r.json()
            if isinstance(j, dict) and "success" in j:
                ok = ok and bool(j["success"])
        except Exception:
            pass
        return {"ok": ok, "status": r.status_code, "text": r.text[:300], "payload": payload}
    except Exception as e:
        return {"ok": False, "status": 0, "text": f"{type(e).__name__}: {e}",
                "payload": payload}
