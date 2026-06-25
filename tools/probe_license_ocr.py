"""ทดลองหา preprocess ที่ทำให้ OCR ใบขับขี่ดีขึ้น (รูป zip export มัก ~800px)
รัน: runtime\\python.exe tools\\probe_license_ocr.py <image>"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from autokey.processing import imread_unicode  # noqa: E402
from autokey.license_ocr import license_score, parse_license_fields  # noqa: E402

img_path = sys.argv[1]
base = imread_unicode(img_path)
print("orig dims:", base.shape)

import easyocr  # noqa: E402
reader = easyocr.Reader(["th", "en"], gpu=False, verbose=False)


def run(name, im):
    rgb = cv2.cvtColor(im, cv2.COLOR_BGR2RGB) if im.ndim == 3 else im
    lines = [t for t in reader.readtext(rgb, detail=0) if t and t.strip()]
    print(f"\n=== {name}  (score {license_score(chr(10).join(lines))}) ===")
    for l in lines:
        print("   ", l)
    print("   fields:", parse_license_fields(lines))


def upscale(im, factor):
    h, w = im.shape[:2]
    return cv2.resize(im, (int(w * factor), int(h * factor)),
                      interpolation=cv2.INTER_CUBIC)


run("original", base)
run("upscale x2", upscale(base, 2))
run("upscale x3", upscale(base, 3))

# grayscale + CLAHE contrast (x2)
gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)
clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
clahe2 = cv2.resize(clahe, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
run("CLAHE x2", clahe2)

# sharpen on upscaled x2
up2 = upscale(base, 2)
blur = cv2.GaussianBlur(up2, (0, 0), 3)
sharp = cv2.addWeighted(up2, 1.5, blur, -0.5, 0)
run("sharpen x2", sharp)
