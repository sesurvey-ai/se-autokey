# -*- coding: utf-8 -*-
"""ปิดงานบน ISURVEY ("ยืนยันการตรวจสอบ") จากเซิร์ฟเวอร์ — เขียนกลับหลังหัวหน้าอนุมัติบนเว็บ se-survey (user เคาะ 08/09/69)

ที่มา: เคสที่ดึงจาก ISURVEY มาตรวจบนเว็บเรา (flow 3) พออนุมัติแล้ว งานต้นทางยังค้าง "รอตรวจข้อมูล"
หัวหน้าต้องไปกดปิดเองบน ISURVEY อีกที = ตรวจ 2 ที่ · โมดูลนี้ทำแทนด้วยบัญชี ISURVEY ของหัวหน้าคนที่อนุมัติ

คำสั่งจริงที่ดักจากหน้าเว็บ 08/09/69 (เคลม 2026013169905 — ดู memory isurvey-writeback-plan):
  ปุ่ม tab1_save "ยืนยันการตรวจสอบ" = POST web/php/supervisor/confirmcase.php ด้วยฟอร์มแท็บ 1 ทั้งชุด (87 ช่อง)
  → {"success":true,"message":"ทำการเปลี่ยนเรียบร้อยแล้ว"} → ISURVEY ตั้ง "จบงาน" (status 100) + ผู้ตรวจ/เวลา = บัญชีที่ล็อกอิน
  (หน้าเว็บยิง GET make_report/PDF_SupvComment.php ตามหลังด้วย — แค่สร้าง PDF ความเห็น ไม่กระทบสถานะ ที่นี่ไม่ทำ)

ค่าทุกช่องอ่านได้จาก getcaseinfo.php tab-1_clone (คีย์เดียวกับฟอร์ม แต่ไม่มี tab1_/-inputEl) แล้วเขียนทับ 3 กลุ่ม:
  1) accident_summary  ← "ผลการดำเนินงาน" บนเว็บเรา (ว่าง = คงของเดิมใน ISURVEY ไม่ล้าง)
  2) ตารางค่าสำรวจ SUR_* (ฝั่งพนักงาน = survey_pay) / INS_* (ฝั่งประกัน = survey_expenses)
     — ส่งแทน extension se-billing ที่ user จะถอดออก (rates=None = คงของเดิมทั้งตาราง)
  3) supervisor_summary = close_case ("ปิดการตรวจสอบ") · กลุ่ม "ข้อมูลความรับผิดชอบ" ไม่ส่ง (user: ปล่อยว่าง)

กติกา: ทำเฉพาะงานที่ยังไม่ปิด (close_datetime ว่าง + status อยู่ในชุดที่เคยเห็นตอน "รอตรวจข้อมูล")
        เจอสถานะอื่นที่ไม่รู้จัก = หยุด ไม่เดา · dry_run คืน payload ให้ดูโดยไม่ยิง
⛔ ตัวเลขเงินส่งแบบไม่มีคอมมา (หน้าเว็บส่ง "1,350.00" แต่ PHP อาจ floatval เป็น 1) — ปลอดภัยกว่าทั้งสองทาง
"""
from __future__ import annotations

import re

from .browser import log
from .isurvey_api import ISurveyAPI, _ddmmyyyy, _money

# status (sttcase_ID) ที่เห็นจริงในงาน "รอตรวจข้อมูล" 08/09/69 (40 ปกติ · 99 มี rpt_flag=X) — ปิดได้
PENDING_STATUS = {"40", "99"}
CLOSED_STATUS = "100"          # "จบงาน"
CONFIRM_PATH = "supervisor/confirmcase.php"
VAT_RATE = 0.07

# แถวในตารางค่าสำรวจ: (คีย์ที่ส่ง, คีย์ในตัวเลือก rates, คีย์ใน bill ที่อ่านมา) — ลำดับตามฟอร์มจริง
_ROWS = ("INVEST", "DIST", "TRANS", "OTHER", "PHOTO", "TEL", "INSURE", "DAILY", "CLAIM", "CARTOW")
_BILL_KEY = {"CARTOW": "TOWCAR"}   # ฟอร์มใช้ CARTOW แต่ API/DB ใช้ TOWCAR


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _hhmm(v) -> str:
    """'13:18:00' → '13:18' · ว่าง → ''"""
    s = _s(v)
    m = re.match(r"(\d{1,2}):(\d{2})", s)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else s


