"""ทดสอบ v18: รับของจำนวน 0 ไม่ได้ + ตำแหน่งในหน้ารับของลิงก์ไปช่องในแผนผัง (กันดูผิดตู้)"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect

with TestClient(app) as c:
    with connect() as con:
        loc = con.execute("SELECT code FROM locations WHERE kind='drawer' ORDER BY cabinet, row, col").fetchone()[0]
    # 1) จำนวน 0 / ติดลบ -> ไม่บันทึก มีคำเตือน
    for q in ("0", "-3"):
        r = c.post("/intake", data={"mpn": "TESTV18", "qty": q, "category": "OTH", "location": loc}, follow_redirects=False)
        assert r.status_code == 303 and "warn=" in r.headers["location"], r.headers.get("location")
    with connect() as con:
        assert con.execute("SELECT COUNT(*) FROM stock_lots").fetchone()[0] == 0
    # 2) จำนวนปกติ -> บันทึก ; รายการล่าสุดมีลิงก์ไปช่องในแผนผัง
    r = c.post("/intake", data={"mpn": "TESTV18", "qty": "5", "category": "OTH", "location": loc})
    assert r.status_code == 200 and f'href="/map?hl={loc}"' in r.text
    assert 'id="f_qty" min="1"' in r.text and "intake.js?v=17" in r.text
    # 3) แผนผัง: ช่องที่ถูกชี้มีอยู่ + มีสคริปต์ไฮไลต์
    r = c.get(f"/map?hl={loc}")
    assert f'href="/loc/{loc}"' in r.text and 'get("hl")' in r.text and "style.css?v=31" in r.text

js = open(os.path.join(ROOT, "app/static/intake.js"), encoding="utf-8").read()
assert "locLink(item.locked_loc)" in js and "ดูช่องนี้ในแผนผังตู้" in js and "locWords(" in js
print("v18 OK")
# 4) ป้ายช่องบนแผนผังมีอักษรตู้ (C-R6-C1 ไม่ใช่แค่ R6-C1)
assert '<b class="cabl">' in open(os.path.join(ROOT, "app/templates/map.html"), encoding="utf-8").read()
