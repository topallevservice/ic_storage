"""ทดสอบ v3: หลายรูป/ตัดกรอบ, แก้ไข/ลบรายการ, ป้ายใช้รูปตัดกรอบ"""
import io, os, shutil, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app
from app.db import connect, IMAGE_DIR

CHIP = r"D:\IC_Storage\data\part_images\2026-10-14_cdb02d3d.jpg"   # รูปจริงของผู้ใช้ (อ่านอย่างเดียว)

def upload(c):
    with open(CHIP, "rb") as f:
        return c.post("/api/ic-photo", files={"photo": ("ic.jpg", f, "image/jpeg")}).json()["image"]

with TestClient(app) as c:
    imgs = [upload(c) for _ in range(3)]
    for n in imgs:
        assert (IMAGE_DIR / f"c_{n}").exists() and (IMAGE_DIR / f"t_{n}").exists()
    c.post("/intake", data={"mpn": "B0505XT-1WR3", "qty": 5, "ic_images": ",".join(imgs)})
    j = c.get("/api/lookup", params={"mpn": "B0505XT-1WR3"}).json()
    assert j["images"] == imgs and j["image"] == imgs[0] and j["category"] == "PM", j
    # ตั้งรูปหลัก / ลบรูป
    with connect() as con:
        ids = [r[0] for r in con.execute("SELECT id FROM part_images ORDER BY id")]
    c.post(f"/part/1/image/{ids[1]}/primary")
    assert c.get("/api/lookup", params={"mpn": "B0505XT-1WR3"}).json()["image"] == imgs[1]
    c.post(f"/part/1/image/{ids[1]}/delete")
    j = c.get("/api/lookup", params={"mpn": "B0505XT-1WR3"}).json()
    assert j["image"] == imgs[0] and len(j["images"]) == 2 and not (IMAGE_DIR / imgs[1]).exists()
    # ตัดกรอบเอง + auto
    assert c.post(f"/crop/{imgs[0]}", data={"x": .2, "y": .3, "w": .5, "h": .3, "back": "/part/1"}, follow_redirects=False).status_code == 303
    assert c.post(f"/crop/{imgs[0]}", data={"x": 0, "y": 0, "w": 1, "h": 1, "auto": "1"}, follow_redirects=False).status_code == 303
    assert c.post("/crop/..%2Fx.jpg", data={"x": 0, "y": 0, "w": 1, "h": 1}).status_code == 404

    # แก้ไขรายการ: จำนวน + ย้ายทั้งเบอร์
    c.post("/intake", data={"mpn": "B0505XT-1WR3", "qty": 3})
    assert c.get("/lot/1/edit").status_code == 200
    c.post("/lot/1/edit", data={"mpn": "B0505XT-1WR3", "category": "PM", "qty": 7, "location": "A-R2-C2"})
    with connect() as con:
        rows = con.execute("SELECT l.qty, loc.code FROM stock_lots l JOIN locations loc ON loc.id=l.location_id ORDER BY l.id").fetchall()
    assert [tuple(r) for r in rows] == [(7, "A-R2-C2"), (3, "A-R2-C2")], [tuple(r) for r in rows]
    # MPN ผิด -> แก้เป็นเบอร์ที่มีอยู่ -> ย้ายไปรวม
    c.post("/intake", data={"mpn": "B0505XT-1WR", "qty": 2})          # สแกนผิด สร้างเบอร์ใหม่
    with connect() as con:
        bad = con.execute("SELECT id FROM stock_lots ORDER BY id DESC LIMIT 1").fetchone()[0]
    c.post(f"/lot/{bad}/edit", data={"mpn": "B0505XT-1WR3", "category": "PM", "qty": 2, "location": "A-R2-C2"})
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM parts").fetchone()[0] == 1
        assert con.execute("SELECT SUM(qty) FROM stock_lots").fetchone()[0] == 12
    # ลบรายการ
    r = c.post(f"/lot/{bad}/delete", follow_redirects=False)
    assert r.status_code == 303
    with connect() as con:
        assert con.execute("SELECT SUM(qty) FROM stock_lots").fetchone()[0] == 10
    # เบิกแล้วลบไม่ได้
    c.post("/part/1/out", data={"qty": 1, "tx_type": "OUT"})
    r = c.post("/lot/1/delete", follow_redirects=False)
    assert "warn=" in r.headers["location"]

    # ป้ายใช้รูปที่ตัดกรอบ (c_) ทั้งสองแบบ + หน้าอื่นๆ เปิดได้
    assert f"c_{imgs[0]}" in c.get("/labels").text
    assert f"c_{imgs[0]}" in c.get("/labels?mode=part").text
    for u in ["/", "/intake", "/map", "/part/1", f"/crop/{imgs[0]}?back=/part/1", "/lot/1/edit"]:
        assert c.get(u).status_code == 200, u
# ฟอร์มแก้ไข/หน้าเบอร์ต้องแสดงข้อมูลเบอร์จริง (เคยโดนตัวแปร p ใน base.html ทับ)
    t = c.get("/lot/1/edit").text
    assert 'value="B0505XT-1WR3"' in t and 'value="PM" selected' in t
    t = c.get("/part/1").text
    assert 'value="PM" selected' in t and "รูปหลัก" in t
print("V3 ALL OK")
