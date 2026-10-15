"""ทดสอบ v13: หน้าจอกระชับ + ถามก่อนลบทุกครั้ง + ปุ่มย้อนกลับทุกหน้า
- ทุกหน้า (ยกเว้นหน้าแรก) มีปุ่ม ← ย้อนกลับ พร้อมหน้าสำรองที่เหมาะสม
- ทุกปุ่ม/ฟอร์มลบข้อมูลมี data-confirm ; ไม่มี onsubmit confirm แบบเก่า ; ยกเลิกการเบิกต้องถามก่อน
- ui.js ถามก่อนเมื่อช่องที่เคยมีข้อมูลถูกล้างจนว่าง
- ข้อความแนะนำยาว ๆ ถูกตัดออก"""
import os, re, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from urllib.parse import unquote

from fastapi.testclient import TestClient
from app.main import app
from app.db import connect

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"

# 1) ไม่มี confirm แบบเก่า (onsubmit) — ใช้ data-confirm ที่เดียว
for f in TPL.glob("*.html"):
    assert "onsubmit=" not in f.read_text(encoding="utf-8"), f.name
# ฟอร์มที่ลบข้อมูลทุกตัวต้องถามก่อน
for f in TPL.glob("*.html"):
    t = f.read_text(encoding="utf-8")
    for m in re.finditer(r"<form[^>]*action=\"[^\"]*/(delete|remove)\"[^>]*>", t, re.S):
        assert "data-confirm=" in m.group(0) and "จริง ๆ ใช่ไหม" in m.group(0), (f.name, m.group(0))

ui = (STATIC / "ui.js").read_text(encoding="utf-8")
assert "dataset.confirm" in ui and "defaultValue" in ui and "ต้องการลบจริง ๆ ใช่ไหม" in ui
out = (STATIC / "out.js").read_text(encoding="utf-8")
assert "ต้องการยกเลิกการเบิกนี้จริง ๆ ใช่ไหม" in out
assert "ต้องการเอารูปนี้ออกจริง ๆ ใช่ไหม" in (STATIC / "intake.js").read_text(encoding="utf-8")

with TestClient(app) as c:
    r = c.post("/intake", data={"mpn": "TJA1042T/1J", "qty": 5, "location": "A-R1-C1", "marking": "TJA1042"},
               follow_redirects=False)
    with connect() as con:
        pid = con.execute("SELECT id FROM parts").fetchone()[0]
        lot = con.execute("SELECT id FROM stock_lots").fetchone()[0]

    # 2) ปุ่มย้อนกลับ: ทุกหน้ายกเว้นหน้าแรก + หน้าสำรอง
    home = c.get("/").text
    assert 'id="backBtn"' not in home and "/static/ui.js?v=1" in home
    for url, fb in [(f"/part/{pid}", "/"), ("/map", "/"), ("/loc/A-R1-C1", "/map"), (f"/lot/{lot}/edit", "/intake"),
                    ("/putaway", "/map"), ("/intake", "/"), ("/labels", "/")]:
        t = c.get(url).text
        assert re.search(r'id="backBtn"[^>]*data-fallback="%s"' % re.escape(fb), t), (url, fb)
        assert "← ย้อนกลับ" in t
    t = c.get("/crop/x.jpg", params={"back": f"/part/{pid}"}).text
    assert f'data-fallback="/part/{pid}"' in t

    # 3) ลบในหน้าจริงมี data-confirm ; แก้ MPN ถามก่อนรวมเบอร์
    t = c.get(f"/lot/{lot}/edit").text
    assert 'data-confirm="ต้องการลบรายการรับเข้า TJA1042T/1J (5 ชิ้น) จริง ๆ ใช่ไหม?' in t
    assert "ถุงนี้จะย้ายไปรวมกับเบอร์นั้น" in t
    t = c.get("/map").text
    assert 'data-confirm="ต้องการลบช่อง A-R1-C2 จริง ๆ ใช่ไหม?"' in t and "ต้องการลบตู้ A ทั้งตู้จริง ๆ ใช่ไหม?" in t

    # 4) ข้อความแนะนำยาว ๆ ถูกตัด
    t = c.get("/intake").text
    for long in ("ระบบอ่านข้อมูลบนถุงให้อัตโนมัติ", "ปกติไม่ต้องแก้", "ระบบตัดกรอบชิปให้เอง", "แก้ได้ถ้านับจริงไม่ตรง"):
        assert long not in t, long
    t = c.get(f"/part/{pid}").text
    for long in ("ใช้ค้นหา / ระบุไอซีจากรูป", "ชื่ออื่นที่ค้นเจอ", "บรรทัดคั่นด้วย /", "ระบบตัดกรอบเฉพาะตัวชิปให้อัตโนมัติ"):
        assert long not in t, long
    assert "ตัวเลขมุมขวา" not in c.get("/map").text and "ไม่ได้ระบุลิ้นชัก" not in c.get("/putaway").text
    assert "ค้นหาตำแหน่งและยอดคงเหลือ" not in home

    # 5) ล้างข้อความบนชิปผ่านฟอร์ม = ช่องมีชื่อให้ ui.js แจ้งว่าลบอะไร
    assert 'data-name="ข้อความบนตัวชิป"' in c.get(f"/part/{pid}").text
print("V13 ALL OK")
