# -*- coding: utf-8 -*-
"""ลำดับ "ครั้งที่" ของงานในเคลมเดียวกัน อ่านจากเลขเรื่องเซอร์เวย์ — กติกา user 13/09/69

EMCS เก็บ 1 เรื่องต่อเลขเคลม แล้วนับ "ครั้งที่ 1..N" ข้างใน (ครั้งแรก + งานติดตาม/เจรจาสินไหม)
ส่วน ISURVEY ออกเลขเรื่องเซอร์เวย์แยกใบต่อครั้ง ภายใต้เลขเคลมเดียวกัน — ตัวนี้ทำหน้าที่เดียว:
เอาทุกใบของเคลมมาเรียงให้ตรงกับครั้งที่ที่ EMCS จะเก็บ (ตรวจกับ EMCS จริง 2 เรื่อง 7 ใบแล้วตรงทุกใบ 13/09/69)

รูปแบบเลข
  ไอโออิ      SEABI-T PP YYMM NNNNN  T=ประเภท (1/2 งานแรก · 3 ติดตาม · 4 เจรจาสินไหม) PP=รหัสจังหวัดที่ออกตรวจ
                                      YYMM=ค.ศ. 2 หลัก+เดือน NNNNN=ลำดับเรื่อง **รันแยกตามจังหวัด+เดือน**
  ไทยไพบูลย์  SETP-YYMM NNNN         YY=พ.ศ. 2 หลัก ไม่มีหลักประเภท/จังหวัด (ลำดับรันรวมทั้งเดือน)

กติกาเรียง (user เคาะ 13/09/69)
  เดือนต่างกัน → ตามเดือน · เดือนเดียวกัน จังหวัดเดียวกัน → ตามลำดับเรื่อง ·
  เดือนเดียวกัน คนละจังหวัด → ลำดับเรื่องเทียบกันไม่ได้ ใช้วันเวลาจ่ายงานของ ISURVEY แทน
  งานที่ยกเลิกเคลม/ไม่รับงาน ไม่นับเป็นครั้ง · ครั้งที่ 1 ต้องเป็นงานประเภท 1/2 เสมอ
  (กติกา user 11/09/69: งานครั้งที่ 1 เปิดเรื่องจาก ISURVEY เสมอ)

ตัวอย่างจริง: เคลม 2026013127658 → 1=SEABI-110260400680 · 2=SEABI-410260501454 · 3=SEABI-410260502277 ·
4=SEABI-410260600413 (ลำดับ 00413 ของ มิ.ย. น้อยกว่าทุกใบ แต่เป็นครั้งสุดท้าย — จึงต้องเทียบเดือนก่อน)
"""
from __future__ import annotations

import re

SEABI_RE = re.compile(r"^SEABI-(\d)(\d{2})(\d{4})(\d{5})$")
SETP_RE = re.compile(r"^SETP-(\d{4})(\d{4})$")

#: สถานะ ISURVEY (masterStatus.sttcase_ID) ที่ไม่นับเป็นครั้ง — 99 ยกเลิกเคลม · 60 ไม่รับงาน
EXCLUDED_STATUS = {"99", "60"}
#: หลักประเภทของงานครั้งแรก (เคลมสด/เคลมแห้ง) — 3 ติดตาม · 4 เจรจาสินไหม เป็นงานต่อเนื่องเสมอ
FIRST_TYPES = {"1", "2"}
FOLLOWUP_TYPES = {"3", "4"}


def parse_survey_no(s) -> dict | None:
    """SEABI-110260400680 → {prefix, type:'1', prov:'10', yymm:'2604', seq:680} · SETP-69050083 → {type:'', prov:'', yymm:'6905', seq:83}
    อ่านไม่ออก = None"""
    s = str(s or "").strip().upper()
    m = SEABI_RE.match(s)
    if m:
        return {"prefix": "SEABI", "type": m.group(1), "prov": m.group(2), "yymm": m.group(3), "seq": int(m.group(4))}
    m = SETP_RE.match(s)
    if m:
        return {"prefix": "SETP", "type": "", "prov": "", "yymm": m.group(1), "seq": int(m.group(2))}
    return None


def is_excluded(row: dict) -> bool:
    """งานที่ไม่นับเป็นครั้ง (ยกเลิกเคลม/ไม่รับงาน) — ดูจาก sttcase_ID ของ listcases หรือ status_id ที่ผู้เรียกเติม"""
    return str(row.get("sttcase_ID") or row.get("status_id") or "").strip() in EXCLUDED_STATUS


def order_claim_jobs(rows) -> list[dict]:
    """เรียงทุกใบของเคลมตามกติกา → list ของ dict (copy) มี 'round' 1..N และ 'parsed'
    ใบที่ยกเลิก/ไม่รับงาน และเลขที่อ่านไม่ออก ถูกตัดออก (ใช้ is_excluded/parse_survey_no แยกรายงานได้)"""
    items = []
    for r in rows or []:
        if is_excluded(r):
            continue
        p = parse_survey_no(r.get("survey_no"))
        if not p:
            continue
        items.append({**r, "parsed": p})

    out: list[dict] = []
    for yymm in sorted({it["parsed"]["yymm"] for it in items}):
        grp = [it for it in items if it["parsed"]["yymm"] == yymm]
        if len({it["parsed"]["prov"] for it in grp}) <= 1:
            grp.sort(key=lambda it: it["parsed"]["seq"])
        else:
            # คนละจังหวัดในเดือนเดียวกัน: ลำดับเรื่องคนละตัวนับ → ใช้วันเวลาจ่ายงาน (ไม่มี = ไปท้ายกลุ่ม แล้วค่อยลำดับเรื่อง)
            grp.sort(key=lambda it: (str(it.get("dispatch_datetime") or "9999"), it["parsed"]["seq"]))
        out.extend(grp)
    for i, it in enumerate(out, 1):
        it["round"] = i
    return out


def round_of(ordered: list[dict], survey_no) -> int | None:
    """ครั้งที่ของเลขเซอร์เวย์นี้ในลำดับที่เรียงแล้ว — None = ไม่อยู่ในรายการ (ยกเลิก/อ่านเลขไม่ออก)"""
    s = str(survey_no or "").strip().upper()
    for it in ordered:
        if str(it.get("survey_no") or "").strip().upper() == s:
            return int(it["round"])
    return None


def first_type_ok(ordered: list[dict]) -> bool:
    """ครั้งที่ 1 ต้องเป็นงานประเภท 1/2 (ไอโออิ) — ไทยไพบูลย์ไม่มีหลักประเภท = ตรวจไม่ได้ ถือว่าผ่าน"""
    if not ordered:
        return True
    t = ordered[0]["parsed"]["type"]
    return (not t) or t in FIRST_TYPES


def describe(ordered: list[dict], survey_no="") -> str:
    """'ครั้งที่ 1 = SEABI-… · ครั้งที่ 2 = SEABI-… ←ใบนี้' ไว้ log/โชว์หน้าเว็บ"""
    s = str(survey_no or "").strip().upper()
    parts = []
    for it in ordered:
        mark = " ←ใบนี้" if s and str(it.get("survey_no") or "").strip().upper() == s else ""
        parts.append(f"ครั้งที่ {it['round']} = {it.get('survey_no')}{mark}")
    return " · ".join(parts)
