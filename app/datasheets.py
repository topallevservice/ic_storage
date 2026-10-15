"""ซิงก์คลัง datasheet (D:\\IC_Datasheets) เข้า Database อัตโนมัติ + ข้อมูลสำหรับหน้าไอซี

แหล่งข้อมูล: <DATASHEET_DIR>/<NN_หมวด>/<MPN>/spec.json  (สร้างโดยงานจัด datasheet)
- ไฟล์ใหม่/แก้ไข -> upsert ตาราง part_specs ; ไฟล์ถูกลบ -> ลบแถว
- เติมข้อมูลเบอร์ในคลังที่ยังว่าง (ผู้ผลิต / แพ็กเกจ) และหมวดที่ยังเป็น OTH (= ไม่รู้หมวด) ตาม datasheet
  ไม่ทับข้อมูลที่ผู้ใช้ใส่เองแล้ว
"""
import importlib.util
import json
import sqlite3

from .db import DATASHEET_DIR, DB_PATH, STAGING, now
from .matcher import split_names
from .parts import norm

LEVEL_TH = {"drop-in": "ใส่แทนได้ทันที", "conditional": "แทนได้แบบมีเงื่อนไข", "criteria": "เกณฑ์เลือกตัวแทน",
            "none": "ห้ามใช้แทน"}
SOURCE_TH = {"datasheet": "ยืนยันจาก datasheet", "engineering-verify": "ต้องตรวจ datasheet ตัวแทนก่อน"}


def _spec_files() -> dict[str, tuple]:
    """{mpn_norm: (path, mtime, spec)} จากทุกหมวด"""
    out = {}
    if not DATASHEET_DIR.is_dir():
        return out
    for f in sorted(DATASHEET_DIR.glob("[0-9][0-9]_*/*/spec.json")):
        try:
            mtime = f.stat().st_mtime
            spec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # ไฟล์กำลังเขียน/เสีย — รอบหน้าค่อยอ่าน
        n = norm(spec.get("mpn", ""))
        if n:
            out[n] = (f, mtime, spec)
    return out


def sync(con: sqlite3.Connection) -> dict:
    """อ่าน spec.json ที่เปลี่ยนเข้า Database ; คืนจำนวนที่เพิ่ม/แก้/ลบ"""
    files = _spec_files()
    have = {r["mpn_norm"]: (r["mtime"], r["folder"]) for r in con.execute("SELECT mpn_norm, mtime, folder FROM part_specs")}
    changed = 0
    for n, (f, mtime, s) in files.items():
        folder = f.parent.relative_to(DATASHEET_DIR).as_posix()
        if have.get(n) == (mtime, folder):
            continue
        ds = s.get("datasheet") or {}
        summary = next((p.name for p in f.parent.glob("*_สรุป.md")), "")
        con.execute(
            """INSERT INTO part_specs(mpn_norm, mpn, category, manufacturer, family, package, function_th, function_en,
                                      spec_json, folder, pdf, summary, mtime, synced_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(mpn_norm) DO UPDATE SET mpn=excluded.mpn, category=excluded.category,
                 manufacturer=excluded.manufacturer, family=excluded.family, package=excluded.package,
                 function_th=excluded.function_th, function_en=excluded.function_en, spec_json=excluded.spec_json,
                 folder=excluded.folder, pdf=excluded.pdf, summary=excluded.summary, mtime=excluded.mtime,
                 synced_at=excluded.synced_at""",
            (n, s.get("mpn", ""), s.get("category", ""), s.get("manufacturer", ""), s.get("family", ""),
             (s.get("package") or {}).get("name", ""), s.get("function_th", ""), s.get("function", ""),
             json.dumps(s, ensure_ascii=False), folder, ds.get("file") or "", summary, mtime, now()))
        changed += 1
    gone = [n for n in have if n not in files]
    for n in gone:
        con.execute("DELETE FROM part_specs WHERE mpn_norm=?", (n,))
    fill_parts(con)
    return {"changed": changed, "removed": len(gone)}


