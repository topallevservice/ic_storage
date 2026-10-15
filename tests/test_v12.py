"""ทดสอบ v12: ค้นแบบเทียบทุกแหล่ง (เบอร์หน้าซอง + ข้อความบนชิป + datasheet) และหาตัวแทนจาก datasheet
- ใช้ spec.json จริงจาก D:\\IC_Datasheets (คัดลอกไปโฟลเดอร์ชั่วคราว ไม่แตะของจริง)
- ไม่มีในคลัง / ของหมด -> ตัวแทนที่มีในคลัง พร้อมเทียบขาต่อขาและค่าไฟ ; ขาสลับ (TL431 vs AS431) ต้องเตือนห้ามใส่
- รูปตัวชิป -> /api/identify-ic คืนผลเทียบ (match) ให้หน้าเว็บแสดงเป็นป๊อปอัพ"""
import os, shutil, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

from fastapi.testclient import TestClient
from app.main import app
from app.db import DATASHEET_DIR, connect
from app import datasheets
from app.matcher import split_names, pin_name, name_rel

REAL = Path(r"D:\IC_Datasheets")
assert "IC_Datasheets" not in str(DATASHEET_DIR)
COPY = ["04_IF_Interface-CAN-LIN-RS485/TJA1042T-1J", "04_IF_Interface-CAN-LIN-RS485/TCAN1051VDRQ1",
        "04_IF_Interface-CAN-LIN-RS485/SIT1044T-3", "07_AN_Analog-OpAmp-ADC-CurrentSense/OPA2348AIDR",
        "07_AN_Analog-OpAmp-ADC-CurrentSense/LP2904DR", "02_PM_Power-LDO-DCDC/TLV70012QDDCRQ1",
        "02_PM_Power-LDO-DCDC/AS431ANTR-E1", "_อ้างอิง_ไม่มีในคลัง/TJA1051T", "_อ้างอิง_ไม่มีในคลัง/LM2904DR",
        "_อ้างอิง_ไม่มีในคลัง/TL431AIDBZR"]
for rel in COPY:
    dst = DATASHEET_DIR / rel
    dst.mkdir(parents=True, exist_ok=True)
    for f in (REAL / rel).iterdir():
        if f.suffix in (".json", ".txt", ".md"):
            shutil.copy(f, dst / f.name)

# 1) หน่วยย่อย
assert split_names("LM2904 / LM358") == ["LM2904", "LM358"]
assert split_names("TJA1145T/FDJ, ATA6570-GNQW0") == ["TJA1145TFDJ", "ATA6570GNQW0"]
assert split_names("TCAN1042-Q1 (ไม่มี V)") == ["TCAN1042Q1"]
assert split_names("SiC MOSFET 1200 V ≤ 80 mΩ ตัวถัง D2PAK-7") == []
assert pin_name("STB (STBY)") == "STB" and pin_name("V- (GND)") == "V-" and pin_name("VIN") == "IN"
assert name_rel("BTS724GXUMA1", "BTS724G")[1] == "suffix" and name_rel("1O42V", "1042V")[1] == "ocr"
assert name_rel("TJA1042", "TJA1042T1J")[1] == "base" and name_rel("TJA1042", "TJA1042T1J")[0] < 0.85


def save(c, **kw):
    r = c.post("/intake", data=kw, follow_redirects=False)
    assert r.status_code in (302, 303), r.text[:300]


def m(c, q):
    return c.get("/api/match", params={"q": q}).json()


