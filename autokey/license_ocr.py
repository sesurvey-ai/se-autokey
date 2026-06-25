"""ตรวจหา + อ่านใบขับขี่รถผู้เอาประกันจากชุดรูป (OCR ในเครื่องด้วย EasyOCR)

ทำสองอย่าง:
  1. หาว่าในโฟลเดอร์รูปมี "ใบขับขี่" ไหม (ดูจาก keyword บนบัตร)
  2. ถ้าเจอ → อ่านฟิลด์ที่ OCR แม่น: เลขใบขับขี่ (8 หลัก), เลขบัตรประชาชน
     (13 หลัก), วันออก/วันหมดอายุ, ชื่ออังกฤษ + best-effort ชื่อไทย

ออกแบบให้ optional: ส่วน parse/score เป็น pure-python (เทสต์ได้โดยไม่ต้องมี
easyocr) ส่วนที่ต้อง OCR จะ import easyocr แบบ lazy — ถ้ายังไม่ลงจะคืน
{'available': False} ไม่ทำให้โปรแกรมหลักพัง
"""
import re
from pathlib import Path

from .browser import log
from .processing import imread_unicode, natural_sort_key

try:
    from rapidfuzz import fuzz
except ImportError:  # ไม่มี rapidfuzz → ถอยไปใช้ match แบบ exact
    fuzz = None

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")

# ------------------------------------------------------------------ keyword
# กลุ่ม keyword ที่บ่งชี้ว่าเป็นใบขับขี่ (นับว่าโดนกี่กลุ่ม = คะแนน)
_KEYWORD_GROUPS = {
    "th_license": ["ใบอนุญาตขับ", "ใบขับขี่", "อนุญาตขับรถ"],
    "en_license": ["DRIVING LICENCE", "DRIVING LICENSE", "DRIVER LICENSE",
                   "DRIVING LICEN"],
    "country": ["ประเทศไทย", "KINGDOM OF THAILAND", "THAILAND"],
    "vehicle": ["รถยนต์", "รถจักรยานยนต์", "PRIVATE CAR", "MOTORCYCLE",
                "PUBLIC"],
}

# คำบนบัตรที่ไม่ใช่ชื่อคน (กันหยิบมาเป็นชื่ออังกฤษผิด)
_NAME_STOPWORDS = {
    "KINGDOM", "OF", "THAILAND", "PRIVATE", "CAR", "DRIVING", "LICENCE",
    "LICENSE", "NAME", "DATE", "ISSUE", "EXPIRY", "BIRTH", "NO", "ID",
    "BANGKOK", "MOTORCYCLE", "PUBLIC", "TEMPORARY", "TRANSPORT", "VEHICLE",
    "DD", "MM", "YYYY", "TYPE",
}
_NAME_TITLES = {"MR", "MRS", "MISS", "MS"}

_EN_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_TH_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5,
    "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10,
    "พฤศจิกายน": 11, "ธันวาคม": 12,
    # ตัวย่อ (เผื่อ OCR อ่านได้แค่บางส่วน)
    "ม.ค": 1, "ก.พ": 2, "มี.ค": 3, "เม.ย": 4, "พ.ค": 5, "มิ.ย": 6,
    "ก.ค": 7, "ส.ค": 8, "ก.ย": 9, "ต.ค": 10, "พ.ย": 11, "ธ.ค": 12,
}


def _matched_groups(text: str, fuzz_threshold: int = 80) -> set:
    """คืน set ของชื่อกลุ่ม keyword ที่เจอในข้อความ

    ใช้ fuzzy match (rapidfuzz) เพราะ OCR บัตรเคลือบมันมัก spell เพี้ยน
    เช่น 'ประเทศไทย'→'ประเรศไทย', 'KINGDOM OF THAILAND'→'KINGDOM OFFTHATAND'
    (ถ้าไม่มี rapidfuzz ถอยไปเทียบแบบ substring ตรงๆ)"""
    up = (text or "").upper()
    raw = text or ""
    hit = set()
    for key, kws in _KEYWORD_GROUPS.items():
        for kw in kws:
            hay = up if kw.isascii() else raw
            needle = kw.upper() if kw.isascii() else kw
            if fuzz is not None:
                ok = fuzz.partial_ratio(needle, hay) >= fuzz_threshold
            else:
                ok = needle in hay
            if ok:
                hit.add(key)
                break
    return hit


