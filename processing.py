"""[Shim เพื่อความเข้ากันได้กับ notebook เดิม]

โค้ดจริงย้ายไปอยู่ที่ autokey/processing.py แล้ว
`from processing import *` ใน notebook เดิมยังใช้ได้เหมือนเดิม
"""
from autokey.processing import *  # noqa: F401,F403
from autokey.processing import process_images_pro  # ให้ชื่อหลักชัดเจน

if __name__ == "__main__":
    import os

    BASE = os.path.dirname(os.path.abspath(__file__))
    process_images_pro(
        os.path.join(BASE, "downloaded_images"),
        os.path.join(BASE, "templates", "check.jpg"),
        threshold=0.75,
    )
