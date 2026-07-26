"""จัดการรูปภาพ: โหลดจาก ISURVEY (zip export หรือ panel) → จัดชื่อด้วย template matching"""
import os
import re
import shutil
import time
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from selenium.webdriver.common.by import By

from .browser import log, default_download_dir
from .processing import natural_sort_key, process_images_pro

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")

# หมวดในตัว zip export ของ EMCS (PICTURES/<CAT>/) สำหรับรูป "flat" (INS/REPORTS/OTHERS) →
# ป้ายประเภทรูป EMCS ให้ _group_flat_by_category จัดกลุ่ม+ตั้งชื่อแยกหมวด (แทนกองเป็นประเภทเดียว).
# หมวดบุคคลที่สาม TP_* จัดการแยกผ่าน subfolder tp_*/ + n_opponents/n_injuries/n_assets (ไม่อยู่ที่นี่)
ZIP_DEFAULT_EMCS_TYPE = "รูปประกอบ"
ZIP_CAT_TO_EMCS = {
    "INS": "รูปรถประกัน",     # รูปรถประกัน
    "ACC_MAP": "รูปแผนที่เกิดเหตุ",   # แผนที่/จุดเกิดเหตุ — EMCS มีหมวดนี้ตรง ๆ
    "OTHERS": "รูปประกอบ",     # เบ็ดเตล็ด
    "REPORTS": "รูปประกอบ",    # เอกสาร/ใบรับงาน — หมวดไม่ชัด ใส่กลางให้หัวหน้าจัดบน EMCS
}


def archive_old_images(folder: Path):
    """ย้ายรูปเก่าจากรอบก่อนไปไว้ใน _old/<timestamp>/ กันรูปข้ามเคลมปนกัน
    (ย้ายแทนการลบ เพื่อความปลอดภัย) — รวมโฟลเดอร์บุคคลที่สาม tp_*/ ทุกตัว
    (tp_veh/tp_person/tp_prop) ด้วย ไม่งั้นรอบถัดไปโหลดทับเป็น _2/_3 สะสม"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    files = [f for f in folder.iterdir() if f.is_file()]
    tp_dirs = [d for d in folder.iterdir()
               if d.is_dir() and d.name.startswith("tp_") and any(d.iterdir())]
    if not files and not tp_dirs:
        return
    dest = folder / "_old" / datetime.now().strftime("%Y%m%d_%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.move(str(f), str(dest / f.name))
    for d in tp_dirs:
        shutil.move(str(d), str(dest / d.name))
    extra = (" + " + "/".join(d.name for d in tp_dirs) + "/") if tp_dirs else ""
    log(f"ย้ายรูปเก่า {len(files)} ไฟล์{extra} ไปที่ {dest}")


def list_images(folder: Path) -> list:
    """รายชื่อไฟล์รูปในโฟลเดอร์ (เรียงแบบ natural, ไม่รวมโฟลเดอร์ย่อย)"""
    folder = Path(folder)
    return sorted(
        [
            f.name
            for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in IMAGE_EXTS
        ],
        key=natural_sort_key,
    )


def download_images(driver, folder: Path):
    """โหลดรูปทั้งหมดในหน้าปัจจุบัน (element class 'center-cropped')
    ใช้ cookies จาก selenium เพื่อให้ผ่านสิทธิ์ login"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    elements = driver.find_elements(By.CSS_SELECTOR, "div.center-cropped")
    log(f"   พบรูปในหน้านี้ {len(elements)} รูป")

    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"])

    saved = 0
    for index, element in enumerate(elements):
        style_attr = element.get_attribute("style") or ""
        match = re.search(r'url\((?:["\']?)(.*?)(?:["\']?)\)', style_attr)
        if not match:
            continue

        relative_url = match.group(1)
        full_url = (
            relative_url
            if relative_url.startswith("http")
            else urljoin(driver.current_url, relative_url)
        )

        try:
            response = session.get(full_url, timeout=60)
            if response.status_code != 200:
                log(f"   ⚠️ โหลดไม่ได้ ({response.status_code}): {full_url}")
                continue

            # ตั้งชื่อจาก URL (ตัด query string, แก้ %20 ฯลฯ)
            name = unquote(os.path.basename(urlparse(full_url).path))
            if not name:
                name = f"image_{index + 1}.jpg"

            # กันชื่อชนกับรูปที่โหลดไปแล้ว (เช่นรูปจากคนละ tab ชื่อซ้ำกัน)
            target = folder / name
            stem, ext = os.path.splitext(name)
            n = 2
            while target.exists():
                target = folder / f"{stem}_{n}{ext}"
                n += 1

            target.write_bytes(response.content)
            saved += 1
        except Exception as e:
            log(f"   ⚠️ Error โหลด {full_url}: {e}")

    log(f"   บันทึกแล้ว {saved} รูป → {folder}")


