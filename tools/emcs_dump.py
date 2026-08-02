# -*- coding: utf-8 -*-
"""emcs_dump.py — ดูด "ข้อมูลของเคส" ออกจากหน้า EMCS  READ-ONLY ล้วน

ต่างจาก emcs_spec.py ที่เก็บ *สเปกฟอร์ม* (ตัวเลือกทั้งหมด/ช่องบังคับ) —
ตัวนี้เก็บ **เฉพาะค่าที่กรอกหรือถูกเลือกอยู่จริง** เพื่อเอาเคสจริงมาเป็นข้อมูลทดสอบ
และเทียบว่า XML ที่ระบบเราสร้าง ตรงกับของจริงใน EMCS แค่ไหน

    text/hidden   → ค่าใน value=""
    textarea      → ข้อความข้างใน
    select        → เฉพาะ option ที่ selected (เก็บทั้งรหัสและป้าย)
    radio/checkbox→ เฉพาะอันที่ checked
    ช่องว่างข้ามหมด · ไม่แตะ option ที่ไม่ได้เลือก · ไม่สนโครงสร้างฟอร์ม

รัน:
    python tools/emcs_dump.py <ไฟล์.html...>                 # ดูค่าที่กรอกไว้
    python tools/emcs_dump.py <ไฟล์...> --xml out.xml        # ประกอบเป็น SURV_REPORT XML
    python tools/emcs_dump.py <ไฟล์...> --diff xml.txt       # เทียบกับ XML ที่ส่งไป tag ต่อ tag
    python tools/emcs_dump.py <ไฟล์...> --learn xml.txt      # ช่วยหา id ↔ tag ตอนเพิ่ม map ใหม่

--diff ตอบคำถามว่า "ที่เราส่งไป กับที่อยู่ใน EMCS ตอนนี้ ต่างกันตรงไหน" ซึ่งมี 3 สาเหตุ
คือ เราไม่ได้ส่ง (tag ว่าง) · EMCS เติมเอง · หัวหน้าแก้ทีหลัง — แยกได้จากตัวค่าที่เห็น

⚠️ กับดัก
 1) หน้า ASP.NET มี hidden ขยะเพียบ (__VIEWSTATE ยาวเป็นแสนตัวอักษร) ต้องกรองทิ้ง
 2) แถวซ้ำ (คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน) ใช้ id จริง ctl00/ctl01 **ห้ามยุบเป็น ctlNN**
    เพราะคนละแถวคือคนละคน — ตรงข้ามกับ emcs_spec.py ที่ต้องยุบ
 3) ป้ายใน dropdown ซ้ำกันได้ (รหัสต่างกันแต่ชื่อเดียวกัน) → เก็บรหัสเป็นหลักเสมอ
"""
import argparse
import json
import re
import sys
from html import unescape
from pathlib import Path

# hidden ของ ASP.NET / ตัวช่วยภายในหน้า ที่ไม่ใช่ข้อมูลเคส
JUNK = re.compile(r"^(__|ctl00_|hif(Mode|Page)|.*_ClientState$)")
JUNK_EXACT = {"__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
              "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS", "__SCROLLPOSITIONX",
              "__SCROLLPOSITIONY"}
TAG_RE = re.compile(r"<(input|select|textarea)\b(.*?)(?:/?>)", re.S | re.I)


def _attr(attrs: str, name: str):
    m = re.search(r'\b%s\s*=\s*"([^"]*)"' % name, attrs, re.I)
    return m.group(1) if m else None


def _has(attrs: str, name: str) -> bool:
    return re.search(r'\b%s\b' % name, attrs, re.I) is not None


