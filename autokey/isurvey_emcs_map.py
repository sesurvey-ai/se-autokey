"""ตารางแปลงรหัส ISURVEY → EMCS — **ไฟล์นี้ถูกสร้างอัตโนมัติ อย่าแก้ด้วยมือ**

สร้างโดย tools/build_code_map.py เมื่อ 2026-08-04 09:14
จับคู่ด้วย "ชื่อ" จาก dropdown จริงของ EMCS (runs/emcs_spec.json) กับตาราง master ของ ISURVEY

ทำไมต้องแปลง: สองระบบใช้รหัสคนละชุดกับของอย่างเดียวกัน —
ชลบุรี ISURVEY 20 / EMCS 9 · ใบขับขี่รถยนต์ส่วนบุคคล ISURVEY 15 / EMCS 19
ส่งรหัสดิบข้ามระบบ = เลือกผิดแบบเงียบ ๆ ไม่มี error
"""

# {รหัสจังหวัด ISURVEY: รหัสจังหวัด EMCS}
PROVINCE_TO_EMCS = {
    "10": "2",
    "11": "58",
    "12": "24",
    "13": "28",
    "14": "70",
    "15": "71",
    "16": "50",
    "17": "63",
    "18": "10",
    "19": "62",
    "20": "9",
    "21": "48",
    "22": "7",
    "23": "16",
    "24": "8",
    "25": "30",
    "26": "18",
    "27": "61",
    "30": "21",
    "31": "27",
    "32": "67",
    "33": "54",
    "34": "76",
    "35": "44",
    "36": "11",
    "37": "72",
    "38": "78",
    "39": "69",
    "40": "6",
    "41": "73",
    "42": "53",
    "43": "68",
    "44": "41",
    "45": "46",
    "46": "4",
    "47": "55",
    "48": "20",
    "49": "42",
    "50": "14",
    "51": "52",
    "52": "51",
    "53": "74",
    "54": "39",
    "55": "26",
    "56": "32",
    "57": "13",
    "58": "43",
    "60": "23",
    "61": "75",
    "62": "5",
    "63": "17",
    "64": "64",
    "65": "36",
    "66": "35",
    "67": "38",
    "70": "49",
    "71": "3",
    "72": "65",
    "73": "19",
    "74": "60",
    "75": "59",
    "76": "37",
    "77": "29",
    "80": "22",
    "81": "1",
    "82": "33",
    "83": "40",
    "84": "66",
    "85": "47",
    "86": "12",
    "90": "56",
    "91": "57",
    "92": "15",
    "93": "34",
    "94": "31",
    "95": "45",
    "96": "25"
}

# {รหัสประเภทใบขับขี่ ISURVEY: รหัส EMCS}
LICENSE_TO_EMCS = {
    "01": "1",
    "02": "2",
    "03": "4",
    "04": "6",
    "05": "10",
    "06": "15",
    "07": "22",
    "08": "23",
    "09": "24",
    "10": "25",
    "11": "27",
    "12": "28",
    "15": "19",
    "16": "20",
    "99": "26"
}

# {related_accidentID ของ ISURVEY: รหัสประเภทผู้บาดเจ็บแบบ XML (DV/PR/ON)}
PERSON_TYPE_TO_XML = {
    "2": "DV",
    "10": "DV",
    "3": "PR",
    "11": "PR",
    "17": "PR",
    "4": "ON",
    "5": "ON",
    "6": "ON",
    "18": "ON",
    "19": "ON"
}

# {injury_type ของ ISURVEY: รหัส ddlWounded_Type ของ EMCS}
WOUNDED_TYPE_TO_EMCS = {
    "I": "02"
}


def province(isurvey_code) -> str:
    """รหัสจังหวัด ISURVEY → EMCS ('' เมื่อแปลงไม่ได้ — อย่าเดา ปล่อยว่างให้คนเลือก)"""
    return PROVINCE_TO_EMCS.get(str(isurvey_code or "").strip(), "")


def person_type(isurvey_related_accident_id) -> str:
    """related_accidentID ของ ISURVEY → รหัสประเภทผู้บาดเจ็บแบบ XML (DV/PR/ON)
    บอทแปลงต่อเป็น value ของ ddlPerson_Type เองใน emcs.PERSON_TYPE_MAP"""
    return PERSON_TYPE_TO_XML.get(str(isurvey_related_accident_id or "").strip(), "")


def wounded_type(isurvey_injury_type) -> str:
    """injury_type ของ ISURVEY → รหัส ddlWounded_Type ของ EMCS ('' = ไม่รู้ ปล่อยว่าง)"""
    return WOUNDED_TYPE_TO_EMCS.get(str(isurvey_injury_type or "").strip(), "")


def license_type(isurvey_code) -> str:
    """รหัสประเภทใบขับขี่ ISURVEY → EMCS ('' เมื่อแปลงไม่ได้)"""
    return LICENSE_TO_EMCS.get(str(isurvey_code or "").strip(), "")


def district(isurvey_amphur_id, isurvey_province_id="") -> str:
    """รหัสอำเภอ ISURVEY → EMCS

    ทั้งสองระบบใช้รูปแบบเดียวกัน = <รหัสจังหวัดของระบบนั้น><ลำดับอำเภอ 2 หลัก>
    (ISURVEY 3607 = จ.36 อำเภอลำดับ 07 · EMCS 7101 = จ.71 อำเภอลำดับ 01)
    ลำดับอำเภอเรียงเหมือนกัน (ทั้งคู่เรียงตามลำดับราชการ) → เปลี่ยนแค่ส่วนรหัสจังหวัด
    คืน '' เมื่อรูปแบบไม่ตรงหรือแปลงจังหวัดไม่ได้"""
    a = str(isurvey_amphur_id or "").strip()
    if not a.isdigit() or len(a) < 3:
        return ""
    seq, isv_prov = a[-2:], a[:-2]
    if isurvey_province_id and str(isurvey_province_id).strip() != isv_prov:
        return ""          # อำเภอไม่ได้อยู่ในจังหวัดที่ระบุ = ข้อมูลไม่สอดคล้อง
    ep = province(isv_prov)
    return f"{ep}{seq}" if ep else ""
