# -*- coding: utf-8 -*-
"""อัปเดตโปรแกรมผ่านเน็ต (user เคาะ 15/09/69 แผนข้อ 1 — เลิกขน USB)

ทางเดิน: หน้าบอทกด "ตรวจอัปเดต" → GET /api/integrations/bot-release/latest ที่เซิร์ฟเวอร์ se-survey (ไฟล์อยู่บน R2
ผ่าน backend, ใช้ token บอทที่ทุกเครื่องมีอยู่แล้ว) → เทียบกับ autokey.__version__ → กด "อัปเดตและรีสตาร์ต" →
โหลด zip → ตรวจ sha256 → แตกทับตัวเอง → เปิดโปรเซสใหม่พอร์ตเดิม แล้วตัวเก่าปิดตัว

กติกาไฟล์ = เดียวกับ tools/update_here.py (USB): **ก๊อปทับอย่างเดียว ไม่ลบอะไร** และไม่แตะของเครื่อง:
  .env* (รหัส) · runs/ (สมุดงาน+log) · runtime/ (Python พกพา 266 MB — เปลี่ยนน้อย และ python.exe ที่กำลังรันเขียนทับไม่ได้)
  · downloaded_images/ zip_import/ (รูปเคส) · settings/keyers.json ถ้ามีอยู่แล้ว (ตารางคนคีย์ที่เครื่องนี้แก้เอง)
ตัว zip สร้างด้วย tools/make_release.py (กติกา exclude ชุดเดียวกับ make_usb) แล้ว backend/src/scripts/publishBotRelease.ts อัปขึ้น R2
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RELEASE_LATEST = "/api/integrations/bot-release/latest"
RELEASE_ZIP = "/api/integrations/bot-release/{version}/zip"

#: โฟลเดอร์ของเครื่อง — zip ไม่มีอยู่แล้ว (make_usb ตัดออก) แต่กันไว้อีกชั้นเผื่อ zip ผิดพลาด
PROTECT_DIRS = {"runtime", "runs", "downloaded_images", "zip_import", ".git", "__pycache__", "dist",
                ".vscode", ".idea", ".pytest_cache", "node_modules"}
#: ไฟล์ของเครื่องที่ **ไม่ทับถ้ามีอยู่แล้ว** (ไฟล์ใหม่ตอนติดตั้งครั้งแรกยังลงได้)
KEEP_IF_EXISTS = {"settings/keyers.json"}
#: ต้องมีใน zip ถึงจะเชื่อว่าเป็น release ของโปรแกรมนี้ (กัน zip ผิดไฟล์ทับโปรแกรมเละ)
REQUIRED_IN_ZIP = ("webui.py", "main.py", "autokey/__init__.py")


def parse_version(v) -> tuple:
    """'1.2.10' → (1, 2, 10) · ส่วนที่ไม่ใช่ตัวเลข = 0 (เทียบเป็นตัวเลข ไม่ใช่ข้อความ: 1.10 > 1.9)"""
    out = []
    for p in str(v or "").strip().split("."):
        m = re.search(r"\d+", p)      # search ไม่ใช่ match — "v2" → 2
        out.append(int(m.group(0)) if m else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def compare_versions(a, b) -> int:
    pa, pb = parse_version(a), parse_version(b)
    return (pa > pb) - (pa < pb)


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_protected(rel: str) -> bool:
    """path สัมพัทธ์ใน zip (posix) — อยู่ใต้โฟลเดอร์ของเครื่อง หรือเป็นไฟล์รหัส = ห้ามทับ"""
    parts = rel.split("/")
    if any(p in PROTECT_DIRS for p in parts[:-1]):
        return True
    name = parts[-1]
    return name == ".env" or name.startswith(".env.")


def _safe_rel(name: str) -> str | None:
    """ชื่อใน zip → path สัมพัทธ์ที่ปลอดภัย (ไม่มี .. / ไม่ใช่ path สัมบูรณ์ / ไม่ใช่โฟลเดอร์) · None = ข้าม"""
    raw = name.replace("\\", "/")
    if raw.endswith("/"):             # รายการโฟลเดอร์ใน zip — ไม่ใช่ไฟล์ (ต้องดูก่อน strip)
        return None
    rel = raw.strip("/")
    if not rel:
        return None
    if rel.startswith("../") or "/../" in rel or rel == ".." or re.match(r"^[A-Za-z]:", rel):
        return None
    return rel


# ---------------------------------------------------------------- ฝั่งเซิร์ฟเวอร์
def _get_json(url: str, token: str, path: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_latest(url: str, token: str) -> dict:
    """latest.json ที่ backend อ่านจาก R2 → {version, file, sha256, size, built_at, notes}
    (backend ห่อเป็น {success, data} ตามแบบ API อื่น — แกะให้ · รับแบบไม่ห่อได้ด้วย)"""
    d = _get_json(url, token, RELEASE_LATEST)
    inner = d.get("data") if isinstance(d, dict) else None
    return inner if isinstance(inner, dict) else d


def check(url: str, token: str, current: str) -> dict:
    """เทียบเวอร์ชันที่รันอยู่กับที่เซิร์ฟเวอร์ปล่อยล่าสุด — คืน dict ให้หน้าเว็บโชว์ (มี error = ตรวจไม่ได้ ไม่ throw)"""
    if not str(token or "").strip():
        return {"current": current, "error": "ยังไม่ตั้ง token ของระบบ se-survey (แท็บ ตั้งค่า) — โหลดอัปเดตไม่ได้"}
    try:
        latest = fetch_latest(url, token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"current": current, "error": "เซิร์ฟเวอร์ยังไม่มีเวอร์ชันที่ปล่อย (ฝั่ง dev ยังไม่เคยรัน make-release.bat)"}
        if e.code in (401, 403):
            return {"current": current, "error": "token ของระบบ se-survey ไม่ถูกต้อง (แท็บ ตั้งค่า) — โหลดอัปเดตไม่ได้"}
        return {"current": current, "error": f"เซิร์ฟเวอร์ตอบ HTTP {e.code} ({url})"}
    except Exception as e:  # noqa: BLE001
        return {"current": current, "error": f"ติดต่อเซิร์ฟเวอร์ไม่ได้: {type(e).__name__}: {e}"}
    ver = str(latest.get("version") or "").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        return {"current": current, "error": f"เวอร์ชันบนเซิร์ฟเวอร์ผิดรูปแบบ: {ver!r}"}
    return {
        "current": current, "latest": ver,
        "update_available": compare_versions(ver, current) > 0,
        "sha256": str(latest.get("sha256") or ""), "size": latest.get("size"),
        "built_at": latest.get("built_at"), "notes": latest.get("notes") or "",
    }


def download(url: str, token: str, version: str, dest: Path, expected_sha256: str, timeout: int = 300) -> Path:
    """โหลด zip ของเวอร์ชันนั้นลง dest แล้วตรวจ sha256 — ไม่ตรง = ลบทิ้งแล้ว raise (ห้ามติดตั้งของที่ไม่ครบ/ถูกแก้)"""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"เวอร์ชันผิดรูปแบบ: {version!r}")
    req = urllib.request.Request(url.rstrip("/") + RELEASE_ZIP.format(version=version),
                                 headers={"Authorization": f"Bearer {token}"})
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
        shutil.copyfileobj(resp, f, 1 << 20)
    got = sha256_file(dest)
    if expected_sha256 and got.lower() != str(expected_sha256).lower():
        try:
            dest.unlink()
        except OSError:
            pass
        raise ValueError(f"ไฟล์ที่โหลดมาไม่ตรง sha256 (ได้ {got[:12]}… คาด {str(expected_sha256)[:12]}…) — ไม่ติดตั้ง")
    return dest


# ---------------------------------------------------------------- ติดตั้ง
def apply_zip(zip_path: Path, root: Path | None = None) -> dict:
    """แตก zip ทับโฟลเดอร์โปรแกรม (ก๊อปทับ ไม่ลบ · ไม่แตะของเครื่อง) → {files, skipped, kept}
    แตกลงโฟลเดอร์ชั่วคราวใต้ root ก่อน (ไดรฟ์เดียวกัน → ย้ายไฟล์เร็วและ atomic ต่อไฟล์) แล้วค่อยวางทับ"""
    root = Path(root or ROOT)
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if _safe_rel(n) and not z.getinfo(n).is_dir()]
        missing = [r for r in REQUIRED_IN_ZIP if r not in {_safe_rel(n) for n in names}]
        if missing:
            raise ValueError(f"zip ไม่ใช่ release ของ se-autokey (ไม่มี {', '.join(missing)})")
        tmp = Path(tempfile.mkdtemp(prefix="se-autokey-update-", dir=str(root)))
        files = skipped = kept = 0
        try:
            for n in names:
                rel = _safe_rel(n)
                if not rel or _is_protected(rel):
                    skipped += 1
                    continue
                target = root / rel
                if rel in KEEP_IF_EXISTS and target.exists():
                    kept += 1
                    continue
                staged = tmp / rel
                staged.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(staged, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)      # ทับไฟล์เดิมทั้งก้อน (ไม่มีจังหวะไฟล์ครึ่งเดียว)
                files += 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"files": files, "skipped": skipped, "kept": kept}


def restart(port: int, host: str = "127.0.0.1", delay: float = 1.5) -> None:
    """เปิดโปรเซสใหม่ (Python ตัวเดิม + webui.py พอร์ตเดิม, ไม่เปิดเบราว์เซอร์ซ้ำ) แล้วปิดตัวเองหลัง delay
    — ตัวใหม่รอพอร์ตว่างเอง (main() วนลอง bind) · หน้าเว็บเดิม poll /healthz จนเห็นเวอร์ชันใหม่แล้วโหลดตัวเองใหม่
    หน้าต่างดำบานใหม่เปิดแยก (CREATE_NEW_CONSOLE) เพราะบานเก่าปิดตามโปรเซสเก่า"""
    args = [sys.executable, str(ROOT / "webui.py"), "--port", str(port), "--host", host, "--no-open"]
    kw: dict = {"cwd": str(ROOT), "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)
    threading.Timer(delay, lambda: os._exit(0)).start()


def bind_with_retry(factory, port: int, tries: int = 60, wait: float = 0.5):
    """สร้างเซิร์ฟเวอร์ — พอร์ตยังไม่ว่าง (ตัวเก่ากำลังปิดหลังอัปเดต / เปิดซ้อน) ให้รอแล้วลองใหม่ ไม่ล้มทันที"""
    last = None
    for i in range(tries):
        try:
            return factory()
        except OSError as e:
            last = e
            if i == 0:
                print(f"  พอร์ต {port} ยังถูกใช้อยู่ (โปรแกรมตัวเก่ากำลังปิด?) — รอ…", flush=True)
            time.sleep(wait)
    raise last if last else OSError(f"bind พอร์ต {port} ไม่ได้")