def values(src: str) -> dict:
    """{id: {kind, value, label?}} เฉพาะช่องที่มีค่า"""
    out = {}
    for m in TAG_RE.finditer(src):
        tag, attrs = m.group(1).lower(), m.group(2)
        eid = _attr(attrs, "id")
        if not eid or eid in JUNK_EXACT or JUNK.match(eid):
            continue

        if tag == "input":
            typ = (_attr(attrs, "type") or "text").lower()
            if typ in ("submit", "button", "image", "reset", "file"):
                continue
            if typ in ("radio", "checkbox"):
                if _has(attrs, "checked"):
                    out[eid] = {"kind": typ, "value": _attr(attrs, "value") or "on",
                                "name": _attr(attrs, "name") or ""}
                continue
            v = unescape(_attr(attrs, "value") or "").strip()
            if v:
                out[eid] = {"kind": "text", "value": v}

        elif tag == "textarea":
            end = src.find("</textarea>", m.end())
            if end < 0:
                continue
            v = unescape(re.sub(r"<[^>]+>", "", src[m.end():end])).strip()
            if v:
                out[eid] = {"kind": "textarea", "value": v}

        elif tag == "select":
            end = src.find("</select>", m.end())
            if end < 0:
                continue
            sel = re.search(r'<option([^>]*\bselected\b[^>]*)>([^<]*)<', src[m.end():end], re.I)
            if not sel:
                continue
            code = unescape(_attr(sel.group(1), "value") or "").strip()
            label = unescape(sel.group(2)).strip()
            if code and not label.startswith("--"):
                out[eid] = {"kind": "select", "value": code, "label": label}
    return out


# ── โหมด --learn: หา id ↔ tag จากเคสที่มีทั้ง HTML และ XML ของตัวเอง ──────────
def _norm(name: str) -> str:
    """ตัด prefix ชนิดคอนโทรลกับตัวเลขท้าย เหลือแก่นชื่อไว้เทียบกับชื่อ tag"""
    n = re.sub(r"^(txt|ddl|rdo|chk|hif|hdf|lbl|cal|wu\w*?_)+", "", name)
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"\d+$", "", n).upper())


def learn(vals: dict, xml_src: str) -> dict:
    """จับคู่ id ↔ tag ด้วย 2 สัญญาณรวมกัน แล้วเลือกตัวที่คะแนนดีที่สุด

    ใช้ค่าอย่างเดียวไม่พอ — ค่าซ้ำกันได้เยอะ (ชื่อผู้เอาประกัน = ผู้แจ้ง = ผู้ขับขี่,
    ช่องเงินเป็น 0 หมด) ใช้ชื่ออย่างเดียวก็ไม่พอ — ddlClm_Cause ↔ CAUSE_CODE ชื่อไม่คล้าย
    เอามารวมกันแล้วตัดคะแนนต่ำทิ้ง เหลือเฉพาะคู่ที่มั่นใจ ที่เหลือรายงานให้แมปมือ
    """
    tags = {t: unescape(v).strip()
            for t, v in re.findall(r"<([A-Z_0-9]+)>([^<]*)</\1>", xml_src)}
    tags = {t: v for t, v in tags.items() if v and v.strip("0.") != ""}

    best = {}
    for tag, tval in tags.items():
        tnorm = _norm(tag)
        for eid, info in vals.items():
            enorm = _norm(eid.split("_wu")[-1] if "_wu" in eid else eid)
            score = 0
            # ค่าสั้น ๆ ชนกันง่าย (รหัส 1-2 หลัก) ให้เครดิตน้อยกว่า
            if tval in (info["value"], info.get("label")):
                score += 3 if len(tval) >= 4 else 1
            if enorm == tnorm:
                score += 4
            elif enorm and (enorm in tnorm or tnorm in enorm):
                score += 2
            if score >= 4 and score > best.get(tag, (0,))[0]:
                best[tag] = (score, eid)

    matched = {t: {"id": e, "score": s} for t, (s, e) in sorted(best.items())}
    return {"matched": matched,
            "unmatched_tags": sorted(set(tags) - set(matched)),
            "unmatched_fields": sorted(set(vals) - {v["id"] for v in matched.values()})}