def license_score(text: str, fuzz_threshold: int = 80) -> int:
    """คะแนนความเป็นใบขับขี่ = จำนวนกลุ่ม keyword ที่เจอ (0-4)"""
    return len(_matched_groups(text, fuzz_threshold))


def is_license_text(text: str, score_threshold: int = 2,
                    fuzz_threshold: int = 80) -> bool:
    """ตัดสินว่าเป็น 'ใบขับขี่' ไหม — ต้องเจอ >= score_threshold กลุ่ม และ
    ต้องมีกลุ่ม keyword เฉพาะใบขับขี่ (th_license/en_license) ด้วย
    (กันสับสนกับเอกสารอื่นที่มีแค่ 'ราชอาณาจักรไทย'+'รถยนต์' เช่นเล่มทะเบียน)"""
    g = _matched_groups(text, fuzz_threshold)
    return len(g) >= score_threshold and bool(g & {"th_license", "en_license"})


def _digit_runs(text: str):
    """คืนชุดเลขล้วนในแต่ละบรรทัด (ยุบช่องว่าง/ขีดในบรรทัดก่อน)
    ใช้จับเลขใบขับขี่ 8 หลัก / เลขบัตรประชาชน 13 หลัก ที่ OCR แยกเป็นกลุ่ม"""
    runs = []
    for line in text.splitlines():
        collapsed = re.sub(r"[\s\-]", "", line)
        runs.extend(re.findall(r"\d+", collapsed))
    # เผื่อทั้งก้อนติดกัน
    runs.extend(re.findall(r"\d+", re.sub(r"[\s\-]", "", text)))
    return runs


def _find_license_no(text: str) -> str:
    """เลขใบขับขี่ = เลข 8 หลัก (เช่น 67004060)"""
    cands = [r for r in _digit_runs(text) if len(r) == 8]
    return cands[0] if cands else ""


def _find_id_no(text: str) -> str:
    """เลขบัตรประชาชน = เลข 13 หลัก (เช่น 1101400724829)"""
    cands = [r for r in _digit_runs(text) if len(r) == 13]
    return cands[0] if cands else ""


def _to_ad_year(year: int) -> int:
    """ปี พ.ศ. (>2400) → ค.ศ. ; ค.ศ. คงเดิม"""
    return year - 543 if year > 2400 else year


def _parse_dates(text: str) -> dict:
    """ดึงวันที่ทั้งหมด + จัดประเภท (issue/expiry/birth) จาก keyword ในบรรทัด
    คืน dict {'issue': 'dd/mm/yyyy', 'expiry': ..., 'birth': ...} (ค.ศ.)"""
    out = {}
    for line in text.splitlines():
        low = line.lower()
        if "expir" in low or "สิ้นอายุ" in line or "สิ้นสุด" in line \
                or "หมดอายุ" in line:
            kind = "expiry"
        elif "birth" in low or "เกิด" in line:
            kind = "birth"
        elif "issue" in low or "วันออก" in line or "ออกใบ" in line:
            kind = "issue"
        else:
            kind = None

        d = _date_in(line)
        if d and kind and kind not in out:
            out[kind] = d
    return out


def _date_in(line: str) -> str:
    """หา 'dd <เดือน> yyyy' (อังกฤษหรือไทย) ในบรรทัด → 'dd/mm/yyyy' (ค.ศ.)"""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,})\.?\s+(\d{4})", line)
    if m:
        mon = _EN_MONTHS.get(m.group(2).lower())
        if mon:
            return f"{int(m.group(1)):02d}/{mon:02d}/{_to_ad_year(int(m.group(3)))}"
    m = re.search(r"(\d{1,2})\s*([ก-๙\.]{2,})\s*(\d{4})", line)
    if m:
        key = m.group(2).rstrip(".")
        mon = _TH_MONTHS.get(key) or _TH_MONTHS.get(key.rstrip("."))
        if mon:
            return f"{int(m.group(1)):02d}/{mon:02d}/{_to_ad_year(int(m.group(3)))}"
    return ""