def _fmt(v, blank_if_empty: bool = True) -> str:
    """เงิน → '700.00' (ไม่มีคอมมา) · None/'' → '' (หรือ '0.00' เมื่อ blank_if_empty=False)"""
    if v is None or _s(v) == "":
        return "" if blank_if_empty else "0.00"
    return f"{_money(v):.2f}"


def _int(v) -> str:
    try:
        return str(int(float(str(v).replace(",", "") or 0)))
    except (TypeError, ValueError):
        return "0"


def build_payload(t1: dict, comment: str | None = None, rates: dict | None = None) -> dict:
    """ประกอบฟอร์มแท็บ 1 (87 ช่อง) จากผล getcaseinfo tab-1 + สิ่งที่จะเขียนทับ

    rates (ไม่ส่ง = คงตารางเดิม):
      {"sur": {invest, trans, dist, other, photo, tel, insure, daily, claim, cartow, deduct},
       "ins": {invest, invest_num, trans, trans_num, dist, dist_num, other, other_desc, photo, photo_num,
               tel, insure, daily, daily_num, claim, cartow}}
    ทุกค่าเป็นตัวเลข (ยอดรวมของแถว ไม่ใช่ราคาต่อหน่วย) — ฝั่ง se-survey คูณจำนวนมาให้แล้ว
    """
    claim = t1.get("Claim") or {}
    disp = t1.get("Dispatch") or {}
    bill = t1.get("bill") or {}
    pol = t1.get("Policy") or {}
    # ส่งมาเฉพาะฝั่งที่เว็บเรามีข้อมูล (มีเฉพาะ survey_pay หรือเฉพาะ survey_expenses ก็ได้) — ฝั่งที่ไม่ส่งคงของเดิม
    sur_in = (rates or {}).get("sur")
    ins_in = (rates or {}).get("ins")
    use_sur = isinstance(sur_in, dict)
    use_ins = isinstance(ins_in, dict)
    sur_in = sur_in or {}
    ins_in = ins_in or {}

    p: dict[str, str] = {
        "caseID": _s(claim.get("caseID") or t1.get("caseID") or bill.get("caseID")),
        "dispID": _s(disp.get("dispID")),
        "button_event": "",
        "notify_no": _s(claim.get("notify_no")),
        "policy_no": _s(pol.get("policy_no")),
        "claim_no": _s(claim.get("claim_no")),
        "survey_no": _s(claim.get("survey_no")),
        "claim_MtypeID": _s(claim.get("claim_MtypeID")),
        "claim_typeID": _s(claim.get("claim_typeID")),
        "follow_num": _s(claim.get("follow_num")) or "1",
        # combobox ประเภทคู่กรณี — หน้าเว็บส่งข้อความที่โชว์ ("กรุณาเลือก" เมื่อยังไม่เลือก)
        "tab1_thirdParty_type-inputEl": _s(claim.get("claim_TP")) or "กรุณาเลือก",
        "surveyorID": _s(claim.get("surveyorID")),
        "surveyor_name": _s(claim.get("surveyor_name")),
        "empcode": _s(claim.get("empcode")),
        "sys_branch": _s(claim.get("sys_branchName")) or _s(claim.get("sys_branch")),
        "useOSS": _s(claim.get("useOSS")) or "N",
        "OSS_companyID": _s(claim.get("OSS_companyID")),
        "OSS_company": _s(claim.get("OSS_company")),
        "OSS_SurveyorName": _s(claim.get("OSS_SurveyorName")),
        "OSS_phone": _s(claim.get("OSS_phone")),
        "acc_provinceID": _s(claim.get("acc_provinceID")),
        "acc_amphurID": _s(claim.get("acc_amphurID")),
        "dispatch_date": _ddmmyyyy(disp.get("dispatch_date")),
        "dispatch_time": _hhmm(disp.get("dispatch_time")),
        "confirm_date": _ddmmyyyy(disp.get("confirm_date")),
        "confirm_time": _hhmm(disp.get("confirm_time")),
        "arrive_date": _ddmmyyyy(disp.get("arrive_date")),
        "arrive_time": _hhmm(disp.get("arrive_time")),
        "finish_date": _ddmmyyyy(disp.get("finish_date")),
        "finish_time": _hhmm(disp.get("finish_time")),
        "sendReportDate": _ddmmyyyy(disp.get("sendReportDate")),
        "sendReportTime": _hhmm(disp.get("sendReportTime")),
        "closeDate": "",                       # server ตั้งเอง (หน้าเว็บก็ส่งว่าง)
        "cmp_arrive": _s(claim.get("cmp_arrive")),
        "acc_zone": _s(claim.get("acc_zone")),
        "tab1_rd-in_out": (_s(claim.get("wrkTime")) or _s(disp.get("WrkTime")) or "ใน"),
        "tab1_chk_co_area": (_s(claim.get("COArea")) or _s(disp.get("COArea")) or "N"),
        "survey_provinceID": _s(claim.get("survey_provinceID")),
        "survey_amphurID": _s(claim.get("survey_amphurID")),
        "service_type": _s(claim.get("service_type")),
        # ความเห็นหัวหน้า: ของเราทับ · ว่าง = คงของเดิม (ห้ามล้างสิ่งที่หัวหน้าเคยพิมพ์ใน ISURVEY)
        "accident_summary": (comment.strip() if comment and comment.strip() else _s(t1.get("accident_summary"))),
        "tab1_PC_SUR_VAT-inputEl": "",
        "tab1_PC_INS_VAT-inputEl": "",
    }

    # ---- ตารางค่าสำรวจ ----
    def sur_val(row):
        k = "SUR_" + _BILL_KEY.get(row, row)
        return sur_in.get(row.lower()) if use_sur else bill.get(k)

    def ins_val(row):
        k = "INS_" + _BILL_KEY.get(row, row)
        return ins_in.get(row.lower()) if use_ins else bill.get(k)

    def num_val(row, bill_key):
        return ins_in.get(row.lower() + "_num") if use_ins else bill.get(bill_key)

    # ช่องที่หน้าเว็บส่ง "0.00" แม้ว่าง (INVEST DIST TRANS OTHER PHOTO DAILY) กับที่ส่ง "" (TEL INSURE CLAIM CARTOW)
    zero_rows = {"INVEST", "DIST", "TRANS", "OTHER", "PHOTO", "DAILY"}

    def money(row, v, ours):
        return _fmt(v, blank_if_empty=(row not in zero_rows) and not ours)

    p["tab1_INVEST_NUM-inputEl"] = _int(num_val("INVEST", "INVEST_NUM"))
    p["tab1_SUR_INVEST-inputEl"] = money("INVEST", sur_val("INVEST"), use_sur)
    p["tab1_INS_INVEST-inputEl"] = money("INVEST", ins_val("INVEST"), use_ins)
    p["tab1_DIST_NUM-inputEl"] = _int(num_val("DIST", "DISTANCE"))
    p["tab1_SUR_DIST-inputEl"] = money("DIST", sur_val("DIST"), use_sur)
    p["tab1_INS_DIST-inputEl"] = money("DIST", ins_val("DIST"), use_ins)
    p["tab1_TRANS_NUM-inputEl"] = _int(num_val("TRANS", "TRANS_NUM"))
    p["tab1_SUR_TRANS-inputEl"] = money("TRANS", sur_val("TRANS"), use_sur)
    p["tab1_INS_TRANS-inputEl"] = money("TRANS", ins_val("TRANS"), use_ins)
    p["tab1_FUL_OTHER-inputEl"] = _s(ins_in.get("other_desc")) if use_ins else _s(bill.get("OTHER_DESC"))
    p["tab1_SUR_OTHER-inputEl"] = money("OTHER", sur_val("OTHER"), use_sur)
    p["tab1_INS_OTHER-inputEl"] = money("OTHER", ins_val("OTHER"), use_ins)
    p["tab1_PHOTO_NUM-inputEl"] = _int(num_val("PHOTO", "PHOTO_NUM"))
    p["tab1_SUR_PHOTO-inputEl"] = money("PHOTO", sur_val("PHOTO"), use_sur)
    p["tab1_INS_PHOTO-inputEl"] = money("PHOTO", ins_val("PHOTO"), use_ins)
    p["tab1_SUR_TEL-inputEl"] = money("TEL", sur_val("TEL"), use_sur)
    p["tab1_INS_TEL-inputEl"] = money("TEL", ins_val("TEL"), use_ins)
    p["tab1_FUL_INSURE-inputEl"] = ""
    p["tab1_SUR_INSURE-inputEl"] = money("INSURE", sur_val("INSURE"), use_sur)
    p["tab1_INS_INSURE-inputEl"] = money("INSURE", ins_val("INSURE"), use_ins)
    p["tab1_DAILY_NUM-inputEl"] = _int(num_val("DAILY", "DAILY_NUM"))
    p["tab1_SUR_DAILY-inputEl"] = money("DAILY", sur_val("DAILY"), use_sur)
    p["tab1_INS_DAILY-inputEl"] = money("DAILY", ins_val("DAILY"), use_ins)
    p["tab1_RECV_CLAIM-inputEl"] = _int(bill.get("RECV_CLAIM"))
    p["tab1_SUR_CLAIM-inputEl"] = money("CLAIM", sur_val("CLAIM"), use_sur)
    p["tab1_INS_CLAIM-inputEl"] = money("CLAIM", ins_val("CLAIM"), use_ins)
    p["tab1_FUL_CARTOW-inputEl"] = ""
    p["tab1_SUR_CARTOW-inputEl"] = money("CARTOW", sur_val("CARTOW"), use_sur)
    p["tab1_INS_CARTOW-inputEl"] = money("CARTOW", ins_val("CARTOW"), use_ins)

    sur_total = sum(_money(p[f"tab1_SUR_{r}-inputEl"]) for r in _ROWS)
    ins_total = sum(_money(p[f"tab1_INS_{r}-inputEl"]) for r in _ROWS)
    inc_vat = _s(bill.get("INC_VAT")).upper()
    rd_vat = inc_vat if inc_vat in ("Y", "N") else "Y"
    ins_vat = round(ins_total * VAT_RATE, 2) if rd_vat == "Y" else 0.0
    p["tab1_SUR_TOTAL-inputEl"] = f"{sur_total:.2f}"
    p["tab1_INS_TOTAL-inputEl"] = f"{ins_total:.2f}"
    p["tab1_SUR_VAT-inputEl"] = "0.00"                       # ฝั่งพนักงานไม่มี VAT (หน้าเว็บส่ง 0.00 เสมอ)
    p["tab1_INS_VAT-inputEl"] = f"{ins_vat:.2f}"
    p["tab1_rd_vat"] = rd_vat
    p["tab1_SUR_TOTAL_NET-inputEl"] = f"{sur_total:.2f}"
    p["tab1_INS_TOTAL_NET-inputEl"] = f"{round(ins_total + ins_vat, 2):.2f}"
    p["memo"] = _s(bill.get("memo"))
    p["chk_claimform"] = _s(t1.get("chk_claimform"))
    p["chk_chassisNo"] = _s(t1.get("chk_chassisNo"))
    p["chk_drvLic"] = _s(t1.get("chk_drvLic"))
    p["chk_prtDoc"] = _s(t1.get("chk_prtDoc"))
    p["chk_other"] = _s(t1.get("chk_other"))
    p["supervisor_summary"] = "close_case"                 # "ปิดการตรวจสอบ" — กติกา user 08/09/69
    p["tab1_deduct_amount"] = _int(sur_in.get("deduct")) if use_sur else "0"
    return p