# ── แปลงค่าที่ดูดมา → INSERT_SURV_REPORT_XML ────────────────────────────────────
#
# ลำดับ tag ยึดตาม backend/src/services/xmlExport.service.ts เป๊ะ ๆ เพื่อให้ diff กับ
# XML ที่ระบบเราสร้างอ่านง่าย  ค่าพิเศษ:
#   @date:<ช่องปฏิทิน>[:HOUR:MIN]  วันที่ (พ.ศ. บนหน้า) แปลงเป็น ค.ศ.
#   @date_be:<ช่องปฏิทิน>          วันที่ที่ XML คง พ.ศ. ไว้ (POLICY_START/END เท่านั้น)
#   @radio:<คำนำหน้า>              radio ที่ติ๊ก
#   @check:<คำนำหน้า>              checkbox ที่ติ๊ก (หลายอันคั่นด้วย ,)
#   ""                             ไม่มีบนหน้านี้ (server เติมเอง / อยู่หน้าอื่น)
REPORT_MAP = [
    ("SURV_JOBNO", "txtSurv_JobNo"), ("REF_CLAIM_NO", "txtRef_Claim_No"),
    ("INSURERBRID", "ddlInsurer_Name"), ("SURVEYID", ""), ("SURVEYBRID", ""),
    ("ACC_CLAIMREF_NO", "txtAcc_ClaimRef_No"), ("ACC_POLICY_NO", "txtAcc_Policy_No"),
    ("ASSURED_NAME", "txtAssured_Name"), ("POLICY_TYPE", "txtPolicy_Type"),
    ("POLICY_START", "@date_be:wuCale_Policy_Start_txtCalendar"),
    ("POLICY_END", "@date_be:wuCale_Policy_End_txtCalendar"),
    ("ACC_DATE", "@date:wuCale_Acc_Date_txtCalendar:txtAcc_Date_Hour:txtAcc_Date_Minute"),
    ("ACC_PLACE", "txtAcc_Place"), ("ACC_DISTRICTID", "ddlAcc_DistrictID"),
    ("ACC_PROVINCEID", "ddlAcc_ProvinceID"), ("ACC_DETAIL", "txtAcc_Detail"),
    ("ACC_CAUSE", "@radio:rdoAcc_Cause"), ("ACC_CALL", "txtAcc_Call"),
    ("ACC_SURV", "txtAcc_Surv"), ("ACC_TEL", "txtAcc_Tel"),
    ("ACC_CALL_DATE",
     "@date:wuCale_Acc_Call_Date_txtCalendar:txtAcc_Call_Date_Hour:txtAcc_Call_Date_Minute"),
    ("ACC_REACH", "@date:wuCale_Acc_Reach_txtCalendar:txtAcc_Reach_Hour:txtAcc_Reach_Minute"),
    ("ACC_FINISH", "@date:wuCale_Acc_Finish_txtCalendar:txtAcc_Finish_Hour:txtAcc_Finish_Minute"),
    ("OPO_RESULT", "@check:chkOpo_Result"), ("OPO_PAY", "txtOpo_Pay"),
    ("OPO_RECOVERY_AMOUNT", "txtOpo_Recovery_Amount"), ("OPO_AMOUNT_TYPE", ""),
    ("POLICE_NAME", "txtPolice_Name"), ("POLICE_STATION", "txtPolice_Station"),
    ("POLICE_COMMENT", "txtPolice_Comment"),
    ("POLICE_DATE", "@date:wuCale_Police_Date_txtCalendar"),
    ("BOOK_NUMBER", "txtBook_Number"), ("PRB_NUMBER", "txtPrb_Number"),
    ("SURV_COMMENT", "txtSurv_Comment"), ("ACC_CAUSE_NO", "txtAcc_Cause_No"),
    ("ALC_CHK", "@radio:rdoAlc_Chk"), ("ALC_RESULT", "txtAlc_Result"),
    ("FLU_TYPE", "ddlFlu_Type"), ("FLU_NO", "ddlFlu_No"),
    ("FLU_DETAIL", "txtFlu_Detail"), ("FLU_DATE", "@date:wuCale_Flu_Date_txtCalendar"),
    ("HEV_CAR", "@radio:rdoHev_Car_"), ("ACC_CRASH_REAR", ""),
    ("HAS_PRB", "@check:chkHas_Prb"), ("RISK_CODE", "txtRisk_Code"),
    ("LOST_CAR", "@check:chkLost_Car"),
    ("INS_CALLING_SURV_DATE", "@date:wuCale_Ins_Calling_Surv_Date_txtCalendar"
     ":txtIns_Calling_Surv_Date_Hour:txtIns_Calling_Surv_Date_Minute"),
    ("SURV_CLAIM_TYPE", "@radio:rdoSurv_Claim_Type_"), ("DRIVER_BY_POLICY", ""),
    ("DEDUCTIBLE", "txtDeductible"), ("CAUSE_CODE", "ddlClm_Cause"),
    ("LOSS_ID", "ddlLoss_ID"),
]

