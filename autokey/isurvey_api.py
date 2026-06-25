"""ฝั่งอ่าน ISURVEY ผ่าน HTTP API (ทางเลือกแทนการ scrape DOM ด้วย Selenium)

ISURVEY backend เป็น PHP JSON API — เร็วกว่าและไม่มี DOM race
flow: login → listcases(เลขเคลม)→caseID → getcaseinfo ทุก tab + ความเสียหาย
ผลลัพธ์เป็น ClaimData รูปแบบเดียวกับฝั่ง scrape (เทียบกันได้ด้วย main.py --compare)

รายละเอียด endpoint + sample response: tools/isurvey_api_findings.md
ดึงข้อมูลให้ "ตรงรูปแบบกับฝั่ง scrape": วันที่เป็น dd/mm/yyyy (ค.ศ.),
claim_type เป็น code ("2"=เคลมแห้ง), จังหวัด/อำเภอ/ผลคดี/ประเภทรถ เป็นชื่อ
(แปลงจาก code ผ่านตาราง master*), ยอดบิลใช้ INS_* + total เป็นผลรวมที่หน้าเว็บคำนวณ
"""
import dataclasses
import json
import re
from urllib.parse import urlparse

import requests

from .browser import log
from .claim_data import ClaimData


def _multi_survey_msg(claim, cases) -> str:
    """ข้อความเตือนเมื่อเคลมมีหลายเซอร์เวย์ + ไม่ได้ระบุเลขเซอร์เวย์ — list แถวให้ผู้ใช้เลือก
    (close_datetime มีค่า = ปิดงาน/จบงานแล้ว = แถวที่มักต้องคีย์)"""
    rows = "\n".join(
        f"   - {c.get('survey_no')}  ({c.get('surveyor_name', '')})"
        + (f"  ✓ ปิดงาน {c.get('close_datetime')}" if c.get('close_datetime')
           else "  (ยังไม่ปิดงาน)")
        for c in cases)
    n = len({str(c.get('survey_no')) for c in cases})
    return (f"เคลม {claim} มี {n} เซอร์เวย์ในระบบ — ต้องระบุ 'เลขเซอร์เวย์' "
            "(ช่องเลขเซอร์เวย์ / --invoice) เพื่อเลือกแถวที่ต้องการ:\n" + rows
            + "\n→ ใส่เลขเซอร์เวย์ของแถวที่ต้องการ (มักเป็นแถวที่ปิดงานแล้ว) แล้วรันใหม่")


def _ddmmyyyy(iso) -> str:
    """'2026-06-09' → '09/06/2026' (คง ค.ศ. เหมือนฝั่ง scrape); ว่าง/None → ''"""
    if not iso:
        return ""
    s = str(iso).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else s


