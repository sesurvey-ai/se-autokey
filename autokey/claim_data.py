"""โครงสร้างข้อมูลเคลมที่อ่านจาก ISURVEY เพื่อส่งต่อให้ EMCS"""
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

# ป้ายชื่อภาษาไทยของแต่ละ field (ใช้ทำรายงานความครบของข้อมูล)
FIELD_LABELS = {
    "claim_value": "เลขเคลม", "invoice_value": "เลขเซอร์เวย์", "notify_value": "เลขรับแจ้ง",
    "policy_value": "เลขกรมธรรม์", "claim_type": "ประเภทเคลม", "surveyor_name": "พนักงานสำรวจ",
    "arrive_date": "วันถึงที่เกิดเหตุ", "arrive_time": "เวลาถึงที่เกิดเหตุ",
    "finish_date": "วันเสร็จงาน", "finish_time": "เวลาเสร็จงาน",
    "accident_summary": "บันทึกความเห็นหัวหน้า",
    "acc_date": "วันที่เกิดเหตุ", "acc_time": "เวลาเกิดเหตุ", "acc_place": "สถานที่เกิดเหตุ",
    "acc_province": "จังหวัดเกิดเหตุ", "acc_amphur": "เขต/อำเภอเกิดเหตุ",
    "acc_type_desc": "สาเหตุการเกิดเหตุ", "acc_detail": "รายละเอียดการเกิดเหตุ",
    "acc_result": "ผลคดี",
    "insure_plate": "ทะเบียนรถ", "prb_number": "เลขที่ พรบ", "prb_car_type": "ประเภทรถ",
    "plate_province": "จังหวัดรถ", "car_brand": "ยี่ห้อรถ", "car_color": "สีรถ",
    "driver_name": "ชื่อผู้ขับขี่", "driver_surname": "นามสกุลผู้ขับขี่",
    "driver_relation": "ความสัมพันธ์", "driver_age": "อายุผู้ขับขี่",
    "driver_address": "ที่อยู่ผู้ขับขี่", "driver_province": "จังหวัดผู้ขับขี่",
    "driver_amphur": "อำเภอผู้ขับขี่", "driver_phone": "เบอร์โทรผู้ขับขี่",
    "driver_idcard": "บัตรประชาชนผู้ขับขี่", "driver_license_no": "ใบขับขี่เลขที่",
    "driver_license_place": "ใบขับขี่ออกที่", "driver_license_type": "ประเภทใบขับขี่",
    "damage_estimate": "ความเสียหายประมาณ", "driver_birthdate": "วันเกิดผู้ขับขี่",
    "license_issue_date": "วันออกใบขับขี่", "license_expiry_date": "วันหมดอายุใบขับขี่",
    "effective_date": "วันคุ้มครอง", "expiry_date": "วันสิ้นสุดคุ้มครอง",
    "insure_name": "ชื่อผู้เอาประกัน", "insure_type": "ประเภทประกัน",
    "insure_model": "รุ่นรถ", "insure_chassis": "เลขตัวถัง", "insure_engine": "เลขเครื่อง",
    "noti_date": "วันที่รับแจ้ง", "noti_time": "เวลารับแจ้ง",
}

# ประเภทเคลมของ ISURVEY → ชื่อ (ตรงกับ radio ฟอร์ม EMCS index = type-1)
CLAIM_TYPE_NAMES = {"1": "เคลมสด", "2": "เคลมแห้ง",
                    "3": "เคลมนัดหมาย", "4": "งานติดตาม"}

# field ที่ EMCS ต้องใช้จริง — ถ้าว่างถือว่าผิดปกติ ต้องให้คนตรวจ
CRITICAL_FIELDS = {
    "claim_value", "invoice_value", "policy_value", "claim_type", "surveyor_name",
    "arrive_date", "arrive_time", "finish_date", "finish_time",
    "acc_date", "acc_time", "acc_place", "acc_province", "acc_amphur", "acc_result",
    "insure_plate", "prb_number", "prb_car_type", "plate_province", "car_brand",
    "driver_name", "effective_date", "expiry_date", "insure_name",
    "noti_date", "noti_time",
}


