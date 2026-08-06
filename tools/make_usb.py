r"""ก๊อปไฟล์โปรแกรม se-autokey ลง USB เพื่อเอาไปอัปเดตเครื่องพนักงาน

เรียกผ่าน make-usb.bat (ไฟล์ .bat เป็น ASCII ล้วน — cmd อ่านภาษาไทยในไฟล์ .bat
แล้ว goto/วงเล็บเพี้ยน ข้อความไทยจึงอยู่ในไฟล์ python นี้แทน)

    make-usb.bat E:\            ไฟล์โปรแกรมอย่างเดียว (~5 MB)
    make-usb.bat E:\ /runtime   เอา Python พกพาไปด้วย (+256 MB)

ไม่ก๊อปไปด้วย: .env (รหัส ISURVEY/EMCS) · runs (สมุดงาน+log)
               downloaded_images (รูปเคสลูกค้า) · ไฟล์ข้อมูลพนักงาน (*.xlsx)
"""
import fnmatch
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_NAME = "se-autokey-update"

# โฟลเดอร์ที่ไม่เอาไป — ของเฉพาะเครื่อง/ข้อมูลลูกค้า/ของหนักที่ไม่ต้องอัปเดต
SKIP_DIRS = {".git", "runtime", "runs", "downloaded_images", "zip_import",
             "__pycache__", ".vscode", ".idea", ".pytest_cache", "node_modules"}
# ไฟล์ที่ไม่เอาไป — .env มีรหัสผ่าน, xlsx มีข้อมูลพนักงาน
SKIP_FILES = [".env", "*.pyc", "*.log", "*.zip", "*.xlsx", "*.bak",
              "claims.txt", "New*.txt", "SURV_REPORT_*.txt"]


def skip_file(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in SKIP_FILES)


def copy_tree(src: Path, dst: Path, skip_dirs=SKIP_DIRS, skip=True):
    """ก๊อปทั้งต้นไม้ (ทับไฟล์เดิม ไม่ลบอะไร) → (จำนวนไฟล์, ขนาดรวม)"""
    n = size = 0
    for item in sorted(src.iterdir()):
        if item.is_dir():
            if item.name in skip_dirs:
                continue
            a, b = copy_tree(item, dst / item.name, skip_dirs, skip)
            n, size = n + a, size + b
        else:
            if skip and skip_file(item.name):
                continue
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst / item.name)
            n, size = n + 1, size + item.stat().st_size
    return n, size


def ask_dest() -> str:
    print()
    print("  ปลายทาง USB — พิมพ์ไดรฟ์ เช่น  E:\\  แล้วกด Enter")
    print("  (หรือลากโฟลเดอร์บน USB มาวางที่หน้าต่างนี้)")
    print()
    try:
        return input("  ปลายทาง: ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def main() -> int:
    args = [a for a in sys.argv[1:] if a.strip()]
    with_runtime = any(a.lower().lstrip("-/") == "runtime" for a in args)
    rest = [a for a in args if a.lower().lstrip("-/") != "runtime"]

    dest_raw = (rest[0] if rest else "") or ask_dest()
    dest_raw = dest_raw.strip().strip('"')
    if not dest_raw:
        print("\n  [ ยกเลิก ] ไม่ได้ใส่ปลายทาง\n")
        return 1

    dest = Path(dest_raw)
    if not dest.is_dir():
        print(f"\n  [ ผิดพลาด ] ไม่พบโฟลเดอร์ปลายทาง: {dest}")
        print("              เสียบ USB แล้วหรือยัง ไดรฟ์ถูกตัวหรือเปล่า\n")
        return 1

    out = dest / OUT_NAME
    print()
    print(f"  ต้นทาง : {ROOT}")
    print(f"  ปลายทาง: {out}")
    print()
    print("  กำลังก๊อปไฟล์โปรแกรม ...", flush=True)

    try:
        out.mkdir(parents=True, exist_ok=True)
        n, size = copy_tree(ROOT, out)
        if with_runtime:
            print("  กำลังก๊อป runtime (Python พกพา ~256 MB) — ใช้เวลาสักครู่ ...",
                  flush=True)
            rt = ROOT / "runtime"
            if rt.is_dir():
                a, b = copy_tree(rt, out / "runtime", skip_dirs=set(), skip=False)
                n, size = n + a, size + b
            else:
                print("  [ ! ] ไม่มีโฟลเดอร์ runtime บนเครื่องนี้ — ข้าม")
        # ตัวช่วยที่เครื่องปลายทาง วางไว้ชั้นบนสุดให้กดง่าย
        helper = out / "tools" / "update-here.bat"
        if helper.is_file():
            shutil.copy2(helper, out / "update-here.bat")
    except OSError as e:
        print(f"\n  [ ผิดพลาด ] ก๊อปไฟล์ไม่สำเร็จ: {e}")
        print("              USB เต็ม ถูกล็อกไม่ให้เขียน หรือถอดออกกลางคัน?\n")
        return 1

    print()
    print(f"  [ เสร็จแล้ว ] {n} ไฟล์ · {size / 1048576:.1f} MB")
    print()
    print("  ที่เครื่องพนักงาน: เปิดโฟลเดอร์  se-autokey-update  บน USB")
    print("                    แล้วดับเบิลคลิก  update-here.bat")
    print()
    print("  [ ! ] ไม่ได้ก๊อป .env (รหัส ISURVEY/EMCS) ไปด้วย")
    print("        เครื่องที่ยังไม่เคยตั้งค่า ต้องเอา .env ไปวางเอง")
    print("        ส่งทางอื่นปลอดภัยกว่า — USB หาย = รหัสหลุด")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
