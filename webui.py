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
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent

# marker ที่ main.py (ผ่าน browser.wait_for_manual_fill) พิมพ์ออก stdout
# เมื่อต้องการให้คนกรอกข้อมูลเอง — ต้องตรงกับค่าใน autokey/browser.py
MANUAL_MARKER = "@@MANUAL_FILL@@"
SUBMIT_MARKER = "@@READY_SUBMIT@@"   # ต้องตรงกับ autokey/browser.py (พร้อมส่งงาน)
SELECT_MARKER = "@@SELECT_IMAGES@@"  # ต้องตรงกับ autokey/browser.py (เลือกรูปอัปโหลด)
INJURY_MARKER = "@@INJURY_INPUTS@@"  # ต้องตรงกับ autokey/browser.py (กรอกข้อมูลผู้บาดเจ็บ)

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
        cmd += ["--skip-images"]
    if params.get("nosaveprice"):
        cmd += ["--no-save-price"]

    # โหมดเคลมสด/นัดหมาย/ติดตาม: ปลดด่านเคลมแห้ง (--allow-fresh) + อ่านด้วย scrape
    # (--scrape) เพื่อดึงคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สินจาก XML — API อ่าน tab-4/5/6 ไม่ได้
    # (ผู้บาดเจ็บ/ทรัพย์สินยังต้องกรอกเองบน EMCS — ฟังก์ชันกรอกยังไม่รองรับ)
    if params.get("claimmode") == "fresh":
        cmd += ["--allow-fresh", "--scrape"]

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
    """เริ่มงานใหม่ — คืน (run_id, error). เต็มขีดจำกัดจะคืน error"""
    cmd, err = _build_cmd(params)
    if err:
        return None, err
    title = _title_from(params)
    kind = "report" if params.get("mode") == "report" else "fill"
    claims = _parse_claims(params.get("claims", ""))

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
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # เงียบ — ไม่ต้อง log ทุก request ออก console

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")  # กัน browser ค้างหน้าเก่า
        self.end_headers()
        self.wfile.write(data)

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
<title>se-autokey · กรอกเคลมอัตโนมัติ</title>
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
  .wrap{max-width:920px; margin:0 auto; padding:24px 18px 48px}
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
  @media(max-width:560px){.grid{grid-template-columns:1fr}.run-cmd{display:none}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">SE</div>
    <div>
      <h1>se-autokey · กรอกเคลมอัตโนมัติ</h1>
      <div class="sub">ใส่เลขเคลม แล้วกดรัน — ระบบจะอ่าน ISURVEY แล้วกรอกลง EMCS ให้</div>
    </div>
  </header>

  <div class="card">
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
    </div>

    <div class="actions">
      <button class="run" id="runbtn">▶ รันโปรแกรม</button>
      <span class="badge idle" id="capbadge">กำลังรัน 0/4</span>
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

  <div id="runs"></div>
  <div class="emptyruns" id="emptyruns">ยังไม่มีงาน — ใส่เลขเคลมแล้วกด "รันโปรแกรม"</div>
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
    }
    c.contBtn.disabled = true;
    try{ await postJSON("/continue", body); }catch(e){}
    c.pauseEl.hidden = true;
    c.galWrap.style.display = "none"; c.galSig = null;
    c.wtWrap.hidden = true; c.wtSig = null;
    c.injWrap.hidden = true; c.injSig = null;
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
  const [cls,txt] = STATUS[r.status] || STATUS.idle;
  c.badgeEl.className = "badge " + cls;
  c.stEl.textContent = txt;
  const active = (r.status === "running" || r.status === "waiting");
  c.stopBtn.hidden = !active;
  c.closeBtn.hidden = active;
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
      c.phint.innerHTML = "ข้อมูลจาก ISURVEY ไม่ครบหรือกรอกอัตโนมัติไม่ได้ — "
        + "กรอก/เลือกช่องนี้ในหน้าต่าง EMCS (Chrome) <b>ของงานนี้</b> ให้เรียบร้อย แล้วกดปุ่ม";
      c.contBtn.textContent = "✓ กรอกเสร็จแล้ว — ดำเนินการต่อ";
      c.contBtn.className = "continue";
    }
    c.contBtn.disabled = false;
    c.pauseEl.hidden = false;
  } else {
    c.pauseEl.hidden = true;
    c.galWrap.style.display = "none"; c.galSig = null;
    c.wtWrap.hidden = true; c.wtSig = null;
    c.injWrap.hidden = true; c.injSig = null;
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
  runBtn.disabled = true;
  const body = {
    claims,
    invoice: $("#invoice").value.trim(),
    severity: $("#severity").value,
    claimmode: $("#claimmode").value,
    readonly: $("#readonly").checked,
    skipimages: $("#skipimages").checked,
    nosaveprice: $("#nosaveprice").checked,
  };
  try{
    const {ok,data} = await postJSON("/run", body);
    if (!ok){ alert(data.error || "เริ่มงานไม่สำเร็จ"); runBtn.disabled=false; return; }
    poll();   // ดึงงานใหม่มาแสดงทันที
  }catch(e){ alert("ติดต่อเซิร์ฟเวอร์ไม่ได้: " + e); runBtn.disabled=false; }
});
$("#claimmode").addEventListener("change", e => {
  $("#cmnote").hidden = (e.target.value !== "fresh");
});
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