def prepare_images(folder: Path, template: Path, threshold: float = 0.75):
    """หารูปที่ตรง template (รูปใบรับงาน) ตั้งเป็น 1.jpg
    ที่เหลือไล่ชื่อ รูปรถประกัน2.jpg, 3, ... (ใช้ processing.process_images_pro)"""
    log(f"จัดชื่อรูปด้วย template matching (threshold {threshold})")
    process_images_pro(str(folder), str(template), threshold=threshold)


# ------------------------------------------------------------------ zip export
# ปุ่มบนแถบล่างของหน้ารายละเอียดเคลม (Tab Summary): "ดาวน์โหลดรูปภาพ" ให้ zip
# ที่รูปแยกหมวด PICTURES/INS, REPORTS, OTHERS มาแล้ว / "ดาวน์โหลด XML" ให้
# SURV_REPORT_*.txt ข้อมูลทั้งเคลมแบบ structured

def _find_button_by_text(driver, text_variants):
    """หา button/anchor จากข้อความ (รองรับหลายตัวสะกด เช่น ดาวน์โหลด/ดาวโหลด)"""
    for t in text_variants:
        for xp in (
            f"//span[contains(text(), '{t}')]",
            f"//a[contains(., '{t}')]",
            f"//button[contains(., '{t}')]",
        ):
            for el in driver.find_elements(By.XPATH, xp):
                try:
                    if el.is_displayed():
                        return el
                except Exception:
                    continue
    return None


def _dump_visible_buttons(driver) -> str:
    """รายชื่อปุ่มที่เห็นบนหน้า (ไว้ debug ตอนหาปุ่มไม่เจอ)"""
    texts = driver.execute_script(
        "return Array.from(document.querySelectorAll('a,button,span'))"
        ".filter(e => e.offsetParent !== null)"
        ".map(e => (e.innerText || '').trim())"
        ".filter(t => t && t.length < 40);"
    )
    return ", ".join(sorted(set(texts))[:50])


def _set_download_dir(driver, folder: Path) -> bool:
    """ชี้ที่เก็บไฟล์ดาวน์โหลดของ Chrome ไปยังโฟลเดอร์ที่ต้องการ (ผ่าน CDP)"""
    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(folder)},
        )
        return True
    except Exception as e:
        log(f"   ⚠️ ตั้งโฟลเดอร์ดาวน์โหลดไม่ได้: {e}")
        return False


def _answer_confirm(driver, accept=True, timeout=10) -> bool:
    """รอกล่อง Confirm ของเว็บ (เช่น 'ยืนยันการดาวน์โหลดรูปภาพ?') แล้วตอบ
    คืน True เมื่อเจอกล่องและกดปุ่มแล้ว / False เมื่อไม่มีกล่องโผล่"""
    texts = (["Yes", "ใช่", "ตกลง", "OK"] if accept
             else ["No", "ไม่", "ยกเลิก", "Cancel"])
    deadline = time.time() + timeout
    while time.time() < deadline:
        btn = _find_button_by_text(driver, texts)
        if btn is not None:
            try:
                btn.click()
                return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def _dismiss_leftover_dialog(driver):
    """ปิด dialog/mask ที่อาจค้างอยู่ (กด No หรือ ESC) กันบล็อกขั้นตอนถัดไป"""
    try:
        if _answer_confirm(driver, accept=False, timeout=2):
            log("   ปิดกล่องยืนยันที่ค้างอยู่แล้ว")
            return
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    except Exception:
        pass


