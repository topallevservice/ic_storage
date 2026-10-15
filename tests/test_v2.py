"""ทดสอบ v2: ล็อกตำแหน่งเดิม, เพิ่ม/ลดช่องตู้, รูปไอซี, หน้าเว็บ"""
import io, os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from PIL import Image
from app.main import app
from app.db import connect

with TestClient(app) as c:
    # รับเบอร์ใหม่ -> พัก STAGING
    r = c.post("/intake", data={"mpn": "BTS724G", "qty": 20, "category": "SW", "location": "STAGING"}, follow_redirects=False)
    assert r.status_code == 303
    j = c.get("/api/lookup", params={"mpn": "BTS724G"}).json()
    assert j["locked_loc"] is None and j["waiting"] == 1, j     # ยังไม่ระบุช่อง -> ไม่ล็อก รอผู้ใช้ระบุ
    assert "BTS724G" in c.get("/putaway").text
    assert 'class="badge">1<' in c.get("/map").text
    # ระบบต้องไม่วางลิ้นชักเอง
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM stock_lots WHERE planned_location_id IS NOT NULL").fetchone()[0] == 0
    # ถุงที่ 2 ยังไม่ระบุ -> รอรวมกัน ; ผู้ใช้ระบุช่องที่หน้า รอระบุช่อง -> ทุกถุงของเบอร์เข้าช่องเดียว
    c.post("/intake", data={"mpn": "BTS724G", "qty": 3, "location": "STAGING"})
    r = c.post("/assign", data={"part_id": 1, "to": ""}, follow_redirects=False)
    assert "warn=" in r.headers["location"]                      # ไม่เลือกช่อง -> เตือน
    c.post("/assign", data={"part_id": 1, "to": "A-R1-C1"})
    with connect() as con:
        assert {x[0] for x in con.execute("SELECT loc.code FROM stock_lots l JOIN locations loc ON loc.id=l.location_id")} == {"A-R1-C1"}
        con.execute("UPDATE stock_lots SET qty=qty")
    assert "ไม่มีของรอระบุช่อง" in c.get("/putaway").text
    # เบอร์ใหม่ที่ผู้ใช้เลือกช่องเองตอนรับของ -> เข้าช่องนั้นเลย
    c.post("/intake", data={"mpn": "TJA1042T", "qty": 10, "location": "A-R2-C3"})
    assert c.get("/api/lookup", params={"mpn": "TJA1042T"}).json()["locked_loc"] == "A-R2-C3"
    # เบอร์ที่รออยู่ แล้วถุงใหม่ระบุช่อง -> ถุงที่รอย้ายตาม
    c.post("/intake", data={"mpn": "LM358", "qty": 5, "location": "STAGING"})
    c.post("/intake", data={"mpn": "LM358", "qty": 5, "location": "A-R3-C3"})
    j = c.get("/api/lookup", params={"mpn": "LM358"}).json()
    assert j["locked_loc"] == "A-R3-C3" and j["waiting"] == 0, j
    # ใส่ของที่รอจากหน้าลิ้นชัก (สแกน QR ลิ้นชัก)
    c.post("/intake", data={"mpn": "BZX84-C3V3", "qty": 20, "location": "STAGING"})
    assert "BZX84-C3V3" in c.get("/loc/A-R4-C3").text
    with connect() as con:
        pid = con.execute("SELECT id FROM parts WHERE mpn='BZX84-C3V3'").fetchone()[0]
    c.post("/assign", data={"part_id": pid, "to": "A-R4-C3", "back": "/loc/A-R4-C3"})
    assert c.get("/api/lookup", params={"mpn": "BZX84-C3V3"}).json()["locked_loc"] == "A-R4-C3"
    with connect() as con:
        con.execute("DELETE FROM transactions WHERE part_id IN (SELECT id FROM parts WHERE mpn IN ('TJA1042T','LM358','BZX84-C3V3'))")
        con.execute("DELETE FROM stock_lots WHERE part_id IN (SELECT id FROM parts WHERE mpn IN ('TJA1042T','LM358','BZX84-C3V3'))")
    j = c.get("/api/lookup", params={"mpn": "bts724g"}).json()
    assert j["locked_loc"] == "A-R1-C1" and "อยู่ที่" in j["locked_note"], j
    # ส่งตำแหน่งอื่นมา -> ระบบบังคับวางที่เดิม
    c.post("/intake", data={"mpn": "BTS724G", "qty": 5, "location": "A-R6-C3"})
    with connect() as con:
        locs = {x[0] for x in con.execute("SELECT loc.code FROM stock_lots l JOIN locations loc ON loc.id=l.location_id")}
    assert locs == {"A-R1-C1"}, locs

    # รูปไอซี
    buf = io.BytesIO(); Image.new("RGB", (2000, 1500), "gray").save(buf, "JPEG"); buf.seek(0)
    img = c.post("/api/ic-photo", files={"photo": ("ic.jpg", buf, "image/jpeg")}).json()["image"]
    c.post("/intake", data={"mpn": "BTS724G", "qty": 1, "ic_images": img})
    assert c.get("/api/lookup", params={"mpn": "BTS724G"}).json()["image"] == img
    assert c.get(f"/images/t_{img}").status_code == 200

    def count(cab):
        with connect() as con:
            return con.execute("SELECT COUNT(*) FROM locations WHERE cabinet=? AND kind='drawer'", (cab,)).fetchone()[0]
    # ตู้ตั้งต้น 1 ชุด = 6 แถว x 3 ช่อง ตามตู้จริง
    assert count("A") == 18
    assert "A-R1-C3" in c.get("/labels").text and "A-R1-C4" not in c.get("/labels").text
    # เพิ่มทีละ 1 ช่องท้ายแถว 2
    r = c.post("/cabinet/A/add-drawer", data={"row": 2}, follow_redirects=False)
    assert "A-R2-C4" in r.headers["location"] and count("A") == 19
    # เพิ่ม 2 แถว แถวละ 4 ช่อง -> R7, R8
    c.post("/cabinet/A/add-rows", data={"n_rows": 2, "per_row": 4})
    assert count("A") == 27
    with connect() as con:
        assert tuple(con.execute("SELECT rows, cols FROM cabinets WHERE name='A'").fetchone()) == (8, 4)
        assert con.execute("SELECT 1 FROM locations WHERE code='A-R8-C4' AND kind='drawer'").fetchone()
    # ลบช่องว่างได้ / ช่องที่มีของลบไม่ได้
    r = c.post("/loc/A-R8-C4/remove", follow_redirects=False)
    assert "msg=" in r.headers["location"] and count("A") == 26
    r = c.post("/loc/A-R1-C1/remove", follow_redirects=False)
    assert "warn=" in r.headers["location"] and count("A") == 26
    # ลบช่องท้ายแถว 2 แล้วเพิ่มกลับ -> ได้รหัสเดิม
    c.post("/loc/A-R2-C4/remove")
    r = c.post("/cabinet/A/add-drawer", data={"row": 2}, follow_redirects=False)
    assert "A-R2-C4" in r.headers["location"]
    # แผนผังแสดงปุ่ม + ทุกแถว
    t = c.get("/map?edit=A").text
    assert t.count('action="/cabinet/A/add-drawer"') == 8 and "cabcard editing" in t
    # ตู้ชุดใหม่ B ค่าเริ่มต้น 6x3, ชื่อซ้ำไม่ได้, ลบตู้
    c.post("/cabinet", data={"name": "b"})
    assert count("B") == 18
    r = c.post("/cabinet", data={"name": "B"}, follow_redirects=False)
    assert "warn=" in r.headers["location"] and count("B") == 18
    c.post("/cabinet/B/delete")
    assert count("B") == 0

    # ทุกหน้าเปิดได้
    for u in ["/", "/?q=bts", "/intake", "/map", "/putaway", "/labels", "/labels?mode=part",
              "/labels?only_used=1", "/loc/A-R1-C1", "/part/1", "/export.xlsx"]:
        assert c.get(u).status_code == 200, u
    assert "c_" + img in c.get("/labels?mode=part").text
print("V2 ALL OK")
