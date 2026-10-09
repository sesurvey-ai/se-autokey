# -*- coding: utf-8 -*-
"""เลขกรมธรรม์เกิน 30 ตัว (เคลม 2026013177918 · 08/10/69)

อาการ: นำเข้า XML ไม่ผ่านทั้งไฟล์ "ข้อมูลยาวเกินขนาดช่อง · POLICYNO (รถคู่กรณีคันที่ 20): ส่งไป
HQ-AV1-0013006-00000-2026-02-001 (32 ตัว) — ช่องรับได้ 30 ตัว" · ช่องบนหน้า EMCS ก็ maxlength=30 (พิมพ์เกิน = ตัดท้ายเงียบ ๆ)
กติกา (user เคาะ 09/10/69): เกิน 30 → ตัดขีดกับช่องว่างออก · ไม่เกิน = ตามเดิม · ตัดแล้วยังเกิน = ไม่ตัดท้าย (เว็บกั้นอนุมัติ/ด่านก่อนนำเข้า)

รัน:  python -m pytest tests/test_emcs_policy_len.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autokey import emcs  # noqa: E402

REAL = "HQ-AV1-0013006-00000-2026-02-001"


def test_rule():
    assert emcs.EMCS_POLICY_MAX == 30
    assert emcs.emcs_policy_no(REAL) == "HQAV1001300600000202602001"
    assert len(emcs.emcs_policy_no(REAL)) == 26
    thirty = "AB-1234-5678-9012-3456-7890-12"        # 30 ตัวพอดี = คงขีดไว้
    assert len(thirty) == 30 and emcs.emcs_policy_no(thirty) == thirty
    assert emcs.emcs_policy_no("  12 34 56 78 90 12 34 56 78 90 12  ") == "1234567890123456789012"
    assert emcs.emcs_policy_no("1/2569-0001-000000000-00000000000") == "1/25690001000000000" + "00000000000"
    long_alnum = "A" * 35                             # ตัดแล้วยังเกิน = ไม่ตัดท้าย
    assert emcs.emcs_policy_no(long_alnum) == long_alnum
    assert emcs.emcs_policy_no(None) == "" and emcs.emcs_policy_no("-") == "-" and emcs.emcs_policy_no(" x ") == "x"


def test_fit_policy_xml_only_long_values():
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<INSERT_SURV_REPORT_XML><TXN_SURV_REPORT>'
           '<ACC_POLICY_NO>1234-5678</ACC_POLICY_NO><ASSURED_NAME>บริษัท ทดสอบ จำกัด</ASSURED_NAME></TXN_SURV_REPORT>'
           f'<TXN_SURV_CAR><CAR_TYPE>0</CAR_TYPE><POLICYNO></POLICYNO></TXN_SURV_CAR>'
           f'<TXN_SURV_CAR><CAR_TYPE>20</CAR_TYPE><POLICYNO>{REAL}</POLICYNO><CLAIMNO>C-1</CLAIMNO></TXN_SURV_CAR>'
           '</INSERT_SURV_REPORT_XML>')
    out, changed = emcs.fit_policy_xml(xml)
    assert changed == [("POLICYNO", REAL, "HQAV1001300600000202602001")]
    assert "<POLICYNO>HQAV1001300600000202602001</POLICYNO>" in out
    assert out == xml.replace(REAL, "HQAV1001300600000202602001")     # ที่เหลือคงทุกตัวอักษร
    same, none = emcs.fit_policy_xml(out)
    assert none == [] and same == out


def test_fit_policy_xml_acc_policy_and_entities():
    long_acc = "POL - 2026 - AB&CD - 0000000001 - 99"
    xml = f"<TXN_SURV_REPORT><ACC_POLICY_NO>{long_acc.replace('&', '&amp;')}</ACC_POLICY_NO></TXN_SURV_REPORT>"
    out, changed = emcs.fit_policy_xml(xml)
    assert changed and changed[0][0] == "ACC_POLICY_NO" and changed[0][2] == "POL2026AB&CD000000000199"
    assert "<ACC_POLICY_NO>POL2026AB&amp;CD000000000199</ACC_POLICY_NO>" in out


def test_policy_for_emcs_logs(monkeypatch):
    seen = []
    monkeypatch.setattr(emcs, "log", lambda m: seen.append(m))
    assert emcs._policy_for_emcs(REAL, "เลขกรมธรรม์คู่กรณี 1") == "HQAV1001300600000202602001"
    assert any("ตัดขีด/ช่องว่างออกเหลือ 26 ตัว" in m for m in seen)
    seen.clear()
    assert emcs._policy_for_emcs("1234-5678", "กรมธรรม์เลขที่") == "1234-5678" and seen == []
    assert emcs._policy_for_emcs("A" * 31, "กรมธรรม์เลขที่") == "A" * 31
    assert any("ตัดท้ายเหลือ 30" in m for m in seen)


def test_web_precheck_blocks_only_when_still_too_long():
    import webui
    rep = {"policy_no": REAL, "opposing_parties": [{"policy_no": REAL}, {"policy_no": "B" * 31}, "x"]}
    out = webui._policy_length_blockers(rep)
    assert len(out) == 1 and "คู่กรณีคันที่ 2" in out[0] and "31 ตัว" in out[0]
    assert webui._policy_length_blockers({"policy_no": "", "opposing_parties": None}) == []
