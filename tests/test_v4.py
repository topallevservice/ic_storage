"""ทดสอบ v4: ลากเรียงรูป -> รูปแรกเป็นรูปหลัก"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app

CHIP = r"D:\IC_Storage\data\part_images\2026-10-14_cdb02d3d.jpg"   # อ่านอย่างเดียว

def upload(c):
    with open(CHIP, "rb") as f:
        return c.post("/api/ic-photo", files={"photo": ("ic.jpg", f, "image/jpeg")}).json()["image"]

def look(c, mpn):
    return c.get("/api/lookup", params={"mpn": mpn}).json()

with TestClient(app) as c:
    a, b, d = upload(c), upload(c), upload(c)
    # รับของ: ถ่าย a,b,d แล้วลากให้ d เป็นรูปแรก
    c.post("/intake", data={"mpn": "TM50P03NF", "qty": 4, "ic_images": f"{a},{b},{d}", "ic_order": f"{d},{a},{b}"})
    j = look(c, "TM50P03NF")
    assert j["images"] == [d, a, b] and j["image"] == d, j
    # หน้าแก้ไข/หน้าเบอร์: ลากเรียงใหม่ -> บันทึกทันที
    r = c.post("/part/1/images/order", data={"files": f"{b},{d},{a}"}).json()
    assert r["primary"] == b
    j = look(c, "TM50P03NF")
    assert j["images"] == [b, d, a] and j["image"] == b, j
    # ส่งมาไม่ครบ / ชื่อแปลก -> ที่เหลือต่อท้าย ไม่หาย
    c.post("/part/1/images/order", data={"files": f"{a},../../etc,zzz.jpg"})
    j = look(c, "TM50P03NF")
    assert j["images"][0] == a and sorted(j["images"]) == sorted([a, b, d]), j
    # ปุ่ม ตั้งเป็นรูปหลัก = ย้ายไปลำดับแรก
    t = c.get("/part/1").text
    assert 'data-file="' + a + '"' in t and "makeSortable" in t
    from app.db import connect
    with connect() as con:
        iid = con.execute("SELECT id FROM part_images WHERE file=?", (d,)).fetchone()[0]
    c.post(f"/part/1/image/{iid}/primary")
    assert look(c, "TM50P03NF")["images"][0] == d
    # ถุงใหม่ของเบอร์เดิม + รูปใหม่ e ลากไว้หน้าสุด (รวมรูปเก่า)
    e = upload(c)
    old = look(c, "TM50P03NF")["images"]
    c.post("/intake", data={"mpn": "TM50P03NF", "qty": 2, "ic_images": e, "ic_order": ",".join([e] + old)})
    j = look(c, "TM50P03NF")
    assert j["images"] == [e] + old and j["image"] == e, j
    # ป้าย/แผนผังใช้รูปแรก
    assert f"c_{e}" in c.get("/labels?mode=part").text
    assert c.get("/static/sortable.js").status_code == 200
print("V4 ALL OK")
