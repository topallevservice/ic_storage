"""ฐานข้อมูล SQLite ของ IC Storage"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("IC_STORAGE_DATA", ROOT / "data"))  # ทดสอบ: ชี้ไปโฟลเดอร์อื่นได้
DB_PATH = DATA / "ic_storage.db"
BACKUP_DIR = ROOT / "backup" if "IC_STORAGE_DATA" not in os.environ else DATA / "backup"
PHOTO_DIR = DATA / "label_photos"   # รูปฉลากถุง
IMAGE_DIR = DATA / "part_images"    # รูปตัวไอซี
# คลัง datasheet แยกหมวด (ทดสอบ: ใช้โฟลเดอร์ว่างใน DATA ไม่แตะของจริง)
DATASHEET_DIR = Path(os.environ.get("IC_DATASHEETS") or
                     (DATA / "datasheets" if "IC_STORAGE_DATA" in os.environ else r"D:\IC_Datasheets"))

STAGING = "STAGING"  # จุดพักของที่สแกนแล้วแต่ยังไม่ได้ใส่ลิ้นชัก

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories(
    code TEXT PRIMARY KEY, name_th TEXT, name_en TEXT, sort INTEGER, color TEXT);

CREATE TABLE IF NOT EXISTS parts(
    id INTEGER PRIMARY KEY,
    mpn TEXT NOT NULL,
    mpn_norm TEXT NOT NULL UNIQUE,
    manufacturer TEXT DEFAULT '',
    category TEXT DEFAULT 'OTH' REFERENCES categories(code),
    package TEXT DEFAULT '',
    description TEXT DEFAULT '',
    marking TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS part_aliases(
    part_id INTEGER REFERENCES parts(id) ON DELETE CASCADE,
    alias_norm TEXT NOT NULL,
    kind TEXT,                       -- LCSC / DIGIKEY / MOUSER / MARKING / OTHER
    UNIQUE(part_id, alias_norm));

CREATE TABLE IF NOT EXISTS locations(
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,       -- A-R1-C2
    cabinet TEXT, row INTEGER, col INTEGER,
    kind TEXT DEFAULT 'drawer',      -- drawer / staging
    capacity INTEGER DEFAULT 12,     -- หน่วยถุง
    note TEXT DEFAULT '');

-- 1 แถว = 1 ถุง/รีล (lot)
CREATE TABLE IF NOT EXISTS stock_lots(
    id INTEGER PRIMARY KEY,
    part_id INTEGER NOT NULL REFERENCES parts(id),
    location_id INTEGER NOT NULL REFERENCES locations(id),
    planned_location_id INTEGER REFERENCES locations(id),
    qty INTEGER NOT NULL DEFAULT 0,
    size_units INTEGER DEFAULT 1,    -- 1 ถุงเล็ก, 2 ถุงใหญ่, 3 รีล
    supplier TEXT DEFAULT '', supplier_pn TEXT DEFAULT '',
    lot TEXT DEFAULT '', date_code TEXT DEFAULT '', order_no TEXT DEFAULT '',
    coo TEXT DEFAULT '', source TEXT DEFAULT 'new',
    raw_code TEXT DEFAULT '', photo TEXT DEFAULT '',
    received_at TEXT);

CREATE TABLE IF NOT EXISTS transactions(
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,              -- IN / OUT / RETURN / ADJUST / MOVE / SCRAP
    part_id INTEGER REFERENCES parts(id),
    lot_id INTEGER REFERENCES stock_lots(id),
    location_id INTEGER REFERENCES locations(id),
    qty INTEGER NOT NULL,
    job_ref TEXT DEFAULT '', note TEXT DEFAULT '', user TEXT DEFAULT '');

CREATE INDEX IF NOT EXISTS ix_lots_part ON stock_lots(part_id);
CREATE INDEX IF NOT EXISTS ix_lots_loc ON stock_lots(location_id);
CREATE INDEX IF NOT EXISTS ix_tx_part ON transactions(part_id, ts);
CREATE INDEX IF NOT EXISTS ix_alias ON part_aliases(alias_norm);
"""

