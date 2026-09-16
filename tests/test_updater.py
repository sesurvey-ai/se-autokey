# -*- coding: utf-8 -*-
"""เทสตัวอัปเดตผ่านเน็ต (autokey/updater.py — user เคาะ 15/09/69 แผนข้อ 1 เลิกขน USB)

ไม่แตะเครือข่าย/ไม่รีสตาร์ตอะไร: แทน urlopen/_get_json ด้วยตัวปลอม · แตก zip ลงโฟลเดอร์ชั่วคราวของ pytest

รัน:  python -m pytest tests/test_updater.py -q
"""
from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import updater  # noqa: E402


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


BASE = {"webui.py": b"new webui", "main.py": b"new main", "autokey/__init__.py": b'__version__ = "9.9.9"\n'}


def test_compare_versions_numeric():
    assert updater.compare_versions("1.10.0", "1.9.9") > 0      # เทียบเป็นตัวเลข ไม่ใช่ข้อความ
    assert updater.compare_versions("1.1.0", "1.1.0") == 0
    assert updater.compare_versions("1.0.9", "1.1.0") < 0
    assert updater.parse_version("v2") == (2, 0, 0)             # ส่วนที่ขาด = 0
    assert updater.parse_version("") == (0, 0, 0)


def test_apply_zip_overwrites_code_but_protects_machine_files(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "webui.py").write_bytes(b"old webui")
    (root / ".env").write_bytes(b"SECRET=1")
    (root / "runtime").mkdir()
    (root / "runtime" / "python.exe").write_bytes(b"py")
    (root / "runs").mkdir()
    (root / "runs" / "job.json").write_bytes(b"{}")
    (root / "settings").mkdir()
    (root / "settings" / "keyers.json").write_bytes(b"[local]")
    zp = tmp_path / "rel.zip"
    zp.write_bytes(_zip({**BASE,
                         ".env": b"LEAK=1", ".env.bak": b"LEAK=2",
                         "runtime/python.exe": b"NEW", "runs/job.json": b"NEW",
                         "settings/keyers.json": b"[from zip]", "settings/new.json": b"[new]",
                         "tools/x.py": b"tool", "autokey/": b"", "../evil.py": b"x"}))
    res = updater.apply_zip(zp, root)
    assert (root / "webui.py").read_bytes() == b"new webui"                  # ทับโค้ด
    assert (root / "main.py").read_bytes() == b"new main"                    # ไฟล์ใหม่ลงได้
    assert (root / "tools" / "x.py").read_bytes() == b"tool"                 # สร้างโฟลเดอร์ใหม่ได้
    assert (root / ".env").read_bytes() == b"SECRET=1"                       # รหัสไม่ถูกทับ
    assert not (root / ".env.bak").exists()
    assert (root / "runtime" / "python.exe").read_bytes() == b"py"           # runtime/runs ไม่แตะ
    assert (root / "runs" / "job.json").read_bytes() == b"{}"
    assert (root / "settings" / "keyers.json").read_bytes() == b"[local]"    # ของเครื่องที่มีอยู่แล้ว
    assert (root / "settings" / "new.json").read_bytes() == b"[new]"
    assert not (tmp_path / "evil.py").exists()                               # path traversal ถูกทิ้ง
    assert res == {"files": 5, "skipped": 4, "kept": 1}
    assert not list(root.glob("se-autokey-update-*"))                        # โฟลเดอร์ชั่วคราวถูกเก็บ