# รถ 1 คัน — รถประกันใช้ id เปล่า ๆ บนหน้าหลัก คู่กรณีใช้ id เดียวกันแต่มี prefix แถว
# ⚠️ ชื่อช่องบางตัวของคู่กรณีไม่ตรงกับของรถประกัน (EMCS สะกด txtDri_Adrress ผิดเฉพาะฝั่ง
#    คู่กรณี · ยี่ห้อเป็น ddlCmfg ไม่ใช่ ddlCMFG) → แยก override ไว้ที่ CAR_OPO_ALIAS
CAR_MAP = [
    ("TYPE", "@type"), ("OPO_NAME", "txtOpo_Name"), ("OPO_ADDRESS", "txtOpo_Address"),
    ("OPO_TYPE", "@opotype"), ("CAR_REGNO", "txtCar_RegNo"),
    ("CAR_PROVINCE", "ddlCar_Province"), ("CHASSISNO", "txtChassisNo"),
    ("ENGINENO", "txtEngineNo"), ("KM_NO", "txtKm_No"), ("CMFG", "@cmfg"),
    ("CMODEL", "txtCModel"), ("CAR_REGNO_YEAR", "ddlCar_RegNo_Year"),
    ("CCL_ID", "ddlCar_Color"), ("DRI_TITLE_ID", "ddlDri_Title_ID"),
    ("DRI_NAME", "@fullname:txtDri_Name01:txtDri_LastName01"), ("DRI_AGE", "txtDri_Age"),
    ("DRI_RELATION", "ddlDri_Relation_ID"), ("DRI_ADDRESS", "txtDri_Address"),
    ("DRI_DISTRICTID", "ddlDri_DistrictID"), ("DRI_PROVINCEID", "ddlDri_ProvinceID"),
    ("DRI_TELNO", "txtDri_TelNo"), ("DRI_CARDID", "txtDri_CardID"),
    ("DRI_DRVID", "txtDri_DrvID"), ("DRI_DRVTYPE", "ddlEmcs_License_Type"),
    ("DRI_DRVPLACE", "txtDri_DrvPlace"),
    ("DRI_DRVDATE_START", "@date:wuCale_Dri_DrvDate_Start_txtCalendar"),
    ("DRI_DRVDATE_END", "@date:wuCale_Dri_DrvDate_End_txtCalendar"), ("DRI_ORDER", ""),
    ("DRI_BIRTHDAY", "@date:wuCale_Dri_BirthDay_txtCalendar"),
    ("DRI_GENDER", "@radio:rdoGender_"), ("HAVE_INSURANCE", "ddlHave_Insurance"),
    ("POLICYNO", "txtPolicyNo"), ("CLAIMNO", "txtClaimNo"),
    ("POLICY_TYPE", "txtPolicy_Type"), ("CTYPECODE", "ddlCType"),
    ("MODELNO", "txtModelNo"), ("COST_DAMAGE", "txtCost_Damage"),
    ("REPAIRER_NAME", ""), ("REPAIRER_TYPE", ""), ("DAMAGE_LIST", ""),
    ("HAS_KFK", "@check:chkHas_KFK"),
]
CAR_OPO_ALIAS = {"txtDri_Address": "txtDri_Adrress", "ddlCMFG": "ddlCmfg"}