def _find_case(api: ISurveyAPI, claim: str, survey_no: str) -> dict:
    """เหมือน api.find_case แต่รอได้นาน — listcases.php บางครั้งช้าเกิน 30 วิ (เจอ 08/09/69)"""
    r = api.s.get(f"{api.base}/supervisor/listcases.php", timeout=150,
                  params=dict(claim_no=claim, claim_status="", claim_date="", page=1, start=0, limit=25))
    r.raise_for_status()
    cases = [c for c in r.json().get("cases", []) if str(c.get("claim_no")) == str(claim)]
    if survey_no:
        cases = [c for c in cases if str(c.get("survey_no")) == str(survey_no)]
    if not cases:
        raise RuntimeError(f"ISURVEY: ไม่พบเคลม {claim}{(' / ' + survey_no) if survey_no else ''}")
    if len({str(c.get("survey_no")) for c in cases}) > 1:
        raise RuntimeError(f"ISURVEY: เคลม {claim} มีหลายเลขเซอร์เวย์ — ต้องระบุเลขเซอร์เวย์ให้ตรง")
    return cases[0]


def check_can_close(case: dict) -> str | None:
    """คืนข้อความเหตุผลที่ปิดไม่ได้ · None = ปิดได้"""
    if _s(case.get("close_datetime")):
        return f"งานนี้ปิดไปแล้วบน ISURVEY เมื่อ {case.get('close_datetime')}"
    st = _s(case.get("sttcase_ID"))
    if st == CLOSED_STATUS:
        return "งานนี้เป็น \"จบงาน\" บน ISURVEY อยู่แล้ว"
    if st not in PENDING_STATUS:
        return f"งานบน ISURVEY อยู่สถานะ {st or '?'} ไม่ใช่ \"รอตรวจข้อมูล\" — ไม่ปิดให้ (กันปิดงานที่ยังไม่ส่งรายงาน)"
    return None


