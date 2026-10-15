"""ทดสอบ v11: ข้อความบนตัวชิป (marking) เป็นข้อมูลหลักของไอซี
- เก็บตามที่พิมพ์บนชิป (อ่านง่าย) + ดัชนีค้นหา (ไม่สนช่องว่าง/ขีด/ตัวพิมพ์)
- date code เข้มงวด: 1042V / 2348A / LP2904 ไม่ใช่ date code
- ถ่ายรูปไอซีตอนรับของ -> อ่านข้อความบนชิปให้อัตโนมัติ ; ปุ่ม "อ่านจากรูป" หน้าไอซี/หน้าแก้ไข
- ค้นหาด้วยข้อความบนชิปเจอ (ทั้งช่องค้นหาและ /api/search) และแสดงข้อความบนชิปในผลค้นหา"""
import io, os, re, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.parse import unquote
from fastapi.testclient import TestClient
from app.main import app
from app.db import connect
from app.parts import is_date_code, marking_tokens_of
from app.identify import marking_tokens

SRC = r"D:\IC_Storage\data\part_images"

# 1) date code / token
assert is_date_code("2531") and is_date_code("G2527") and is_date_code("2329")
for real in ("1042V", "2348A", "LP2904", "1051V", "C8781", "TJA1043"):
    assert not is_date_code(real), real
assert marking_tokens_of("TI OPA / 2348A / 53ERZ (ใต้ตัว: KA608)") == ["OPA", "2348A", "53ERZ", "KA608"]
assert marking_tokens_of("NXP TJA1145T/FD / EC10004H / ZnD24281") == ["TJA1145TFD", "EC10004H", "ZND24281"]
assert marking_tokens_of("FP6296 / 51h-40L") == ["FP6296", "51H40L"]
assert marking_tokens(["721", "2531", "WT3", "TI", "CHN 348", "1042V"]) == ["721", "WT3", "348", "1042V"]


def save(c, **kw):
    return unquote(c.post("/intake", data=kw, follow_redirects=False).headers["location"])


def photo(name):
    return ("ic.jpg", open(os.path.join(SRC, name), "rb"), "image/jpeg")


def found(c, q):
    return [x["mpn"] for x in c.get("/api/search", params={"q": q}).json()["items"]]


