"""ทดสอบ v7: จำนวนจริงบนแผนผัง + เบิกใช้ (ตัดสต๊อก) จากแผนผัง / สแกน / ค้นเบอร์ + ยกเลิกการเบิก"""
import io, json, os, re, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from html import unescape
from urllib.parse import unquote
from PIL import Image
from fastapi.testclient import TestClient
from app.main import app
from app.db import IMAGE_DIR, PHOTO_DIR, connect

QR = "{pbn:PICK2410140001,on:GB2410140001,pc:C82656,pm:BTS724G,qty:20,mc:,cc:1,pdi:123,hp:0}"
QR2 = QR.replace("PICK2410140001", "PICK2410140002").replace("qty:20", "qty:30")
CHIP = r"D:\IC_Storage\data\part_images\2026-10-14_a5d13ff1.jpg"   # รูปจริง BTS724G (อ่านอย่างเดียว)


def save(c, **kw):
    return unquote(c.post("/intake", data=kw, follow_redirects=False).headers["location"])


def out(c, **kw):
    return c.post("/api/out", data=kw).json()


def drawer_items(c, code):
    """อ่าน data-items ของลิ้นชักจากหน้าแผนผัง"""
    t = c.get("/map").text
    m = re.search(r'data-loc="%s" data-items=\'([^\']*)\'' % code, t)
    return json.loads(unescape(m.group(1))) if m else None