def _wait_download(folders, patterns, before, timeout=300):
    """รอไฟล์ใหม่ที่ตรง pattern โผล่ในโฟลเดอร์ใดโฟลเดอร์หนึ่ง และโหลดจบแล้ว
    (ไม่มี .crdownload และขนาดไฟล์นิ่ง)"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for folder in folders:
            folder = Path(folder)
            if not folder.exists():
                continue
            for pattern in patterns:
                for f in folder.glob(pattern):
                    if f in before:
                        continue
                    if f.suffix == ".crdownload" or \
                            f.with_name(f.name + ".crdownload").exists():
                        continue
                    size = f.stat().st_size
                    time.sleep(1.0)
                    if f.exists() and size > 0 and f.stat().st_size == size:
                        return f
        time.sleep(1)
    return None


def _click_and_download(driver, claim, dest_dir, button_texts, patterns,
                        what, timeout=300) -> Path:
    """กดปุ่มดาวน์โหลดแล้วรอไฟล์ — คืน path ไฟล์ หรือ None ถ้าไม่สำเร็จ"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not _set_download_dir(driver, dest_dir):
        return None

    btn = _find_button_by_text(driver, button_texts)
    if btn is None:
        log(f"   ⚠️ ไม่เจอปุ่ม '{button_texts[0]}' — ปุ่มที่เห็นบนหน้า: "
            + _dump_visible_buttons(driver))
        return None

    # เผื่อ Chrome เทไฟล์ลงโฟลเดอร์ดาวน์โหลด default (กรณีปุ่มเปิด tab ใหม่)
    # ใช้โฟลเดอร์ default เฉพาะรอบนี้ถ้ามี (กันไฟล์ปนกันเมื่อรันหลายงานพร้อมกัน)
    # ไม่งั้น fallback เป็น ~/Downloads กลางตามเดิม
    fallback_dl = default_download_dir() or (Path.home() / "Downloads")
    before = set(dest_dir.glob("*")) | set(
        fallback_dl.glob("*") if fallback_dl.exists() else []
    )

    btn.click()
    log(f"   กดปุ่มดาวน์โหลด{what}แล้ว")

    # เว็บจะเด้งกล่อง 'คุณต้องการยืนยันการดาวน์โหลด...?' — ตอบ Yes ให้
    if _answer_confirm(driver, accept=True, timeout=10):
        log("   ✓ ตอบยืนยัน (Yes) แล้ว รอไฟล์...")
    else:
        log("   ไม่มีกล่องยืนยัน — รอไฟล์...")

    f = _wait_download([dest_dir, fallback_dl], patterns, before,
                       timeout=timeout)
    if f is None:
        log(f"   ⚠️ รอไฟล์ {what} เกินเวลา — ข้าม")
        _dismiss_leftover_dialog(driver)
        return None

    if f.parent != dest_dir:  # ไฟล์ไปตกที่ Downloads — ย้ายเข้าที่
        target = dest_dir / f.name
        shutil.move(str(f), str(target))
        f = target
    log(f"   ✓ ได้ไฟล์ {f.name} ({f.stat().st_size // 1024} KB)")
    return f