CATEGORIES = [
    # code, ไทย, English, สี
    ("GD", "เกตไดรเวอร์ / Isolator / Opto", "Gate driver / Isolator / Optocoupler", "#e8590c"),
    ("PM", "ไอซีจ่ายไฟ LDO / DC-DC / Ref", "Power management", "#d6336c"),
    ("SW", "High/Low-side switch / Load driver", "Smart switch / Load driver", "#ae3ec9"),
    ("IF", "สื่อสาร CAN / LIN / RS485", "Interface transceiver", "#7048e8"),
    ("AFE", "BMS AFE / Battery monitor", "BMS AFE", "#4263eb"),
    ("MCU", "MCU / Memory / Logic", "MCU / Memory / Logic", "#1c7ed6"),
    ("AN", "Op-amp / Comparator / ADC / Current sense", "Analog", "#1098ad"),
    ("SEN", "เซนเซอร์ Hall / อุณหภูมิ / Resolver", "Sensor", "#0ca678"),
    ("MOS", "MOSFET", "MOSFET", "#37b24d"),
    ("IGBT", "IGBT / Power module", "IGBT", "#74b816"),
    ("DIO", "ไดโอด / Zener / TVS", "Diode / Zener / TVS", "#f59f00"),
    ("BJT", "ทรานซิสเตอร์ BJT", "BJT", "#f76707"),
    ("XTL", "คริสตัล / ออสซิลเลเตอร์", "Crystal / Oscillator", "#868e96"),
    ("RES", "ตัวต้านทาน", "Resistor", "#a9744f"),
    ("CAP", "ตัวเก็บประจุ", "Capacitor", "#5c7cfa"),
    ("IND", "ตัวเหนี่ยวนำ / Ferrite / หม้อแปลง", "Inductor / Ferrite", "#495057"),
    ("CON", "คอนเนกเตอร์ / รีเลย์ / ฟิวส์", "Connector / Relay / Fuse", "#343a40"),
    ("OTH", "อื่นๆ", "Other", "#adb5bd"),
]

# ตู้ลิ้นชัก 1 ชุด (ตามรูปตู้จริง): 6 แถว x 3 ช่อง = 18 ลิ้นชัก
DEFAULT_ROWS, DEFAULT_COLS = 6, 3
DEFAULT_CABINETS = [("A", DEFAULT_ROWS, DEFAULT_COLS)]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def loc_code(cabinet: str, row: int, col: int) -> str:
    return f"{cabinet}-R{row}-C{col}"


def init_db() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript(SCHEMA)
        for i, (code, th, en, color) in enumerate(CATEGORIES):
            con.execute(
                "INSERT OR IGNORE INTO categories(code,name_th,name_en,sort,color) VALUES(?,?,?,?,?)",
                (code, th, en, i, color))
        con.execute(
            "INSERT OR IGNORE INTO locations(code,cabinet,kind,capacity,note) VALUES(?,?,?,?,?)",
            (STAGING, "", "staging", 100000, "ของที่สแกนแล้ว รอใส่ลิ้นชัก"))
        _migrate(con)
        if con.execute("SELECT COUNT(*) FROM cabinets").fetchone()[0] == 0:
            known = con.execute("SELECT cabinet, MAX(row), MAX(col) FROM locations WHERE kind='drawer' "
                                "GROUP BY cabinet").fetchall()
            for cab, rows, cols in (known or DEFAULT_CABINETS):
                con.execute("INSERT INTO cabinets(name,rows,cols) VALUES(?,?,?)", (cab, rows, cols))
                if not known:
                    create_rect(con, cab, rows, cols)


