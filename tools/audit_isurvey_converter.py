# -*- coding: utf-8 -*-
"""audit ตัวแปลง ISURVEY → se-survey (isurvey_to_sesurvey.build_case) ทีละช่อง

เทียบ "สิ่งที่ตัวแปลงส่งออก" กับ "ชุดค่าที่ปลายทางรับ" โดยอ่านชุดค่านั้นจาก **ซอร์สจริงของ se-survey**
(caseOptions.ts / CaseDetail.tsx / RecordEditors.tsx / districtOptions.ts / xmlExport.service.ts)
จึงไม่ต้องคัดลอกลิสต์มาไว้ที่นี่ — เว็บเปลี่ยนลิสต์เมื่อไหร่ audit ก็เห็นทันที

ทำไมต้องมี: 03/09/69 เคส #221 เจอ 4 บั๊กจากชุดรหัสไม่ตรงกัน (เพศ / ระดับความเสียหาย / ค่ารูป / NBSP)
ทีละบั๊ก ทีละรอบ — ตัวนี้ไล่ทุกช่องในครั้งเดียว

ใช้:  python tools/audit_isurvey_converter.py --claim 2026013167173 --survey SEABI-220260800746
      python tools/audit_isurvey_converter.py --raw runs/audit/2026013167173_raw.json   (ออฟไลน์ จากไฟล์ที่เคยดัมป์)
ผล:   ตารางบนจอ + runs/audit/<claim>_audit.md + runs/audit/<claim>_raw.json (ข้อมูลดิบ ISURVEY — gitignored)

⚠️ อ่าน ISURVEY อย่างเดียว ไม่เขียนกลับ ไม่แตะ EMCS ไม่สร้างเคสบน se-survey
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_SESURVEY = ROOT.parent / "se-survey"

# ── อ่านชุดค่าที่ปลายทางรับ จากซอร์ส se-survey ──────────────────────────────────

def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _quoted(block: str) -> list[str]:
    """ดึงสตริงในเครื่องหมายคำพูดเดี่ยวทั้งหมดในบล็อก (ลิสต์ของเว็บเป็น 'ก', 'ข' ทั้งหมด)"""
    return [m.group(1) for m in re.finditer(r"'((?:[^'\\]|\\.)*)'", block)]


def _const_array(src: str, name: str) -> list[str]:
    m = re.search(r"(?:export )?const %s\b[^=]*=\s*\[(.*?)\];" % re.escape(name), src, re.S)
    return _quoted(m.group(1)) if m else []


def _const_record_keys(src: str, name: str) -> list[str]:
    """key ของ Record<string,string> = { 'ก': '1', ... }"""
    m = re.search(r"(?:export )?const %s\b[^=]*=\s*\{(.*?)\n\};" % re.escape(name), src, re.S)
    if not m:
        return []
    return [k for k, _v in re.findall(r"'((?:[^'\\]|\\.)*)'\s*:\s*'([^']*)'", m.group(1))]


def _select_values(src: str, name: str) -> list[str]:
    m = re.search(r'<select[^>]*name="%s"[^>]*>(.*?)</select>' % re.escape(name), src, re.S)
    return re.findall(r'<option[^>]*value="([^"]*)"', m.group(1)) if m else []


def load_targets(sesurvey: Path) -> dict:
    web = sesurvey / "web" / "src" / "components" / "cases"
    opt = _read(web / "caseOptions.ts")
    detail = _read(web / "CaseDetail.tsx")
    rec = _read(web / "RecordEditors.tsx")
    dist = _read(web / "districtOptions.ts")
    xml = _read(sesurvey / "backend" / "src" / "services" / "xmlExport.service.ts")
    ins_path = web / "insurerOptions.ts"
    # ไฟล์นี้ generate มาเป็นเครื่องหมายคำพูดคู่ (ต่างจากไฟล์อื่น) — อ่านทั้งสองแบบ
    insurers = (re.findall(r'"([^"\n]*)"', _read(ins_path)) + _quoted(_read(ins_path))) if ins_path.exists() else []

    brands: dict[str, list[str]] = {}
    m = re.search(r"CAR_BRANDS_BY_TYPE[^=]*=\s*\{(.*?)\n\};", opt, re.S)
    if m:
        for code, body in re.findall(r"\n\s*([A-Z]):\s*\[(.*?)\]", m.group(1), re.S):
            brands[code] = _quoted(body)

    districts: dict[str, list[str]] = {}
    for prov, body in re.findall(r"'([^']+)':\s*\[(.*?)\]", dist, re.S):
        vals = _quoted(body)
        if vals:
            districts[prov] = vals

    fault_forms: dict[str, list[str]] = {}
    for v, cond in re.findall(r'name="acc_fault" value="([^"]*)"[^>]*defaultChecked=\{([^}]*)\}', detail):
        fault_forms[v] = re.findall(r"=== '([^']*)'", cond) or [v]

    maxlen: dict[str, int] = {}
    m = re.search(r"const EMCS_MAXLEN[^=]*=\s*\{(.*?)\n\};", xml, re.S)
    if m:
        for k, v in re.findall(r"([A-Z_]+):\s*(\d+)", m.group(1)):
            maxlen[k] = int(v)

    return {
        "provinces": [p for p in _const_array(opt, "PROVINCE_OPTIONS") if not p.startswith("--")],
        "brands": brands,
        "policy_types": [p for p in _const_array(opt, "POLICY_TYPE_OPTIONS") if not p.startswith("--")],
        "policy_type_codes": _const_record_keys(xml, "POLICY_TYPE_CODE"),
        "colors": [c for c in _const_array(opt, "CAR_COLOR_OPTIONS") if not c.startswith("--")],
        "acc_causes": [c for c in _const_array(opt, "ACC_CAUSE_OPTIONS") if not c.startswith("--")],
        "cause_codes": _const_record_keys(xml, "CAUSE"),
        "damage_types": [c for c in _const_array(opt, "ACC_DAMAGE_TYPE_OPTIONS") if not c.startswith("--")],
        "claim_types": re.findall(r"code:\s*'([A-Z])'", opt),
        "car_types": [v for v in _select_values(detail, "car_type") if v != "0"],
        "titles": [v for v in _select_values(detail, "driver_title") if v != "0"],
        "relations": [v for v in _select_values(detail, "driver_relation") if v != "0"],
        "license_types": [v for v in _select_values(detail, "driver_license_type") if v != "0"],
        "fault_forms": fault_forms,
        "fault_codes": _const_record_keys(xml, "FAULT"),
        "person_types": _const_array(rec, "PERSON_TYPES"),
        "wound_levels": _const_array(rec, "WOUND_LEVELS"),
        "opo_relations": _const_array(rec, "RELATIONS"),
        "genders_th": _const_array(rec, "GENDERS"),
        "opo_titles": _const_array(rec, "TITLES"),
        "opo_car_types": _const_array(rec, "OPO_CAR_TYPES"),
        "opo_license_types": _const_array(rec, "LICENSE_TYPES"),
        "insurers": insurers,
        "districts": districts,
        "parts_no_side": _const_array(_read(web / "DamageEditor.tsx"), "PARTS_NO_SIDE"),
        "parts_with_side": _const_array(_read(web / "DamageEditor.tsx"), "PARTS_WITH_SIDE"),
        "maxlen": maxlen,
    }


# ── ตัวตรวจ ──────────────────────────────────────────────────────────────────

DATE_RE = re.compile(r"^\d{2}/\d{2}/(\d{4})$")
DT_RE = re.compile(r"^\d{2}/\d{2}/(\d{4})(\|\d{2}:\d{2})?$")
TIME_RE = re.compile(r"^\d{2}:\d{2}$")
HIDDEN_RE = re.compile("[   　​‌‍﻿]")
NAME_OK_RE = re.compile(r"^[ a-zA-Z0-9ก-๙.\-]*$")   # กติกาเดียวกับ EMCS_NAME_OK ของเว็บ


class Audit:
    def __init__(self):
        self.rows: list[tuple[str, str, str, str, str]] = []   # (section, field, value, status, note)

    def add(self, section, field, value, status, note=""):
        v = "" if value is None else str(value)
        if len(v) > 60:
            v = v[:57] + "…"
        self.rows.append((section, field, v, status, note))

    def check_in(self, section, field, value, allowed, empty_ok=True, note_extra=""):
        if value in (None, ""):
            self.add(section, field, value, "EMPTY" if empty_ok else "MISSING", note_extra)
        elif str(value) in allowed:
            self.add(section, field, value, "OK", note_extra)
        else:
            self.add(section, field, value, "MISMATCH", f"ไม่อยู่ในลิสต์ปลายทาง ({len(allowed)} ค่า) {note_extra}".strip())

    def check_date(self, section, field, value, allow_time=False):
        if value in (None, ""):
            self.add(section, field, value, "EMPTY"); return
        m = (DT_RE if allow_time else DATE_RE).match(str(value))
        if not m:
            self.add(section, field, value, "MISMATCH", "รูปแบบต้อง dd/mm/yyyy(พ.ศ.)" + ("|HH:MM" if allow_time else "")); return
        y = int(m.group(1))
        self.add(section, field, value, "OK" if y >= 2400 else "MISMATCH", "" if y >= 2400 else "ปีไม่ใช่ พ.ศ.")

    def check_text(self, section, field, value, maxlen=None, name_like=False):
        if value in (None, ""):
            self.add(section, field, value, "EMPTY"); return
        s = str(value)
        issues = []
        if HIDDEN_RE.search(s):
            issues.append("มีอักขระซ่อน (NBSP/zero-width)")
        if maxlen and len(s) > maxlen:
            issues.append(f"ยาว {len(s)} > EMCS maxlength {maxlen}")
        if name_like and not NAME_OK_RE.match(s):
            bad = "".join(sorted({ch for ch in s if not NAME_OK_RE.match(ch)}))
            issues.append(f"อักขระที่ EMCS ไม่รับในชื่อ: {bad!r}")
        self.add(section, field, value, "MISMATCH" if issues else "OK", " · ".join(issues))


def run_checks(payload: dict, T: dict) -> Audit:
    A = Audit()
    r = payload["report"]
    ML = T["maxlen"]

    # ── หัวเคส / กรมธรรม์ ──
    A.check_text("เคส", "survey_job_no", r.get("survey_job_no"), ML.get("SURV_JOBNO"))
    A.check_text("เคส", "claim_no", r.get("claim_no"))
    A.check_text("เคส", "claim_ref_no", r.get("claim_ref_no"), ML.get("ACC_CLAIMREF_NO"))
    A.check_text("เคส", "policy_no", r.get("policy_no"), ML.get("ACC_POLICY_NO"))
    A.check_text("เคส", "assured_name", r.get("assured_name"), ML.get("ASSURED_NAME"), name_like=True)
    A.check_in("เคส", "policy_type", r.get("policy_type"), set(T["policy_types"]) | set(T["policy_type_codes"]),
               note_extra="(dropdown เว็บ + คีย์ที่ export แปลงเป็นรหัสได้)")
    A.check_date("เคส", "policy_start", r.get("policy_start"))
    A.check_date("เคส", "policy_end", r.get("policy_end"))
    A.check_in("เคส", "claim_type", r.get("claim_type"), T["claim_types"])

    # ── อุบัติเหตุ ──
    A.check_date("อุบัติเหตุ", "acc_date", r.get("acc_date"))
    t = r.get("acc_time")
    A.add("อุบัติเหตุ", "acc_time", t, "EMPTY" if not t else ("OK" if TIME_RE.match(str(t)) else "MISMATCH"))
    A.check_text("อุบัติเหตุ", "acc_place", r.get("acc_place"), ML.get("ACC_PLACE"))
    A.check_in("อุบัติเหตุ", "acc_province", r.get("acc_province"), T["provinces"])
    prov, distv = r.get("acc_province"), r.get("acc_district")
    A.check_in("อุบัติเหตุ", "acc_district", distv, T["districts"].get(prov or "", []),
               note_extra=f"(ลิสต์อำเภอของ '{prov}')")
    A.check_in("อุบัติเหตุ", "acc_cause", r.get("acc_cause"), set(T["acc_causes"]) | set(T["cause_codes"]))
    fault = r.get("acc_fault")
    accepted_forms = {f for forms in T["fault_forms"].values() for f in forms} | set(T["fault_codes"])
    A.check_in("อุบัติเหตุ", "acc_fault", fault, accepted_forms, note_extra="(radio เว็บ + FAULT ของ export)")
    A.check_text("อุบัติเหตุ", "acc_surveyor", r.get("acc_surveyor"), ML.get("ACC_SURV"), name_like=True)
    A.check_text("อุบัติเหตุ", "acc_surveyor_phone", r.get("acc_surveyor_phone"))
    ph = r.get("acc_surveyor_phone")
    if ph and not re.fullmatch(r"\d{9,10}", str(ph)):
        A.add("อุบัติเหตุ", "acc_surveyor_phone(รูปแบบ)", ph, "MISMATCH", "EMCS รับตัวเลข ≤10 หลัก")
    for f in ("acc_customer_report_date", "acc_insurance_notify_date",
              "acc_survey_arrive_date", "acc_survey_complete_date", "acc_police_date"):
        A.check_date("ไทม์ไลน์", f, r.get(f), allow_time=True)
    A.check_text("ตำรวจ", "acc_police_name", r.get("acc_police_name"), ML.get("POLICE_NAME"))
    A.check_text("ตำรวจ", "acc_police_station", r.get("acc_police_station"), ML.get("POLICE_STATION"))
    A.check_text("ความเห็น", "acc_detail", r.get("acc_detail"))
    A.check_text("ความเห็น", "review_comment", r.get("review_comment"))
    A.check_text("ความเห็น", "surveyor_comment", r.get("surveyor_comment"))

    # ── รถประกัน ──
    A.check_text("รถประกัน", "license_plate", r.get("license_plate"), ML.get("CAR_REGNO"))
    A.check_in("รถประกัน", "car_province", r.get("car_province"), T["provinces"])
    A.check_in("รถประกัน", "car_type", r.get("car_type"), T["car_types"])
    ct = r.get("car_type") or ""
    A.check_in("รถประกัน", "car_brand", r.get("car_brand"), T["brands"].get(ct, []),
               note_extra=f"(ยี่ห้อของประเภท '{ct}' — ไม่อยู่ในลิสต์ = บอทต้อง fuzzy บน EMCS)")
    A.check_text("รถประกัน", "car_model", r.get("car_model"), ML.get("MODELNO"))
    A.check_in("รถประกัน", "car_color", r.get("car_color"), T["colors"])
    A.check_text("รถประกัน", "chassis_no", r.get("chassis_no"), ML.get("CHASSISNO"))
    A.check_text("รถประกัน", "engine_no", r.get("engine_no"), ML.get("ENGINENO"))
    ec = r.get("estimated_cost")
    A.add("รถประกัน", "estimated_cost", ec, "EMPTY" if ec in (None, "") else ("OK" if isinstance(ec, (int, float)) else "MISMATCH"))

    # ── ผู้ขับขี่ ──
    A.check_in("ผู้ขับขี่", "driver_title", r.get("driver_title"), T["titles"])
    A.check_text("ผู้ขับขี่", "driver_first_name", r.get("driver_first_name"), name_like=True)
    A.check_text("ผู้ขับขี่", "driver_last_name", r.get("driver_last_name"), name_like=True)
    A.check_in("ผู้ขับขี่", "driver_gender", r.get("driver_gender"), ["M", "F"])
    ag = r.get("driver_age")
    A.add("ผู้ขับขี่", "driver_age", ag, "EMPTY" if ag in (None, "") else ("OK" if isinstance(ag, int) else "MISMATCH"))
    A.check_date("ผู้ขับขี่", "driver_birthdate", r.get("driver_birthdate"))
    A.check_text("ผู้ขับขี่", "driver_address", r.get("driver_address"), ML.get("DRI_ADDRESS"))
    A.check_in("ผู้ขับขี่", "driver_province", r.get("driver_province"), T["provinces"])
    dp, dd = r.get("driver_province"), r.get("driver_district")
    A.check_in("ผู้ขับขี่", "driver_district", dd, T["districts"].get(dp or "", []), note_extra=f"(ลิสต์อำเภอของ '{dp}')")
    A.check_text("ผู้ขับขี่", "driver_phone", r.get("driver_phone"))
    cid = r.get("driver_id_card")
    thai = bool(cid) and bool(re.fullmatch(r"\d{13}", str(cid)))
    idt = r.get("driver_id_type")
    want = "thai" if (thai or not cid) else "foreign"
    A.add("ผู้ขับขี่", "driver_id_card", cid, "EMPTY" if not cid else ("OK" if thai else "INFO"),
          "" if not cid or thai else f"ไม่ใช่เลข 13 หลัก → driver_id_type ควรเป็น foreign (ได้ {idt!r})")
    A.check_in("ผู้ขับขี่", "driver_id_type", idt, [want], empty_ok=False, note_extra=f"(คาดว่า {want} ตามเลขบัตร)")
    A.check_text("ผู้ขับขี่", "driver_license_no", r.get("driver_license_no"), ML.get("DRI_DRVID"))
    A.check_in("ผู้ขับขี่", "driver_license_type", r.get("driver_license_type"), T["license_types"])
    A.check_text("ผู้ขับขี่", "driver_license_place", r.get("driver_license_place"), ML.get("DRI_DRVPLACE"))
    A.check_date("ผู้ขับขี่", "driver_license_start", r.get("driver_license_start"))
    A.check_date("ผู้ขับขี่", "driver_license_end", r.get("driver_license_end"))
    A.check_in("ผู้ขับขี่", "driver_relation", r.get("driver_relation"), T["relations"])

    # ── ความเสียหายรถประกัน ──
    parts_all = set(T["parts_no_side"]) | set(T["parts_with_side"])
    for i, it in enumerate(r.get("insured_damage") or [], 1):
        part, pos, lvl = it.get("part"), it.get("pos"), it.get("level")
        in_list = part in parts_all
        side_word = re.search(r"(ซ้าย|ขวา)$", part or "")
        A.add("ความเสียหาย", f"[{i}] part", part, "OK" if in_list else "WARN",
              "" if in_list else ("ชื่อมีคำบอกข้าง ('%s') ท้ายชื่อ — เว็บ/EMCS แยกข้างเป็น pos; ควรตัดคำแล้วตั้ง pos" % side_word.group(1)
                                  if side_word else "ไม่ตรง checklist 22 ชิ้น → ตกช่องอิสระบน EMCS (ยอมรับได้)"))
        A.check_in("ความเสียหาย", f"[{i}] level", lvl, ["L", "M", "H", "X"], empty_ok=False)
        A.check_in("ความเสียหาย", f"[{i}] pos", pos, ["L", "R", "A"], empty_ok=False,
                   note_extra="(ไม่มี pos → บอทใช้ 'ทั้งคู่' ทั้งที่ชิ้นส่วนบอกข้าง)" if not pos else "")

    # ── คู่กรณี ──
    for i, o in enumerate(r.get("opposing_parties") or [], 1):
        S = f"คู่กรณี {i}"
        A.check_in(S, "title", o.get("title"), T["opo_titles"])
        A.check_text(S, "first_name", o.get("first_name"), name_like=True)
        A.check_in(S, "gender", o.get("gender"), T["genders_th"])
        A.check_date(S, "birthdate", o.get("birthdate"))
        A.check_in(S, "province(ป้ายทะเบียน)", o.get("province"), T["provinces"])
        A.check_in(S, "home_province", o.get("home_province"), T["provinces"])
        hp, pp = o.get("home_province"), o.get("province")
        A.check_in(S, "district", o.get("district"), T["districts"].get(hp or "", []),
                   note_extra=("⚠️ เว็บกรองลิสต์อำเภอด้วยจังหวัดป้ายทะเบียน ไม่ใช่ภูมิลำเนา — จังหวัดต่างกัน อำเภอนี้จะไม่โผล่บนเว็บ"
                               if hp and pp and hp != pp else ""))
        A.check_in(S, "car_type", o.get("car_type"), T["opo_car_types"])
        A.check_in(S, "car_color", o.get("car_color"), T["colors"])
        A.check_in(S, "insurer", o.get("insurer"), T["insurers"], note_extra="(ชื่อบริษัทที่ EMCS มี)")
        A.check_in(S, "policy_type", o.get("policy_type"), set(T["policy_types"]) | set(T["policy_type_codes"]))
        A.check_in(S, "license_type", o.get("license_type"), T["opo_license_types"])
        A.check_in(S, "relation", o.get("relation"), T["opo_relations"])
        A.check_date(S, "license_start", o.get("license_start"))
        A.check_date(S, "license_end", o.get("license_end"))
        A.check_text(S, "owner_name", o.get("owner_name"), name_like=True)

    # ── ผู้บาดเจ็บ ──
    for i, p in enumerate(r.get("injured_persons") or [], 1):
        S = f"ผู้บาดเจ็บ {i}"
        A.check_in(S, "person_type", p.get("person_type"), T["person_types"], empty_ok=False)
        A.check_in(S, "wound_level", p.get("wound_level"), T["wound_levels"])
        A.check_in(S, "gender", p.get("gender"), T["genders_th"])
        A.check_text(S, "name", p.get("name"), name_like=True)
        cidp = p.get("cid")
        if cidp and not re.fullmatch(r"\d{13}", str(cidp)):
            A.add(S, "cid", cidp, "WARN", "ไม่ใช่ 13 หลัก (EMCS เช็ค checksum บัตรผู้บาดเจ็บ)")

    # ── ทรัพย์สิน ──
    for i, a in enumerate(r.get("damaged_property") or [], 1):
        A.check_text(f"ทรัพย์สิน {i}", "item", a.get("item"))
        A.check_text(f"ทรัพย์สิน {i}", "owner_name", a.get("owner_name"), name_like=True)

    # ── ค่าใช้จ่าย ──
    ex = payload.get("expenses")
    if ex:
        for k, v in ex.items():
            A.add("ค่าใช้จ่าย", k, v, "EMPTY" if v in (None, "") else ("OK" if isinstance(v, (int, float)) or re.fullmatch(r"\d+(\.\d+)?", str(v)) else "MISMATCH"))
        pc, pp_ = ex.get("photo_fee_count"), ex.get("photo_fee_price")
        if pc and pp_:
            A.add("ค่าใช้จ่าย", "ค่ารูป = ราคาต่อรูป × จำนวน", f"{pp_} × {pc} = {float(pp_) * float(pc):g}", "INFO",
                  "photo_fee_price ต้องเป็นราคาต่อรูป (5) ไม่ใช่ยอดรวม")
    else:
        A.add("ค่าใช้จ่าย", "(ทั้งก้อน)", "", "EMPTY", "ISURVEY ยังไม่กรอกยอด — หัวหน้ากรอกบนเว็บ")

    for w in payload.get("warnings") or []:
        A.add("คำเตือนจากตัวแปลง", "", w, "INFO")
    return A


# ── main ─────────────────────────────────────────────────────────────────────

def dump_raw(api, case, cid) -> dict:
    raw = {"case": case, "tabs": {}, "parts": api.get_parts(cid), "records": {}, "masters": {}}
    for t in (1, 2, 3, 7, 8):
        raw["tabs"][str(t)] = api.get_tab(cid, t)
    for t in (4, 5, 6):
        rows = api.list_records(cid, t)
        raw["records"][str(t)] = [{"row": row, "rec": api.get_record(cid, t, row.get("ikey")),
                                   "parts": (api.opponent_parts(cid, row.get("ikey")) if t == 4 else None)}
                                  for row in rows if row.get("ikey")]
    # ชื่อบริษัทประกันของคู่กรณี resolve ผ่าน api._company(รหัส) — เก็บผลไว้ให้โหมด --raw เล่นซ้ำได้
    raw["company_names"] = {}
    for x in raw["records"].get("4", []):
        code = str((x["rec"] or {}).get("oth_insure_companyID") or "").strip()
        if code and code not in raw["company_names"]:
            raw["company_names"][code] = api._company(code)
    for name, k, v in (("masterClaimVerdict", "cvdID", "claim_verdict"), ("masterClaimMType", "clMTID", "claim_Mtype"),
                       ("masterRelation", "relID", "relation"), ("masterDrvLicense", "dvlTID", "dvl_type"),
                       ("masterPolicyType", "poTID", "policy_type"), ("masterVehType", "vehTID", "veh_type")):
        raw["masters"][name] = api.master(name, k, v)
    return raw


class _ReplayAPI:
    """เล่นซ้ำจากไฟล์ดิบ — ให้ build_case ทำงานได้โดยไม่ต้องต่อ ISURVEY"""
    def __init__(self, raw):
        self.raw = raw
    def get_tab(self, cid, t): return self.raw["tabs"].get(str(t), {})
    def get_parts(self, cid): return self.raw.get("parts", [])
    def list_records(self, cid, t): return [x["row"] for x in self.raw["records"].get(str(t), [])]
    def get_record(self, cid, t, ikey):
        for x in self.raw["records"].get(str(t), []):
            if x["row"].get("ikey") == ikey:
                return x["rec"]
        return {}
    def master(self, name, k, v): return self.raw["masters"].get(name, {})
    def _company(self, code): return self.raw.get("company_names", {}).get(str(code), "")
    def opponent_parts(self, cid, ikey):
        for x in self.raw["records"].get("4", []):
            if x["row"].get("ikey") == ikey:
                return x.get("parts") or []
        return []
    def _tumbon(self, c): return ""
    def _amphur(self, c): return ""
    def _prov(self, c): return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claim")
    ap.add_argument("--survey", default="")
    ap.add_argument("--raw", help="ไฟล์ดิบที่เคยดัมป์ (ออฟไลน์)")
    ap.add_argument("--sesurvey", default=str(DEFAULT_SESURVEY))
    args = ap.parse_args()

    T = load_targets(Path(args.sesurvey))
    out_dir = ROOT / "runs" / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    from autokey.isurvey_to_sesurvey import build_case
    if args.raw:
        raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
        api, case = _ReplayAPI(raw), raw["case"]
        claim = str(case.get("claim_no"))
    else:
        from autokey.config import load_config
        from autokey.isurvey_api import ISurveyAPI
        api = ISurveyAPI(load_config(require=("ISURVEY",)))
        api.login()
        case = api.find_case(args.claim, args.survey)
        claim = args.claim
        raw = dump_raw(api, case, case["caseID"])
        (out_dir / f"{claim}_raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"ดัมป์ข้อมูลดิบ → runs/audit/{claim}_raw.json")

    payload = build_case(api, case["caseID"], case)
    (out_dir / f"{claim}_payload.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    A = run_checks(payload, T)

    counts = {}
    for _, _, _, st, _ in A.rows:
        counts[st] = counts.get(st, 0) + 1
    lines = [f"# audit ตัวแปลง ISURVEY → se-survey — เคลม {claim} / {case.get('survey_no', '')}", "",
             "สรุป: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items())), "",
             "| หมวด | ช่อง | ค่า | ผล | หมายเหตุ |", "|---|---|---|---|---|"]
    for sec, f, v, st, note in A.rows:
        lines.append(f"| {sec} | {f} | {v.replace('|', '¦')} | {st} | {note} |")
    md = "\n".join(lines)
    (out_dir / f"{claim}_audit.md").write_text(md, encoding="utf-8")

    print("\n".join(lines[:3]))
    for sec, f, v, st, note in A.rows:
        if st in ("MISMATCH", "MISSING", "WARN"):
            print(f"  {st:8} {sec} · {f} = {v!r}  {note}")
    print(f"\nรายงานเต็ม → runs/audit/{claim}_audit.md")


if __name__ == "__main__":
    main()
