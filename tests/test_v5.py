"""ทดสอบ v5: ตรวจเบอร์ซ้ำ/ถุงซ้ำตั้งแต่สแกนฉลาก -> ใส่ช่องเดิม เพิ่มจำนวน"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.parse import unquote
from fastapi.testclient import TestClient
from app.main import app

QR = "{pbn:PICK2410140001,on:GB2410140001,pc:C82656,pm:BTS724G,qty:20,mc:,cc:1,pdi:123,hp:0}"
QR2 = QR.replace("PICK2410140001", "PICK2410140002").replace("qty:20", "qty:30")

def parse(c, text):
    return c.post("/api/parse", data={"text": text}).json()["items"][0]

def save(c, **kw):
    r = c.post("/intake", data=kw, follow_redirects=False)
    return unquote(r.headers["location"])

with TestClient(app) as c:
    # 1) เบอร์ใหม่
    it = parse(c, QR)
    assert not it["existing"] and not it["duplicate"] and it["stock_qty"] == 0, it
    loc = save(c, mpn="BTS724G", qty=20, supplier="LCSC", supplier_pn="C82656", raw_code=QR, location="A-R1-C1")
    assert "เบอร์ใหม่" in loc, loc

    # 2) สแกนถุงเดิมซ้ำ -> รู้ทันทีตอนสแกน ว่าถุงซ้ำ + เบอร์ซ้ำ + ช่องเดิม + ยอดเดิม
    it = parse(c, QR)
    assert it["duplicate"] and it["dup_info"]["qty"] == 20 and it["dup_info"]["loc"] == "A-R1-C1", it
    assert it["existing"] and it["locked_loc"] == "A-R1-C1" and it["stock_qty"] == 20, it
    # บันทึกโดยไม่ยืนยัน -> เซิร์ฟเวอร์ไม่ยอม
    loc = save(c, mpn="BTS724G", qty=20, raw_code=QR, location="STAGING")
    assert "ไม่ได้บันทึก" in loc, loc
    assert parse(c, QR)["stock_qty"] == 20

    # 3) ถุงใหม่ของเบอร์เดิม (โค้ดต่าง) -> ไม่ใช่ถุงซ้ำ แต่เป็นเบอร์ซ้ำ ใส่ช่องเดิมแม้เลือกที่อื่น
    it = parse(c, QR2)
    assert not it["duplicate"] and it["existing"] and it["locked_loc"] == "A-R1-C1", it
    loc = save(c, mpn="BTS724G", qty=30, raw_code=QR2, location="A-R3-C3")
    assert "เบอร์ซ้ำ" in loc and "A-R1-C1" in loc and "รวม 50" in loc, loc

    # 4) ยืนยันว่าเป็นถุงใหม่จริง (ฉลากเหมือนกัน) -> บันทึกได้
    loc = save(c, mpn="BTS724G", qty=20, raw_code=QR, dup_ok="1")
    assert "รวม 70" in loc, loc

    # 5) ฉลากไม่มี MPN แต่มีเบอร์ LCSC เดิม -> จับได้ว่าซ้ำ
    it = parse(c, "C82656")
    assert it["existing"] and it["mpn"] == "BTS724G" and it["matched_by"] == "supplier_pn" and it["stock_qty"] == 70, it

    # 6) MPN ต่างแค่ท้าย packing (,215 / -TR) = เบอร์เดียวกัน
    save(c, mpn="BZX84-C3V3,215", qty=100, location="A-R2-C1")
    j = c.get("/api/lookup", params={"mpn": "BZX84C3V3"}).json()
    assert j["existing"] and j["locked_loc"] == "A-R2-C1" and j["stock_qty"] == 100, j
    loc = save(c, mpn="bzx84-c3v3", qty=50)
    assert "รวม 150" in loc and "A-R2-C1" in loc, loc

    # 7) เบอร์ใกล้เคียง (ต่างท้ายรหัสสั่งซื้อ) -> เสนอให้เลือก ไม่รวมเอง
    j = c.get("/api/lookup", params={"mpn": "BTS724GXUMA1"}).json()
    assert not j["existing"] and [s["mpn"] for s in j["similar"]] == ["BTS724G"], j
    assert j["similar"][0]["loc"] == "A-R1-C1" and j["similar"][0]["qty"] == 70
    assert c.get("/api/lookup", params={"mpn": "XYZ"}).json()["similar"] == []

    # 8) เบอร์ซ้ำที่ยังรอระบุช่อง
    save(c, mpn="TJA1043T", qty=10)
    loc = save(c, mpn="TJA1043T", qty=5)
    assert "ยังรอระบุช่อง" in loc and "รวม 15" in loc, loc

    # หน้า intake มีกล่องตรวจซ้ำ
    t = c.get("/intake").text
    assert 'id="dupBox"' in t and 'id="btnSave"' in t
print("V5 ALL OK")
