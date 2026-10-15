"""จัดตำแหน่งอัตโนมัติ: เอาถุงใน STAGING ไปวางลิ้นชัก โดยให้หมวดเดียวกันอยู่ติดกัน และเบอร์เดียวกันอยู่ลิ้นชักเดียวกัน"""
import sqlite3
from collections import defaultdict

from .db import STAGING, now, staging_id


class Drawer:
    def __init__(self, row: sqlite3.Row):
        self.id = row["id"]
        self.code = row["code"]
        self.capacity = row["capacity"]
        self.used = 0
        self.parts: set[int] = set()
        self.cats: dict[str, int] = defaultdict(int)

    @property
    def free(self) -> int:
        return self.capacity - self.used

    @property
    def main_cat(self) -> str | None:
        return max(self.cats, key=self.cats.get) if self.cats else None

    def put(self, part_id: int, cat: str, units: int) -> None:
        self.used += units
        self.parts.add(part_id)
        self.cats[cat] += units


def _load_drawers(con: sqlite3.Connection, replan: bool) -> list[Drawer]:
    drawers = [Drawer(r) for r in con.execute(
        "SELECT * FROM locations WHERE kind='drawer' ORDER BY cabinet, row, col")]
    by_id = {d.id: d for d in drawers}
    # ของที่อยู่ในลิ้นชักแล้ว (และแผนที่ค้างอยู่ ถ้าไม่ได้สั่งจัดใหม่)
    q = """SELECT l.part_id, p.category, l.size_units,
                  CASE WHEN l.planned_location_id IS NOT NULL THEN l.planned_location_id ELSE l.location_id END AS lid
           FROM stock_lots l JOIN parts p ON p.id=l.part_id
           WHERE (l.qty>0 OR l.planned_location_id IS NOT NULL)"""
    if replan:
        q += " AND l.planned_location_id IS NULL"
    for r in con.execute(q):
        d = by_id.get(r["lid"])
        if d:
            d.put(r["part_id"], r["category"], r["size_units"] or 1)
    return drawers


def _choose(drawers: list[Drawer], part_id: int, cat: str, units: int) -> Drawer | None:
    # 1) ลิ้นชักที่มีเบอร์นี้อยู่แล้ว
    for d in drawers:
        if part_id in d.parts and d.free >= units:
            return d
    # 2) ลิ้นชักของหมวดเดียวกันที่ยังว่าง (เลือกตัวท้ายสุด เพื่อให้เรียงต่อกัน)
    same = [d for d in drawers if d.main_cat == cat and d.free >= units]
    if same:
        return same[-1]
    # 3) ลิ้นชักว่างตัวถัดไป
    for d in drawers:
        if d.used == 0:
            return d
    # 4) ลิ้นชักเต็มหมดแล้ว: ฝากในลิ้นชักที่ว่างมากสุด (ปนหมวด ดีกว่าไม่มีที่)
    spare = [d for d in drawers if d.free >= units]
    return max(spare, key=lambda d: d.free) if spare else None


def plan(con: sqlite3.Connection, replan: bool = False) -> dict:
    """ใส่ planned_location_id ให้ถุงที่อยู่ STAGING; คืนสรุป"""
    sid = staging_id(con)
    if replan:
        con.execute("UPDATE stock_lots SET planned_location_id=NULL WHERE location_id=?", (sid,))
    drawers = _load_drawers(con, replan)
    lots = con.execute(
        """SELECT l.id, l.part_id, l.size_units, p.category, p.mpn FROM stock_lots l
           JOIN parts p ON p.id=l.part_id JOIN categories c ON c.code=p.category
           WHERE l.location_id=? AND l.planned_location_id IS NULL
           ORDER BY c.sort, p.mpn_norm, l.id""", (sid,)).fetchall()
    placed, unplaced = 0, []
    for lot in lots:
        units = lot["size_units"] or 1
        d = _choose(drawers, lot["part_id"], lot["category"], units)
        if d is None:
            unplaced.append(lot["mpn"])
            continue
        d.put(lot["part_id"], lot["category"], units)
        con.execute("UPDATE stock_lots SET planned_location_id=? WHERE id=?", (d.id, lot["id"]))
        placed += 1
    return {"placed": placed, "unplaced": unplaced,
            "drawers_used": sum(1 for d in drawers if d.used), "drawers_total": len(drawers)}


def suggest(con: sqlite3.Connection, part_id: int, category: str, units: int = 1) -> str | None:
    """เสนอลิ้นชักสำหรับถุงเดียว (ตอนรับของเข้าภายหลัง)"""
    d = _choose(_load_drawers(con, False), part_id, category, units)
    return d.code if d else None


def confirm_putaway(con: sqlite3.Connection, lot_ids: list[int], user: str = "") -> int:
    """ยืนยันว่าเอาถุงใส่ลิ้นชักตามแผนแล้ว -> ย้าย location จริง + บันทึก MOVE"""
    n = 0
    for lid in lot_ids:
        lot = con.execute("SELECT * FROM stock_lots WHERE id=? AND planned_location_id IS NOT NULL", (lid,)).fetchone()
        if not lot:
            continue
        con.execute("UPDATE stock_lots SET location_id=planned_location_id, planned_location_id=NULL WHERE id=?", (lid,))
        con.execute("INSERT INTO transactions(ts,type,part_id,lot_id,location_id,qty,note,user) VALUES(?,?,?,?,?,?,?,?)",
                    (now(), "MOVE", lot["part_id"], lid, lot["planned_location_id"], 0,
                     f"{STAGING} -> ลิ้นชัก", user))
        n += 1
    return n
