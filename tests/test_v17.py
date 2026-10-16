"""ทดสอบ v17: iPhone ส่งรูปแล้ว "การเชื่อมต่อขาดไป" — สาย keep-alive ยาวพอ + ส่งรูปแบบลองซ้ำเอง"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 1) ตัวเปิดโปรแกรม: เซิร์ฟเวอร์ไม่ตัดสายว่างเร็วเกินไป (ค่าเดิม 5 วิ = สั้นกว่าเวลาเปิดกล้องถ่ายรูป)
bat = open(os.path.join(ROOT, "start_ic_storage.bat"), encoding="utf-8").read()
assert "--timeout-keep-alive 300" in bat

# 2) ทุกหน้าที่ส่งรูป ใช้ Net (ลองซ้ำ 1 ครั้ง + เวลาจำกัด) ; ฟอร์มเพิ่มรูปส่งผ่าน requestSubmit (ให้ ui.js ดักได้)
ui = open(os.path.join(ROOT, "app/static/ui.js"), encoding="utf-8").read()
assert "window.Net" in ui and "multipart/form-data" in ui
T = lambda n: open(os.path.join(ROOT, "app/templates", n), encoding="utf-8").read()
assert "Net.json(" in T("home.html") and "Net.json(" in T("_marking_input.html")
assert "requestSubmit" in T("_gallery.html")

# 3) ฟอร์มเพิ่มรูป: ส่งแบบ fetch แล้วตามไปหน้าผลลัพธ์ได้ (เซิร์ฟเวอร์ตอบ redirect 303 -> หน้าเบอร์)
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect
import io
from PIL import Image
with TestClient(app) as c:
    with connect() as con:
        con.execute("INSERT INTO parts(mpn, mpn_norm, category) VALUES('TESTV17','TESTV17','OTH')")
        pid = con.execute("SELECT id FROM parts WHERE mpn='TESTV17'").fetchone()[0]
    buf = io.BytesIO(); Image.new("RGB", (400, 300), "white").save(buf, "JPEG")
    r = c.post(f"/part/{pid}/image", files={"photo": ("a.jpg", buf.getvalue(), "image/jpeg")}, data={"back": f"/part/{pid}"})
    assert r.status_code == 200 and r.url.path == f"/part/{pid}", (r.status_code, r.url)
    assert "/static/ui.js?v=4" in r.text

    # 4) ส่งซ้ำด้วยรหัสคำขอเดิม (สายหลุดแล้วส่งใหม่ / กดบันทึกซ้ำ) -> ตอบผลเดิม ไม่บันทึกซ้ำ ; รหัสใหม่ = บันทึกปกติ
    def bags():
        with connect() as con:
            return con.execute("SELECT COUNT(*), COALESCE(SUM(qty),0) FROM stock_lots WHERE part_id=?", (pid,)).fetchone()[:]
    form = {"mpn": "TESTV17", "qty": "7", "category": "OTH", "location": "STAGING", "supplier": "ร้านไทย"}
    r1 = c.post("/intake", data=form, headers={"X-Req-Id": "abc-1"}, follow_redirects=False)
    r2 = c.post("/intake", data=form, headers={"X-Req-Id": "abc-1"}, follow_redirects=False)
    assert r1.status_code == 303 and r2.status_code == 303 and r1.headers["location"] == r2.headers["location"]
    assert tuple(bags()) == (1, 7), bags()
    c.post("/intake", data=form, headers={"X-Req-Id": "abc-2"}, follow_redirects=False)
    assert tuple(bags()) == (2, 14), bags()
    c.post("/intake", data=form, follow_redirects=False)   # ไม่มีรหัส = ทำงานเหมือนเดิม
    assert tuple(bags()) == (3, 21), bags()
    # รูปซ้ำรหัสเดิม -> ไม่เพิ่มรูปซ้ำ
    n0 = c.get(f"/part/{pid}").text.count('class="gimg')
    for _ in range(2):
        c.post(f"/part/{pid}/image", files={"photo": ("b.jpg", buf.getvalue(), "image/jpeg")}, data={"back": f"/part/{pid}"},
               headers={"X-Req-Id": "img-1"})
    assert c.get(f"/part/{pid}").text.count('class="gimg') == n0 + 1

# 5) หน้าเว็บ: ฟอร์มบันทึกทุกฟอร์มส่งผ่าน Net + จำงานรับของที่ค้าง + พากลับหน้าล่าสุด
assert "X-Req-Id" in ui and "ic_last" in ui and "pendbar" in ui
js = open(os.path.join(ROOT, "app/static/intake.js"), encoding="utf-8").read()
assert "restoreDraft" in js and "saveDraft" in js
assert 'data-draft="ic_intake_draft"' in T("intake.html")
print("V17 ALL OK")