def _find_name_en(lines) -> str:
    """ชื่ออังกฤษ (โรมัน) บนบัตร — best-effort
    เลือกบรรทัดที่ขึ้นต้นด้วยคำนำหน้า (MR/MRS/MISS/MS) ก่อน ถ้าไม่มี
    ค่อยใช้บรรทัดอักษรละตินที่ยาวสุดซึ่งไม่ใช่คำบนบัตร
    (รับตัวพิมพ์เล็กด้วย เพราะ EasyOCR มักคืน lowercase)"""
    titled, others = [], []
    for line in lines:
        toks = [t for t in re.split(r"[^A-Za-z]+", line) if len(t) >= 2]
        if len(toks) < 2:
            continue
        up = [t.upper() for t in toks]
        has_title = up[0] in _NAME_TITLES
        words = [t for t in up if t not in _NAME_TITLES]
        if not words or any(w in _NAME_STOPWORDS for w in words):
            continue
        cand = " ".join(words)
        (titled if has_title else others).append(cand)
    if titled:
        return max(titled, key=len)
    return max(others, key=len) if others else ""


def _find_card_type(text: str) -> str:
    """ประเภทใบขับขี่ (best-effort)"""
    if "ส่วนบุคคล" in text or "PRIVATE" in text.upper():
        kind = "ส่วนบุคคล"
    elif "สาธารณะ" in text or "PUBLIC" in text.upper():
        kind = "สาธารณะ"
    elif "ชั่วคราว" in text or "TEMPORARY" in text.upper():
        kind = "ชั่วคราว"
    else:
        kind = ""
    if "จักรยานยนต์" in text or "MOTORCYCLE" in text.upper():
        veh = "รถจักรยานยนต์"
    elif "รถยนต์" in text or "CAR" in text.upper():
        veh = "รถยนต์"
    else:
        veh = ""
    return (f"{veh}{kind}").strip()


def parse_license_fields(lines) -> dict:
    """แปลงบรรทัด OCR → ฟิลด์ใบขับขี่ (pure-python, เทสต์ได้ไม่ต้องมี easyocr)"""
    if isinstance(lines, str):
        lines = lines.splitlines()
    text = "\n".join(lines)
    dates = _parse_dates(text)
    return {
        "license_no": _find_license_no(text),
        "id_no": _find_id_no(text),
        "name_en": _find_name_en(lines),
        "issue_date": dates.get("issue", ""),
        "expiry_date": dates.get("expiry", ""),
        "birth_date": dates.get("birth", ""),
        "card_type": _find_card_type(text),
    }


# ------------------------------------------------------------------ OCR (lazy)
_READER = None


def get_reader():
    """สร้าง EasyOCR Reader ครั้งเดียว (lazy) — คืน None ถ้ายังไม่ได้ลง easyocr"""
    global _READER
    if _READER is not None:
        return _READER
    try:
        import easyocr
    except ImportError:
        log("   ⚠️ ยังไม่ได้ติดตั้ง easyocr — ข้ามการตรวจใบขับขี่ "
            "(ลง: runtime\\python.exe -m pip install easyocr)")
        return None
    log("   กำลังโหลดโมเดล OCR (ครั้งแรกอาจดาวน์โหลดสักครู่)...")
    _READER = easyocr.Reader(["th", "en"], gpu=False, verbose=False)
    return _READER


def ocr_image(reader, image_path, target_dim=1600, max_upscale=3) -> list:
    """OCR รูปเดียว → list ของข้อความ
    ปรับขนาดให้ด้านยาวสุด ≈ target_dim: รูปใหญ่ย่อลง (เร็วขึ้น), รูปเล็ก
    ขยายขึ้น (รูป zip export มัก ~800px ตัวอักษรเล็กเกินไป — ขยาย x2-3 ช่วยมาก)"""
    import cv2
    img = imread_unicode(str(image_path))
    if img is None:
        return []
    h, w = img.shape[:2]
    scale = target_dim / max(h, w)
    if scale > 1.0:
        scale = min(scale, max_upscale)
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_CUBIC)
    elif scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    try:
        return [t for t in reader.readtext(rgb, detail=0) if t and t.strip()]
    except Exception as e:
        log(f"   ⚠️ OCR {Path(image_path).name} ไม่ได้: {type(e).__name__}")
        return []


