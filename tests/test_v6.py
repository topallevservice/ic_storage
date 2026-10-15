"""ทดสอบ v6: ไอซีไม่มีถุงฉลาก -> ถ่ายรูปตัวชิป -> หาเบอร์ซ้ำจาก marking + รูปร่าง -> ยืนยันแล้วเพิ่มจำนวน
ใช้รูปจริงจากคลัง (อ่านอย่างเดียว) กับฐานข้อมูลชั่วคราว"""
import io, os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.parse import unquote
from PIL import Image, ImageEnhance
from fastapi.testclient import TestClient
from app.main import app

SRC = r"D:\IC_Storage\data\part_images"
TOP = {"BTS724G": "2026-10-14_a5d13ff1.jpg", "ACPL-C87BT-500E": "2026-10-14_0aafde30.jpg",
       "TJA1042T/1J": "2026-10-14_d577c3ec.jpg", "ATA6570-GNQW0": "2026-10-14_18cbfa3f.jpg"}
TRAFO_BOTTOM = "2026-10-14_84d524a7.jpg"   # หม้อแปลง (ไม่มีในคลังทดสอบ)


def photo(file, rot=0, bright=1.0):
    """จำลองถ่ายรูปใหม่: หมุน + แสงต่าง"""
    im = Image.open(os.path.join(SRC, file)).convert("RGB").rotate(rot, expand=True)
    im = ImageEnhance.Brightness(im).enhance(bright)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    b.seek(0)
    return b


def identify(c, f):
    return c.post("/api/identify-ic", files={"photo": ("chip.jpg", f, "image/jpeg")}).json()


def save(c, **kw):
    return unquote(c.post("/intake", data=kw, follow_redirects=False).headers["location"])


with TestClient(app) as c:
    # คลังทดสอบ: 4 เบอร์ พร้อมรูปด้าน marking
    for k, (mpn, f) in enumerate(TOP.items()):
        img = c.post("/api/ic-photo", files={"photo": ("ic.jpg", photo(f), "image/jpeg")}).json()["image"]
        save(c, mpn=mpn, qty=10, ic_images=img, location=f"A-R{k + 1}-C1")

    # 1) ถ่ายชิป BTS724G ใหม่ (หมุน 90°, แสงต่าง) -> เบอร์นี้อันดับ 1 แบบมั่นใจ + ช่องเดิม + ยอดเดิม
    r = identify(c, photo(TOP["BTS724G"], rot=90, bright=1.2))
    top = r["candidates"][0]
    assert top["mpn"] == "BTS724G" and top["level"] == "strong", r
    assert top["locked_loc"] == "A-R1-C1" and top["qty"] == 10 and top["can_add_image"], top
    assert "BTS724G" in " ".join(r["lines"]).replace(" ", "") or top["shape"] >= 0.5
    # เบอร์อื่นต้องไม่มั่นใจเกินจริง
    assert all(x["level"] == "weak" for x in r["candidates"][1:]), r["candidates"]

    # 2) ยืนยันในป๊อปอัพ: ใส่จำนวน -> เพิ่มเข้าเบอร์เดิม ช่องเดิม + จำ marking + เก็บรูป
    loc = save(c, mpn=top["mpn"], qty=7, source="nolabel", location="STAGING", ic_images=r["image"],
               marking=" ".join(r["tokens"]))
    assert "เบอร์ซ้ำ BTS724G" in loc and "A-R1-C1" in loc and "รวม 17" in loc, loc
    j = c.get("/api/lookup", params={"mpn": "BTS724G"}).json()
    assert r["image"] in j["images"] and j["stock_qty"] == 17, j

    # 3) marking ที่ OCR อ่านเพี้ยน (ACPL-C87BT -> C878T) ยังจับได้ ; ยืนยันแล้วจำไว้ ครั้งต่อไปคะแนนสูงขึ้น
    r = identify(c, photo(TOP["ACPL-C87BT-500E"], rot=180))
    top = r["candidates"][0]
    assert top["mpn"] == "ACPL-C87BT-500E" and top["level"] == "strong", r
    save(c, mpn=top["mpn"], qty=3, marking=" ".join(r["tokens"]))
    from app.db import connect
    with connect() as con:
        mk = con.execute("SELECT marking FROM parts WHERE mpn='ACPL-C87BT-500E'").fetchone()[0]
    from app.identify import is_date_code
    keep = {t for t in r["tokens"] if not is_date_code(t)}
    assert keep and keep <= set((mk or "").split()), (r["tokens"], mk)
    assert is_date_code("G2527") and is_date_code("2531") and not is_date_code("C8781") and not is_date_code("C878T")

    # 3b) date code (ปี/สัปดาห์ผลิต) ต้องไม่ถูกจำเป็น marking — ซ้ำข้ามเบอร์ได้ จะจับผิดตัว
    save(c, mpn="BTS724G", qty=1, marking="G2527 2531 1E529361B01")
    with connect() as con:
        mk = con.execute("SELECT marking FROM parts WHERE mpn='BTS724G'").fetchone()[0].split()
    assert "G2527" not in mk and "2531" not in mk and "1E529361B01" in mk, mk

    # 4) ไม่มีอะไรเทียบได้ในคลัง -> ไม่มีผลลัพธ์ (หน้าเว็บจะเปิดฟอร์มให้กรอกเอง) ไม่เดามั่ว
    r = identify(c, photo(TRAFO_BOTTOM))
    assert r["candidates"] == [] and r["image"], r

    # 5) รูปไอซีครบ 6 รูปแล้ว -> ไม่เพิ่มรูปอีก (แต่ยังเพิ่มจำนวนได้)
    for _ in range(6):
        img = c.post("/api/ic-photo", files={"photo": ("ic.jpg", photo(TOP["TJA1042T/1J"]), "image/jpeg")}).json()["image"]
        save(c, mpn="TJA1042T/1J", qty=1, ic_images=img)
    assert len(c.get("/api/lookup", params={"mpn": "TJA1042T/1J"}).json()["images"]) == 6
    r = identify(c, photo(TOP["TJA1042T/1J"], rot=270))
    assert r["candidates"][0]["mpn"] == "TJA1042T/1J" and not r["candidates"][0]["can_add_image"], r

    # หน้า intake มีปุ่มและป๊อปอัพ
    t = c.get("/intake").text
    assert 'id="chipPhoto"' in t and 'id="idDlg"' in t and 'name="marking"' in t
print("V6 ALL OK")