with TestClient(app) as c:
    # 2) หน้ารับของ: ช่องข้อความบนชิปอยู่ขั้นถ่ายรูป (ไม่ซ่อนในกล่องแก้ไข)
    t = c.get("/intake").text
    assert 'id="f_marking"' in t and t.index('id="f_marking"') > t.index('id="icPhoto"'), "ช่อง marking ต้องอยู่ขั้นถ่ายรูป"
    assert t.index('id="f_marking"') > t.index("</details>")

    # 3) ถ่ายรูปไอซี -> อ่านข้อความบนชิปให้เลย
    r = c.post("/api/ic-photo", files={"photo": photo("2026-10-14_a478b7f7.jpg")}).json()   # OPA2348: TI OPA / 2348A / 53ERZ
    assert r["image"] and "2348A" in r["chip_text"].replace(" ", "") and "2348A" in r["tokens"], r
    img = r["image"]

    # 4) บันทึกพร้อมข้อความบนชิป -> เก็บแบบอ่านง่าย + ค้นได้หลายแบบ
    save(c, mpn="OPA2348AIDR", qty=10, location="A-R1-C1", ic_images=img, marking="TI OPA / 2348A / 53ERZ (ใต้ตัว: KA608)")
    save(c, mpn="FP6296", qty=8, location="A-R1-C2", marking="FP6296 / 51h-40L")
    save(c, mpn="TJA1145T/FDJ", qty=5, location="A-R1-C3", marking="NXP TJA1145T/FD / EC10004H / ZnD24281")
    with connect() as con:
        pid = con.execute("SELECT id, marking FROM parts WHERE mpn='OPA2348AIDR'").fetchone()
    assert pid["marking"] == "TI OPA / 2348A / 53ERZ (ใต้ตัว: KA608)", pid["marking"]
    pid = pid["id"]
    for q in ("2348A", "2348a", "53ERZ", "KA608", "OPA 2348A", "opa2348a"):
        assert "OPA2348AIDR" in found(c, q), q
    for q in ("51h-40L", "51H40L", "51h 40l"):
        assert found(c, q) == ["FP6296"], (q, found(c, q))
    for q in ("znd24281", "ZnD24281", "EC10004H", "TJA1145T/FD"):
        assert "TJA1145T/FDJ" in found(c, q), q
    item = c.get("/api/search", params={"q": "53ERZ"}).json()["items"][0]
    assert item["marking"].startswith("TI OPA"), item
    assert "🔤 บนชิป" in c.get("/").text   # ผลค้นหาแสดงข้อความบนชิป

    # 5) หน้าไอซี: แสดงข้อความบนชิปเด่น + กล่องแก้ไข + ปุ่มอ่านจากรูป
    t = c.get(f"/part/{pid}").text
    assert 'class="chiptext"' in t and "TI OPA / 2348A / 53ERZ" in t and "🔍 อ่านจากรูป" in t
    assert f'data-files="{img}"' in t
    with connect() as con:
        pid2 = con.execute("SELECT id FROM parts WHERE mpn='FP6296'").fetchone()[0]
    c.post(f"/part/{pid2}/marking", data={"marking": ""})
    assert "ยังไม่มีข้อความบนตัวชิป" in c.get(f"/part/{pid2}").text
    assert found(c, "51H40L") == [], "ล้างแล้วต้องค้นไม่เจอ (ดัชนีเก่าถูกลบ)"
    c.post(f"/part/{pid2}/marking", data={"marking": "FP6296 / 51h-40L"})
    assert found(c, "51h40l") == ["FP6296"]

    # 6) ฟอร์มข้อมูลอะไหล่ (ไม่มีช่อง marking แล้ว) ต้องไม่ลบข้อความบนชิป
    c.post(f"/part/{pid}/edit", data={"category": "AN", "manufacturer": "TI", "package": "SOIC-8"})
    with connect() as con:
        assert con.execute("SELECT marking FROM parts WHERE id=?", (pid,)).fetchone()[0].startswith("TI OPA")

    # 7) หน้าแก้ไขรายการรับเข้า: มีช่องข้อความบนชิป + บันทึกได้
    with connect() as con:
        lot = con.execute("SELECT id FROM stock_lots WHERE part_id=?", (pid,)).fetchone()[0]
    t = c.get(f"/lot/{lot}/edit").text
    assert 'name="marking"' in t and "🔍 อ่านจากรูป" in t
    c.post(f"/lot/{lot}/edit", data={"mpn": "OPA2348AIDR", "category": "AN", "qty": 10, "location": "A-R1-C1",
                                     "marking": "TI OPA / 2348A / 53ERZ / NEWCODE9"})
    assert "OPA2348AIDR" in found(c, "NEWCODE9")

    # 8) ปุ่มอ่านจากรูป (รูปที่มีอยู่แล้ว)
    r = c.post("/api/read-marking", data={"files": img}).json()
    assert "2348A" in r["chip_text"].replace(" ", ""), r
    assert c.post("/api/read-marking", data={"files": "../../etc/passwd"}).json()["chip_text"] == ""

    # 9) รับของซ้ำเบอร์เดิมพร้อม marking ใหม่ = ต่อท้ายเฉพาะคำใหม่ (ไม่ทับ ไม่ซ้ำ)
    save(c, mpn="OPA2348AIDR", qty=1, marking="2348A 53ERZ XYZ77 2531")
    with connect() as con:
        mk = con.execute("SELECT marking FROM parts WHERE id=?", (pid,)).fetchone()[0]
    assert mk.startswith("TI OPA / 2348A") and mk.count("2348A") == 1 and "XYZ77" in mk and "2531" not in mk, mk
    # หน้ารับของ: สแกนเบอร์เดิม -> เติมข้อความบนชิปที่บันทึกไว้ให้
    assert c.post("/api/parse", data={"text": "OPA2348AIDR"}).json()["items"][0]["marking"].startswith("TI OPA")
print("V11 ALL OK")