@dataclass
class ClaimData:
    # ---- Tab 1: Summary ----
    claim_value: str = ""          # เลขเคลม
    invoice_value: str = ""        # เลขเซอร์เวย์
    notify_value: str = ""         # เลขรับแจ้ง
    policy_value: str = ""         # เลขกรมธรรม์
    claim_type: str = ""           # ประเภทเคลม (1-4)
    pay_type: str = ""             # ประเภทการจ่าย (เช่น ส่งพนักงาน)
    third_party_condition: str = ""  # เงื่อนไขฝ่ายถูก
    branch: str = ""               # ศูนย์
    service_total: str = ""        # ค่าบริการรวมเป็นเงิน
    service_vat: str = ""          # ภาษีมูลค่าเพิ่ม
    service_total_net: str = ""    # จำนวนเงินรวมสุทธิ
    surveyor_name: str = ""        # พนักงานสำรวจ
    oss_company: str = ""          # บริษัท outsource (ถ้าเป็นงาน OSS)
    oss_surveyor: str = ""         # ชื่อพนักงาน outsource
    oss_phone: str = ""            # เบอร์พนักงาน outsource
    arrive_date: str = ""          # วันถึงที่เกิดเหตุ
    arrive_time: str = ""          # เวลาถึงที่เกิดเหตุ
    finish_date: str = ""          # วันเสร็จงาน
    finish_time: str = ""          # เวลาเสร็จงาน
    accident_summary: str = ""     # บันทึกความเห็นหัวหน้า

    # ---- Tab 2: Accident info ----
    acc_date: str = ""             # วันที่เกิดเหตุ
    acc_time: str = ""             # เวลาเกิดเหตุ
    acc_place: str = ""            # สถานที่เกิดเหตุ
    acc_province: str = ""         # จังหวัดเกิดเหตุ
    acc_amphur: str = ""           # เขต/อำเภอเกิดเหตุ
    acc_type_desc: str = ""        # สาเหตุการเกิดเหตุ
    acc_detail: str = ""           # รายละเอียดการเกิดเหตุ
    acc_result: str = ""           # ผลคดี

    # ---- Tab 3: Insurance info ----
    insure_plate: str = ""         # ทะเบียนรถ
    prb_number: str = ""           # เลขที่ พรบ
    prb_car_type: str = ""         # ประเภทรถ
    plate_province: str = ""       # จังหวัดรถ
    car_brand: str = ""            # ยี่ห้อรถ
    car_color: str = ""            # สีรถ
    driver_name: str = ""          # ชื่อผู้ขับขี่
    driver_surname: str = ""       # นามสกุลผู้ขับขี่
    driver_gender: str = ""        # เพศผู้ขับขี่ (M/W จาก XML — EMCS บังคับ)
    driver_relation: str = ""      # ความสัมพันธ์
    driver_age: str = ""           # อายุผู้ขับขี่
    driver_address: str = ""       # ที่อยู่ผู้ขับขี่
    driver_province: str = ""      # จังหวัดผู้ขับขี่
    driver_amphur: str = ""        # อำเภอผู้ขับขี่
    driver_phone: str = ""         # เบอร์โทรผู้ขับขี่
    driver_idcard: str = ""        # บัตรประชาชนผู้ขับขี่
    driver_license_no: str = ""    # ใบขับขี่เลขที่
    driver_license_place: str = "" # ใบขับขี่ออกที่
    driver_license_type: str = ""  # ประเภทใบขับขี่
    damage_estimate: str = ""      # ความเสียหายประมาณ
    driver_birthdate: str = ""     # วันเกิดผู้ขับขี่
    license_issue_date: str = ""   # วันออกใบขับขี่
    license_expiry_date: str = ""  # วันหมดอายุใบขับขี่

    # ---- Tab 3: รายการความเสียหาย ----
    damage: list = field(default_factory=list)        # ชิ้นส่วน
    type_damage: list = field(default_factory=list)   # ประเภท (ครูด/บุบ)
    rank_damage: list = field(default_factory=list)   # ระดับ (A-D)
    cost_damage: list = field(default_factory=list)   # ราคาประเมินต่อชิ้น (บาท)

    # ---- Tab 4-6: คู่กรณี / ผู้บาดเจ็บ / ทรัพย์สิน ----
    # เก็บเป็น list ของ dict (เคลมแห้งจะเป็น list ว่าง)
    third_parties: list = field(default_factory=list)
    injuries: list = field(default_factory=list)
    assets: list = field(default_factory=list)

    # ---- Tab 7: Policy info ----
    effective_date: str = ""       # วันคุ้มครอง
    expiry_date: str = ""          # วันสิ้นสุดคุ้มครอง
    insure_name: str = ""          # ชื่อผู้เอาประกัน
    insure_type: str = ""          # ประเภทประกัน
    insure_model: str = ""         # รุ่นรถ
    insure_chassis: str = ""       # เลขตัวถัง
    insure_engine: str = ""        # เลขเครื่อง

    # ---- Tab 8: Notify info ----
    noti_date: str = ""            # วันที่รับแจ้ง
    noti_time: str = ""            # เวลารับแจ้ง

    # ---- อื่นๆ ----
    xml_file: str = ""             # path ไฟล์ SURV_REPORT XML ที่ดาวน์โหลดไว้
    # ค่าสำรวจฝั่ง "เสนอ" จาก XML (invest/trans/photo/tel/daily/other ฯลฯ)
    bill: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    def save(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "ClaimData":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def summary(self) -> str:
        """สรุปข้อมูลหลักให้คนตรวจก่อนเริ่มกรอก EMCS"""
        lines = [
            f"เลขเคลม        : {self.claim_value}",
            f"เลขเซอร์เวย์    : {self.invoice_value}",
            f"กรมธรรม์       : {self.policy_value}",
            f"ประเภทเคลม     : {self.claim_type}",
            f"ทะเบียนรถ      : {self.insure_plate} ({self.plate_province})",
            f"รถ             : {self.car_brand} {self.insure_model} สี{self.car_color}",
            f"ผู้ขับขี่       : {self.driver_name} {self.driver_surname} (อายุ {self.driver_age})",
            f"ผู้เอาประกัน    : {self.insure_name}",
            f"วันเกิดเหตุ     : {self.acc_date} {self.acc_time} @ {self.acc_place}",
            f"จังหวัด/อำเภอ  : {self.acc_province} / {self.acc_amphur}",
            f"ผลคดี          : {self.acc_result}",
            f"ความเสียหาย    : {len(self.damage)} รายการ ≈ {self.damage_estimate} บาท",
        ]
        costs = self.cost_damage + [""] * (len(self.damage) - len(self.cost_damage))
        for i, (d, t, r, c) in enumerate(
            zip(self.damage, self.type_damage, self.rank_damage, costs), 1
        ):
            cost_txt = f" | {c} บาท" if str(c).strip() else ""
            lines.append(f"   {i}. {d} | {t} | ระดับ {r}{cost_txt}")

        if self.third_parties:
            lines.append(f"คู่กรณี        : {len(self.third_parties)} ราย")
            for i, tp in enumerate(self.third_parties, 1):
                insurer = tp.get("insurer", "").strip()
                lines.append(
                    f"   {i}. {tp.get('plate_no', '')} "
                    f"{tp.get('car_brand', '')} — {tp.get('drv_name', '')}"
                    + (f" | ประกัน: {insurer}" if insurer else "")
                )
        if self.injuries:
            lines.append(f"ผู้บาดเจ็บ      : {len(self.injuries)} ราย")
            for i, inj in enumerate(self.injuries, 1):
                lines.append(f"   {i}. {inj.get('name', '')} "
                             f"({inj.get('injury_type', '')})")
        if self.assets:
            lines.append(f"ทรัพย์สินเสียหาย : {len(self.assets)} รายการ")
            for i, a in enumerate(self.assets, 1):
                lines.append(f"   {i}. {a.get('name', '')} "
                             f"≈ {a.get('damage_cost', '')} บาท")

        if self.bill:
            labels = [("invest", "ค่าบริการ"), ("trans", "ค่าเดินทาง"),
                      ("dist", "ค่าระยะทาง"), ("photo", "ค่ารูป"),
                      ("tel", "ค่าโทรศัพท์"), ("insure", "ค่าประกัน"),
                      ("claim", "ค่าเคลม"), ("daily", "ค่าคัดประจำวัน"),
                      ("other", "อื่นๆ"), ("cartow", "ค่ายกลาก")]
            items = []
            for key, name in labels:
                raw = str(self.bill.get(key, "")).replace(",", "").strip()
                try:
                    if float(raw or 0) > 0:
                        items.append(f"{name} {raw}")
                except ValueError:
                    pass
            src = ("จอ ISURVEY ชุดอนุมัติ"
                   if self.bill.get("source") == "isurvey_screen" else "XML")
            total = self.bill.get("total_net") or self.bill.get("total") or ""
            lines.append(
                f"ค่าสำรวจ→EMCS  : {' / '.join(items) if items else '(ทุกรายการ 0)'}"
                + (f" | รวมสุทธิ {total}" if str(total).strip() else "")
                + f" [{src}]"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def claim_type_name(self) -> str:
        t = self.claim_type.strip()
        return CLAIM_TYPE_NAMES.get(t, f"ไม่ทราบประเภท ({t or 'ว่าง'})")

    def dry_claim_block_reason(self) -> str:
        """เหตุผลที่ "ไม่ใช่เคลมแห้งแท้" — คืน '' เมื่อเป็นเคลมแห้งกรอกได้
        เช็คสองชั้น: ประเภทเคลมต้องเป็น 2 (เคลมแห้ง) และต้องไม่มี
        คู่กรณี/ผู้บาดเจ็บ/ทรัพย์สิน (กันกรณีข้อมูลประเภทเพี้ยน)"""
        if self.claim_type.strip() != "2":
            return f"ประเภทเคลม = {self.claim_type_name()} (ไม่ใช่เคลมแห้ง)"
        if self.third_parties or self.injuries or self.assets:
            return (f"มีคู่กรณี {len(self.third_parties)} / ผู้บาดเจ็บ "
                    f"{len(self.injuries)} / ทรัพย์สิน {len(self.assets)}")
        return ""

    def validate(self) -> dict:
        """ตรวจความครบของข้อมูล
        คืน {'critical': [field สำคัญที่ว่าง], 'optional': [field รองที่ว่าง],
             'warnings': [ความผิดปกติอื่น]}"""
        critical, optional = [], []
        for fname, label in FIELD_LABELS.items():
            if not str(getattr(self, fname, "")).strip():
                (critical if fname in CRITICAL_FIELDS else optional).append(label)

        warnings = []
        n = len(self.damage)
        if n == 0:
            warnings.append("ไม่มีรายการความเสียหายเลย")
        if not (n == len(self.type_damage) == len(self.rank_damage)):
            warnings.append(
                f"รายการความเสียหายไม่ครบคู่ (ชิ้นส่วน {n} / "
                f"ประเภท {len(self.type_damage)} / ระดับ {len(self.rank_damage)})"
            )
        bad_rank = [r for r in self.rank_damage
                    if r.strip().upper() not in ("A", "B", "C", "D")]
        if bad_rank:
            warnings.append(f"ระดับความเสียหายไม่รู้จัก: {bad_rank}")
        if n > 8:
            warnings.append(f"ความเสียหาย {n} รายการ เกินที่หน้า EMCS รับได้ (8)")
        if "คู่กรณี" in self.acc_result and not self.third_parties:
            warnings.append("ผลคดีกล่าวถึงคู่กรณี แต่ไม่พบข้อมูลคู่กรณีใน Tab 4")
        return {"critical": critical, "optional": optional, "warnings": warnings}

    def validation_report(self) -> str:
        """รายงานความครบของข้อมูลแบบอ่านง่าย ให้คนตัดสินใจก่อนกรอก EMCS"""
        v = self.validate()
        lines = []
        if v["critical"]:
            lines.append(f"❌ field สำคัญที่ยังว่าง ({len(v['critical'])}): "
                         + ", ".join(v["critical"]))
        for w in v["warnings"]:
            lines.append(f"⚠️ {w}")
        if v["optional"]:
            lines.append(f"ℹ️ field รองที่ว่าง ({len(v['optional'])}): "
                         + ", ".join(v["optional"]))
        if not lines:
            lines.append("✅ ข้อมูลครบทุก field")
        return "\n".join(lines)