INJ_MAP = [
    ("INJ_SEQ", "@seq"), ("NAME", "txtInj_Name"), ("AGE", "txtInj_Age"),
    ("CITIZEN_ID", "txtCitizen_ID"), ("DRI_RELATION_ID", "ddlDri_Relation_ID"),
    ("JOB", "txtInj_Job"), ("CAR_REGNO", "txtCar_RegNo"), ("ADDRESS", "txtInj_Address"),
    ("TEL_NO", "txtInj_Tel_No"), ("WORK_PLACE", "txtInj_Work_Place"),
    ("POSITION", "txtInj_Position"), ("INCOME", "txtInj_Income"),
    ("HOS_NAME", "txtInj_Hos_Name"), ("FROM_DATE", "@date:wuCale_From_Date_txtCalendar"),
    ("TO_DATE", "@date:wuCale_To_Date_txtCalendar"), ("COST", "txtInj_Cost"),
    ("INJURE", "txtInj_Injure"), ("GENDER", "@radio:rdoGender_"),
    ("PERSON_TYPE", "ddlPerson_Type"), ("WOUNDED_TYPE", "ddlWounded_Type"),
]

ASSET_MAP = [
    ("ASSET_SEQ", "@seq"), ("ASSET_DESC", "txtAsset_Desc"),
    ("ASSET_DAMAGE_CAUSE", "txtAsset_Damage_Cause"), ("ASSET_DAMAGE", "txtAsset_Damage"),
    ("COST_DAMAGE", "txtCost_Damage"), ("OWNER", "txtOwner"), ("ADDRESS", "txtAddress"),
    ("TEL_NO", "txtTel_No"),
]


def _date(vals, spec_: str, to_ce: bool) -> str:
    """'02/06/2569' + ชั่วโมง/นาที → '2026-06-02 13:27:00'"""
    parts = spec_.split(":")
    raw = (vals.get(parts[0], {}) or {}).get("value", "")
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", raw or "")
    if not m:
        return ""
    d, mo, y = m.group(1), m.group(2), int(m.group(3))
    if to_ce and y > 2200:                      # หน้าเว็บเป็น พ.ศ. เสมอ
        y -= 543
    hh = (vals.get(parts[1], {}) or {}).get("value", "00") if len(parts) > 2 else "00"
    mi = (vals.get(parts[2], {}) or {}).get("value", "00") if len(parts) > 2 else "00"
    return f"{y}-{mo}-{d} {str(hh).zfill(2)}:{str(mi).zfill(2)}:00"


def resolve(vals: dict, source: str, prefix: str = "", **kw) -> str:
    """แปลงคำสั่งใน map เป็นค่าจริงจากหน้าเว็บ"""
    if not source:
        return ""
    if source == "@type":
        return str(kw.get("type", 0))
    if source == "@opotype":
        return "รถประกัน" if kw.get("insured") else "รถคู่กรณี"
    if source == "@seq":
        return str(kw.get("seq", 1))
    if source == "@cmfg":
        # รหัสยี่ห้อใน XML = อักษรประเภทรถ + ชื่อยี่ห้อ (T + ISUZU = TISUZU)
        # ไม่ใช่ค่า option ของ ddlCMFG ซึ่งเป็นเลขรันของ EMCS ('134T')
        ct = (vals.get(prefix + "ddlCType") or {}).get("value", "")
        brand = (vals.get(prefix + "ddlCMFG") or vals.get(prefix + "ddlCmfg") or {}).get("label", "")
        return f"{ct}{brand}" if brand else ""
    if source.startswith("@date"):
        body = source.split(":", 1)[1]
        body = prefix + body if prefix else body
        if prefix:                              # ใส่ prefix ให้ช่องชั่วโมง/นาทีด้วย
            body = ":".join(prefix + p if i else p for i, p in enumerate(body.split(":")))
        return _date(vals, body, to_ce=not source.startswith("@date_be"))
    if source.startswith("@fullname:"):
        a, b = source.split(":")[1:3]
        both = f"{(vals.get(prefix + a, {}) or {}).get('value', '')} " \
               f"{(vals.get(prefix + b, {}) or {}).get('value', '')}".strip()
        return both or (vals.get(prefix + "txtDri_Name", {}) or {}).get("value", "")
    if source.startswith("@radio:") or source.startswith("@check:"):
        want = prefix + source.split(":", 1)[1]
        hits = [i["value"] for k, i in vals.items() if k.startswith(want)]
        return ",".join(h for h in hits if h)
    return _num(vals.get(prefix + source, {}).get("value", "") if vals.get(prefix + source) else "")