def extract_zip_images(zip_path: Path, folder: Path) -> dict:
    """แตกไฟล์รูปจาก zip โดยใช้หมวดในตัว zip (PICTURES/<หมวด>/...)

    - INS / REPORTS / OTHERS → ลงโฟลเดอร์หลักแบบแบน (ชุดที่จะอัปโหลด EMCS)
    - หมวดบุคคลที่สาม TP_* → แยกใต้ tp_<xxx>/ ไม่ปนรูปรถประกัน:
      TP_VEH→tp_veh/ (รถคู่กรณี), TP_PERSON→tp_person/ (ผู้บาดเจ็บ),
      TP_PROP→tp_prop/ (ทรัพย์สิน) — โครงสร้าง zip: PICTURES/<CAT>/<id ย่อย>/file
    - ข้ามไฟล์ที่ไม่ใช่รูป (เช่น PDF)

    คืน dict นับจำนวนต่อหมวด เช่น {'INS': 23, 'TP_VEH': 10, 'TP_PERSON': 7}"""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    counts = {}
    cat_map = {}   # ชื่อไฟล์ flat → ประเภทรูป EMCS (INS/REPORTS/OTHERS) ให้ upload_images แยกกลุ่ม
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = Path(info.filename).name
            if info.is_dir() or not name:
                continue
            if Path(name).suffix.lower() not in IMAGE_EXTS:
                continue

            parts = Path(info.filename).parts  # ('PICTURES', 'INS', ...)
            category = parts[1].upper() if len(parts) > 1 else "OTHERS"
            counts[category] = counts.get(category, 0) + 1

            if category.startswith("TP_"):
                # รูปบุคคลที่สาม (รถคู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน) — แยกใต้ tp_<xxx>/
                sub = folder / category.lower()   # tp_veh / tp_person / tp_prop
                sub.mkdir(exist_ok=True)
                # ใส่ชื่อโฟลเดอร์ย่อย (คัน/คน/ชิ้น) นำหน้า กันชนกันเมื่อมีหลายราย
                grp = parts[2] if len(parts) > 3 else ""
                target = sub / (f"{grp}_{name}" if grp else name)
            else:
                target = folder / name

            stem, ext = os.path.splitext(target.name)
            k = 2
            while target.exists():
                target = target.parent / f"{stem}_{k}{ext}"
                k += 1
            target.write_bytes(zf.read(info))
            # รูป flat (INS/REPORTS/OTHERS) → จำประเภทรูป EMCS ไว้ให้ _group_flat_by_category
            # แยกกลุ่ม (INS→รูปรถประกัน, OTHERS→รูปประกอบ) + ตั้งชื่อไทยตามหมวด แทนกองประเภทเดียว
            if not category.startswith("TP_"):
                cat_map[target.name] = ZIP_CAT_TO_EMCS.get(category, ZIP_DEFAULT_EMCS_TYPE)

    if cat_map:   # sidecar รูป flat — TP_* ไม่ต้อง (จัดการผ่าน tp_*/ + n_opponents)
        import json
        try:
            (folder / "_categories.json").write_text(
                json.dumps(cat_map, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log(f"   ⚠️ เขียน _categories.json ไม่ได้: {e}")

    total = sum(counts.values())
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    log(f"   แตก zip ได้รูป {total} ไฟล์ ({detail}) → {folder}")
    return counts


_TP_EXPORT_LABEL = {
    "TP_VEH": "รูปรถคู่กรณี คันที่{n}",
    # ป้าย option dynamic จริงของ EMCS (ยืนยันหน้าจริง 2026-06-18) — ฐานคนละคำกับหมวดในแอป
    "TP_PERSON": "รูปผู้บาดเจ็บ คนที่{n}",
    "TP_PROP": "รูปทรัพย์สิน รายการที่{n}",
}


def _export_entries(search_dir: Path, claim: str):
    """yield (parts, filename) ของรูปใน PICTURES/ จากโฟลเดอร์ zip ที่แตกแล้ว หรือไฟล์ .zip ใน search_dir.
    parts = tuple ใต้ PICTURES เช่น ('INS','a.jpg') / ('TP_VEH','111','b.jpg')"""
    search_dir = Path(search_dir)
    if not search_dir.is_dir():
        return
    # 1) โฟลเดอร์แตกแล้ว (มี PICTURES/ อยู่ข้างใน) — ลองโฟลเดอร์ที่ชื่อมีเลขเคลมก่อน แล้วค่อย search_dir เอง
    for cand in sorted(search_dir.glob(f"*{claim}*")) + [search_dir]:
        if not cand.is_dir():
            continue
        root = next((p for p in cand.rglob("PICTURES") if p.is_dir()), None)
        if root:
            for f in sorted(root.rglob("*")):
                if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
                    yield f.relative_to(root).parts, f.name
            return
    # 2) ไฟล์ .zip (อาจอยู่ใน subfolder ต่อเคลม เช่น zip_import/<เคลม>/export_*.zip) — หา recursive
    #    อ่าน entry ตรง ๆ ไม่ต้องแตก. เจาะจงเลขเคลมก่อน กัน .zip เคลมอื่นปน
    zips = sorted(search_dir.rglob(f"export_{claim}_*.zip"))
    if not zips:   # เผื่อชื่อ zip ไม่มีเลขเคลม แต่อยู่ในโฟลเดอร์ที่ชื่อมีเลขเคลม
        for cand in sorted(search_dir.glob(f"*{claim}*")):
            if cand.is_dir():
                zips = sorted(cand.rglob("*.zip"))
                if zips:
                    break
    if zips:
        with zipfile.ZipFile(zips[0]) as zf:
            for info in sorted(zf.infolist(), key=lambda i: i.filename):
                if info.is_dir():
                    continue
                p = Path(info.filename)
                if p.suffix.lower() in IMAGE_EXTS and "PICTURES" in p.parts:
                    idx = p.parts.index("PICTURES")
                    yield p.parts[idx + 1:], p.name


def categories_from_export(search_dir, claim: str) -> dict:
    """map {ชื่อไฟล์รูป: ประเภทรูป EMCS} จาก zip export ของเคลม (โฟลเดอร์แตกแล้ว/ .zip) ใน search_dir —
    fallback หมวดให้ flow นำเข้า se-survey เมื่อ API ไม่มี category (รูปที่โหลด/รับมาจากภายนอก LINE/อีเมล/ระบบอื่น ไม่ถูก tag).
    INS→รูปรถประกัน, OTHERS/REPORTS→รูปประกอบ, TP_VEH/PERSON/PROP→'...คันที่/คนที่/รายการที่ N'
    (N=ลำดับกลุ่มย่อยเรียงตามชื่อ). จับคู่รูปด้วยชื่อไฟล์ (basename เดียวกับที่โหลดจาก API).
    คืน {} เมื่อไม่พบ export"""
    entries = list(_export_entries(Path(search_dir), claim))
    if not entries:
        return {}
    # ลำดับกลุ่มย่อยของหมวดบุคคลที่สาม (คันที่/คนที่/รายการที่ N) เรียงตามชื่อกลุ่ม
    raw = {}
    for parts, _ in entries:
        cat = parts[0].upper()
        if cat.startswith("TP_") and len(parts) > 2:
            raw.setdefault(cat, set()).add(parts[1])
    order = {c: {g: i + 1 for i, g in enumerate(sorted(gs))} for c, gs in raw.items()}
    out = {}
    for parts, name in entries:
        cat = parts[0].upper()
        if cat.startswith("TP_"):
            tmpl = _TP_EXPORT_LABEL.get(cat)
            if not tmpl:
                continue
            n = order.get(cat, {}).get(parts[1], 1) if len(parts) > 2 else 1
            out[name] = tmpl.format(n=n)
        else:
            out[name] = ZIP_CAT_TO_EMCS.get(cat, ZIP_DEFAULT_EMCS_TYPE)
    return out


def images_from_zip(driver, claim: str, folder: Path) -> dict:
    """โหลดรูปทั้งเคลมผ่านปุ่ม 'ดาวน์โหลดรูปภาพ' (zip) แล้วแตกลงโฟลเดอร์
    คืน dict จำนวนรูปต่อหมวด ({} เมื่อไม่สำเร็จ — ผู้เรียกควร fallback)"""
    zip_path = _click_and_download(
        driver, claim, Path(folder) / "_zip",
        ["ดาวน์โหลดรูปภาพ", "ดาวโหลดรูปภาพ", "โหลดรูปภาพ"],
        [f"export_{claim}_*.zip", "export_*.zip"],
        "รูปภาพ (zip)",
    )
    if zip_path is None:
        return {}
    return extract_zip_images(zip_path, folder)


def download_xml_export(driver, claim: str, dest_dir: Path) -> Path:
    """โหลดไฟล์ XML ของเคลม (SURV_REPORT_*.txt) เก็บไว้ใช้ภายหลัง
    ไม่สำเร็จก็ไม่เป็นไร — เป็นข้อมูลเสริม"""
    f = _click_and_download(
        driver, claim, dest_dir,
        ["ดาวน์โหลด XML", "ดาวโหลด XML", "โหลด XML"],
        ["SURV_REPORT_*.txt", "SURV_REPORT_*.xml", "*.xml"],
        "XML",
        timeout=90,  # ข้อมูลเสริม — ไม่ควรถ่วง batch นาน (ปกติมาใน ~2 วิ)
    )
    if f is not None:
        # ใส่เลขเคลมนำหน้าชื่อไฟล์ให้หาเจอง่าย (ทับของเก่าได้ — เคลมเดียวกัน)
        target = f.parent / f"{claim}_{f.name}"
        f = Path(f.replace(target))
        log(f"   ✓ เก็บ XML → {f}")
    return f