def close_case(api: ISurveyAPI, claim: str, survey_no: str = "", comment: str | None = None,
               rates: dict | None = None, dry_run: bool = True) -> dict:
    """ปิดงาน 1 เรื่อง — คืน {ok, dry_run, case, payload, message, closed}

    dry_run=True: ประกอบคำสั่งครบแล้วคืนให้ดู **ไม่ยิง** (ค่าเริ่มต้น — เปิดยิงจริงต้องสั่งชัดเจน)
    ยิงจริง: ตอบ success แล้ว **อ่านกลับ** ยืนยันว่าเป็น "จบงาน" จริง ไม่เชื่อแค่ข้อความตอบ
    """
    case = _find_case(api, claim, survey_no)
    # ปิดไปแล้ว (หัวหน้าปิดมือก่อนหน้า / ยิงซ้ำ) = ไม่ใช่ความผิดพลาด — คืนสถานะให้ backend จดว่า "ปิดแล้ว" ได้เลย
    if _s(case.get("close_datetime")) or _s(case.get("sttcase_ID")) == CLOSED_STATUS:
        return {"ok": True, "dry_run": False, "closed": True, "skipped": "already_closed",
                "close_datetime": _s(case.get("close_datetime")),
                "case": {"caseID": _s(case.get("caseID")), "claim_no": _s(case.get("claim_no")),
                         "survey_no": _s(case.get("survey_no")), "status_before": _s(case.get("sttcase_ID"))},
                "message": f"งานนี้ปิดบน ISURVEY ไปก่อนแล้ว{(' เมื่อ ' + _s(case.get('close_datetime'))) if _s(case.get('close_datetime')) else ''}"}
    why = check_can_close(case)
    if why:
        raise RuntimeError(why)
    cid = str(case.get("caseID"))
    t1 = api.get_tab(cid, 1)
    if not (t1.get("Claim") or {}).get("claim_no"):
        raise RuntimeError(f"ISURVEY: อ่านแท็บ 1 ของ caseID {cid} ไม่ได้")
    payload = build_payload(t1, comment=comment, rates=rates)
    info = {"caseID": cid, "claim_no": _s(case.get("claim_no")), "survey_no": _s(case.get("survey_no")),
            "status_before": _s(case.get("sttcase_ID")), "surveyor": _s(case.get("surveyor_name"))}
    if dry_run:
        log(f"   [dry-run] ISURVEY confirmcase เคลม {claim}: ประกอบ {len(payload)} ช่อง ไม่ยิง")
        return {"ok": True, "dry_run": True, "case": info, "payload": payload, "closed": False,
                "message": "dry-run: ประกอบคำสั่งแล้ว ยังไม่ยิงจริง"}

    log(f"   ISURVEY confirmcase เคลม {claim} (caseID {cid}) ...")
    r = api.s.post(f"{api.base}/{CONFIRM_PATH}", data=payload, timeout=120)
    text = r.text[:300]
    try:
        j = r.json()
    except ValueError:
        j = {}
    if r.status_code != 200 or not (isinstance(j, dict) and j.get("success")):
        raise RuntimeError(f"ISURVEY ตอบ {r.status_code}: {(j.get('message') if isinstance(j, dict) else '') or text}")
    # อ่านกลับ — สถานะต้องเปลี่ยนเป็น "จบงาน" จริง
    after = _find_case(api, claim, survey_no)
    closed = _s(after.get("sttcase_ID")) == CLOSED_STATUS or bool(_s(after.get("close_datetime")))
    if not closed:
        raise RuntimeError(f"ISURVEY ตอบสำเร็จแต่สถานะยังเป็น {after.get('sttcase_ID')} — ตรวจบนหน้าเว็บ ISURVEY")
    return {"ok": True, "dry_run": False, "case": info, "payload": payload, "closed": True,
            "message": _s(j.get("message")) or "ทำการเปลี่ยนเรียบร้อยแล้ว",
            "close_datetime": _s(after.get("close_datetime"))}
