"""ทดสอบตัวแปลงโค้ด + การจัดลิ้นชัก (ใช้ DB ชั่วคราว)"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import barcodes, allocate
from app.db import init_db, connect, staging_id, now
from app.parts import norm, guess_category, find_or_create_part, add_alias, search

# Digikey DataMatrix จริงจากรูปถุงกล่องแดง (zxing คืน ␝ ␞ ␄)
DK = "[)>\u241e06\u241dP541-100KHCT-ND\u241d1PCRCW0603100KFKEA\u241d30P541-100KHCT-ND\u241dK\u241d1K93398483\u241d10K113986589\u241d9D2448\u241d1TT448YB018P\u241d11K1\u241d4LTW\u241dQ100\u241d11ZPICK\u241d12Z1180038\u241d13Z999999\u241d20Z000\u241e\u2404"
r = barcodes.parse_text(DK)
assert r["mpn"] == "CRCW0603100KFKEA" and r["qty"] == 100 and r["supplier"] == "Digikey", r
assert r["supplier_pn"] == "541-100KHCT-ND" and r["date_code"] == "2448" and r["coo"] == "TW", r

LC = "{pbn:PICK2510090043,on:GB2510090973,pc:C82656,pm:BTS724G,qty:20,mc:,cc:1,pdi:12345,hp:null,wc:JS}"
r = barcodes.parse_text(LC)
assert r["mpn"] == "BTS724G" and r["qty"] == 20 and r["supplier_pn"] == "C82656", r

MO = ">[)>\x1e06\x1dK3525981\x1d14K004\x1dP771-BZX84-C2V4-215\x1d1PBZX84-C2V4,215\x1dQ20\x1d11K0123\x1d4LCN\x1d1VNexperia\x1e\x04"
r = barcodes.parse_text(MO)
assert r["mpn"] == "BZX84-C2V4,215" and r["qty"] == 20 and r["supplier"] == "Mouser" and r["manufacturer"] == "Nexperia", r

assert norm("BZX84-C2V4,215") == "BZX84C2V4"
for mpn, cat in [("BTS724G","SW"),("BZX84-C2V4,215","DIO"),("CRCW0603100KFKEA","RES"),("1ED020I12FTA","GD"),
                 ("TJA1042T/3","IF"),("BQ79616PAPRQ1","AFE"),("IPD50N04S4L","MOS"),("TPS7B6933QDBVRQ1","PM")]:
    assert guess_category(mpn) == cat, (mpn, guess_category(mpn))

init_db()
with connect() as con:
    sid = staging_id(con)
    # 40 ถุง หลายหมวด + เบอร์ซ้ำ
    specs = [("BTS724G","SW")]*2 + [(f"BZX84-C{i}V3","DIO") for i in range(14)] + [(f"CRCW0603{i}K","RES") for i in range(20)] + [("TJA1042T","IF")]*4
    for mpn, cat in specs:
        pid, _ = find_or_create_part(con, mpn, category=cat)
        con.execute("INSERT INTO stock_lots(part_id,location_id,qty,received_at) VALUES(?,?,?,?)", (pid, sid, 10, now()))
    res = allocate.plan(con)
    assert res["placed"] == 40 and not res["unplaced"], res
    rows = con.execute("""SELECT loc.code, p.category, p.mpn FROM stock_lots l JOIN parts p ON p.id=l.part_id
                          JOIN locations loc ON loc.id=l.planned_location_id ORDER BY loc.row, loc.col""").fetchall()
    by_drawer = {}
    for code, cat, mpn in rows:
        by_drawer.setdefault(code, set()).add(cat)
    assert all(len(c) == 1 for c in by_drawer.values()), by_drawer   # ไม่ปนหมวด
    bts = {code for code, cat, mpn in rows if mpn == "BTS724G"}
    assert len(bts) == 1, bts                                         # เบอร์เดียวอยู่ลิ้นชักเดียว
    print("drawers:", {k: sorted(v) for k, v in by_drawer.items()})
    ids = [r[0] for r in con.execute("SELECT id FROM stock_lots")]
    assert allocate.confirm_putaway(con, ids) == 40
    pid = con.execute("SELECT id FROM parts WHERE mpn='BTS724G'").fetchone()[0]
    add_alias(con, pid, "C82656", "LCSC")
    hits = search(con, "c82656")
    assert hits and hits[0]["mpn"] == "BTS724G" and hits[0]["locs"], dict(hits[0])
    print("search C82656 ->", hits[0]["mpn"], hits[0]["locs"], hits[0]["total_qty"])
    assert allocate.suggest(con, pid, "SW") == hits[0]["locs"]
print("ALL OK")
