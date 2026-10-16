"""ทดสอบ v10: การเชื่อมต่อสะดุด/หลุด
- /api/events ส่งเลขรุ่นจาก Database ทันที (ไม่ช้ากว่าหน้าที่เพิ่งโหลด -> หน้าไม่โหลดซ้ำเอง)
- เบิกซ้ำด้วยรหัสคำขอเดิม (Wi-Fi หลุดหลังตัดสต๊อก แล้วกดใหม่) = ไม่ตัดสต๊อกซ้ำ
- live.js: ไม่แจ้งหลุดตอนต่อใหม่ตามรอบ, ไม่สนเลขรุ่นเก่า, ปิดสายเมื่อแท็บถูกซ่อน"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect, get_rev

ROOT = Path(__file__).resolve().parent.parent

with TestClient(app) as c:
    c.post("/intake", data={"mpn": "TJA1043T/1J", "qty": 10, "location": "A-R1-C1"}, follow_redirects=False)
    with connect() as con:
        pid = con.execute("SELECT id FROM parts WHERE mpn='TJA1043T/1J'").fetchone()[0]

    # 1) เขียนแล้วเปิดสายทันที: ข้อความแรก = เลขรุ่นล่าสุดใน Database (ไม่ใช่ค่าเก่าของเธรดเบื้องหลัง)
    for _ in range(3):
        c.post("/api/out", data={"part_id": pid, "qty": 1})
        page_rev = int(c.get("/map").text.split('data-rev="')[1].split('"')[0])
        with c.stream("GET", "/api/events", params={"lifetime": 0.5}) as s:
            first = next(l for l in s.iter_lines() if l.startswith("data:"))
        assert int(first[5:]) == page_rev == get_rev(), (first, page_rev, get_rev())

    # 2) ส่งเบิกซ้ำด้วยรหัสคำขอเดิม -> ตัดครั้งเดียว ได้ผลเดิม
    r1 = c.post("/api/out", data={"part_id": pid, "qty": 2, "req": "abc123"}).json()
    r2 = c.post("/api/out", data={"part_id": pid, "qty": 2, "req": "abc123"}).json()
    assert r1["ok"] and r2["ok"] and r2.get("repeat") and r1["left"] == r2["left"] == 5, (r1, r2)
    assert c.get(f"/api/part/{pid}/card").json()["qty"] == 5
    r3 = c.post("/api/out", data={"part_id": pid, "qty": 2, "req": "abc124"}).json()   # คำขอใหม่ = ตัดจริง
    assert r3["ok"] and r3["left"] == 3 and not r3.get("repeat"), r3
    # ส่งไม่สำเร็จ (ของไม่พอ) ไม่ถูกจำ -> แก้จำนวนแล้วส่งรหัสเดิมได้
    assert not c.post("/api/out", data={"part_id": pid, "qty": 99, "req": "x1"}).json()["ok"]
    assert c.post("/api/out", data={"part_id": pid, "qty": 1, "req": "x1"}).json()["left"] == 2

    # 3) หน้าเว็บโหลดสคริปต์รุ่นใหม่ (มือถือไม่ใช้ไฟล์เก่าใน cache)
    t = c.get("/").text
    assert "/static/live.js?v=3" in t and "/static/out.js?v=6" in t

js = (ROOT / "app/static/live.js").read_text(encoding="utf-8")
assert "rev <= known" in js, "ต้องไม่สนเลขรุ่นที่เก่ากว่า/เท่ากับที่แสดงอยู่"
assert "visibilitychange" in js and "es.close()" in js, "แท็บที่ซ่อนต้องปิดสาย"
assert "setTimeout(() => body.classList.add(\"offline\"), 5000)" in js, "แจ้งหลุดเฉพาะเมื่อต่อไม่ได้เกิน 5 วิ"
out = (ROOT / "app/static/out.js").read_text(encoding="utf-8")
assert 'fd.append("req", reqId)' in out
print("V10 ALL OK")