def test_apply_zip_installs_keyers_when_missing(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    zp = tmp_path / "rel.zip"
    zp.write_bytes(_zip({**BASE, "settings/keyers.json": b"[from zip]"}))
    res = updater.apply_zip(zp, root)
    assert (root / "settings" / "keyers.json").read_bytes() == b"[from zip]"
    assert res["kept"] == 0


def test_apply_zip_rejects_foreign_zip(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / "webui.py").write_bytes(b"old")
    zp = tmp_path / "rel.zip"
    zp.write_bytes(_zip({"readme.txt": b"hi", "webui.py": b"x"}))
    with pytest.raises(ValueError, match="ไม่ใช่ release"):
        updater.apply_zip(zp, root)
    assert (root / "webui.py").read_bytes() == b"old"                        # ไม่แตะอะไรเลย


def test_download_verifies_sha256(tmp_path, monkeypatch):
    payload = _zip(BASE)
    calls = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        calls.append((req.full_url, req.get_header("Authorization")))
        return _Resp(payload)

    monkeypatch.setattr(updater.urllib.request, "urlopen", fake_urlopen)
    good = hashlib.sha256(payload).hexdigest()
    dest = tmp_path / "dl" / "r.zip"
    assert updater.download("https://api.example/", "tok", "1.1.0", dest, good.upper()) == dest
    assert dest.read_bytes() == payload
    assert calls[0] == ("https://api.example/api/integrations/bot-release/1.1.0/zip", "Bearer tok")
    with pytest.raises(ValueError, match="sha256"):
        updater.download("https://api.example", "tok", "1.1.0", tmp_path / "bad.zip", "0" * 64)
    assert not (tmp_path / "bad.zip").exists()                               # ของที่ไม่ตรงถูกลบ
    with pytest.raises(ValueError):
        updater.download("https://api.example", "tok", "../x", tmp_path / "x.zip", good)


def test_check_compares_with_server(monkeypatch):
    # backend ห่อ {success, data} — fetch_latest แกะเอง
    monkeypatch.setattr(updater, "_get_json", lambda url, token, path, timeout=20:
                        {"success": True, "data": {"version": "1.2.0", "sha256": "ab" * 32, "size": 10, "notes": "n"}})
    info = updater.check("https://api.example", "tok", "1.1.0")
    assert info["update_available"] is True and info["latest"] == "1.2.0" and info["sha256"] == "ab" * 32
    assert updater.check("https://api.example", "tok", "1.2.0")["update_available"] is False
    # เครื่องนี้ใหม่กว่าเซิร์ฟเวอร์ (เครื่อง dev) = ไม่ถอยลง
    assert updater.check("https://api.example", "tok", "1.3.0")["update_available"] is False


def test_check_reports_errors_instead_of_raising(monkeypatch):
    assert "token" in updater.check("https://api.example", "", "1.1.0")["error"]

    def boom(*a, **k):
        raise OSError("no net")
    monkeypatch.setattr(updater, "_get_json", boom)
    assert "no net" in updater.check("https://api.example", "tok", "1.1.0")["error"]
    monkeypatch.setattr(updater, "_get_json", lambda *a, **k: {"data": {"version": "latest"}})
    assert "ผิดรูปแบบ" in updater.check("https://api.example", "tok", "1.1.0")["error"]

    import urllib.error

    def http(code):
        def _f(*a, **k):
            raise urllib.error.HTTPError("https://api.example/x", code, "err", {}, None)
        return _f
    monkeypatch.setattr(updater, "_get_json", http(404))     # ยังไม่เคยปล่อยเวอร์ชัน
    assert "ยังไม่มีเวอร์ชัน" in updater.check("https://api.example", "tok", "1.1.0")["error"]
    monkeypatch.setattr(updater, "_get_json", http(401))     # token ผิด
    assert "token" in updater.check("https://api.example", "tok", "1.1.0")["error"]
    monkeypatch.setattr(updater, "_get_json", http(502))
    assert "502" in updater.check("https://api.example", "tok", "1.1.0")["error"]


def test_bind_with_retry_waits_for_port():
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("in use")
        return "srv"
    assert updater.bind_with_retry(factory, 8765, tries=5, wait=0) == "srv"
    assert len(attempts) == 3

    def always_busy():
        raise OSError("x")
    with pytest.raises(OSError):
        updater.bind_with_retry(always_busy, 8765, tries=2, wait=0)