with TestClient(app) as c:
    save(c, mpn="TJA1042T/1J", qty=3, location="A-R1-C1", marking="NXP TJA1042 / G6721R / Tn5261")
    save(c, mpn="TCAN1051VDRQ1", qty=10, location="A-R1-C2", marking="1051V / TI45M / AOQ2 (ใต้ตัว: 3C513)")
    save(c, mpn="SIT1044T/3", qty=9, location="A-R1-C3", marking="SIT / SIT1044T/3 / D9H4955S")
    save(c, mpn="OPA2348AIDR", qty=17, location="A-R2-C1", marking="TI OPA / 2348A / 53ERZ (ใต้ตัว: KA608)")
    save(c, mpn="LP2904DR", qty=8, location="A-R2-C2", marking="LP2904 / TI 18M / A0FH G4")
    save(c, mpn="TLV70012QDDCRQ1", qty=10, location="A-R2-C3", marking="SDO")
    save(c, mpn="AS431ANTR-E1", qty=10, location="A-R3-C1", marking="EB5")
    with connect() as con:
        datasheets.sync(con)

    # 2) หน้าค้นหา: มีปุ่มเทียบ + ป๊อปอัพ
    t = c.get("/").text
    assert 'id="uGo"' in t and "/static/match.js?v=4" in t and "/static/out.js?v=6" in t

    # 3) ข้อความบนชิป + datasheet ยืนยัน
    r = m(c, "SDO")
    assert r["status"] == "found" and r["matches"][0]["mpn"] == "TLV70012QDDCRQ1", r["status"]
    top = r["matches"][0]
    assert top["ds_confirm"] and {"marking", "datasheet"} <= {e["kind"] for e in top["evidence"]}, top["evidence"]
    assert top["locs"] and top["locs"][0]["code"] == "A-R2-C3" and top["qty"] == 10
    assert r["substitutes"] == []   # เจอแล้ว ไม่ต้องหาตัวแทน
    r = m(c, "EB5")
    assert r["status"] == "found" and r["matches"][0]["mpn"] == "AS431ANTR-E1" and r["matches"][0]["ds_confirm"]
    # OCR/ตาอ่านสลับ B<->8
    r = m(c, "E85")
    assert r["matches"][0]["mpn"] == "AS431ANTR-E1" and r["matches"][0]["score"] >= 0.85
    # พิมพ์ทั้งบรรทัดตามที่เห็นบนชิป
    r = m(c, "TI OPA 2348A 53ERZ")
    assert r["status"] == "found" and r["matches"][0]["mpn"] == "OPA2348AIDR"
    # เบอร์หน้าซอง (ต่างรหัสสั่งซื้อ) ; เบอร์หลักเฉย ๆ = ให้ผู้ใช้ตรวจรุ่นย่อย
    assert m(c, "TJA1042T/1J")["status"] == "found"
    r = m(c, "TJA1042")
    assert r["matches"][0]["mpn"] == "TJA1042T/1J"   # ข้อความบนชิป "TJA1042" ตรง

    # 4) ไม่มีในคลัง แต่มี datasheet อ้างอิง -> ตัวแทนจากการเทียบ datasheet
    r = m(c, "LM2904")
    assert r["status"] == "not_found" and r["target"]["mpn"] == "LM2904DR" and r["target"]["ref"]
    subs = {s["mpn"]: s for s in r["substitutes"]}
    assert r["substitutes"][0]["mpn"] == "LP2904DR" and subs["LP2904DR"]["level"] == "conditional", r["substitutes"]
    assert subs["LP2904DR"]["from"] == "datasheet ของ LP2904DR" and subs["LP2904DR"]["pins"] == []
    assert subs["OPA2348AIDR"]["level"] == "param-bad" and subs["OPA2348AIDR"]["pins"] == []   # ขาตรง แต่ทนไฟแค่ 5.5 V
    sup = next(p for p in subs["OPA2348AIDR"]["params"] if p["key"] == "supply_v")
    assert sup["flag"] in ("warn", "bad") and sup["a"].startswith("3")
    assert subs["OPA2348AIDR"]["locs"][0]["code"] == "A-R2-C1"

    # 5) ขาสลับ: TL431 (SOT-23 ขา 1 = K) vs AS431 (ขา 1 = REF) -> ห้ามใส่แทนตรง ๆ
    r = m(c, "TL431")
    s = next(x for x in r["substitutes"] if x["mpn"] == "AS431ANTR-E1")
    assert s["level"] == "none" and {d["pin"] for d in s["pins"]} == {"1", "2"}, s
    assert "ห้าม" in s["level_th"]

    # 6) CAN: TJA1051T (ขา 8 = Silent) -> TJA1042 (ขา 8 = STB) แทนได้แบบมีเงื่อนไข ; SIT1044/3 ขาต่าง 2 ขา
    r = m(c, "TJA1051T")
    subs = {s["mpn"]: s for s in r["substitutes"]}
    assert subs["TJA1042T/1J"]["level"] == "conditional" and "TJA1042T/1J" in subs["TJA1042T/1J"]["from"]
    assert subs["SIT1044T/3"]["level"] == "pin-diff" and len(subs["SIT1044T/3"]["pins"]) == 2
    assert [x["level"] for x in r["substitutes"]] == sorted([x["level"] for x in r["substitutes"]],
                                                            key=lambda l: ["drop-in", "conditional", "pin-same", "criteria", "param-bad", "name", "pin-diff", "none"].index(l))

    # 7) ของหมด -> ตัวแทน (datasheet ของเบอร์นั้นระบุ SIT1044T/3)
    with connect() as con:
        pid = con.execute("SELECT id FROM parts WHERE mpn='TJA1042T/1J'").fetchone()[0]
    assert c.post("/api/out", data={"part_id": pid, "qty": 3}).json()["ok"]
    r = m(c, "TJA1042T/1J")
    assert r["status"] == "out_of_stock" and r["target"]["mpn"] == "TJA1042T/1J"
    subs = {s["mpn"]: s for s in r["substitutes"]}
    assert "SIT1044T/3" in subs and subs["SIT1044T/3"]["level"] == "conditional" and subs["SIT1044T/3"]["qty"] == 9
    assert "TJA1042T/1J" not in subs

    # 8) ไม่รู้จักเบอร์ (ไม่มี datasheet) -> เทียบจากชื่อเท่านั้น / ไม่เจออะไรเลย
    r = m(c, "SIT1042T")
    assert r["status"] == "not_found" and r["target"] is None
    assert any(s["level"] == "name" for s in r["substitutes"]), r["substitutes"]
    r = m(c, "XYZ9999")
    assert r["status"] == "not_found" and r["matches"] == [] and r["substitutes"] == []
    assert m(c, "")["matches"] == []

    # 9) รูปตัวชิป -> ผลเทียบสำหรับป๊อปอัพ (ไม่เก็บไฟล์เมื่อ keep=0)
    img = Path(r"D:\IC_Storage\data\part_images\2026-10-14_a478b7f7.jpg")   # OPA2348: TI OPA / 2348A / 53ERZ
    d = c.post("/api/identify-ic", files={"photo": ("ic.jpg", img.open("rb"), "image/jpeg")}, data={"keep": "0"}).json()
    assert d["match"]["matches"][0]["mpn"] == "OPA2348AIDR" and d["match"]["status"] == "found", d["match"]["status"]
    assert d["match"]["query"] and d["image"] == ""

    # 10) หน้าไอซี: ตัวแทนชื่อรวม "OPA2348AIDR / TLV9062IDR" แสดงยอดในคลังได้
    with connect() as con:
        info = datasheets.for_part(con, "LP2904DR")
    sub = next(x for x in info["subs"] if x["mpn"].startswith("OPA2348AIDR"))
    assert sub["stock"] and sub["stock"]["mpn"] == "OPA2348AIDR" and sub["stock"]["qty"] == 17
print("V12 ALL OK")