def find_and_read_license(folder, score_threshold=2, max_images=40) -> dict:
    """สแกนรูปในโฟลเดอร์ (เฉพาะโฟลเดอร์หลัก ไม่รวม tp_*) หาใบขับขี่ที่
    คะแนน keyword สูงสุด ถ้าถึงเกณฑ์ → อ่านฟิลด์ออกมา

    คืน dict:
      {'available': False}                      = ยังไม่ได้ลง easyocr
      {'available': True, 'found': False, ...}  = ไม่เจอใบขับขี่
      {'available': True, 'found': True, 'image': ชื่อไฟล์, 'score': n,
       'fields': {...}, 'text': [...] }         = เจอ + อ่านได้
    """
    folder = Path(folder)
    reader = get_reader()
    if reader is None:
        return {"available": False}
    if not folder.is_dir():
        return {"available": True, "found": False, "score": 0,
                "threshold": score_threshold}

    files = sorted(
        [f for f in folder.iterdir()
         if f.is_file() and f.suffix.lower() in IMAGE_EXTS],
        key=lambda f: natural_sort_key(f.name),
    )
    if len(files) > max_images:
        log(f"   รูป {len(files)} เกิน {max_images} — สแกนหาใบขับขี่แค่ "
            f"{max_images} ไฟล์แรก (ปรับ max_images ได้)")
        files = files[:max_images]

    best = None     # (score, file, lines) เฉพาะรูปที่เป็นใบขับขี่จริง
    top_score = 0   # คะแนนสูงสุดที่เจอ (ไว้ log ตอนไม่พบ)
    for f in files:
        lines = ocr_image(reader, f)
        if not lines:
            continue
        text = "\n".join(lines)
        groups = _matched_groups(text)
        top_score = max(top_score, len(groups))
        if len(groups) >= score_threshold and (groups & {"th_license",
                                                         "en_license"}):
            if best is None or len(groups) > best[0]:
                best = (len(groups), f, lines)
            # เจอใบขับขี่ที่มีเลขใบขับขี่ชัด → มั่นใจแล้ว ไม่ต้องสแกนรูปที่เหลือ
            if _find_license_no(text):
                break

    if best is None:
        log(f"   ไม่พบใบขับขี่ในชุดรูป (คะแนนสูงสุด {top_score}/{score_threshold})")
        return {"available": True, "found": False,
                "score": top_score, "threshold": score_threshold}

    score, f, lines = best
    fields = parse_license_fields(lines)
    log(f"   ✓ พบใบขับขี่: {f.name} (คะแนน {score}) — "
        f"เลขที่ {fields['license_no'] or '?'}, "
        f"หมดอายุ {fields['expiry_date'] or '?'}")
    return {"available": True, "found": True, "image": f.name,
            "score": score, "fields": fields, "text": lines}


# ------------------------------------------------------------------ cross-check
def _digits(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def cross_check(fields: dict, data) -> list:
    """เทียบฟิลด์ที่อ่านได้กับข้อมูลในเคลม — คืน list ของ
    {'field', 'ocr', 'claim', 'match'} เฉพาะฟิลด์ที่มีข้อมูลให้เทียบ"""
    out = []
    pairs = [
        ("เลขใบขับขี่", _digits(fields.get("license_no")),
         _digits(getattr(data, "driver_license_no", ""))),
        ("เลขบัตรประชาชน", _digits(fields.get("id_no")),
         _digits(getattr(data, "driver_idcard", ""))),
    ]
    for label, ocr_v, claim_v in pairs:
        if ocr_v and claim_v:
            out.append({"field": label, "ocr": ocr_v, "claim": claim_v,
                        "match": ocr_v == claim_v})
    return out


# ------------------------------------------------------------------ CLI (test)
if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    if target.is_file():
        r = get_reader()
        if r is None:
            sys.exit(1)
        ls = ocr_image(r, target)
        print("OCR lines:")
        for l in ls:
            print("  ", l)
        print("score:", license_score("\n".join(ls)))
        print("fields:", parse_license_fields(ls))
    else:
        import json
        print(json.dumps(find_and_read_license(target),
                         ensure_ascii=False, indent=2))
