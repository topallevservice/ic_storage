"""ทดสอบ v15: C / R / L ที่ตัวไม่มีพิมพ์ -> ใช้ค่าบนฉลากเป็นข้อความบนตัว (เช่น 4.7uF / 50V / ±10% / X7R / 1206)"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

from fastapi.testclient import TestClient
from app import barcodes
from app.main import app, _enrich
from app.db import connect
from app.parts import label_value, marking_tokens_of, no_print

# 1) ดึงค่าจากคำอธิบายบนฉลาก
assert label_value("CAP", "4.7uF ±10% 50V Ceramic Capacitor X7R 1206") == "4.7uF / 50V / ±10% / X7R / 1206 (ค่าจากฉลาก)"
assert label_value("RES", "10kΩ ±1% 100mW Thick Film Resistor 0603") == "10kΩ / ±1% / 100mW / 0603 (ค่าจากฉลาก)"
assert label_value("RES", "4.7 kohm 1/4W 5%").startswith("4.7kΩ / 5% / 1/4W")
assert label_value("IND", "51uH@100kHz 2Line Common Mode Choke 200mA", "EIA 1812").startswith("51uH @100kHz / 200mA / 1812")
assert label_value("IND", "600Ω@100MHz 2A Ferrite Bead 0805").startswith("600Ω @100MHz / 2A / 0805")
assert label_value("IND", "Gate drive transformer 1:1, 750 µH, 2.5 kV").startswith("750uH / 2.5kV / 1:1")
assert label_value("CAP", "Ceramic Capacitor") == ""                 # ไม่มีค่า = ไม่เดา
assert label_value("AN", "Dual 10 MHz op amp, 5.5 V") == ""          # ไอซีมีตัวหนังสือบนตัว -> ไม่ดึงค่า
assert "47UF" in marking_tokens_of(label_value("CAP", "4.7uF 50V")) and "50V" in marking_tokens_of(label_value("CAP", "4.7uF 50V"))
assert no_print("") and no_print("(ไม่มีตัวหนังสือ — จุดสีขาวที่มุม)") and not no_print("G5240 99")

# 2) ฉลากจริง LCSC: ค่าบรรทัดที่ 2 (X7R 1206) ต้องเข้าคำอธิบาย + D/C ที่ OCR อ่านเป็น DIC + COO
o = barcodes.ocr_label(r"D:\IC_Storage\data\label_photos\2026-10-15_25b074d1.jpg")
assert o["mpn"] == "GRM31CR71H475KA12L" and "X7R 1206" in o["description"] and o["package"] == "1206", o
assert o["date_code"] == "26+" and o["coo"] == "JP", o

with TestClient(app) as c:
    # 3) สแกนถุง C ใหม่ -> ช่องข้อความบนตัวเติมค่าจากฉลากให้ ; ไอซีไม่เติม
    with connect() as con:
        r = _enrich(con, dict(o))
        assert r["category"] == "CAP" and r["marking"] == "4.7uF / 50V / ±10% / X7R / 1206 (ค่าจากฉลาก)", r
        ic = _enrich(con, {"mpn": "TLV9062IDR", "description": "Dual 10 MHz op amp", "marking": ""})
        assert ic["marking"] == "", ic
    # 4) บันทึกโดยไม่กรอกข้อความบนตัว -> ระบบใส่ค่าจากฉลากให้ (เฉพาะ C/R/L)
    c.post("/intake", data={"mpn": "RC0603FR-0710KL", "qty": 100, "category": "RES", "location": "A-R1-C1",
                            "description": "10kΩ ±1% 100mW Thick Film Resistor 0603"})
    c.post("/intake", data={"mpn": "LM358DR", "qty": 5, "category": "AN", "location": "A-R1-C2",
                            "description": "Dual op amp 1 MHz"})
    with connect() as con:
        m = {r["mpn"]: r["marking"] for r in con.execute("SELECT mpn, marking FROM parts")}
        assert m["RC0603FR-0710KL"] == "10kΩ / ±1% / 100mW / 0603 (ค่าจากฉลาก)", m
        assert not m["LM358DR"], m
        pid = con.execute("SELECT id FROM parts WHERE mpn='RC0603FR-0710KL'").fetchone()["id"]
        # ค้นด้วยค่าได้
        from app.parts import search
        assert any(r["mpn"] == "RC0603FR-0710KL" for r in search(con, "10k"))
    # ผู้ใช้กรอกเองแล้ว ไม่ทับ
    c.post("/intake", data={"mpn": "GRM188R71H104KA93D", "qty": 50, "category": "CAP", "location": "A-R1-C3",
                            "description": "100nF ±10% 50V X7R 0603", "marking": "ไม่มี"})
    with connect() as con:
        assert con.execute("SELECT marking FROM parts WHERE mpn='GRM188R71H104KA93D'").fetchone()["marking"] == "ไม่มี"
        con.execute("UPDATE parts SET marking='' WHERE id=?", (pid,))
    # 5) หน้าเบอร์: ปุ่ม "ค่าจากฉลาก" แสดงเฉพาะ C/R/L ; ไอซีไม่แสดง
    h = c.get(f"/part/{pid}").text
    assert "📋 ค่าจากฉลาก" in h and 'data-v="10kΩ / ±1% / 100mW / 0603 (ค่าจากฉลาก)"' in h
    with connect() as con:
        ic_id = con.execute("SELECT id FROM parts WHERE mpn='LM358DR'").fetchone()["id"]
    assert "📋 ค่าจากฉลาก" not in c.get(f"/part/{ic_id}").text
print("V15 ALL OK")
