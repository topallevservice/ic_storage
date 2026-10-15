"""ทดสอบ v8: อัปเดตเรียลไทม์ (เลขรุ่นข้อมูล + /api/events) + ซิงก์ datasheet เข้า Database + เติมหมวดจาก datasheet"""
import json, os, sys, tempfile, time
from pathlib import Path
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.parse import unquote
from fastapi.testclient import TestClient
from app.main import app, LIVE
from app.db import DATASHEET_DIR, connect, get_rev
from app import datasheets

# คลัง datasheet ทดสอบ (โฟลเดอร์ชั่วคราว — ไม่แตะ D:\IC_Datasheets)
assert "datasheets" in str(DATASHEET_DIR) and "IC_Datasheets" not in str(DATASHEET_DIR), DATASHEET_DIR
SPEC = {"mpn": "BTS724G", "family": "PROFET", "manufacturer": "Infineon", "category": "SW",
        "function": "Smart high-side switch", "function_th": "สวิตช์ไฮไซด์ 4 ช่อง",
        "package": {"name": "PG-DSO-20", "pins": 20}, "marking": ["BTS724G"],
        "pinout": {"1": "Vbb", "2": "GND1/2", "3": "IN1"}, "params": {"vbb_v": [5.5, 40], "ron_mohm_per_ch_max": 90},
        "features": ["4 ช่อง"], "repair_notes": ["วัด Vbb–OUT"],
        "substitutes": [{"mpn": "TJA1042T/1J", "level": "conditional", "source": "engineering-verify", "note": "ทดสอบตัวแทนที่มีในคลัง"},
                        {"mpn": "BTS716G", "level": "conditional", "source": "engineering-verify", "note": "ไม่มีในคลัง"}],
        "datasheet": {"file": "BTS724G_datasheet.pdf"}}


