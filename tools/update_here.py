"""รันที่ "เครื่องพนักงาน" จาก USB — ก๊อปไฟล์โปรแกรมทับของเดิม

เรียกผ่าน update-here.bat ที่ make_usb.py วางไว้ชั้นบนสุดของโฟลเดอร์
se-autokey-update บน USB (ไฟล์ .bat เป็น ASCII ล้วน ข้อความไทยอยู่ที่นี่)

ไม่แตะของเครื่องปลายทาง: .env (รหัส) · runs (สมุดงาน+log) · รูปเคส
— เพราะ USB ไม่มีไฟล์พวกนั้นตั้งแต่แรก และก๊อปแบบทับอย่างเดียว ไม่ลบอะไร
"""
import shutil
import sys
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parent.parent   # โฟลเดอร์ se-autokey-update
SELF_BAT = "update-here.bat"


def copy_tree(src: Path, dst: Path, top=True):
    """ก๊อปทับ ไม่ลบอะไรที่ปลายทาง → (จำนวนไฟล์, ขนาดรวม)"""
    n = size = 0
    for item in sorted(src.iterdir()):
        if item.is_dir():
            if item.name in ("__pycache__", ".git"):
                continue
            a, b = copy_tree(item, dst / item.name, top=False)
            n, size = n + a, size + b
        else:
            if top and item.name == SELF_BAT:      # ตัวมันเองไม่ต้องไปอยู่ที่ปลายทาง
                continue
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst / item.name)
            n, size = n + 1, size + item.stat().st_size
    return n, size


def find_target(argv) -> Path:
    if argv:
        t = Path(argv[0].strip().strip('"'))
        if (t / "webui.py").is_file():
            return t
    default = Path.home() / "Desktop" / "se-autokey"
    if (default / "webui.py").is_file():
        return default
    print()
    print("  หาโฟลเดอร์ se-autokey บนเครื่องนี้ไม่เจอ")
    print(f"  (ลองหาที่ {default} แล้ว)")
    print()
    print("  ลากโฟลเดอร์ se-autokey มาวางที่หน้าต่างนี้ แล้วกด Enter")
    print()
    try:
        typed = input("  โฟลเดอร์: ").strip().strip('"')
    except (EOFError, KeyboardInterrupt):
        typed = ""
    return Path(typed) if typed else None


def main() -> int:
    target = find_target(sys.argv[1:])
    if target is None or not (target / "webui.py").is_file():
        print("\n  [ ผิดพลาด ] โฟลเดอร์นั้นไม่ใช่ se-autokey (ไม่เจอไฟล์ webui.py)\n")
        return 1

    print()
    print(f"  จะอัปเดตโปรแกรมที่: {target}")
    print("  สมุดงาน / รหัสผ่าน / รูปเคส ของเครื่องนี้ ไม่ถูกแตะ")
    print()
    try:
        yn = input("  ทำต่อ? พิมพ์ y แล้วกด Enter: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        yn = ""
    if yn != "y":
        print("\n  [ ยกเลิก ] ไม่ได้แก้อะไร\n")
        return 1

    # สำรองตารางคนคีย์ของเครื่องนี้ไว้ก่อนทับ (เผื่อเคยแก้เอง)
    keyers = target / "settings" / "keyers.json"
    if keyers.is_file():
        shutil.copy2(keyers, keyers.with_suffix(".json.bak"))

    print()
    print("  กำลังก๊อป ...", flush=True)
    try:
        n, size = copy_tree(PAYLOAD, target)
    except OSError as e:
        print(f"\n  [ ผิดพลาด ] ก๊อปไม่สำเร็จ: {e}")
        print("              โปรแกรมเปิดค้างอยู่หรือเปล่า ปิดให้หมดแล้วลองใหม่\n")
        return 1

    print()
    print(f"  [ เสร็จแล้ว ] {n} ไฟล์ · {size / 1048576:.1f} MB")
    print()
    print("  ขั้นต่อไป: ปิดหน้าต่างดำของ se-autokey ที่เปิดค้างอยู่ (ถ้ามี)")
    print("            แล้วดับเบิลคลิก  start-webui.bat  ใหม่")
    print("            ไม่ปิด-เปิดใหม่ จะยังเห็นหน้าเว็บเวอร์ชันเก่า")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
