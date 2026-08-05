"""สมุดงาน: บันทึกเลขเคลม/เลขเซอร์เวย์ที่ทำไปแล้ว ไว้ดูย้อนหลัง

ทำไมต้องมี: log ของแต่ละรอบ (`runs/logs/run_*.log`) ละเอียดก็จริง แต่ต้องรู้ก่อนว่า
งานนั้นรันตอนไหนถึงจะเปิดไฟล์ถูก และการ์ดบนหน้าเว็บหายเมื่อรีสตาร์ต — เลยเก็บ
"สรุปหนึ่งบรรทัดต่อเหตุการณ์" ไว้ถาวรอีกที่ ให้ค้นด้วยเลขเคลม/เลขเซอร์เวย์ได้

รูปแบบ: JSONL (บรรทัดละ 1 เหตุการณ์) — append อย่างเดียว ไม่แก้ของเดิม
ไฟล์เสียบางบรรทัดก็ยังอ่านบรรทัดที่เหลือได้ ต่างจาก JSON ก้อนเดียวที่พังทั้งไฟล์

event: 'draft' = กรอกครบเป็น draft แล้ว / 'sent' = ส่งงาน+แจ้ง ISURVEY สำเร็จ
"""
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
JOBS_FILE = BASE_DIR / "runs" / "jobs.jsonl"

EVENTS = ("draft", "sent")


def record(event: str, claim: str, invoice: str = "", esurvey: str = "",
           keyer: str = "", work_type: str = "", note: str = "") -> bool:
    """บันทึก 1 เหตุการณ์ — คืน True เมื่อเขียนสำเร็จ

    ล้มเหลวไม่โยน error: สมุดงานเป็นของบันทึกไว้ดู ไม่ควรทำให้งานหลักพัง
    """
    row = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "event": str(event or "").strip(),
        "claim": str(claim or "").strip(),
        "invoice": str(invoice or "").strip(),
        "esurvey": str(esurvey or "").strip(),
        "keyer": str(keyer or "").strip(),
        "work_type": str(work_type or "").strip(),
        "note": str(note or "").strip(),
    }
    try:
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(JOBS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read_jobs(limit: int = 500, q: str = "") -> list:
    """อ่านสมุดงาน — ใหม่สุดก่อน; q = ค้นด้วยเลขเคลม/เซอร์เวย์/e-Survey/ชื่อคนคีย์

    บรรทัดที่ parse ไม่ได้ = ข้ามไป (ไม่ทำให้ทั้งไฟล์ใช้ไม่ได้)
    """
    try:
        lines = JOBS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    q = str(q or "").strip().lower()
    out = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if q and q not in " ".join(
                str(row.get(k, "")) for k in
                ("claim", "invoice", "esurvey", "keyer", "work_type")).lower():
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out