def fill_parts(con: sqlite3.Connection) -> None:
    """เติมข้อมูลเบอร์ในคลังจาก datasheet: เฉพาะช่องที่ยังว่าง และหมวดที่ยังเป็น OTH
    (UPDATE มีเงื่อนไข -> ไม่มีอะไรต้องแก้ = ไม่มีแถวเปลี่ยน = ไม่กระตุ้นให้หน้าจอรีเฟรชเปล่า ๆ)"""
    con.execute("""UPDATE parts SET category=(SELECT s.category FROM part_specs s WHERE s.mpn_norm=parts.mpn_norm),
                          updated_at=?
                   WHERE category='OTH' AND EXISTS(SELECT 1 FROM part_specs s WHERE s.mpn_norm=parts.mpn_norm
                     AND s.category<>'' AND s.category<>'OTH'
                     AND s.category IN (SELECT code FROM categories))""", (now(),))
    for col in ("manufacturer", "package"):
        con.execute(f"""UPDATE parts SET {col}=(SELECT s.{col} FROM part_specs s WHERE s.mpn_norm=parts.mpn_norm),
                               updated_at=?
                        WHERE ({col} IS NULL OR {col}='') AND EXISTS(SELECT 1 FROM part_specs s
                          WHERE s.mpn_norm=parts.mpn_norm AND s.{col}<>'')""", (now(),))


def _stock_by_norm(con, n: str) -> dict | None:
    """เบอร์ในคลังที่ตรงกับชื่อนี้ (MPN หรือชื่อเรียกอื่น) พร้อมยอดและช่อง"""
    if not n:
        return None
    p = con.execute("SELECT id, mpn FROM parts WHERE mpn_norm=?", (n,)).fetchone() or con.execute(
        "SELECT p.id, p.mpn FROM part_aliases a JOIN parts p ON p.id=a.part_id WHERE a.alias_norm=?", (n,)).fetchone()
    if not p:
        return None
    locs = con.execute("""SELECT loc.code, SUM(l.qty) AS q FROM stock_lots l JOIN locations loc ON loc.id=l.location_id
                          WHERE l.part_id=? AND l.qty>0 GROUP BY loc.code""", (p["id"],)).fetchall()
    return {"id": p["id"], "mpn": p["mpn"], "qty": sum(r["q"] for r in locs),
            "locs": ["รอระบุช่อง" if r["code"] == STAGING else r["code"] for r in locs]}


def for_part(con, mpn_norm: str) -> dict | None:
    """ข้อมูล datasheet ของเบอร์นี้สำหรับหน้าไอซี — ตัวแทนแต่ละตัวบอกยอดในคลังแบบสด"""
    row = con.execute("SELECT * FROM part_specs WHERE mpn_norm=?", (mpn_norm,)).fetchone()
    if not row:
        return None
    s = json.loads(row["spec_json"])
    subs = []
    for x in s.get("substitutes") or []:
        # ชื่อรวมหลายเบอร์ เช่น "OPA2348AIDR / TLV9062IDR" -> ตัวแรกที่มีในคลัง
        stock = next((st for n in [norm(x.get("mpn", ""))] + split_names(x.get("mpn", ""))
                      if (st := _stock_by_norm(con, n))), None)
        subs.append(dict(x, level_th=LEVEL_TH.get(x.get("level"), x.get("level", "")),
                         source_th=SOURCE_TH.get(x.get("source"), x.get("source", "")), stock=stock))
    base = f"/datasheets/{row['folder']}/"
    return {"spec": s, "subs": subs, "folder": row["folder"], "synced_at": row["synced_at"],
            "pdf_url": base + row["pdf"] if row["pdf"] else "", "summary_url": base + row["summary"] if row["summary"] else ""}


def rebuild_index(db_path=DB_PATH) -> bool:
    """สร้าง 00_INDEX.md / ic_specs.json ของคลัง datasheet ใหม่ (ยอดคงเหลือล่าสุดจาก Database)"""
    tool = DATASHEET_DIR / "_tools" / "build_index.py"
    if not tool.exists():
        return False
    spec = importlib.util.spec_from_file_location("ic_build_index", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DB = str(db_path)
    mod.ROOT = str(DATASHEET_DIR)
    mod.main()
    return True