def _num(v: str) -> str:
    """ช่องเงินบนหน้าโชว์ '6,000.00' แต่ XML ใช้ '6000' — ตัดคอมมาและศูนย์ท้ายทิ้ง
    ให้เหมือน money() ของ xmlExport ไม่งั้น diff จะเต็มไปด้วยความต่างของรูปแบบ"""
    if not re.fullmatch(r"-?[\d,]+(\.\d+)?", v or ""):
        return v
    n = v.replace(",", "")
    return n[:-3] if n.endswith(".00") else n


def build_xml(pages: list) -> str:
    """ประกอบ INSERT_SURV_REPORT_XML จากค่าที่ดูดมาทุกหน้า"""
    vals = {}
    for p in pages:
        vals.update(p["values"])

    def block(name, mapping, prefix="", **kw):
        body = "".join(f"<{t}>{_esc(resolve(vals, src, prefix, **kw)) or ' '}</{t}>"
                       for t, src in mapping)
        return f"<{name}>{body}</{name}>"

    out = [block("TXN_SURV_REPORT", REPORT_MAP)]
    out.append(block("TXN_SURV_CAR", CAR_MAP, insured=True, type=0))
    for n, pre in enumerate(_rows(vals, "dtlOpo_ctl", "_wuOpo_", ("txtOpo_Name", "txtCar_RegNo"))):
        alias = [(t, CAR_OPO_ALIAS.get(s, s)) for t, s in CAR_MAP]
        out.append(block("TXN_SURV_CAR", alias, pre, insured=False, type=20 + n))
    for n, pre in enumerate(_rows(vals, "dtlAsset_ctl", "_wuAsset_", ("txtAsset_Desc", "txtOwner"))):
        out.append(block("TXN_SURV_ASSET", ASSET_MAP, pre, seq=n + 1))
    for n, pre in enumerate(_rows(vals, "dtlInj_ctl", "_wuInj_", ("txtInj_Name", "txtCitizen_ID"))):
        out.append(block("TXN_SURV_INJ", INJ_MAP, pre, seq=n + 1))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<INSERT_SURV_REPORT_XML>'
            + "".join(out) + "</INSERT_SURV_REPORT_XML>")


def _rows(vals: dict, head: str, tail: str, keys: tuple) -> list:
    """prefix ของแถวที่ "มีข้อมูลจริง" เท่านั้น

    EMCS render แถวเปล่าไว้ 20-32 แถวเสมอ และแถวเปล่าก็ยังมี hidden ที่มีค่า default
    (dtlOpo_ctlNN_wuOpo_hdfCType='T') → ถ้านับว่า "มีช่องไหนไม่ว่างก็ถือว่ามีข้อมูล"
    จะได้คู่กรณีผีมาครบ 20 คัน จึงดูเฉพาะช่องหลักที่คนต้องกรอกเท่านั้น
    """
    pres = sorted({k[:k.index(tail) + len(tail)] for k in vals
                   if k.startswith(head) and tail in k})
    return [p for p in pres
            if any((vals.get(p + k) or {}).get("value", "").strip() for k in keys)]


