# -*- coding: utf-8 -*-
"""รูปบุคคลที่สามแยกตามคัน/คน/ชิ้น (แท็บ "นำเข้า ISURVEY") — user สั่งแก้ 07/10/69

ISURVEY เก็บรูปแยกโฟลเดอร์ต่อรายการ ชื่อโฟลเดอร์มี 2 แบบ (ตรวจงานจริง 385 โฟลเดอร์):
  ตัวเลข "1791202382" = เพิ่มคู่กรณีในแอปมือถือ · "tp_car20260921112413" = เพิ่มบนหน้าเว็บ (มี "_" ในชื่อ)
บั๊กเดิม: _tp_image_batches ตัดชื่อที่ "_" ตัวแรก → แบบเว็บได้ "tp" ทุกคัน → รูปทุกคันลง "คันที่ 1"

รัน:  python -m pytest tests/test_tp_photo_groups.py -q
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import emcs  # noqa: E402
from autokey.images import tp_group_key, tp_group_sort_key  # noqa: E402


def _batches(names, count):
    with tempfile.TemporaryDirectory() as d:
        tp = Path(d) / "tp_veh"
        tp.mkdir()
        for i, n in enumerate(names):
            (tp / n).write_bytes(b"img%d" % i)          # เนื้อไม่ซ้ำ ไม่โดนตัวกันรูปซ้ำ
        out = emcs._tp_image_batches(d, "tp_veh", count, "รูปรถคู่กรณี คันที่{i}",
                                     "รูปรถคู่กรณีคันที่{i}_{seq}", rename=False)
        return [(t, sorted(p.name for p in paths)) for t, paths in out]


def test_group_key_both_folder_styles():
    assert tp_group_key("1791202382_rn_image_picker_lib_temp_x.jpg") == "1791202382"
    assert tp_group_key("tp_car20260921112413__1_.jpg") == "tp_car20260921112413"
    assert tp_group_key("in_inj20261001090000_02___00_001.jpg") == "in_inj20261001090000"
    assert tp_group_key("abc_1.jpg") == "abc"                       # ไม่รู้จัก = พฤติกรรมเดิม


def test_numeric_folders_split_by_vehicle():
    names = ["1791202803_rn_c.jpg", "1791202382_rn_a.jpg", "1791202382_rn_b.jpg", "1791202803_rn_d.jpg"]
    assert _batches(names, 2) == [
        ("รูปรถคู่กรณี คันที่1", ["1791202382_rn_a.jpg", "1791202382_rn_b.jpg"]),
        ("รูปรถคู่กรณี คันที่2", ["1791202803_rn_c.jpg", "1791202803_rn_d.jpg"]),
    ]


def test_web_folders_split_by_vehicle_not_lumped_into_first():
    # เคลม 2026013077730: 2 คันเพิ่มบนเว็บห่างกัน 8 วิ — เดิมรวมเป็นคันที่ 1 ทั้งหมด
    names = ["tp_car20260921112413__1_.jpg", "tp_car20260921112413__2_.jpg",
             "tp_car20260921112405__1_.jpg", "tp_car20260921112405__2_.jpg"]
    assert _batches(names, 2) == [
        ("รูปรถคู่กรณี คันที่1", ["tp_car20260921112405__1_.jpg", "tp_car20260921112405__2_.jpg"]),
        ("รูปรถคู่กรณี คันที่2", ["tp_car20260921112413__1_.jpg", "tp_car20260921112413__2_.jpg"]),
    ]


def test_folder_count_mismatch_still_lumps_with_warning():
    # โฟลเดอร์ไม่เท่าจำนวนคัน = แยกไม่ได้แน่ → คงกติกาเดิม รวมเป็นคันที่ 1 (ไม่เดา)
    assert _batches(["1791202382_a.jpg", "1791202382_b.jpg"], 2) == [
        ("รูปรถคู่กรณี คันที่1", ["1791202382_a.jpg", "1791202382_b.jpg"])]


def test_sort_by_creation_time_across_prefixes():
    # ผู้บาดเจ็บในรถประกัน (in_inj) เพิ่มทีหลังคนในรถคู่กรณี (tp_inj) — เรียงตามตัวอักษรจะได้ in_ ก่อนเสมอ (ผิด)
    keys = ["in_inj20261001100000", "tp_inj20261001090000"]
    assert sorted(keys, key=tp_group_sort_key) == ["tp_inj20261001090000", "in_inj20261001100000"]
    # ตัวเลข (Unix วินาที) เทียบกับแบบเว็บด้วยเวลาจริง · อ่านเวลาไม่ออกไปท้ายแถว
    assert sorted(["zzz", "1791202803", "1791202382"], key=tp_group_sort_key) == ["1791202382", "1791202803", "zzz"]


if __name__ == "__main__":
    import traceback
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception:
                fails += 1; print("FAIL", name); traceback.print_exc()
    sys.exit(1 if fails else 0)
