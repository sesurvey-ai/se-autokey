"""โหลด config และ credentials จากไฟล์ .env (หรือ environment variables)

ลำดับความสำคัญ: environment variable > ไฟล์ .env
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

# โฟลเดอร์หลักของโปรเจกต์ (โฟลเดอร์ที่มี main.py)
BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> dict:
    """อ่านไฟล์ .env แบบง่าย (KEY=VALUE บรรทัดละคู่) ไม่ต้องพึ่ง python-dotenv"""
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


@dataclass
class Config:
    isurvey_username: str
    isurvey_password: str
    emcs_username: str
    emcs_password: str

    base_dir: Path = field(default_factory=lambda: BASE_DIR)
    download_dir: Path = field(default_factory=lambda: BASE_DIR / "downloaded_images")
    template_path: Path = field(default_factory=lambda: BASE_DIR / "templates" / "check.jpg")
    runs_dir: Path = field(default_factory=lambda: BASE_DIR / "runs")

    isurvey_url: str = "https://cloud.isurvey.mobi/index.php"
    emcs_login_url: str = "https://eclaim3.blueventuregroup.co.th/esurvey/frmLogin.aspx"

    # แจ้งสถานะ "ส่งงานแล้ว" กลับ ISURVEY (คนละ host/auth — โหลดจาก .env แบบ optional)
    isurvey_report_url: str = "https://se.isurvey.mobi/service/srvEMCSrpt.php"
    isurvey_report_user: str = ""
    isurvey_report_pass: str = ""

    # บันทึกงานที่ทำเสร็จลงฐานข้อมูลกลาง se-key (key.sesurvey.cloud) — optional
    # ตั้งทั้งคู่ใน .env ถึงจะเปิดใช้ (ไม่ตั้ง = ข้ามการบันทึก/ตรวจซ้ำทั้งหมด)
    sekey_api_url: str = "https://key.sesurvey.cloud"
    sekey_api_key: str = ""


def load_config() -> Config:
    env = _load_env_file(BASE_DIR / ".env")

    def get(key: str) -> str:
        return os.environ.get(key, env.get(key, ""))

    cfg = Config(
        isurvey_username=get("ISURVEY_USERNAME"),
        isurvey_password=get("ISURVEY_PASSWORD"),
        emcs_username=get("EMCS_USERNAME"),
        emcs_password=get("EMCS_PASSWORD"),
        isurvey_report_user=get("ISURVEY_REPORT_USER"),
        isurvey_report_pass=get("ISURVEY_REPORT_PASS"),
        sekey_api_key=get("SE_KEY_API_KEY"),
    )
    if get("ISURVEY_REPORT_URL"):
        cfg.isurvey_report_url = get("ISURVEY_REPORT_URL")
    if get("SE_KEY_API_URL"):
        cfg.sekey_api_url = get("SE_KEY_API_URL")

    missing = [
        name for name, val in [
            ("ISURVEY_USERNAME", cfg.isurvey_username),
            ("ISURVEY_PASSWORD", cfg.isurvey_password),
            ("EMCS_USERNAME", cfg.emcs_username),
            ("EMCS_PASSWORD", cfg.emcs_password),
        ] if not val
    ]
    if missing:
        raise SystemExit(
            f"ไม่พบ credentials: {', '.join(missing)}\n"
            f"กรุณา copy .env.example เป็น .env แล้วใส่ค่าให้ครบ (ที่ {BASE_DIR / '.env'})"
        )
    return cfg
