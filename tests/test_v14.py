"""ทดสอบ v14: OCR แยก ตัวอักษร / ตัวเลข / โลโก้ผู้ผลิต (ตัวชิป + ฉลาก) — ใช้รูปจริงในคลัง (อ่านอย่างเดียว)
วัดความแม่นทั้งชุด (scratchpad eval_chip.py / eval_label.py, 2026-10-15):
  ตัวชิป 70.0% -> 78.9% (คำขยะ 17 -> 9) ; ฉลาก (OCR อย่างเดียว ไม่ใช้ QR) เบอร์ถูก 58% -> 87%"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path

from PIL import Image
from fastapi.testclient import TestClient
from app import barcodes, identify
from app.ocrtext import digits, junk, kind, mfr_fuzzy, mfr_of, find_mfr
from app.main import app
from app.db import connect

IMG = Path(r"D:\IC_Storage\data\part_images")
LBL = Path(r"D:\IC_Storage\data\label_photos")

# 1) แยกชนิดคำ
assert digits("1O5") == "105" and digits("Q:2O") == "020"
assert mfr_of("Nexperialnlonmluas") == "Nexperia" and mfr_of("STMiCro") == "STMicroelectronics" and mfr_of("Wurth").startswith("Würth")
assert mfr_fuzzy("Inrineon") == ("Infineon", "Infineon") and mfr_fuzzy("TI")[0] == "Texas Instruments"
assert mfr_fuzzy("OPA") is None and mfr_fuzzy("AHC") is None
assert junk("OTUInALUUUnNA") and junk("OLML立") and not junk("SGM721XN5") and not junk("TLV70012QDDCRQ1")
assert kind("2531") == "date" and kind("NXP") == "mfr" and kind("2348A") == "code" and kind("721") == "digits"
assert find_mfr(["Regulators - Linear", "Toshiba OTUInALUUUnNA"]) == "Toshiba"


# 2) แยกโลโก้ออกจากบรรทัด (ตำแหน่งตัวอักษรจริงจาก OCR)
def line(text, chars):
    return {"text": text, "score": 0.95, "chars": chars, "y": 0, "h": 50}


t, lg, _ = identify.clean_line(line("4EB5", [("4", .75, 168, 212), ("E", 1, 233, 268), ("B", 1, 268, 300), ("5", 1, 300, 342)]))
assert t == "EB5", t                                   # โลโก้ Diodes อ่านเป็น "4" มีช่องว่างคั่น -> ตัดทิ้ง
t, lg, _ = identify.clean_line(line("@423", [("@", .44, 236, 273), ("4", 1, 285, 316), ("2", 1, 316, 347), ("3", 1, 347, 379)]))
assert t == "423", t
t, lg, _ = identify.clean_line(line("TI57ALLGM", [("T", 1, 151, 182), ("I", 1, 182, 214), ("5", 1, 241, 272), ("7", 1, 272, 304),
                                                 ("A", 1, 311, 342), ("L", .67, 342, 372), ("L", 1, 372, 403), ("G", 1, 420, 452), ("M", 1, 452, 483)]))
assert t == "TI 57ALLGM" and lg == "Texas Instruments", (t, lg)   # โลโก้ TI ติดข้อความ -> แยกคำ
t, lg, items = identify.clean_line(line("OLML立", []))
assert t == "" and items[0]["kind"] == "logo"
t, lg, _ = identify.clean_line(line("Inrineon", []))
assert t == "Infineon" and lg == "Infineon"
# ช่องไฟในเบอร์ไม่สม่ำเสมอ ห้ามตัดคำ (SIT10 44T/3)
t, lg, _ = identify.clean_line(line("SIT1044T/3", [("S", 1, 195, 214), ("I", 1, 214, 233), ("T", 1, 233, 259), ("1", 1, 259, 285), ("0", 1, 285, 310),
                                                  ("4", 1, 323, 349), ("4", 1, 349, 374), ("T", 1, 387, 413), ("/", 1, 413, 432), ("3", 1, 432, 451)]))
assert t == "SIT1044T/3", t

# 3) รูปตัวชิปจริง
r = identify.read_chip(Image.open(IMG / "c_2026-10-14_a478b7f7.jpg").convert("RGB"))   # OPA2348: TI OPA / 2348A / 53ERZ
assert r["logo"] == "Texas Instruments" and "2348A" in r["tokens"], r
r = identify.read_chip(Image.open(IMG / "c_2026-10-15_e262212d.jpg").convert("RGB"))   # AS431: (โลโก้) EB5
assert "EB5" in r["tokens"] and "4EB5" not in r["tokens"], r
r = identify.read_chip(Image.open(IMG / "c_2026-10-14_a5d13ff1.jpg").convert("RGB"))   # BTS724G: Infineon (อ่านได้ Inrineon)
assert r["logo"] == "Infineon" and "BTS724G" in r["tokens"], r

# 4) ฉลากจริง (OCR อย่างเดียว)
o = barcodes.ocr_label(str(LBL / "2026-10-15_5295e2e0.jpg"))       # LCSC: OCR อ่าน "MPN" เป็น "NPM"
assert o["mpn"] == "TLV70012QDDCRQ1" and o["qty"] == 10 and o["supplier_pn"] == "C2873043", o
o = barcodes.ocr_label(str(LBL / "2026-10-14_7d25c9c6.jpg"))       # Mouser: 595-TCAN1051VDRQ1
assert o["mpn"] == "TCAN1051VDRQ1" and o["supplier"] == "Mouser" and o["supplier_pn"] == "595-TCAN1051VDRQ1", o
o = barcodes.ocr_label(str(LBL / "2026-10-14_97763ac1.jpg"))       # Würth (Mouser) 760301302
assert o["mpn"] == "760301302" and o["manufacturer"].startswith("Würth") and "RLESOLMM" not in o["description"], o
o = barcodes.ocr_label(str(LBL / "2026-10-15_092c6dfb.jpg"))       # Toshiba: คำอธิบายไม่มีคำขยะจากโลโก้
assert o["manufacturer"] == "Toshiba" and "OTUIn" not in o["description"], o
o = barcodes.ocr_label(str(LBL / "2026-10-14_e4087647.jpg"), {identify.canon("ATA6570-GNQW0"): "ATA6570-GNQW0"})
assert o["mpn"] == "ATA6570-GNQW0", o                               # OCR อ่าน GNQWO -> เบอร์ที่รู้จักแก้เป็น 0

# 5) ค้นจากรูป: คำที่ OCR อ่านผิด 1 ตัว = "น่าจะใช่" ; คำที่พิมพ์เองไม่เดา
with TestClient(app) as c:
    c.post("/intake", data={"mpn": "ATA6570-GNQW0", "qty": 3, "location": "A-R1-C1", "manufacturer": "Microchip",
                            "marking": "Atmel 133 / ATA6570H / 2133CUS"})
    from app import matcher
    with connect() as con:
        r = matcher.match(con, tokens=["2133CU9"], logo="Microchip (Atmel)")
        top = r["matches"][0]
        assert top["mpn"] == "ATA6570-GNQW0" and 0.6 <= top["score"] < 0.85, top
        assert any("ต่าง 1 ตัว" in e["text"] for e in top["evidence"]) and any("โลโก้ผู้ผลิตตรง" in e["text"] for e in top["evidence"])
        assert matcher.match(con, "2133CU9")["matches"] == []
    d = c.post("/api/ic-photo", files={"photo": ("ic.jpg", (IMG / "2026-10-14_a478b7f7.jpg").open("rb"), "image/jpeg")}).json()
    assert d["logo"] == "Texas Instruments" and "2348A" in d["tokens"], d
print("V14 ALL OK")