def write_spec(spec, cat_dir="03_SW_SmartSwitch-LoadDriver"):
    d = DATASHEET_DIR / cat_dir / spec["mpn"].replace("/", "-")
    d.mkdir(parents=True, exist_ok=True)
    (d / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    (d / "BTS724G_datasheet.pdf").write_bytes(b"%PDF-1.4 test")
    (d / f"{d.name}_สรุป.md").write_text("# test", encoding="utf-8")
    return d


def save(c, **kw):
    return unquote(c.post("/intake", data=kw, follow_redirects=False).headers["location"])


def sync():
    with connect() as con:
        return datasheets.sync(con)


with TestClient(app) as c:
    # 1) ทุกการเขียน = เลขรุ่นข้อมูลเพิ่ม ; หน้าเว็บรู้ว่าตัวเองแสดงรุ่นไหน
    r0 = get_rev()
    save(c, mpn="BTS724G", qty=10, category="OTH", location="A-R1-C1")
    save(c, mpn="TJA1042T/1J", qty=8, location="A-R1-C2")
    r1 = get_rev()
    assert r1 > r0, (r0, r1)
    t = c.get("/map").text
    assert f'data-rev="{r1}"' in t and "/static/live.js" in t and "Live.on(" in t
    assert 'data-live="manual"' in c.get("/intake").text      # หน้ารับของ: แค่แจ้ง ไม่โหลดเอง
    with connect() as con:
        bts = con.execute("SELECT id FROM parts WHERE mpn='BTS724G'").fetchone()[0]
        tja = con.execute("SELECT id FROM parts WHERE mpn='TJA1042T/1J'").fetchone()[0]
    c.post("/api/out", data={"part_id": tja, "qty": 1})
    assert get_rev() > r1

    # 2) /api/events: ส่งเลขรุ่นปัจจุบันทันที (เธรดเบื้องหลังอัปเดตทุก 0.5 วิ) ; หมดอายุแล้วปิดเอง
    deadline = time.time() + 5
    while LIVE["rev"] != get_rev() and time.time() < deadline:
        time.sleep(0.1)
    with c.stream("GET", "/api/events", params={"lifetime": 1}) as s:
        assert s.headers["content-type"].startswith("text/event-stream") and s.headers["cache-control"] == "no-store"
        lines = [l for l in s.iter_lines() if l.startswith("data:")]
    assert lines and int(lines[0][5:]) == get_rev(), (lines, get_rev())

    # 3) /api/map-state: จำนวนจริงต่อช่อง (ให้แผนผังที่เปิดค้างอัปเดตเอง)
    st = c.get("/api/map-state").json()
    assert st["drawers"]["A-R1-C1"] == [{"id": bts, "qty": 10}] and st["drawers"]["A-R1-C2"] == [{"id": tja, "qty": 7}], st
    assert st["drawers"]["A-R2-C1"] == [] and st["rev"] == get_rev()

    # 4) datasheet ใหม่ใน D:\IC_Datasheets -> เข้า Database ; เติมผู้ผลิต/แพ็กเกจที่ว่าง ; หมวด OTH -> SW
    folder = write_spec(SPEC)
    assert sync() == {"changed": 1, "removed": 0}
    with connect() as con:
        p = con.execute("SELECT category, manufacturer, package FROM parts WHERE id=?", (bts,)).fetchone()
        row = con.execute("SELECT * FROM part_specs WHERE mpn_norm='BTS724G'").fetchone()
    assert tuple(p) == ("SW", "Infineon", "PG-DSO-20"), tuple(p)
    assert row["folder"] == "03_SW_SmartSwitch-LoadDriver/BTS724G" and row["pdf"] == "BTS724G_datasheet.pdf"
    assert sync() == {"changed": 0, "removed": 0}          # ไม่เปลี่ยน = ไม่เขียนซ้ำ
    rev = get_rev(); sync(); assert get_rev() == rev        # ไม่มีอะไรเปลี่ยน = หน้าจอไม่ต้องรีเฟรช

    # หมวด/ข้อมูลที่ผู้ใช้ตั้งเองแล้ว ไม่ถูกทับ
    c.post(f"/part/{bts}/edit", data={"category": "IF", "manufacturer": "ผู้ใช้ใส่เอง", "package": "X"})
    sync()
    with connect() as con:
        assert tuple(con.execute("SELECT category, manufacturer FROM parts WHERE id=?", (bts,)).fetchone()) == ("IF", "ผู้ใช้ใส่เอง")
    c.post(f"/part/{bts}/edit", data={"category": "SW", "manufacturer": "Infineon", "package": "PG-DSO-20"})

    # 5) หน้าไอซีแสดง datasheet จาก Database: ขา, ค่า, ตัวแทน + ยอดในคลังสด, ลิงก์ PDF
    t = c.get(f"/part/{bts}").text
    assert "📄 Datasheet" in t and "สวิตช์ไฮไซด์ 4 ช่อง" in t and "GND1/2" in t and "5.5 – 40" in t
    assert f'href="/part/{tja}">TJA1042T/1J' in t and "<b>7</b> @" in t and "BTS716G" in t, "ตัวแทนต้องบอกยอดในคลัง"
    pdf = "/datasheets/03_SW_SmartSwitch-LoadDriver/BTS724G/BTS724G_datasheet.pdf"
    assert pdf in t and c.get(pdf).content.startswith(b"%PDF")
    assert "ยังไม่มี datasheet" in c.get(f"/part/{tja}").text

    # 6) แก้ spec.json -> ซิงก์ค่าใหม่ ; ลบโฟลเดอร์ -> ลบออกจาก Database
    time.sleep(0.05)
    SPEC["function_th"] = "แก้แล้ว"
    (folder / "spec.json").write_text(json.dumps(SPEC, ensure_ascii=False), encoding="utf-8")
    os.utime(folder / "spec.json", (time.time() + 5, time.time() + 5))
    assert sync()["changed"] == 1 and "แก้แล้ว" in c.get(f"/part/{bts}").text
    for f in folder.iterdir():
        f.unlink()
    folder.rmdir()
    assert sync() == {"changed": 0, "removed": 1}
    assert "ยังไม่มี datasheet" in c.get(f"/part/{bts}").text

    # 7) เบอร์ที่รับเข้าทีหลัง แต่มี datasheet อยู่ก่อน -> เติมหมวดให้เอง
    write_spec(dict(SPEC, mpn="ACPL-C87BT-500E", category="GD", manufacturer="Broadcom",
                    package={"name": "SSO-8", "pins": 8}), "01_GD_GateDriver-Isolator-Opto")
    sync()
    save(c, mpn="ACPL-C87BT-500E", qty=3, category="OTH", location="A-R2-C2")
    sync()
    with connect() as con:
        assert con.execute("SELECT category FROM parts WHERE mpn='ACPL-C87BT-500E'").fetchone()[0] == "GD"

    # 8) เธรดเบื้องหลังซิงก์เองภายในไม่กี่วินาที (ไม่ต้องเรียก sync)
    write_spec(dict(SPEC, mpn="TJA1042T/1J", category="IF"), "04_IF_Interface-CAN-LIN-RS485")
    deadline = time.time() + 8
    while time.time() < deadline:
        with connect() as con:
            if con.execute("SELECT 1 FROM part_specs WHERE mpn_norm='TJA1042T1J'").fetchone():
                break
        time.sleep(0.3)
    else:
        raise AssertionError("เธรดเบื้องหลังไม่ซิงก์ datasheet")
print("V8 ALL OK")