def _migrate(con: sqlite3.Connection) -> None:
    """เพิ่มคอลัมน์/ตารางใหม่ให้ฐานข้อมูลเดิม"""
    cols = {r[1] for r in con.execute("PRAGMA table_info(parts)")}
    if "image" not in cols:
        con.execute("ALTER TABLE parts ADD COLUMN image TEXT DEFAULT ''")
    con.execute("""CREATE TABLE IF NOT EXISTS cabinets(
        name TEXT PRIMARY KEY, rows INTEGER NOT NULL, cols INTEGER NOT NULL,
        split_after INTEGER DEFAULT 0,   -- เว้นช่องว่างในแผนผังหลังคอลัมน์นี้ (ตู้ 2 ชุดวางติดกัน)
        capacity INTEGER DEFAULT 12, note TEXT DEFAULT '')""")
    # หลายรูปต่อ 1 เบอร์ (parts.image = รูปหลัก)
    con.execute("""CREATE TABLE IF NOT EXISTS part_images(
        id INTEGER PRIMARY KEY, part_id INTEGER NOT NULL REFERENCES parts(id) ON DELETE CASCADE,
        file TEXT NOT NULL, created_at TEXT)""")
    if "sort" not in {r[1] for r in con.execute("PRAGMA table_info(part_images)")}:
        con.execute("ALTER TABLE part_images ADD COLUMN sort INTEGER DEFAULT 0")  # ลำดับรูป (ตัวแรก = รูปหลัก)
    con.execute("""INSERT INTO part_images(part_id, file, created_at)
                   SELECT id, image, updated_at FROM parts p WHERE image<>'' AND image IS NOT NULL
                     AND NOT EXISTS(SELECT 1 FROM part_images i WHERE i.part_id=p.id AND i.file=p.image)""")
    # ข้อมูลจาก datasheet (ซิงก์อัตโนมัติจาก D:\IC_Datasheets\<หมวด>\<MPN>\spec.json) — ผูกกับเบอร์ด้วย mpn_norm
    con.execute("""CREATE TABLE IF NOT EXISTS part_specs(
        mpn_norm TEXT PRIMARY KEY, mpn TEXT NOT NULL, category TEXT, manufacturer TEXT, family TEXT,
        package TEXT, function_th TEXT, function_en TEXT, spec_json TEXT NOT NULL,
        folder TEXT, pdf TEXT, summary TEXT, mtime REAL, synced_at TEXT)""")
    # เลขรุ่นข้อมูล (rev): ทุกการเขียนตารางหลักเพิ่มเลขนี้ -> หน้าจอทุกเครื่องรู้ทันทีว่าต้องอัปเดต
    con.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
    con.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('rev', 0)")
    for t in REV_TABLES:
        for op in ("INSERT", "UPDATE", "DELETE"):
            con.execute(f"CREATE TRIGGER IF NOT EXISTS trg_rev_{t}_{op.lower()} AFTER {op} ON {t} "
                        "BEGIN UPDATE meta SET value = value + 1 WHERE key = 'rev'; END")


REV_TABLES = ("parts", "part_aliases", "locations", "stock_lots", "transactions", "cabinets", "part_images",
              "part_specs")


def get_rev(con: sqlite3.Connection | None = None) -> int:
    """เลขรุ่นข้อมูลปัจจุบัน (เพิ่มขึ้นทุกครั้งที่มีการเขียน Database)"""
    if con is not None:
        row = con.execute("SELECT value FROM meta WHERE key='rev'").fetchone()
        return row[0] if row else 0
    c = sqlite3.connect(DB_PATH, timeout=5)
    try:
        row = c.execute("SELECT value FROM meta WHERE key='rev'").fetchone()
        return row[0] if row else 0
    finally:
        c.close()


# ---------- ลิ้นชักรายช่อง: ตู้ 1 ชุดไม่จำเป็นต้องเป็นสี่เหลี่ยม (แต่ละแถวมีกี่ช่องก็ได้) ----------