with TestClient(app) as c:
    # คลัง: BTS724G 2 ถุง (20 + 30) ช่อง A-R1-C1 ; TJA1042 15 ชิ้นอยู่ช่องเดียวกัน ; ATA6570 รอระบุช่อง
    save(c, mpn="BTS724G", qty=20, raw_code=QR, supplier="LCSC", supplier_pn="C82656", location="A-R1-C1")
    save(c, mpn="BTS724G", qty=30, raw_code=QR2, location="A-R1-C1")
    save(c, mpn="TJA1042T/1J", qty=15, location="A-R1-C1")
    save(c, mpn="ATA6570-GNQW0", qty=4, location="STAGING")
    with connect() as con:
        bts = con.execute("SELECT id FROM parts WHERE mpn='BTS724G'").fetchone()[0]
        tja = con.execute("SELECT id FROM parts WHERE mpn='TJA1042T/1J'").fetchone()[0]
        ata = con.execute("SELECT id FROM parts WHERE mpn='ATA6570-GNQW0'").fetchone()[0]

    # 1) แผนผัง: ตัวเลขจำนวนจริงต่อช่อง (รวม) + ต่อไอซี ; ไม่มีแถบความจุแล้ว
    t = c.get("/map").text
    assert re.search(r'class="dq"[^>]*>65<', t), "ช่อง A-R1-C1 ต้องแสดง 65 (20+30+15)"
    items = {it["mpn"]: it["qty"] for it in drawer_items(c, "A-R1-C1")}
    assert items == {"BTS724G": 50, "TJA1042T/1J": 15}, items
    assert 'class="tq">50<' in t and 'class="tq">15<' in t
    assert 'class="bar"' not in t and "/static/out.js" in t
    assert "<title>แผนผังตู้ — IC Storage</title>" in t
    assert 'id="tapMode"' in t and 'data-m="info"' in t and f'data-pid="{bts}"' in t   # สลับไปโหมดดูข้อมูลไอซีได้

    # 2) เบิกจากแผนผัง: FIFO ถุงเก่าก่อน ข้ามถุงได้ ; จำกัดเฉพาะช่องนั้น
    r = out(c, part_id=bts, qty=25, loc="A-R1-C1", job_ref="JOB-TEST", note="U3")
    assert r["ok"] and r["left"] == 25 and r["left_here"] == 25 and len(r["tx"]) == 2, r
    assert "เบิก BTS724G 25 ชิ้น จาก A-R1-C1" in r["msg"], r
    with connect() as con:
        q = [x[0] for x in con.execute("SELECT qty FROM stock_lots WHERE part_id=? ORDER BY received_at, id", (bts,))]
        tx = con.execute("SELECT job_ref, note, qty FROM transactions WHERE type='OUT' ORDER BY id").fetchall()
    assert q == [0, 25], q                                  # ถุงแรกหมด ถุงสองเหลือ 25
    assert [tuple(x) for x in tx] == [("JOB-TEST", "U3", -20), ("JOB-TEST", "U3", -5)], [tuple(x) for x in tx]
    assert {it["mpn"]: it["qty"] for it in drawer_items(c, "A-R1-C1")}["BTS724G"] == 25

    # 3) ของไม่พอ / จำนวนผิด / ช่องผิด -> ไม่ตัด
    assert not out(c, part_id=tja, qty=16, loc="A-R1-C1")["ok"]
    assert "เหลือ 15" in out(c, part_id=tja, qty=16)["msg"]
    assert not out(c, part_id=tja, qty=0)["ok"]
    assert not out(c, part_id=tja, qty=1, loc="A-R2-C2")["ok"]     # เบอร์นี้ไม่ได้อยู่ช่องนั้น
    assert c.get(f"/api/part/{tja}/card").json()["qty"] == 15

    # 4) ยกเลิกการเบิก (กดผิด) -> คืนเข้าถุงเดิม + ลบประวัติ OUT
    r = out(c, part_id=tja, qty=15)
    assert r["ok"] and r["left"] == 0 and "หมดแล้ว" in r["msg"], r
    assert drawer_items(c, "A-R1-C1") and "TJA1042T/1J" not in {it["mpn"] for it in drawer_items(c, "A-R1-C1")}
    u = c.post("/api/out/undo", data={"tx": ",".join(map(str, r["tx"]))}).json()
    assert u["ok"] and "คืน 15" in u["msg"], u
    assert c.get(f"/api/part/{tja}/card").json()["qty"] == 15
    # ยกเลิกซ้ำ = ไม่มีรายการแล้ว ; เกินเวลา = ไม่ยอม
    assert not c.post("/api/out/undo", data={"tx": ",".join(map(str, r["tx"]))}).json()["ok"]
    r = out(c, part_id=tja, qty=1)
    with connect() as con:
        con.execute("UPDATE transactions SET ts='2000-01-01T00:00:00' WHERE id=?", (r["tx"][0],))
    u = c.post("/api/out/undo", data={"tx": str(r["tx"][0])}).json()
    assert not u["ok"] and "15 นาที" in u["msg"], u
    assert c.get(f"/api/part/{tja}/card").json()["qty"] == 14

    # 5) หน้าเบิกใช้: ค้นเบอร์ (พิมพ์บางส่วน / LCSC C-number) + ของรอระบุช่องก็เบิกได้
    s = c.get("/api/search", params={"q": "bts72"}).json()["items"]
    assert s[0]["mpn"] == "BTS724G" and s[0]["qty"] == 25 and s[0]["locs"][0]["code"] == "A-R1-C1", s
    assert c.get("/api/search", params={"q": "C82656"}).json()["items"][0]["part_id"] == bts
    card = c.get(f"/api/part/{ata}/card").json()
    assert card["locs"][0]["name"] == "รอระบุช่อง", card
    r = out(c, part_id=ata, qty=1)
    assert r["ok"] and r["loc"] == "STAGING" and "รอระบุช่อง" in r["msg"], r
    # ค้นหา + เบิก รวมเป็นแท็บเดียวที่หน้าแรก ; ลิงก์ /use เดิมยังพามาที่นี่
    t = c.get("/").text
    assert 'id="uPhoto"' in t and 'id="uChip"' in t and 'id="uQ"' in t and "ATA6570-GNQW0" in t   # เบิกล่าสุด
    assert "ค้นหา / เบิก" in t and 'href="/use"' not in t
    assert f"card({bts})" in c.get(f"/?pid={bts}").text
    r = c.get(f"/use?pid={bts}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/?pid={bts}", r.headers
    assert 'value="BTS724G"' in c.get("/", params={"q": "BTS724G"}).text and "doSearch();" in c.get("/?q=BTS724G").text
    assert f'href="/?pid={bts}"' in c.get(f"/part/{bts}").text
    assert c.get("/api/search", params={"q": "bts"}).headers["cache-control"] == "no-store"   # ยอดไม่ค้างแคช

    # 6) สแกนฉลากตอนเบิก: เจอเบอร์ในคลัง (part_id) ; ไม่เก็บรูปถ่าย (keep=0)
    it = c.post("/api/parse", data={"text": QR}).json()["items"][0]
    assert it["part_id"] == bts, it
    n_before = len(os.listdir(PHOTO_DIR))
    img = Image.new("RGB", (200, 200), "white")
    b = io.BytesIO(); img.save(b, "JPEG"); b.seek(0)
    c.post("/api/decode-image", files={"photo": ("x.jpg", b, "image/jpeg")}, data={"keep": "0"})
    assert len(os.listdir(PHOTO_DIR)) == n_before

    # 7) ถ่ายรูปชิปตอนเบิก: หาเบอร์ได้ ไม่เก็บรูป
    c.post(f"/part/{bts}/image", files={"photo": ("ic.jpg", open(CHIP, "rb"), "image/jpeg")})
    n_img = len(os.listdir(IMAGE_DIR))
    d = c.post("/api/identify-ic", files={"photo": ("chip.jpg", open(CHIP, "rb"), "image/jpeg")}, data={"keep": "0"}).json()
    assert d["candidates"] and d["candidates"][0]["part_id"] == bts and d["image"] == "", d
    assert len(os.listdir(IMAGE_DIR)) == n_img

    # 8) ฟอร์มเบิกเดิมที่หน้าเบอร์ยังใช้ได้ (ใช้ฟังก์ชันเดียวกัน)
    loc = unquote(c.post(f"/part/{bts}/out", data={"qty": 5, "tx_type": "OUT"}, follow_redirects=False).headers["location"])
    assert "OUT 5" in loc, loc
    loc = unquote(c.post(f"/part/{bts}/out", data={"qty": 999, "tx_type": "OUT"}, follow_redirects=False).headers["location"])
    assert "ของไม่พอ" in loc, loc
    assert c.get(f"/api/part/{bts}/card").json()["qty"] == 20
print("V7 ALL OK")