def _blocks(xml: str) -> dict:
    """{'TXN_SURV_CAR#0': {tag: value}} — บล็อกรถ/ผู้บาดเจ็บชื่อซ้ำกันได้ ถ้ายุบเป็น dict
    เดียวจะทับกันจนเทียบผิด (รถประกันโดนคู่กรณีคันสุดท้ายทับ) จึงต่อลำดับท้ายคีย์"""
    out, seen = {}, {}
    for m in re.finditer(r"<(TXN_[A-Z_0-9]+)>(.*?)</\1>", xml, re.S):
        name = m.group(1)
        n = seen[name] = seen.get(name, -1) + 1
        out[f"{name}#{n}"] = {t: unescape(v).strip() for t, v
                              in re.findall(r"<([A-Z_0-9]+)>([^<]*)</\1>", m.group(2))}
    return out


def _esc(v) -> str:
    return (str(v or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\n", "&#13;\n"))


def main():
    ap = argparse.ArgumentParser(description="ดูดข้อมูลที่กรอกไว้จริงจากหน้า EMCS")
    ap.add_argument("files", nargs="+")
    ap.add_argument("--learn", metavar="XML", help="ไฟล์ XML ของเคสเดียวกัน เพื่อจับคู่ id ↔ tag")
    ap.add_argument("--xml", metavar="OUT", help="ประกอบเป็น INSERT_SURV_REPORT_XML")
    ap.add_argument("--diff", metavar="XML", help="เทียบ XML ที่สร้างกับไฟล์อ้างอิง tag ต่อ tag")
    ap.add_argument("--out", default="runs/emcs_dump.json")
    a = ap.parse_args()

    paths = []
    for pat in a.files:
        if any(c in pat for c in "*?"):
            p = Path(pat)
            paths += sorted((p.parent if p.parent.is_absolute() else Path()).glob(p.name))
        else:
            paths.append(Path(pat))
    paths = [p for p in paths if p.is_file()]
    if not paths:
        sys.exit("ไม่พบไฟล์")

    merged, dump = {}, []
    for p in paths:
        v = values(p.read_text(encoding="utf-8", errors="replace"))
        dump.append({"file": p.name, "values": v})
        merged.update(v)
        kinds = {}
        for i in v.values():
            kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
        print(f"\n=== {p.name} ===")
        print("  " + " · ".join(f"{k} {n}" for k, n in sorted(kinds.items()))
              + f"  (รวม {len(v)} ช่องที่มีค่า)")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n✓ เขียน {out} ({out.stat().st_size:,} bytes)")

    if a.xml or a.diff:
        xml = build_xml(dump)
        if a.xml:
            Path(a.xml).write_text(xml, encoding="utf-8")
            print(f"✓ เขียน {a.xml} ({len(xml):,} bytes)")
        if a.diff:
            ref = Path(a.diff).read_text(encoding="utf-8", errors="replace")
            mine, gold = _blocks(xml), _blocks(ref)
            ok = bad = 0
            print(f"\n=== เทียบกับ {Path(a.diff).name} ===")
            for key in sorted(set(mine) | set(gold)):
                m, g = mine.get(key, {}), gold.get(key, {})
                if not m or not g:
                    side = "เรา" if m else "อ้างอิง"
                    print(f"  – {key}: มีแค่ฝั่ง{side} ({len(m or g)} tag)")
                    continue
                rows = [(t, m.get(t, ""), g[t]) for t in g if m.get(t, "") != g[t]]
                ok += len(g) - len(rows)
                bad += len(rows)
                if rows:
                    print(f"  {key}: ต่าง {len(rows)}/{len(g)}")
                    for t, x, y in rows:
                        print(f"     ✗ {t:<20} เรา={x!r:<28} อ้างอิง={y!r}")
            print(f"\n  รวม: ตรง {ok} · ต่าง {bad}")

    if a.learn:
        res = learn(merged, Path(a.learn).read_text(encoding="utf-8", errors="replace"))
        print(f"\n=== จับคู่ได้ {len(res['matched'])} tag ===")
        for t, d in res["matched"].items():
            print(f"  {t:<24} ← {d['id']:<38} (คะแนน {d['score']})")
        print(f"\n=== tag ที่ยังจับคู่ไม่ได้ ต้องแมปมือ ({len(res['unmatched_tags'])}) ===")
        print("  " + ", ".join(res["unmatched_tags"]))
        Path("runs/emcs_map.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