def _activate(con: sqlite3.Connection, cabinet: str, row: int, col: int) -> str:
    """สร้างลิ้นชักใหม่ หรือเปิดใช้ช่องที่เคยลบ (ประวัติเดิมยังอยู่)"""
    code = loc_code(cabinet, row, col)
    cap = con.execute("SELECT capacity FROM cabinets WHERE name=?", (cabinet,)).fetchone()
    cap = cap[0] if cap else 12
    if con.execute("SELECT 1 FROM locations WHERE code=?", (code,)).fetchone():
        con.execute("UPDATE locations SET kind='drawer' WHERE code=?", (code,))
    else:
        con.execute("INSERT INTO locations(code,cabinet,row,col,kind,capacity) VALUES(?,?,?,?,'drawer',?)",
                    (code, cabinet, row, col, cap))
    return code


def recalc_cabinet(con: sqlite3.Connection, cabinet: str) -> None:
    """อัปเดตจำนวนแถว/ช่องสูงสุดของตู้ (ใช้วาดแผนผัง)"""
    r, c = con.execute("SELECT COALESCE(MAX(row),0), COALESCE(MAX(col),0) FROM locations "
                       "WHERE cabinet=? AND kind='drawer'", (cabinet,)).fetchone()
    con.execute("UPDATE cabinets SET rows=?, cols=? WHERE name=?", (r, c, cabinet))


def create_rect(con: sqlite3.Connection, cabinet: str, rows: int, cols: int) -> None:
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            _activate(con, cabinet, r, c)
    recalc_cabinet(con, cabinet)


def add_drawer(con: sqlite3.Connection, cabinet: str, row: int) -> str:
    """เพิ่ม 1 ลิ้นชักต่อท้ายแถว"""
    last = con.execute("SELECT COALESCE(MAX(col),0) FROM locations WHERE cabinet=? AND row=? AND kind='drawer'",
                       (cabinet, row)).fetchone()[0]
    code = _activate(con, cabinet, row, last + 1)
    recalc_cabinet(con, cabinet)
    return code


def add_rows(con: sqlite3.Connection, cabinet: str, n_rows: int, per_row: int) -> list[str]:
    """เพิ่มแถวใหม่ต่อด้านล่าง n แถว แถวละ per_row ช่อง"""
    last = con.execute("SELECT COALESCE(MAX(row),0) FROM locations WHERE cabinet=? AND kind='drawer'",
                       (cabinet,)).fetchone()[0]
    codes = [_activate(con, cabinet, last + r, c) for r in range(1, n_rows + 1) for c in range(1, per_row + 1)]
    recalc_cabinet(con, cabinet)
    return codes


def drawer_busy(con: sqlite3.Connection, loc_id: int) -> bool:
    return con.execute("SELECT COUNT(*) FROM stock_lots WHERE (location_id=? AND qty>0) OR planned_location_id=?",
                       (loc_id, loc_id)).fetchone()[0] > 0


def remove_drawer(con: sqlite3.Connection, code: str) -> str | None:
    """ลบ 1 ลิ้นชัก (ต้องว่าง) คืนข้อความ error หรือ None"""
    loc = con.execute("SELECT * FROM locations WHERE code=? AND kind='drawer'", (code,)).fetchone()
    if not loc:
        return f"ไม่พบลิ้นชัก {code}"
    if drawer_busy(con, loc["id"]):
        return f"ลบ {code} ไม่ได้ ยังมีของอยู่ — ย้ายของออกก่อน"
    con.execute("UPDATE locations SET kind='retired' WHERE id=?", (loc["id"],))
    recalc_cabinet(con, loc["cabinet"])
    return None


def backup_db(keep: int = 30) -> None:
    if not DB_PATH.exists():
        return
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    target = BACKUP_DIR / f"ic_storage_{stamp}.db"
    if not target.exists():
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(target)
        src.backup(dst)
        dst.close()
        src.close()
    old = sorted(BACKUP_DIR.glob("ic_storage_*.db"))[:-keep]
    for f in old:
        f.unlink()


def staging_id(con: sqlite3.Connection) -> int:
    return con.execute("SELECT id FROM locations WHERE code=?", (STAGING,)).fetchone()[0]
