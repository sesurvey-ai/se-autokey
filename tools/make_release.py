# -*- coding: utf-8 -*-
r"""สร้างไฟล์ปล่อยเวอร์ชันของบอท (zip โค้ดอย่างเดียว + latest.json) ไว้ที่ dist/ — ขั้นแรกของการอัปเดตผ่านเน็ต

    make-release.bat ["หมายเหตุสั้น ๆ ที่จะโชว์ให้คนกดอัปเดตเห็น"]

กติกาไฟล์ที่เอาไป = เดียวกับ make_usb.py (ไม่เอา .env / runs / runtime / รูปเคส / xlsx) + ไม่เอา dist/
เวอร์ชันอ่านจาก autokey/__init__.py — **ต้องขยับเลขทุกครั้งที่ปล่อย** ไม่งั้นเครื่องปลายทางไม่รู้ว่ามีของใหม่
ขั้นถัดไป (เครื่อง dev): backend ของ se-survey → npx ts-node --transpile-only src/scripts/publishBotRelease.ts <dist>
(make-release.bat ทำต่อให้เองถ้ามี ../se-survey อยู่ข้าง ๆ)
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_usb import SKIP_DIRS, skip_file  # noqa: E402  (กติกาชุดเดียวกับ USB)

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
EXTRA_SKIP_DIRS = {"dist"}


def read_version() -> str:
    text = (ROOT / "autokey" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"(\d+\.\d+\.\d+)"', text)
    if not m:
        raise SystemExit("อ่าน __version__ จาก autokey/__init__.py ไม่ได้ (ต้องเป็น x.y.z)")
    return m.group(1)


def iter_files(root: Path):
    skip_dirs = SKIP_DIRS | EXTRA_SKIP_DIRS
    for item in sorted(root.iterdir()):
        if item.is_dir():
            if item.name in skip_dirs:
                continue
            yield from iter_files(item)
        elif not skip_file(item.name):
            yield item


def git_notes() -> str:
    try:
        return subprocess.check_output(["git", "log", "-1", "--format=%s"], cwd=str(ROOT), text=True,
                                       encoding="utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        return ""


def main(argv: list[str]) -> int:
    version = read_version()
    notes = " ".join(argv).strip() or git_notes()
    DIST.mkdir(exist_ok=True)
    zip_name = f"se-autokey-{version}.zip"
    zip_path = DIST / zip_name
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in iter_files(ROOT):
            z.write(f, f.relative_to(ROOT).as_posix())
            n += 1
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    latest = {
        "version": version, "file": zip_name, "sha256": sha, "size": zip_path.stat().st_size,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "notes": notes,
    }
    (DIST / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  release v{version}: {n} ไฟล์ · {latest['size'] / 1048576:.2f} MB · sha256 {sha[:12]}…")
    print(f"  → {zip_path}")
    print(f"  → {DIST / 'latest.json'}  หมายเหตุ: {notes or '(ว่าง)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