def _money(v) -> float:
    try:
        return float(str(v if v is not None else 0).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


class ISurveyAPI:
    """client บางๆ ของ ISURVEY PHP API — ใช้ requests.Session เก็บ PHPSESSID"""

    def __init__(self, cfg):
        self.cfg = cfg
        u = urlparse(cfg.isurvey_url)
        self.base = f"{u.scheme}://{u.netloc}/web/php"
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0",
            "X-Requested-With": "XMLHttpRequest",
        })
        self._masters = {}
        self.last_case_id = ""   # caseID ของเคลมที่อ่านล่าสุด (ใช้โหลดรูปต่อ)
        p = urlparse(cfg.isurvey_url)
        self._host = f"{p.scheme}://{p.netloc}"   # โดเมนสำหรับโหลดไฟล์รูป

    # ------------------------------------------------------------------ HTTP
    def _get(self, path, **params):
        r = self.s.get(f"{self.base}/{path}", params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def login(self):
        log("ISURVEY-API: login")
        # เปิดหน้าแรกก่อนเพื่อให้ได้ PHPSESSID เริ่มต้น แล้วค่อย POST login
        try:
            self.s.get(self.cfg.isurvey_url, timeout=30)
        except Exception:
            pass
        self.s.post(f"{self.base}/login.php",
                    data={"username": self.cfg.isurvey_username,
                          "password": self.cfg.isurvey_password}, timeout=30)
        try:
            who = self._get("getUserData.php", _dc=0)
            if who.get("success"):
                log(f"ISURVEY-API: login แล้ว ({who.get('message', '')})")
                return
        except Exception:
            pass
        raise RuntimeError("ISURVEY-API: login ไม่สำเร็จ — ตรวจ ISURVEY_USERNAME/PASSWORD ใน .env")

    # ---------------------------------------------------------------- master
    def master(self, name, key, val) -> dict:
        """โหลดตาราง lookup (cache ทั้ง process) คืน dict {str(key): val}"""
        if name not in self._masters:
            try:
                rows = self._get(f"list/{name}.php", page=1, start=0, limit=100000)
                self._masters[name] = {str(r.get(key)): r.get(val)
                                       for r in rows.get("data", [])}
            except Exception as e:
                log(f"   ⚠️ โหลดตาราง {name} ไม่ได้: {type(e).__name__}")
                self._masters[name] = {}
        return self._masters[name]

    def _prov(self, code) -> str:
        if not code:
            return ""
        return self.master("masterProvince", "provinceID", "provincename").get(str(code), "")

    def _amphur(self, code) -> str:
        if not code:
            return ""
        return self.master("masterAmphur", "amphurID", "amphurname").get(str(code), "")

    # ------------------------------------------------------------- เปิดเคลม
    def find_case(self, claim, invoice="") -> dict:
        d = self._get("supervisor/listcases.php", claim_no=claim, claim_status="",
                      claim_date="", page=1, start=0, limit=25)
        cases = [c for c in d.get("cases", [])
                 if str(c.get("claim_no")) == str(claim)]
        if invoice:                       # ระบุเลขเซอร์เวย์ → เลือกแถวที่ตรงเป๊ะ
            for c in cases:
                if str(c.get("survey_no")) == str(invoice):
                    return c
            raise RuntimeError(f"ISURVEY-API: ไม่พบเคลม {claim} / {invoice}")
        if not cases:
            raise RuntimeError(f"ISURVEY-API: ไม่พบเคลม {claim}")
        # หลายเซอร์เวย์ + ไม่ระบุ invoice → หยุด ให้ผู้ใช้เลือกเอง (กันหยิบงานยกเลิก/
        # งานผิดแถว เพราะเดิมหยิบแถวแรกโดยไม่ดูสถานะ)
        if len({str(c.get("survey_no")) for c in cases}) > 1:
            raise RuntimeError(_multi_survey_msg(claim, cases))
        return cases[0]

    def get_tab(self, case_id, tab) -> dict:
        return self._get("supervisor/getcaseinfo.php", caseID=case_id,
                         tab=f"tab-{tab}_clone").get("message", {}) or {}

    def get_parts(self, case_id) -> list:
        return self._get("supervisor/list_parts_ins_car.php", caseID=case_id,
                         page=1, start=0, limit=1000).get("parts", []) or []

    # ------------------------------------------------------------------- รูป
    def get_images_list(self, case_id, t) -> list:
        """รายการรูปของหมวด t (1=OTHERS, 2=REPORTS, 3=INS, อื่นๆ=ว่าง/คู่กรณี)"""
        try:
            return self._get("supervisor/get-images.php", caseID=case_id, t=t,
                             page=1, start=0, limit=2000).get("images", []) or []
        except Exception:
            return []

    @staticmethod
    def _img_category(url: str) -> str:
        """หมวดของรูปจาก path เช่น .../PICTURES/INS/... → 'INS'"""
        if "PICTURES/" in url:
            return url.split("PICTURES/")[1].split("/")[0].upper()
        return "OTHERS"

    def download_images(self, case_id, dest_dir, ts=(1, 2, 3, 4, 5, 6)) -> dict:
        """โหลดรูปทุกหมวดของเคลมลง dest_dir (จัดวางแบบเดียวกับวิธี zip:
        INS/REPORTS/OTHERS แบนในโฟลเดอร์, หมวด TP_* ลง tp_<xxx>/ —
        tp_veh/tp_person/tp_prop)
        คืน dict นับจำนวนต่อหมวด เช่น {'INS': 22, 'REPORTS': 4, 'OTHERS': 1}"""
        from pathlib import Path
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        counts, seen, failed = {}, set(), 0
        cat_map = {}   # {ชื่อไฟล์: หมวด} เฉพาะรูปที่ลงโฟลเดอร์หลัก (ไม่รวม tp_*)
        for t in ts:
            for im in self.get_images_list(case_id, t):
                name, url = im.get("name"), im.get("url")
                if not name or not url or name in seen:
                    continue
                seen.add(name)
                cat = self._img_category(url)
                target = (dest_dir / cat.lower() / name) if cat.startswith("TP_") \
                    else (dest_dir / name)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    r = self.s.get(f"{self._host}/{url.lstrip('/')}", timeout=60)
                    if r.status_code == 200 and r.content:
                        target.write_bytes(r.content)
                        counts[cat] = counts.get(cat, 0) + 1
                        if not cat.startswith("TP_"):
                            cat_map[name] = cat
                    else:
                        failed += 1
                        log(f"   ⚠️ รูป {name}: HTTP {r.status_code}")
                except Exception as e:
                    failed += 1
                    log(f"   ⚠️ โหลดรูป {name} ไม่ได้: {type(e).__name__}")
        # บันทึกหมวดของแต่ละรูป (ให้แกลเลอรีหน้าเว็บจัดกลุ่มได้) + เคลียร์ map ชื่อเก่า
        try:
            (dest_dir / "_categories.json").write_text(
                json.dumps(cat_map, ensure_ascii=False), encoding="utf-8")
            stale = dest_dir / "_rename_map.json"
            if stale.exists():
                stale.unlink()
        except Exception:
            pass
        log(f"ISURVEY-API: โหลดรูป {counts} (รวม {sum(counts.values())}"
            + (f", พลาด {failed}" if failed else "") + ")")
        return counts

    # ------------------------------------------------------- ประกอบ ClaimData
    def read_claim(self, claim, invoice="", expect_claim="") -> ClaimData:
        case = self.find_case(claim, invoice)
        cid = case["caseID"]
        self.last_case_id = cid
        log(f"ISURVEY-API: เปิดเคลม {claim} (caseID {cid})")

        t1 = self.get_tab(cid, 1)
        t2 = self.get_tab(cid, 2)
        t3 = self.get_tab(cid, 3)
        t7 = self.get_tab(cid, 7)
        t8 = self.get_tab(cid, 8)
        parts = self.get_parts(cid)

        claim_d = t1.get("Claim", {}) or {}
        bill = t1.get("bill", {}) or {}
        disp = t1.get("Dispatch", {}) or {}
        acc2 = t2.get("Accident", {}) or {}
        notify = t8.get("Notify", {}) or {}
        acc8 = t8.get("Accident", {}) or {}
        pol = t7.get("Policy", {}) or {}
        drv = t3.get("Driver", {}) or {}

        d = ClaimData()

        # ---- Tab 1: Summary ----
        d.claim_value = claim_d.get("claim_no") or claim
        d.invoice_value = claim_d.get("survey_no") or case.get("survey_no", "")
        d.notify_value = claim_d.get("notify_no", "")
        d.policy_value = (t1.get("Policy", {}) or {}).get("policy_no", "")
        d.claim_type = str(claim_d.get("claim_MtypeID", "") or "")
        d.pay_type = case.get("claim_type", "")           # listcases.claim_type = ชื่อประเภทการจ่าย
        d.surveyor_name = claim_d.get("surveyor_name", "")
        d.branch = claim_d.get("sys_branchName", "")
        d.oss_company = claim_d.get("OSS_company") or ""
        d.oss_surveyor = claim_d.get("OSS_SurveyorName") or ""
        d.oss_phone = claim_d.get("OSS_phone") or ""
        # งาน outsource (useOSS=Y): ช่องพนักงานบริษัทว่าง → ใช้ชื่อพนักงาน outsource
        # แทน (ตรงกับ fallback ฝั่ง scrape ใน isurvey.py — กัน "พนักงานสำรวจ" ว่าง)
        if not (d.surveyor_name or "").strip() and d.oss_surveyor.strip():
            d.surveyor_name = d.oss_surveyor
            log(f"   ใช้ชื่อพนักงาน outsource เป็นพนักงานสำรวจ: {d.oss_surveyor}"
                + (f" ({d.oss_company})" if d.oss_company.strip() else ""))
        d.arrive_date = _ddmmyyyy(disp.get("arrive_date"))
        d.arrive_time = disp.get("arrive_time", "")
        d.finish_date = _ddmmyyyy(disp.get("finish_date"))
        d.finish_time = disp.get("finish_time", "")
        d.accident_summary = t1.get("accident_summary", "")

        # ---- Tab 2/8: Accident (ใช้ Notify — จังหวัด/อำเภอเป็นชื่อแล้ว) ----
        d.acc_date = _ddmmyyyy(notify.get("acc_date"))
        d.acc_time = notify.get("acc_time", "")
        d.acc_place = notify.get("acc_place", "")
        d.acc_province = notify.get("acc_provinceID", "")
        d.acc_amphur = notify.get("acc_amphurID", "")
        d.acc_type_desc = notify.get("acc_type_desc", "")
        d.acc_detail = acc2.get("surveyor_comment", "")   # รายงานเซอร์เวย์ (เหมือน tab2_surveyor_comment)
        d.acc_result = self.master("masterClaimVerdict", "cvdID", "claim_verdict") \
            .get(str(claim_d.get("acc_verdictID", "") or ""), "")

        # ---- Tab 3: Insurance/รถ/ผู้ขับขี่ ----
        d.insure_plate = t3.get("plate_no", "")
        d.prb_number = t3.get("oth_comp_no", "")
        d.prb_car_type = self.master("masterVehType", "vehTID", "vt_description") \
            .get(str(t3.get("vehTID") or ""), "")
        d.plate_province = self._prov(t3.get("plate_provinceID"))
        d.car_brand = t3.get("car_brand", "")
        d.car_color = t3.get("car_color", "")
        parts_name = (drv.get("drv_name") or "").split()
        d.driver_name = parts_name[0] if parts_name else ""
        d.driver_surname = parts_name[1] if len(parts_name) > 1 else ""
        d.driver_gender = drv.get("drv_gender", "")
        d.driver_relation = drv.get("relation", "")
        d.driver_age = str(drv.get("age", "") or "")
        d.driver_address = drv.get("address", "")
        d.driver_province = self._prov(drv.get("drv_provinceID"))
        d.driver_amphur = self._amphur(drv.get("drv_amphurID"))
        d.driver_phone = drv.get("drv_phone", "")
        d.driver_idcard = drv.get("IDcard_no", "")
        d.driver_license_no = drv.get("lic_no", "")
        d.driver_license_place = self._prov(drv.get("lic_issue_provinceID"))
        d.driver_license_type = self.master("masterDrvLicense", "dvlTID", "dvl_type") \
            .get(str(drv.get("lic_typeID") or ""), "")
        d.damage_estimate = str(t3.get("D_TOTAL_COST", "") or "")
        d.driver_birthdate = _ddmmyyyy(drv.get("birthdate"))
        d.license_issue_date = _ddmmyyyy(drv.get("lic_issueDate"))
        d.license_expiry_date = _ddmmyyyy(drv.get("lic_expireDate"))

        # ---- รายการความเสียหาย ----
        for p in parts:
            d.damage.append(p.get("partname", "") or "")
            d.type_damage.append(p.get("damage_type_detail", "") or "")
            d.rank_damage.append(p.get("damaged_level", "") or "")
            d.cost_damage.append(str(p.get("LABOUR_COST", "") or ""))

        # ---- Tab 7: Policy ----
        d.effective_date = _ddmmyyyy(pol.get("effective_date"))
        d.expiry_date = _ddmmyyyy(pol.get("expiry_date"))
        d.insure_name = pol.get("assured_name", "")
        d.insure_type = pol.get("policy_TypeID", "")      # tab-7 ให้ชื่อ เช่น 'ประเภท 1'
        d.insure_model = pol.get("car_model", "")
        d.insure_chassis = pol.get("chassis_no", "")
        d.insure_engine = pol.get("engine_no", "")

        # ---- Tab 8: Notify ----
        d.noti_date = _ddmmyyyy(acc8.get("notified_date"))
        d.noti_time = acc8.get("notified_time", "")

        # ---- บิล (ยอดอนุมัติ INS_* ; total = ผลรวมที่หน้าเว็บคำนวณ, net = +VAT 7%) ----
        ins_keys = ["INS_INVEST", "INS_TRANS", "INS_DIST", "INS_PHOTO", "INS_TEL",
                    "INS_INSURE", "INS_CLAIM", "INS_DAILY", "INS_OTHER", "INS_TOWCAR"]
        total = sum(_money(bill.get(k)) for k in ins_keys)
        # ยอดฝั่งเซอร์เวย์ "เสนอ" (SUR_*) — EMCS ไม่ได้ใช้ แต่เก็บให้ตรงฝั่ง scrape
        sur_keys = ["SUR_INVEST", "SUR_TRANS", "SUR_DIST", "SUR_PHOTO", "SUR_TEL",
                    "SUR_INSURE", "SUR_CLAIM", "SUR_DAILY", "SUR_OTHER", "SUR_TOWCAR"]
        sur_total = sum(_money(bill.get(k)) for k in sur_keys)
        d.service_total = f"{sur_total:,.2f}"
        d.service_vat = "0.00"
        d.service_total_net = f"{sur_total:,.2f}"
        d.bill = {
            "source": "isurvey_screen",
            "invest": bill.get("INS_INVEST", ""), "trans": bill.get("INS_TRANS", ""),
            "dist": bill.get("INS_DIST", ""), "photo": bill.get("INS_PHOTO", ""),
            "photo_num": bill.get("PHOTO_NUM", ""), "tel": bill.get("INS_TEL", ""),
            "insure": bill.get("INS_INSURE", ""), "claim": bill.get("INS_CLAIM", ""),
            "daily": bill.get("INS_DAILY", ""), "daily_num": bill.get("DAILY_NUM", ""),
            "other": bill.get("INS_OTHER", ""), "cartow": bill.get("INS_TOWCAR", ""),
            "total": f"{total:,.2f}", "total_net": f"{total * 1.07:,.2f}",
        }

        # ทำให้ตรงรูปแบบฝั่ง scrape: API คืน None เมื่อว่าง (scrape ให้ '') และ
        # คืน \r\n ใน text หลายบรรทัด (DOM textarea ให้ \n) → coerce ทั้งสองอย่าง
        d.bill = {k: ("" if v is None else v) for k, v in d.bill.items()}
        for fld in dataclasses.fields(d):
            v = getattr(d, fld.name)
            if v is None:
                setattr(d, fld.name, "")
            elif isinstance(v, str) and "\r" in v:
                setattr(d, fld.name, v.replace("\r\n", "\n").replace("\r", "\n"))

        # ---- คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน ----
        # API ยังไม่อ่าน tab-4/5/6 — เคลมแห้ง (type 2) ว่างอยู่แล้ว แต่เคลมสดจะขาดส่วนนี้
        if d.claim_type.strip() != "2":
            log(f"   ⚠️ เคลมนี้เป็น {d.claim_type_name()} — API ยังไม่อ่านคู่กรณี/"
                f"ผู้บาดเจ็บ/ทรัพย์สิน (tab-4/5/6); ใช้ --scrape ถ้าต้องการส่วนนี้ครบ")
        if expect_claim and d.claim_value.strip() != expect_claim.strip():
            raise RuntimeError(
                f"ISURVEY-API: ได้เคลม {d.claim_value} ไม่ตรงกับที่ขอ ({expect_claim})")
        log("ISURVEY-API: อ่านข้อมูลครบแล้ว")
        return d


def read_claim_api(cfg, claim, invoice="", expect_claim="") -> ClaimData:
    """ทางลัด: สร้าง client + login + อ่านเคลม → ClaimData"""
    api = ISurveyAPI(cfg)
    api.login()
    return api.read_claim(claim, invoice, expect_claim=expect_claim)
