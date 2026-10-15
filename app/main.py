"""IC Storage — Web app (FastAPI)  รัน: start_ic_storage.bat"""
import asyncio
import io
import json
import re
import shutil
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import barcodes, datasheets, identify, images, matcher
from .db import (DATASHEET_DIR, DB_PATH, DEFAULT_COLS, DEFAULT_ROWS, IMAGE_DIR, PHOTO_DIR, STAGING, add_drawer,
                 add_rows, backup_db, connect, create_rect, get_rev, init_db, now, remove_drawer, staging_id)
from .parts import (PASSIVE, add_alias, find_or_create_part, guess_category, label_value, marking_tokens_of, no_print, norm,
                    search, set_marking)

APP_DIR = Path(__file__).resolve().parent
app = FastAPI(title="IC Storage")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/photos", StaticFiles(directory=PHOTO_DIR, check_dir=False), name="photos")
app.mount("/images", StaticFiles(directory=IMAGE_DIR, check_dir=False), name="images")
app.mount("/datasheets", StaticFiles(directory=DATASHEET_DIR, check_dir=False), name="datasheets")
tpl = Jinja2Templates(directory=APP_DIR / "templates")


class NoCacheAPI:
    """ยอดคงเหลือเปลี่ยนตลอด: ห้ามเบราว์เซอร์จำผล /api/ (กดย้อนกลับแล้วต้องเห็นยอดล่าสุด)
    เขียนเป็น ASGI ล้วน — ไม่ไปหน่วงการสตรีม /api/events"""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.inner(scope, receive, send)

        async def send2(msg):
            if msg["type"] == "http.response.start":
                hs = [(k, v) for k, v in msg.get("headers", []) if k.lower() != b"cache-control"]
                msg = dict(msg, headers=hs + [(b"cache-control", b"no-store")])
            await send(msg)
        await self.inner(scope, receive, send2)


app.add_middleware(NoCacheAPI)

# ---------------- อัปเดตเรียลไทม์ ----------------
# เธรดเบื้องหลัง 1 ตัว: อ่านเลขรุ่นข้อมูล (rev) ทุก 0.5 วิ ให้หน้าจอที่เปิดค้างรู้ว่าต้องอัปเดต,
# ซิงก์ datasheet ที่เปลี่ยนเข้า Database ทุก 3 วิ, สร้างดัชนี datasheet ใหม่เมื่อยอดเปลี่ยน (หน่วง 10 วิ)
LIVE = {"rev": 0}
_stop = threading.Event()


def _background():
    last_sync, built_rev, last_build = 0.0, -1, 0.0
    while not _stop.is_set():
        try:
            LIVE["rev"] = get_rev()
            t = time.monotonic()
            if t - last_sync >= 3:
                last_sync = t
                with connect() as con:
                    datasheets.sync(con)
                LIVE["rev"] = get_rev()
            if LIVE["rev"] != built_rev and t - last_build >= 10:
                built_rev, last_build = LIVE["rev"], t
                datasheets.rebuild_index(DB_PATH)
        except Exception as e:  # อย่าให้เธรดตาย — ลองใหม่รอบหน้า
            print("background:", repr(e))
        _stop.wait(0.5)


@app.on_event("shutdown")
def _shutdown():
    _stop.set()


@app.on_event("startup")
def _startup():
    init_db()
    backup_db()
    # โหลดโมเดล OCR ล่วงหน้า (เบื้องหลัง) รูปแรกจะได้ไม่ช้า
    threading.Thread(target=barcodes._ocr_engine, daemon=True).start()
    with connect() as con:
        images.ensure_derivatives([r[0] for r in con.execute("SELECT file FROM part_images")])
        # ระบบไม่วางแผนลิ้นชักเองแล้ว: ผู้ใช้เป็นคนระบุช่อง -> ล้างแผนเก่าที่ยังไม่ยืนยัน
        con.execute("UPDATE stock_lots SET planned_location_id=NULL WHERE planned_location_id IS NOT NULL")
        datasheets.sync(con)
    LIVE["rev"] = get_rev()
    threading.Thread(target=_background, daemon=True, name="ic-live").start()


def categories(con):
    return con.execute("SELECT * FROM categories ORDER BY sort").fetchall()


def locname(code: str) -> str:
    """STAGING แสดงเป็น 'รอระบุช่อง'"""
    return "รอระบุช่อง" if code == STAGING else code


tpl.env.filters["locname"] = locname
tpl.env.filters["label_value"] = lambda p: label_value(p["category"], p["description"] or "", p["package"] or "")


def unassigned_count(con) -> int:
    return con.execute(
        """SELECT COUNT(DISTINCT l.part_id) FROM stock_lots l JOIN locations loc ON loc.id=l.location_id
           WHERE loc.code=? AND l.qty>0""", (STAGING,)).fetchone()[0]


def page(request: Request, name: str, **ctx):
    with connect() as con:
        ctx.setdefault("nav_unassigned", unassigned_count(con))
        ctx.setdefault("rev", get_rev(con))  # หน้านี้สร้างจากข้อมูลรุ่นไหน (เทียบกับ /api/events)
    return tpl.TemplateResponse(request, name, ctx)


def unassigned_parts(con) -> list:
    """เบอร์ที่ยังรอผู้ใช้ระบุช่อง (รวมถุงของเบอร์เดียวกัน)"""
    return con.execute(
        """SELECT p.id, p.mpn, p.category, p.description, p.image,
                  SUM(l.qty) AS qty, COUNT(*) AS bags, GROUP_CONCAT(DISTINCT l.supplier) AS suppliers,
                  MAX(l.received_at) AS received_at
           FROM stock_lots l JOIN parts p ON p.id=l.part_id JOIN locations loc ON loc.id=l.location_id
           WHERE loc.code=? AND l.qty>0
           GROUP BY p.id ORDER BY MAX(l.received_at) DESC""", (STAGING,)).fetchall()


# ---------------- หน้าแรก / ค้นหา ----------------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = "", pid: int = 0):
    """ค้นหา / เบิก (แท็บเดียว): พิมพ์เบอร์ / สแกนฉลาก / ถ่ายรูปชิป -> เห็นตำแหน่ง+ยอด -> เบิก หรือ ดูข้อมูล"""
    with connect() as con:
        stats = con.execute(
            """SELECT (SELECT COUNT(*) FROM parts) AS parts,
                      (SELECT COUNT(*) FROM stock_lots WHERE qty>0) AS lots,
                      (SELECT COUNT(*) FROM stock_lots l JOIN locations c ON c.id=l.location_id
                         WHERE c.code=? AND l.qty>0) AS staging,
                      (SELECT COUNT(*) FROM stock_lots WHERE planned_location_id IS NOT NULL) AS planned""",
            (STAGING,)).fetchone()
        cat_colors = {c["code"]: c["color"] for c in categories(con)}
        return page(request, "home.html", q=q, stats=stats, recent=recent_out(con), open_pid=pid, cat_colors=cat_colors)


# ---------------- รับของเข้า ----------------

@app.get("/intake", response_class=HTMLResponse)
def intake_page(request: Request, msg: str = "", warn: str = ""):
    with connect() as con:
        recent = con.execute(
            """SELECT l.*, p.mpn, p.category, p.image, loc.code AS loc_code FROM stock_lots l
               JOIN parts p ON p.id=l.part_id JOIN locations loc ON loc.id=l.location_id
               ORDER BY l.id DESC LIMIT 15""").fetchall()
        drawers = con.execute("SELECT code FROM locations WHERE kind IN ('drawer','staging') "
                              "ORDER BY kind DESC, cabinet, row, col").fetchall()
        cats = categories(con)
        cats_json = json.dumps({c["code"]: {"name": c["name_th"], "color": c["color"]} for c in cats}, ensure_ascii=False)
        return page(request, "intake.html", cats=cats, cats_json=cats_json, recent=recent, drawers=drawers,
                    msg=msg, warn=warn, staging=STAGING)


def existing_location(con, part_id: int) -> tuple[str, str] | None:
    """ลิ้นชักเดิมของเบอร์นี้ (ถ้ามี) — ของเบอร์เดียวกันต้องอยู่ช่องเดียวกันเสมอ
    คืน (รหัสลิ้นชัก, ข้อความอธิบาย) ; ยังไม่เคยระบุช่อง -> None"""
    row = con.execute(
        """SELECT loc.code FROM stock_lots l JOIN locations loc ON loc.id=l.location_id
           WHERE l.part_id=? AND loc.kind='drawer' ORDER BY l.qty>0 DESC, l.id DESC LIMIT 1""", (part_id,)).fetchone()
    if row:
        return row["code"], f"เบอร์นี้อยู่ที่ {row['code']} แล้ว — ใส่ถุงนี้ไว้ที่เดิม"
    return None


def waiting_bags(con, part_id: int) -> int:
    return con.execute(
        """SELECT COUNT(*) FROM stock_lots l JOIN locations loc ON loc.id=l.location_id
           WHERE l.part_id=? AND loc.code=? AND l.qty>0""", (part_id, STAGING)).fetchone()[0]


def find_existing_part(con, mpn: str, supplier_pn: str = ""):
    """หาเบอร์ซ้ำในคลัง: MPN ตรง (ตัดท้าย packing แล้ว) -> ชื่อเรียกอื่น -> เบอร์ผู้ขาย (เช่น LCSC C-number)
    คืน (แถว parts, วิธีที่จับได้) หรือ (None, "")"""
    n = norm(mpn)
    if n:
        row = con.execute("SELECT * FROM parts WHERE mpn_norm=?", (n,)).fetchone()
        if row:
            return row, "mpn"
        row = con.execute("SELECT p.* FROM part_aliases a JOIN parts p ON p.id=a.part_id WHERE a.alias_norm=?",
                          (n,)).fetchone()
        if row:
            return row, "alias"
    s = norm(supplier_pn)
    if s:
        row = con.execute("SELECT p.* FROM part_aliases a JOIN parts p ON p.id=a.part_id WHERE a.alias_norm=?",
                          (s,)).fetchone()
        if row:
            return row, "supplier_pn"
    return None, ""


def stock_of(con, pid: int) -> dict:
    """ยอดคงเหลือรวมทุกถุงของเบอร์นี้"""
    row = con.execute("SELECT COALESCE(SUM(qty),0) AS qty, COUNT(CASE WHEN qty>0 THEN 1 END) AS bags "
                      "FROM stock_lots WHERE part_id=?", (pid,)).fetchone()
    return {"qty": row["qty"], "bags": row["bags"]}


def similar_parts(con, mpn: str, limit: int = 5) -> list[dict]:
    """เบอร์ใกล้เคียงที่อาจเป็นตัวเดียวกัน (ต่างแค่ท้ายรหัสสั่งซื้อ เช่น BTS724G / BTS724GXUMA1)"""
    n = norm(mpn)
    if len(n) < 5:
        return []
    rows = con.execute(
        """SELECT p.id, p.mpn, p.image, COALESCE((SELECT SUM(qty) FROM stock_lots l WHERE l.part_id=p.id),0) AS qty,
                  (SELECT loc.code FROM stock_lots l JOIN locations loc ON loc.id=l.location_id
                    WHERE l.part_id=p.id ORDER BY loc.kind='drawer' DESC, l.qty>0 DESC, l.id DESC LIMIT 1) AS loc
           FROM parts p WHERE p.mpn_norm<>? AND (p.mpn_norm LIKE ? OR ? LIKE p.mpn_norm || '%')
             AND LENGTH(p.mpn_norm)>=5
           ORDER BY ABS(LENGTH(p.mpn_norm)-?) LIMIT ?""", (n, n + "%", n, len(n), limit)).fetchall()
    return [dict(r) for r in rows]


def _enrich(con, r: dict) -> dict:
    """เติมหมวดที่เดา + ตรวจเบอร์ซ้ำ/ถุงซ้ำ + ยอดคงเหลือ + ตำแหน่งเดิม"""
    existing, matched_by = find_existing_part(con, r.get("mpn", ""), r.get("supplier_pn", ""))
    if existing and matched_by != "mpn":
        r["scanned_mpn"] = r.get("mpn", "")
        r["mpn"] = existing["mpn"]  # ใช้เบอร์ที่มีในคลังอยู่แล้ว ไม่สร้างเบอร์ใหม่ซ้ำ
    r["matched_by"] = matched_by
    r["locked_loc"], r["locked_note"], r["image"], r["part_id"] = None, "", "", None
    r["stock_qty"], r["stock_bags"], r["similar"] = 0, 0, []
    if existing:
        r["category"] = existing["category"]
        r["existing"] = True
        r["part_id"] = existing["id"]
        r["image"] = existing["image"] or ""
        r["images"] = [i["file"] for i in part_images_of(con, existing["id"])]
        r["manufacturer"] = r.get("manufacturer") or existing["manufacturer"]
        r["description"] = existing["description"] or r.get("description", "")
        r["package"] = existing["package"] or r.get("package", "")
        r["marking"] = existing["marking"] or r.get("marking", "")   # ข้อความบนตัวชิปที่บันทึกไว้แล้ว
        loc = existing_location(con, existing["id"])
        if loc:
            r["locked_loc"], r["locked_note"] = loc
        r["waiting"] = waiting_bags(con, existing["id"])
        st = stock_of(con, existing["id"])
        r["stock_qty"], r["stock_bags"] = st["qty"], st["bags"]
        r["suggest_loc"] = None
    else:
        r["category"] = guess_category(r.get("mpn", ""), r.get("description", ""))
        r["existing"] = False
        r["suggest_loc"] = None
        r["similar"] = similar_parts(con, r.get("mpn", ""))
    # C / R / L ตัวไม่มีพิมพ์ -> ใช้ค่าบนฉลากเป็นข้อความบนตัว (ผู้ใช้แก้ได้ก่อนบันทึก)
    if r["category"] in PASSIVE and no_print(r.get("marking", "")):
        r["marking"] = label_value(r["category"], r.get("description", ""), r.get("package", "")) or r.get("marking", "")
    # ถุงซ้ำ: โค้ด 2 มิติตัวเดียวกันเคยบันทึกแล้ว = น่าจะสแกนถุงเดิมซ้ำ (จำนวนจะนับเกิน)
    r["duplicate"], r["dup_info"] = False, None
    if r.get("raw") and r.get("source") in ("LCSC-QR", "ECIA-DM"):
        dup = con.execute(
            """SELECT l.id, l.qty, l.received_at, loc.code FROM stock_lots l JOIN locations loc ON loc.id=l.location_id
               WHERE l.raw_code=? AND l.raw_code<>'' ORDER BY l.id DESC LIMIT 1""", (r["raw"],)).fetchone()
        if dup:
            r["duplicate"] = True
            r["dup_info"] = {"lot_id": dup["id"], "qty": dup["qty"], "at": (dup["received_at"] or "")[:16].replace("T", " "),
                             "loc": locname(dup["code"])}
    return r


@app.post("/api/parse")
def api_parse(text: str = Form(...)):
    with connect() as con:
        return JSONResponse({"items": [_enrich(con, barcodes.parse_text(text))]})


@app.get("/api/lookup")
def api_lookup(mpn: str):
    """พิมพ์ MPN เอง -> เช็คว่ามีในระบบ/ตำแหน่งเดิมไหม"""
    with connect() as con:
        r = barcodes.empty_result(mpn, "TEXT")
        r["mpn"] = mpn.strip()
        return JSONResponse(_enrich(con, r))


MAX_IMAGES = 6  # รูปต่อ 1 เบอร์


def add_part_image(con, pid: int, name: str) -> None:
    """เพิ่มรูปต่อท้าย; ถ้ายังไม่มีรูปหลัก ใช้รูปนี้เป็นรูปหลัก"""
    nxt = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM part_images WHERE part_id=?", (pid,)).fetchone()[0]
    con.execute("INSERT INTO part_images(part_id,file,sort,created_at) VALUES(?,?,?,?)", (pid, name, nxt, now()))
    con.execute("UPDATE parts SET image=?, updated_at=? WHERE id=? AND (image IS NULL OR image='')", (name, now(), pid))


def add_marking(con, pid: int, marking: str) -> None:
    """จำข้อความบนตัวชิปที่ผู้ใช้ยืนยันแล้ว (ต่อท้ายข้อความเดิม เฉพาะคำที่ยังไม่มี)
    — ครั้งหน้าถ่ายรูปชิปแบบเดียวกัน/ค้นด้วยข้อความบนชิป จะเจอเบอร์นี้ทันที"""
    old = con.execute("SELECT marking FROM parts WHERE id=?", (pid,)).fetchone()["marking"] or ""
    have = set(marking_tokens_of(old))
    new = [t for t in marking_tokens_of(marking) if t not in have]
    if not new and old:
        return
    set_marking(con, pid, (old + " " + " ".join(new)).strip() if old else marking)


def chip_text(lines_per_image: list[list[str]]) -> str:
    """ข้อความที่ OCR อ่านได้จากหลายรูป -> ข้อความเดียวให้ผู้ใช้ตรวจ: บรรทัดคั่นด้วย / ; รูปถัดไป (มักเป็นด้านล่าง) คั่นด้วย ·"""
    return " · ".join(" / ".join(ls) for ls in lines_per_image if ls)


def read_chip_text(files: list[str]) -> tuple[str, list[list[str]], str]:
    """อ่านตัวหนังสือจากรูปไอซี (ตัดโลโก้ออกแล้ว) -> (ข้อความรวม, บรรทัดต่อรูป, ผู้ผลิตจากโลโก้)"""
    from PIL import Image
    per, logo = [], ""
    for f in files:
        p = IMAGE_DIR / f"c_{f}"
        if p.exists():
            r = identify.read_chip(Image.open(p).convert("RGB"))
            per.append([ln for ln in r["lines"] if len(norm(ln)) >= 2])
            logo = logo or r["logo"]
    return chip_text(per), per, logo


def part_images_of(con, pid: int) -> list:
    return con.execute("SELECT * FROM part_images WHERE part_id=? ORDER BY sort, id", (pid,)).fetchall()


def set_image_order(con, pid: int, files: list[str]) -> None:
    """เรียงรูปตามที่ผู้ใช้ลาก: รูปแรก = รูปหลัก (ใช้บนป้าย/แผนผัง)"""
    known = {r["file"] for r in part_images_of(con, pid)}
    order = [f for f in files if f in known]
    order += [r["file"] for r in part_images_of(con, pid) if r["file"] not in order]  # ที่ไม่ได้ส่งมา ต่อท้าย
    for i, f in enumerate(order):
        con.execute("UPDATE part_images SET sort=? WHERE part_id=? AND file=?", (i, pid, f))
    if order:
        con.execute("UPDATE parts SET image=?, updated_at=? WHERE id=?", (order[0], now(), pid))


@app.post("/api/ic-photo")
def api_ic_photo(photo: UploadFile = File(...)):
    """อัปโหลดรูปไอซี -> ตัดเฉพาะตัวชิปอัตโนมัติ + อ่านข้อความบนตัวชิป (ให้หน้ารับของเติมช่อง marking ให้ผู้ใช้ตรวจ)"""
    name = images.save_upload(photo.file)
    text, per, logo = read_chip_text([name])
    return JSONResponse({"image": name, "chip_text": text, "tokens": marking_tokens_of(text), "logo": logo})


@app.post("/api/read-marking")
def api_read_marking(files: str = Form("")):
    """อ่านข้อความบนตัวชิปจากรูปที่มีอยู่แล้ว (ปุ่ม "🔍 อ่านจากรูป") — คืนข้อความให้ผู้ใช้ตรวจ/แก้ก่อนบันทึก"""
    names = [f for f in files.split(",") if re.fullmatch(r"[\w\-.]+\.jpg", f)][:6]
    text, per, logo = read_chip_text(names)
    return JSONResponse({"chip_text": text, "per_image": per, "logo": logo})


@app.post("/api/identify-ic")
def api_identify_ic(photo: UploadFile = File(...), keep: str = Form("1")):
    """ไอซีไม่มีถุงฉลาก: ถ่ายรูปตัวชิป -> อ่าน marking + เทียบรูปร่างกับคลัง -> รายการที่น่าจะใช่ ให้ผู้ใช้ยืนยัน
    keep=0 (หน้าเบิกใช้): ใช้รูปค้นหาอย่างเดียว ไม่เก็บไฟล์"""
    from PIL import Image
    name = images.save_upload(photo.file)
    img = Image.open(IMAGE_DIR / f"c_{name}").convert("RGB")
    with connect() as con:
        res = identify.identify(con, img, exclude_files={name})
        cands = []
        for c in res["candidates"]:
            loc = existing_location(con, c["part_id"])
            n_img = len(part_images_of(con, c["part_id"]))
            cands.append({k: c[k] for k in ("part_id", "mpn", "package", "category", "image", "match_image", "qty",
                                            "score", "text", "shape", "reasons", "level")}
                         | {"locked_loc": loc[0] if loc else "", "waiting": waiting_bags(con, c["part_id"]),
                            "can_add_image": n_img < MAX_IMAGES})
        # เทียบเพิ่ม: เบอร์หน้าซอง + ข้อความบนชิป + datasheet ; ไม่เจอ -> หาตัวแทน (หน้าค้นหาแสดงเป็นป๊อปอัพให้เลือก)
        m = match_result(con, tokens=res["tokens"], logo=res["logo"],
                         extra={c["part_id"]: {"score": c["shape"],
                                               "reasons": [x for x in c["reasons"] if "รูป" in x or "ลวดลาย" in x]}
                                for c in res["all"] if c["shape"] >= identify.WEAK})
        m["query"] = " / ".join(res["lines"])
    if keep == "0":
        images.delete_files(name)
        name = ""
    return JSONResponse({"image": name, "lines": res["lines"], "tokens": res["tokens"], "logo": res["logo"],
                         "candidates": cands, "match": m})


@app.post("/part/{pid}/image")
def part_image(pid: int, photo: UploadFile = File(...), back: str = Form("")):
    back = back or f"/part/{pid}"
    with connect() as con:
        if len(part_images_of(con, pid)) >= MAX_IMAGES:
            return RedirectResponse(f"{back}?msg=รูปครบ {MAX_IMAGES} รูปแล้ว ลบรูปเก่าก่อน", status_code=303)
        add_part_image(con, pid, images.save_upload(photo.file))
    return RedirectResponse(f"{back}?msg=เพิ่มรูปแล้ว", status_code=303)


@app.post("/part/{pid}/images/order")
def part_images_order(pid: int, files: str = Form(...)):
    with connect() as con:
        set_image_order(con, pid, [f for f in files.split(",") if f])
        first = part_images_of(con, pid)
    return JSONResponse({"ok": True, "primary": first[0]["file"] if first else ""})


@app.post("/part/{pid}/image/{iid}/primary")
def part_image_primary(pid: int, iid: int, back: str = Form("")):
    with connect() as con:
        img = con.execute("SELECT file FROM part_images WHERE id=? AND part_id=?", (iid, pid)).fetchone()
        if img:
            files = [r["file"] for r in part_images_of(con, pid)]
            set_image_order(con, pid, [img["file"]] + [f for f in files if f != img["file"]])
    return RedirectResponse(back or f"/part/{pid}", status_code=303)


@app.post("/part/{pid}/image/{iid}/delete")
def part_image_delete(pid: int, iid: int, back: str = Form("")):
    with connect() as con:
        img = con.execute("SELECT file FROM part_images WHERE id=? AND part_id=?", (iid, pid)).fetchone()
        if img:
            con.execute("DELETE FROM part_images WHERE id=?", (iid,))
            p = con.execute("SELECT image FROM parts WHERE id=?", (pid,)).fetchone()
            if p["image"] == img["file"]:
                nxt = con.execute("SELECT file FROM part_images WHERE part_id=? ORDER BY sort, id LIMIT 1", (pid,)).fetchone()
                con.execute("UPDATE parts SET image=? WHERE id=?", (nxt["file"] if nxt else "", pid))
            images.delete_files(img["file"])
    return RedirectResponse(back or f"/part/{pid}", status_code=303)


@app.get("/crop/{name}", response_class=HTMLResponse)
def crop_page(request: Request, name: str, back: str = "/"):
    return page(request, "crop.html", img=name, back=back)


@app.post("/crop/{name}")
def crop_save(name: str, x: float = Form(...), y: float = Form(...), w: float = Form(...), h: float = Form(...),
              back: str = Form("/"), auto: str = Form("")):
    if Path(name).name != name or not (IMAGE_DIR / name).exists():
        return HTMLResponse("ไม่พบรูป", status_code=404)
    if auto:
        images.auto_crop(name)
    else:
        images.manual_crop(name, x, y, w, h)
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}msg=ตัดกรอบรูปแล้ว", status_code=303)


def known_mpns() -> dict[str, str]:
    """เบอร์ที่ระบบรู้จัก (ในคลัง + datasheet) {canon: สะกดถูก} — ให้ OCR ฉลากแก้ O/0, S/5 ในเบอร์ที่รู้จักแล้ว"""
    vocab = {identify.canon(it["mpn"]): it["mpn"] for it in matcher.library()["items"]}
    with connect() as con:
        vocab.update({identify.canon(r[0]): r[0] for r in con.execute("SELECT mpn FROM parts")})
    return vocab


@app.post("/api/decode-image")
def api_decode_image(photo: UploadFile = File(...), keep: str = Form("1")):
    ext = Path(photo.filename or "x.jpg").suffix.lower() or ".jpg"
    name = f"{now()[:10]}_{uuid.uuid4().hex[:8]}{ext}"
    path = PHOTO_DIR / name
    with path.open("wb") as f:
        shutil.copyfileobj(photo.file, f)
    try:
        items = barcodes.read_image(str(path), known_mpns())
    except Exception as e:  # รูปเสีย/อ่านไม่ได้
        items, err = [], str(e)
    else:
        err = ""
    if keep == "0":  # หน้าเบิกใช้: อ่านฉลากอย่างเดียว ไม่เก็บรูป
        path.unlink(missing_ok=True)
        name = ""
    if err:
        return JSONResponse({"items": [], "photo": name, "error": err})
    with connect() as con:
        items = [_enrich(con, r) for r in items]
    return JSONResponse({"items": items, "photo": name})


@app.post("/intake")
def intake_save(mpn: str = Form(...), qty: int = Form(...), category: str = Form(""),
                manufacturer: str = Form(""), package: str = Form(""), description: str = Form(""),
                supplier: str = Form(""), supplier_pn: str = Form(""), lot: str = Form(""),
                date_code: str = Form(""), order_no: str = Form(""), coo: str = Form(""),
                size_units: int = Form(1), source: str = Form("new"), location: str = Form(STAGING),
                raw_code: str = Form(""), photo: str = Form(""), ic_images: str = Form(""), ic_order: str = Form(""),
                dup_ok: str = Form(""), marking: str = Form(""), user: str = Form("")):
    mpn = mpn.strip()
    if not mpn:
        return RedirectResponse("/intake?warn=ต้องใส่เบอร์ MPN", status_code=303)
    with connect() as con:
        if raw_code and not dup_ok:
            dup = con.execute("SELECT 1 FROM stock_lots WHERE raw_code=? AND raw_code<>''", (raw_code,)).fetchone()
            if dup:
                return RedirectResponse("/intake?warn=ไม่ได้บันทึก — ถุงนี้ (โค้ดเดียวกัน) บันทึกเข้าไปแล้ว "
                                        "ถ้าเป็นถุงใหม่จริงให้ติ๊กยืนยันก่อนบันทึก", status_code=303)
        # เบอร์ซ้ำ (รวมกรณีจับได้จากชื่อเรียกอื่น/เบอร์ผู้ขาย) -> เพิ่มเข้าเบอร์เดิม ไม่สร้างใหม่
        same, _ = find_existing_part(con, mpn)  # ตอนสแกนเปลี่ยน MPN ตามเบอร์ผู้ขายให้แล้ว ; ผู้ใช้แก้เองถือตามผู้ใช้
        if same:
            mpn = same["mpn"]
        pid, created = find_or_create_part(con, mpn, manufacturer=manufacturer, category=category,
                                           package=package, description=description)
        if not created and category:
            con.execute("UPDATE parts SET category=? WHERE id=?", (category, pid))
        for name in [n for n in ic_images.split(",") if n][:MAX_IMAGES]:
            if Path(name).name == name and (IMAGE_DIR / name).exists() and len(part_images_of(con, pid)) < MAX_IMAGES:
                add_part_image(con, pid, name)
        if marking.strip():
            add_marking(con, pid, marking)
        else:
            pr = con.execute("SELECT category, description, package, marking FROM parts WHERE id=?", (pid,)).fetchone()
            if pr["category"] in PASSIVE and no_print(pr["marking"]):
                lv = label_value(pr["category"], pr["description"] or "", pr["package"] or "")
                if lv:
                    set_marking(con, pid, lv)
        if ic_order:
            set_image_order(con, pid, ic_order.split(","))
        if supplier_pn:
            add_alias(con, pid, supplier_pn, supplier.upper() or "OTHER")
        if not created:
            # เบอร์ที่มีตำแหน่งอยู่แล้ว ต้องวางที่เดิมเท่านั้น
            locked = existing_location(con, pid)
            if locked:
                location = locked[0]
        loc = con.execute("SELECT id FROM locations WHERE code=?", (location or STAGING,)).fetchone()
        loc_id = loc["id"] if loc else staging_id(con)
        cur = con.execute(
            """INSERT INTO stock_lots(part_id,location_id,qty,size_units,supplier,supplier_pn,lot,date_code,
               order_no,coo,source,raw_code,photo,received_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, loc_id, qty, size_units, supplier, supplier_pn, lot, date_code, order_no, coo, source,
             raw_code, photo, now()))
        con.execute("INSERT INTO transactions(ts,type,part_id,lot_id,location_id,qty,note,user) VALUES(?,?,?,?,?,?,?,?)",
                    (now(), "IN", pid, cur.lastrowid, loc_id, qty, "รับเข้าครั้งแรก" if created else "รับเข้า", user))
        if loc_id != staging_id(con):
            for w in con.execute("SELECT id FROM stock_lots WHERE part_id=? AND location_id=? AND qty>0",
                                 (pid, staging_id(con))).fetchall():
                con.execute("UPDATE stock_lots SET location_id=? WHERE id=?", (loc_id, w["id"]))
                con.execute("INSERT INTO transactions(ts,type,part_id,lot_id,location_id,qty,note) VALUES(?,?,?,?,?,?,?)",
                            (now(), "MOVE", pid, w["id"], loc_id, 0, "ระบุช่องพร้อมถุงใหม่"))
        total = stock_of(con, pid)["qty"]
        where = con.execute("SELECT code FROM locations WHERE id=?", (loc_id,)).fetchone()["code"]
    if created:
        msg = f"บันทึกเบอร์ใหม่ {mpn} x{qty} → {locname(where)}"
    elif where == STAGING:
        msg = f"เบอร์ซ้ำ {mpn}: เพิ่มอีก {qty} ชิ้น (ยังรอระบุช่อง) → คงเหลือรวม {total} ชิ้น"
    else:
        msg = f"เบอร์ซ้ำ {mpn}: เพิ่มอีก {qty} ชิ้น ใส่ช่องเดิม {where} → คงเหลือรวม {total} ชิ้น"
    return RedirectResponse(f"/intake?msg={msg}", status_code=303)


# ---------------- แผนผังตู้ / ลิ้นชัก ----------------

def _drawer_grid(con):
    rows = con.execute(
        """SELECT loc.*,
             (SELECT COALESCE(SUM(l.size_units),0) FROM stock_lots l
                WHERE (l.location_id=loc.id AND l.qty>0 AND l.planned_location_id IS NULL)
                   OR l.planned_location_id=loc.id) AS used,
             (SELECT COUNT(*) FROM stock_lots l WHERE l.planned_location_id=loc.id) AS planned,
             (SELECT p.category FROM stock_lots l JOIN parts p ON p.id=l.part_id
                WHERE l.location_id=loc.id OR l.planned_location_id=loc.id
                GROUP BY p.category ORDER BY SUM(l.size_units) DESC LIMIT 1) AS main_cat,
             (SELECT GROUP_CONCAT(mpn, ', ') FROM (SELECT DISTINCT p.mpn FROM stock_lots l JOIN parts p ON p.id=l.part_id
                WHERE (l.location_id=loc.id AND l.qty>0) OR l.planned_location_id=loc.id ORDER BY p.mpn_norm)) AS mpns
           FROM locations loc WHERE kind='drawer' ORDER BY cabinet, row, col""").fetchall()
    # อะไหล่ในแต่ละลิ้นชัก (พร้อมรูป + จำนวนจริงในช่องนั้น) สำหรับแผนผัง / ป้าย / เบิกจากแผนผัง
    items: dict[int, list] = {}
    for it in con.execute(
            """SELECT COALESCE(l.planned_location_id, l.location_id) AS lid, p.id, p.mpn, p.image, p.mpn_norm,
                      SUM(CASE WHEN l.planned_location_id IS NULL THEN l.qty ELSE 0 END) AS qty
               FROM stock_lots l JOIN parts p ON p.id=l.part_id
               WHERE l.qty>0 OR l.planned_location_id IS NOT NULL
               GROUP BY lid, p.id ORDER BY p.mpn_norm"""):
        items.setdefault(it["lid"], []).append(dict(it))
    cabinets: dict[str, dict] = {}
    for c in con.execute("SELECT * FROM cabinets ORDER BY name"):
        cabinets[c["name"]] = {"info": c, "rows": {}, "max_col": c["cols"]}
    for r in rows:
        cab = cabinets.setdefault(r["cabinet"], {"info": None, "rows": {}, "max_col": 0})
        d = dict(r)
        d["items"] = items.get(r["id"], [])
        d["qty"] = sum(it["qty"] for it in d["items"])
        cab["rows"].setdefault(r["row"], []).append(d)
        cab["max_col"] = max(cab["max_col"], r["col"])
    return cabinets


@app.get("/map", response_class=HTMLResponse)
def map_page(request: Request, msg: str = "", warn: str = "", edit: str = ""):
    with connect() as con:
        cats = {c["code"]: c for c in categories(con)}
        return page(request, "map.html", cabinets=_drawer_grid(con), cats=cats, msg=msg, warn=warn, edit=edit,
                    default_rows=DEFAULT_ROWS, default_cols=DEFAULT_COLS)


@app.post("/cabinet")
def cabinet_create(name: str = Form(...), rows: int = Form(DEFAULT_ROWS), cols: int = Form(DEFAULT_COLS),
                   capacity: int = Form(12)):
    """เพิ่มตู้ชุดใหม่ (ค่าเริ่มต้น 6 แถว x 3 ช่อง ตามตู้จริง)"""
    name = name.strip().upper().replace("-", "")
    if not name or not name.isalnum() or not (1 <= rows <= 50 and 1 <= cols <= 50):
        return RedirectResponse("/map?warn=ชื่อตู้ (ตัวอักษร/ตัวเลข) หรือจำนวนแถว/ช่องไม่ถูกต้อง (1–50)", status_code=303)
    with connect() as con:
        if con.execute("SELECT 1 FROM cabinets WHERE name=?", (name,)).fetchone():
            return RedirectResponse(f"/map?warn=มีตู้ {name} อยู่แล้ว", status_code=303)
        con.execute("INSERT INTO cabinets(name,rows,cols,capacity) VALUES(?,?,?,?)", (name, rows, cols, capacity))
        create_rect(con, name, rows, cols)
    return RedirectResponse(f"/map?msg=เพิ่มตู้ {name} ({rows} แถว × {cols} ช่อง = {rows * cols} ลิ้นชัก) แล้ว&edit={name}",
                            status_code=303)


@app.post("/cabinet/{name}/add-drawer")
def cabinet_add_drawer(name: str, row: int = Form(...)):
    """เพิ่มทีละ 1 ช่อง ต่อท้ายแถวที่เลือก"""
    with connect() as con:
        if not con.execute("SELECT 1 FROM cabinets WHERE name=?", (name,)).fetchone() or not 1 <= row <= 50:
            return RedirectResponse("/map?warn=ไม่พบตู้/แถว", status_code=303)
        code = add_drawer(con, name, row)
    return RedirectResponse(f"/map?msg=เพิ่มช่อง {code} แล้ว&edit={name}", status_code=303)


@app.post("/cabinet/{name}/add-rows")
def cabinet_add_rows(name: str, n_rows: int = Form(1), per_row: int = Form(DEFAULT_COLS)):
    """เพิ่มแถวใหม่ต่อด้านล่าง: กี่แถว แถวละกี่ช่อง"""
    if not (1 <= n_rows <= 20 and 1 <= per_row <= 50):
        return RedirectResponse(f"/map?warn=จำนวนแถว 1–20 / ช่องต่อแถว 1–50&edit={name}", status_code=303)
    with connect() as con:
        if not con.execute("SELECT 1 FROM cabinets WHERE name=?", (name,)).fetchone():
            return RedirectResponse("/map?warn=ไม่พบตู้", status_code=303)
        codes = add_rows(con, name, n_rows, per_row)
    return RedirectResponse(f"/map?msg=เพิ่ม {n_rows} แถว แถวละ {per_row} ช่อง ({codes[0]} … {codes[-1]}) แล้ว&edit={name}",
                            status_code=303)


@app.post("/loc/{code}/remove")
def location_remove(code: str, back: str = Form("/map")):
    """ลบ 1 ช่อง (ต้องว่าง)"""
    with connect() as con:
        loc = con.execute("SELECT cabinet FROM locations WHERE code=?", (code.upper(),)).fetchone()
        err = remove_drawer(con, code.upper())
    sep = "&" if "?" in back else "?"
    edit = f"&edit={loc['cabinet']}" if loc else ""
    if err:
        return RedirectResponse(f"{back}{sep}warn={err}{edit}", status_code=303)
    return RedirectResponse(f"{back}{sep}msg=ลบช่อง {code.upper()} แล้ว{edit}", status_code=303)


@app.post("/cabinet/{name}/capacity")
def cabinet_capacity(name: str, capacity: int = Form(...)):
    """ความจุเริ่มต้นของตู้ (ช่องที่ยังใช้ค่าเดิมจะเปลี่ยนตาม)"""
    capacity = max(1, capacity)
    with connect() as con:
        old = con.execute("SELECT capacity FROM cabinets WHERE name=?", (name,)).fetchone()
        if old:
            con.execute("UPDATE locations SET capacity=? WHERE cabinet=? AND capacity=?", (capacity, name, old[0]))
            con.execute("UPDATE cabinets SET capacity=? WHERE name=?", (capacity, name))
    return RedirectResponse(f"/map?msg=ตั้งความจุตู้ {name} = {capacity} หน่วย/ช่อง&edit={name}", status_code=303)


@app.post("/cabinet/{name}/delete")
def cabinet_delete(name: str):
    with connect() as con:
        busy = con.execute(
            """SELECT loc.code FROM locations loc WHERE loc.cabinet=? AND loc.kind='drawer' AND EXISTS(
                 SELECT 1 FROM stock_lots l WHERE (l.location_id=loc.id AND l.qty>0) OR l.planned_location_id=loc.id)""",
            (name,)).fetchall()
        if busy:
            return RedirectResponse(f"/map?warn=ลบตู้ {name} ไม่ได้ ยังมีของใน {', '.join(b[0] for b in busy)}",
                                    status_code=303)
        con.execute("UPDATE locations SET kind='retired' WHERE cabinet=?", (name,))
        con.execute("DELETE FROM cabinets WHERE name=?", (name,))
    return RedirectResponse(f"/map?msg=ลบตู้ {name} แล้ว", status_code=303)


@app.post("/loc/{code}/capacity")
def location_capacity(code: str, capacity: int = Form(...)):
    with connect() as con:
        con.execute("UPDATE locations SET capacity=? WHERE code=?", (max(1, capacity), code.upper()))
    return RedirectResponse(f"/loc/{code}?msg=ตั้งความจุ {capacity} หน่วยแล้ว", status_code=303)


@app.get("/putaway", response_class=HTMLResponse)
def putaway_page(request: Request, msg: str = "", warn: str = ""):
    """รอระบุช่อง: ผู้ใช้เลือกลิ้นชักเองทีละเบอร์"""
    with connect() as con:
        drawers = con.execute("SELECT code FROM locations WHERE kind='drawer' ORDER BY cabinet, row, col").fetchall()
        return page(request, "putaway.html", parts=unassigned_parts(con), drawers=drawers, msg=msg, warn=warn,
                    cats={c["code"]: c for c in categories(con)})


@app.post("/assign")
def assign(part_id: int = Form(...), to: str = Form(""), back: str = Form("/putaway")):
    """ใส่เบอร์ที่รอระบุช่องเข้าลิ้นชักที่ผู้ใช้เลือก (ทุกถุงของเบอร์นี้ที่รออยู่)"""
    sep = "&" if "?" in back else "?"
    with connect() as con:
        loc = con.execute("SELECT * FROM locations WHERE code=? AND kind='drawer'", (to.upper(),)).fetchone()
        if not loc:
            return RedirectResponse(f"{back}{sep}warn=กรุณาเลือกช่องก่อน", status_code=303)
        lots = con.execute(
            """SELECT l.id FROM stock_lots l JOIN locations s ON s.id=l.location_id
               WHERE l.part_id=? AND s.code=? AND l.qty>0""", (part_id, STAGING)).fetchall()
        for l in lots:
            con.execute("UPDATE stock_lots SET location_id=?, planned_location_id=NULL WHERE id=?", (loc["id"], l["id"]))
            con.execute("INSERT INTO transactions(ts,type,part_id,lot_id,location_id,qty,note) VALUES(?,?,?,?,?,?,?)",
                        (now(), "MOVE", part_id, l["id"], loc["id"], 0, f"ระบุช่อง {loc['code']}"))
        mpn = con.execute("SELECT mpn FROM parts WHERE id=?", (part_id,)).fetchone()["mpn"]
    return RedirectResponse(f"{back}{sep}msg=ใส่ {mpn} ({len(lots)} ถุง) ที่ {loc['code']} แล้ว", status_code=303)


@app.get("/loc/{code}", response_class=HTMLResponse)
def location_page(request: Request, code: str, msg: str = "", warn: str = ""):
    with connect() as con:
        loc = con.execute("SELECT * FROM locations WHERE code=?", (code.upper(),)).fetchone()
        if not loc:
            return HTMLResponse("ไม่พบตำแหน่ง", status_code=404)
        lots = con.execute(
            """SELECT l.*, p.mpn, p.id AS pid, p.category, p.description, p.manufacturer, p.image,
                      CASE WHEN l.planned_location_id=? AND l.location_id<>? THEN 1 ELSE 0 END AS is_planned
               FROM stock_lots l JOIN parts p ON p.id=l.part_id
               WHERE (l.location_id=? AND l.qty>0) OR l.planned_location_id=?
               ORDER BY p.mpn_norm""", (loc["id"], loc["id"], loc["id"], loc["id"])).fetchall()
        drawers = con.execute("SELECT code FROM locations WHERE kind IN ('drawer','staging') "
                              "ORDER BY kind DESC, cabinet, row, col").fetchall()
        waiting = unassigned_parts(con) if loc["kind"] == "drawer" else []
        return page(request, "location.html", loc=loc, lots=lots, msg=msg, warn=warn, drawers=drawers,
                    waiting=waiting, cats={c["code"]: c for c in categories(con)})


@app.post("/lot/{lot_id}/move")
def move_lot(lot_id: int, to: str = Form(...), back: str = Form("/")):
    with connect() as con:
        loc = con.execute("SELECT id FROM locations WHERE code=?", (to.upper(),)).fetchone()
        lot = con.execute("SELECT * FROM stock_lots WHERE id=?", (lot_id,)).fetchone()
        if loc and lot:
            con.execute("UPDATE stock_lots SET location_id=?, planned_location_id=NULL WHERE id=?", (loc["id"], lot_id))
            con.execute("INSERT INTO transactions(ts,type,part_id,lot_id,location_id,qty,note) VALUES(?,?,?,?,?,?,?)",
                        (now(), "MOVE", lot["part_id"], lot_id, loc["id"], 0, f"ย้ายไป {to.upper()}"))
    return RedirectResponse(back, status_code=303)


# ---------------- แก้ไข / ลบ รายการรับเข้า ----------------

@app.get("/lot/{lot_id}/edit", response_class=HTMLResponse)
def lot_edit_page(request: Request, lot_id: int, msg: str = "", warn: str = ""):
    with connect() as con:
        lot = con.execute(
            """SELECT l.*, loc.code AS loc_code, pl.code AS plan_code FROM stock_lots l
               JOIN locations loc ON loc.id=l.location_id LEFT JOIN locations pl ON pl.id=l.planned_location_id
               WHERE l.id=?""", (lot_id,)).fetchone()
        if not lot:
            return RedirectResponse("/intake?warn=ไม่พบรายการนี้ (อาจถูกลบแล้ว)", status_code=303)
        p = con.execute("SELECT * FROM parts WHERE id=?", (lot["part_id"],)).fetchone()
        drawers = con.execute("SELECT code FROM locations WHERE kind IN ('drawer','staging') "
                              "ORDER BY kind DESC, cabinet, row, col").fetchall()
        has_out = con.execute("SELECT COUNT(*) FROM transactions WHERE lot_id=? AND type='OUT'", (lot_id,)).fetchone()[0]
        return page(request, "lot_edit.html", lot=lot, p=p, imgs=part_images_of(con, p["id"]), drawers=drawers,
                    cats=categories(con), msg=msg, warn=warn, has_out=has_out, max_images=MAX_IMAGES)


@app.post("/lot/{lot_id}/edit")
def lot_edit_save(lot_id: int, mpn: str = Form(...), category: str = Form("OTH"), manufacturer: str = Form(""),
                  package: str = Form(""), description: str = Form(""), qty: int = Form(...),
                  size_units: int = Form(1), supplier: str = Form(""), supplier_pn: str = Form(""),
                  lot: str = Form(""), date_code: str = Form(""), source: str = Form("new"),
                  location: str = Form(""), marking: str | None = Form(None)):
    back = f"/lot/{lot_id}/edit"
    with connect() as con:
        l = con.execute("SELECT * FROM stock_lots WHERE id=?", (lot_id,)).fetchone()
        if not l:
            return RedirectResponse("/intake", status_code=303)
        pid = l["part_id"]
        # --- เปลี่ยน MPN (เช่น อ่านฉลากผิด) ---
        cur = con.execute("SELECT * FROM parts WHERE id=?", (pid,)).fetchone()
        if norm(mpn) and norm(mpn) != cur["mpn_norm"]:
            other = con.execute("SELECT id FROM parts WHERE mpn_norm=?", (norm(mpn),)).fetchone()
            only_this = con.execute("SELECT COUNT(*) FROM stock_lots WHERE part_id=?", (pid,)).fetchone()[0] == 1
            if other:
                new_pid = other["id"]            # มีเบอร์ที่ถูกอยู่แล้ว -> ย้ายถุงนี้ไปเบอร์นั้น
            elif only_this:
                con.execute("UPDATE parts SET mpn=?, mpn_norm=? WHERE id=?", (mpn.strip(), norm(mpn), pid))
                new_pid = pid
            else:
                new_pid, _ = find_or_create_part(con, mpn, category=category)
            if new_pid != pid:
                con.execute("UPDATE stock_lots SET part_id=? WHERE id=?", (new_pid, lot_id))
                con.execute("UPDATE transactions SET part_id=? WHERE lot_id=?", (new_pid, lot_id))
                if only_this:  # เบอร์เดิมไม่เหลือถุงแล้ว: ย้ายรูปไปเบอร์ใหม่ แล้วลบเบอร์ผิดทิ้ง
                    for img in part_images_of(con, pid):
                        add_part_image(con, new_pid, img["file"])
                    con.execute("DELETE FROM transactions WHERE part_id=?", (pid,))
                    con.execute("DELETE FROM parts WHERE id=?", (pid,))
                pid = new_pid
        con.execute("UPDATE parts SET category=?, manufacturer=?, package=?, description=?, updated_at=? WHERE id=?",
                    (category, manufacturer, package, description, now(), pid))
        if marking is not None:
            set_marking(con, pid, marking)
        if supplier_pn:
            add_alias(con, pid, supplier_pn, supplier.upper() or "OTHER")
        # --- ข้อมูลถุง ---
        con.execute("UPDATE stock_lots SET size_units=?, supplier=?, supplier_pn=?, lot=?, date_code=?, source=? WHERE id=?",
                    (size_units, supplier, supplier_pn, lot, date_code, source, lot_id))
        if qty != l["qty"]:
            con.execute("UPDATE stock_lots SET qty=? WHERE id=?", (qty, lot_id))
            con.execute("INSERT INTO transactions(ts,type,part_id,lot_id,location_id,qty,note) VALUES(?,?,?,?,?,?,?)",
                        (now(), "ADJUST", pid, lot_id, l["location_id"], qty - l["qty"], "แก้ไขจำนวนรายการรับเข้า"))
        # --- ย้ายตำแหน่ง: เบอร์เดียวกันต้องอยู่ที่เดียวกัน -> ย้ายทุกถุงของเบอร์นี้ ---
        loc_row = con.execute("SELECT * FROM locations WHERE code=?", (location,)).fetchone() if location else None
        if loc_row and loc_row["id"] != l["location_id"]:
            moved = con.execute("SELECT id FROM stock_lots WHERE part_id=? AND (qty>0 OR id=?)", (pid, lot_id)).fetchall()
            for m in moved:
                con.execute("UPDATE stock_lots SET location_id=?, planned_location_id=NULL WHERE id=?", (loc_row["id"], m["id"]))
                con.execute("INSERT INTO transactions(ts,type,part_id,lot_id,location_id,qty,note) VALUES(?,?,?,?,?,?,?)",
                            (now(), "MOVE", pid, m["id"], loc_row["id"], 0, f"ย้ายทั้งเบอร์ไป {location}"))
    return RedirectResponse(f"{back}?msg=บันทึกการแก้ไขแล้ว", status_code=303)


@app.post("/lot/{lot_id}/delete")
def lot_delete(lot_id: int):
    """ลบรายการที่สแกนผิด/ซ้ำ (ทำได้เฉพาะถุงที่ยังไม่เคยเบิก)"""
    with connect() as con:
        l = con.execute("SELECT * FROM stock_lots WHERE id=?", (lot_id,)).fetchone()
        if not l:
            return RedirectResponse("/intake", status_code=303)
        if con.execute("SELECT COUNT(*) FROM transactions WHERE lot_id=? AND type='OUT'", (lot_id,)).fetchone()[0]:
            return RedirectResponse(f"/lot/{lot_id}/edit?warn=ถุงนี้มีประวัติเบิกแล้ว ลบไม่ได้ — ใช้ปรับยอดแทน", status_code=303)
        con.execute("DELETE FROM transactions WHERE lot_id=?", (lot_id,))
        con.execute("DELETE FROM stock_lots WHERE id=?", (lot_id,))
        pid = l["part_id"]
        if not con.execute("SELECT 1 FROM stock_lots WHERE part_id=?", (pid,)).fetchone():
            # ไม่เหลือถุงของเบอร์นี้แล้ว -> ลบเบอร์ + รูป
            for img in part_images_of(con, pid):
                images.delete_files(img["file"])
            con.execute("DELETE FROM transactions WHERE part_id=?", (pid,))
            con.execute("DELETE FROM parts WHERE id=?", (pid,))
    return RedirectResponse("/intake?msg=ลบรายการแล้ว", status_code=303)


# ---------------- รายละเอียดอะไหล่ / เบิก ----------------

@app.get("/part/{pid}", response_class=HTMLResponse)
def part_page(request: Request, pid: int, msg: str = ""):
    with connect() as con:
        p = con.execute("SELECT p.*, c.name_th AS cat_name, c.color AS cat_color FROM parts p "
                        "JOIN categories c ON c.code=p.category WHERE p.id=?", (pid,)).fetchone()
        if not p:
            return HTMLResponse("ไม่พบอะไหล่", status_code=404)
        lots = con.execute(
            """SELECT l.*, loc.code AS loc_code, pl.code AS plan_code FROM stock_lots l
               JOIN locations loc ON loc.id=l.location_id LEFT JOIN locations pl ON pl.id=l.planned_location_id
               WHERE l.part_id=? ORDER BY l.qty=0, l.received_at""", (pid,)).fetchall()
        aliases = con.execute("SELECT * FROM part_aliases WHERE part_id=?", (pid,)).fetchall()
        imgs = part_images_of(con, pid)
        tx = con.execute(
            """SELECT t.*, loc.code AS loc_code FROM transactions t LEFT JOIN locations loc ON loc.id=t.location_id
               WHERE t.part_id=? ORDER BY t.id DESC LIMIT 50""", (pid,)).fetchall()
        return page(request, "part.html", p=p, lots=lots, aliases=aliases, tx=tx, msg=msg, cats=categories(con),
                    imgs=imgs, max_images=MAX_IMAGES, ds=datasheets.for_part(con, p["mpn_norm"]))


@app.post("/part/{pid}/edit")
def part_edit(pid: int, category: str = Form(...), manufacturer: str = Form(""), package: str = Form(""),
              description: str = Form(""), marking: str | None = Form(None), notes: str = Form("")):
    with connect() as con:
        con.execute("UPDATE parts SET category=?,manufacturer=?,package=?,description=?,notes=?,updated_at=? WHERE id=?",
                    (category, manufacturer, package, description, notes, now(), pid))
        if marking is not None:   # ฟอร์มที่ไม่มีช่อง marking = ไม่แตะของเดิม
            set_marking(con, pid, marking)
    return RedirectResponse(f"/part/{pid}?msg=บันทึกแล้ว", status_code=303)


@app.post("/part/{pid}/marking")
def part_marking(pid: int, marking: str = Form("")):
    """บันทึกข้อความบนตัวชิป (กล่อง "ข้อความบนตัวชิป" หน้าไอซี)"""
    with connect() as con:
        set_marking(con, pid, marking)
    return RedirectResponse(f"/part/{pid}?msg=บันทึกข้อความบนตัวชิปแล้ว", status_code=303)


UNDO_MINUTES = 15  # กด "ยกเลิก" การเบิกที่เพิ่งทำได้ภายในกี่นาที


def withdraw(con, pid: int, qty: int, job_ref: str = "", note: str = "", loc: str = "", user: str = "") -> dict:
    """ตัดสต๊อกแบบ FIFO (ถุงเก่าสุดก่อน) ; loc = เบิกจากลิ้นชักนี้เท่านั้น (เบิกจากหน้าแผนผัง)
    ของไม่พอ / จำนวนผิด -> ValueError (ข้อความไทยพร้อมแสดงผู้ใช้)"""
    if qty <= 0:
        raise ValueError("จำนวนต้องมากกว่า 0")
    sql = ("SELECT l.*, loc.code FROM stock_lots l JOIN locations loc ON loc.id=l.location_id "
           "WHERE l.part_id=? AND l.qty>0")
    args: list = [pid]
    if loc:
        sql += " AND loc.code=?"
        args.append(loc.upper())
    lots = con.execute(sql + " ORDER BY l.received_at, l.id", args).fetchall()
    avail = sum(l["qty"] for l in lots)
    if qty > avail:
        where = f"ในช่อง {locname(loc.upper())} " if loc else ""
        raise ValueError(f"ของไม่พอ — {where}เหลือ {avail} ชิ้น")
    remain, tx_ids, codes = qty, [], []
    for l in lots:
        if remain <= 0:
            break
        take = min(l["qty"], remain)
        con.execute("UPDATE stock_lots SET qty=qty-? WHERE id=?", (take, l["id"]))
        cur = con.execute("INSERT INTO transactions(ts,type,part_id,lot_id,location_id,qty,job_ref,note,user) "
                          "VALUES(?,?,?,?,?,?,?,?,?)", (now(), "OUT", pid, l["id"], l["location_id"], -take,
                                                        job_ref.strip(), note.strip(), user))
        tx_ids.append(cur.lastrowid)
        if l["code"] not in codes:
            codes.append(l["code"])
        remain -= take
    left = stock_of(con, pid)["qty"]
    left_here = con.execute(
        "SELECT COALESCE(SUM(l.qty),0) FROM stock_lots l JOIN locations loc ON loc.id=l.location_id "
        "WHERE l.part_id=? AND loc.code=?", (pid, codes[0])).fetchone()[0]
    return {"tx": tx_ids, "taken": qty, "left": left, "left_here": left_here, "loc": codes[0], "locs": codes}


def part_card(con, pid: int) -> dict | None:
    """ข้อมูลย่อของเบอร์สำหรับหน้าต่างเบิก: รูป, ยอดคงเหลือ, ช่องที่เก็บ"""
    p = con.execute("SELECT id, mpn, image, category, package, description, marking FROM parts WHERE id=?", (pid,)).fetchone()
    if not p:
        return None
    locs = con.execute(
        """SELECT loc.code, SUM(l.qty) AS qty FROM stock_lots l JOIN locations loc ON loc.id=l.location_id
           WHERE l.part_id=? AND l.qty>0 GROUP BY loc.code ORDER BY loc.kind DESC""", (pid,)).fetchall()
    return {"part_id": p["id"], "mpn": p["mpn"], "image": p["image"] or "", "category": p["category"],
            "package": p["package"] or "", "description": p["description"] or "", "marking": p["marking"] or "",
            "qty": sum(r["qty"] for r in locs), "locs": [{"code": r["code"], "name": locname(r["code"]), "qty": r["qty"]} for r in locs]}


@app.get("/api/events")
async def api_events(request: Request, lifetime: float = 25):
    """Server-Sent Events: ส่งเลขรุ่นข้อมูลทุกครั้งที่ Database เปลี่ยน (หน้าเว็บต่อใหม่อัตโนมัติเมื่อหมดอายุ)
    อายุสั้น = ปิดโปรแกรม/รีสตาร์ตไม่ค้างรอการเชื่อมต่อ"""
    async def stream():
        yield "retry: 1500\n\n"
        last, t0, beat = None, time.monotonic(), time.monotonic()
        while time.monotonic() - t0 < lifetime and not _stop.is_set():
            if await request.is_disconnected():
                break
            # อ่านจาก Database ตรง (เร็วมาก) — ค่าในเธรดเบื้องหลังอาจช้ากว่าหน้าที่เพิ่งโหลด 0.5 วิ ทำให้หน้าโหลดซ้ำเอง
            rev = get_rev()
            if rev != last:
                last = rev
                yield f"data: {last}\n\n"
                beat = time.monotonic()
            elif time.monotonic() - beat > 10:
                yield f"data: {last}\n\n"   # สัญญาณชีพ: หน้าเว็บรู้ว่าสายยังดี (ถ้าเงียบเกิน 30 วิ จะต่อใหม่เอง)
                beat = time.monotonic()
            await asyncio.sleep(0.3)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.get("/api/map-state")
def api_map_state():
    """จำนวนไอซีจริงต่อช่อง (ให้แผนผังที่เปิดค้างอัปเดตตัวเลขเอง)"""
    with connect() as con:
        out = {}
        for cab in _drawer_grid(con).values():
            for drawers in cab["rows"].values():
                for d in drawers:
                    out[d["code"]] = [{"id": it["id"], "qty": it["qty"]} for it in d["items"]]
        return JSONResponse({"rev": get_rev(con), "drawers": out})


@app.get("/api/part/{pid}/card")
def api_part_card(pid: int):
    with connect() as con:
        c = part_card(con, pid)
    return JSONResponse(c or {"error": "ไม่พบเบอร์นี้"}, status_code=200 if c else 404)


def match_result(con, text: str = "", tokens: list[str] | None = None, extra: dict | None = None, logo: str = "") -> dict:
    """ผลเทียบจาก matcher + ตำแหน่ง/ยอดสดของแต่ละเบอร์ (สำหรับป๊อปอัพผลการค้นหา)"""
    r = matcher.match(con, text, tokens, extra, logo=logo)
    for x in r["matches"] + r["substitutes"]:
        c = part_card(con, x["part_id"]) or {}
        x["locs"], x["qty"], x["description"] = c.get("locs", []), c.get("qty", 0), c.get("description", "")
    return r


@app.get("/api/match")
def api_match(q: str = ""):
    """ค้นแบบเทียบทุกแหล่ง (เบอร์หน้าซอง / ข้อความบนชิป / datasheet) + ตัวแทนเมื่อไม่มีในคลัง"""
    with connect() as con:
        return JSONResponse(match_result(con, q))


@app.get("/api/search")
def api_search(q: str = ""):
    """ค้นเบอร์สำหรับหน้าเบิก (พิมพ์ไปเรื่อย ๆ ได้)"""
    with connect() as con:
        return JSONResponse({"items": [part_card(con, r["id"]) for r in search(con, q, limit=30)]})


_OUT_DONE: "OrderedDict[str, dict]" = OrderedDict()   # รหัสคำขอ -> ผลลัพธ์ (กันตัดสต๊อกซ้ำเมื่อส่งซ้ำ)
_OUT_LOCK = threading.Lock()


@app.post("/api/out")
def api_out(part_id: int = Form(...), qty: int = Form(...), job_ref: str = Form(""), note: str = Form(""),
            loc: str = Form(""), req: str = Form("")):
    """เบิกใช้ (ตัดสต๊อก) จากหน้าแผนผัง / หน้าเบิกใช้
    req = รหัสคำขอจากหน้าเว็บ: Wi-Fi หลุดหลังเซิร์ฟเวอร์ตัดสต๊อกแล้วแต่คำตอบมาไม่ถึง -> กดส่งซ้ำได้ ไม่ตัดซ้ำ"""
    req = req.strip()[:64]
    with _OUT_LOCK:
        if req and req in _OUT_DONE:
            return JSONResponse(_OUT_DONE[req] | {"repeat": True})
        resp = _api_out(part_id, qty, job_ref, note, loc)
        if req and resp.status_code == 200 and json.loads(resp.body).get("ok"):
            _OUT_DONE[req] = json.loads(resp.body)
            while len(_OUT_DONE) > 500:
                _OUT_DONE.popitem(last=False)
        return resp


def _api_out(part_id, qty, job_ref, note, loc):
    with connect() as con:
        p = con.execute("SELECT mpn FROM parts WHERE id=?", (part_id,)).fetchone()
        if not p:
            return JSONResponse({"ok": False, "msg": "ไม่พบเบอร์นี้"}, status_code=404)
        try:
            r = withdraw(con, part_id, qty, job_ref, note, loc)
        except ValueError as e:
            return JSONResponse({"ok": False, "msg": str(e)})
    where = locname(r["loc"])
    msg = f"เบิก {p['mpn']} {qty} ชิ้น จาก {where} → เหลือ {r['left']} ชิ้น" + (" (หมดแล้ว)" if r["left"] == 0 else "")
    return JSONResponse({"ok": True, "msg": msg, "mpn": p["mpn"], "part_id": part_id,
                         "job_ref": job_ref.strip(), "note": note.strip()} | r)


@app.post("/api/out/undo")
def api_out_undo(tx: str = Form(...)):
    """ยกเลิกการเบิกที่เพิ่งกด (กดผิด) — คืนจำนวนเข้าถุงเดิม ภายใน UNDO_MINUTES นาที"""
    from datetime import datetime, timedelta
    ids = [int(x) for x in tx.split(",") if x.strip().isdigit()]
    limit = (datetime.now() - timedelta(minutes=UNDO_MINUTES)).isoformat(timespec="seconds")
    back = 0
    with connect() as con:
        for t in con.execute(f"SELECT * FROM transactions WHERE type='OUT' AND id IN ({','.join('?' * len(ids))})", ids) if ids else []:
            if t["ts"] < limit:
                return JSONResponse({"ok": False, "msg": f"ยกเลิกได้เฉพาะภายใน {UNDO_MINUTES} นาที — ใช้ 'คืนเข้าคลัง' ที่หน้าเบอร์แทน"})
        for t in con.execute(f"SELECT * FROM transactions WHERE type='OUT' AND id IN ({','.join('?' * len(ids))})", ids) if ids else []:
            con.execute("UPDATE stock_lots SET qty=qty+? WHERE id=?", (-t["qty"], t["lot_id"]))
            con.execute("DELETE FROM transactions WHERE id=?", (t["id"],))
            back += -t["qty"]
    return JSONResponse({"ok": back > 0, "msg": f"ยกเลิกการเบิกแล้ว — คืน {back} ชิ้นเข้าที่เดิม" if back else "ไม่พบรายการ"})


def recent_out(con, limit: int = 15) -> list:
    return con.execute(
        """SELECT t.id, t.ts, t.qty, t.job_ref, t.note, p.id AS pid, p.mpn, p.image, loc.code
           FROM transactions t JOIN parts p ON p.id=t.part_id LEFT JOIN locations loc ON loc.id=t.location_id
           WHERE t.type='OUT' ORDER BY t.id DESC LIMIT ?""", (limit,)).fetchall()


@app.get("/use")
def use_page(pid: int = 0):
    """หน้าเบิกใช้เดิม รวมเข้ากับหน้าค้นหาแล้ว (ลิงก์เก่ายังใช้ได้)"""
    return RedirectResponse(f"/?pid={pid}" if pid else "/", status_code=303)


@app.post("/part/{pid}/out")
def part_out(pid: int, qty: int = Form(...), job_ref: str = Form(""), note: str = Form(""),
             tx_type: str = Form("OUT"), user: str = Form("")):
    """เบิกออกแบบ FIFO (ถุงเก่าสุดก่อน) / RETURN คืนเข้าถุงล่าสุด / ADJUST ปรับยอดตรวจนับ"""
    with connect() as con:
        lots = con.execute("SELECT * FROM stock_lots WHERE part_id=? ORDER BY received_at, id", (pid,)).fetchall()
        if not lots:
            return RedirectResponse(f"/part/{pid}?msg=ไม่มีถุงในคลัง", status_code=303)
        if tx_type == "OUT":
            try:
                withdraw(con, pid, qty, job_ref, note, user=user)
            except ValueError as e:
                return RedirectResponse(f"/part/{pid}?msg={e}", status_code=303)
        else:
            l = lots[-1]
            delta = qty if tx_type == "RETURN" else qty - l["qty"]  # ADJUST = ตั้งยอดถุงล่าสุดเป็น qty
            con.execute("UPDATE stock_lots SET qty=qty+? WHERE id=?", (delta, l["id"]))
            con.execute("INSERT INTO transactions(ts,type,part_id,lot_id,location_id,qty,job_ref,note,user) VALUES(?,?,?,?,?,?,?,?,?)",
                        (now(), tx_type, pid, l["id"], l["location_id"], delta, job_ref, note, user))
    return RedirectResponse(f"/part/{pid}?msg={tx_type} {qty} เรียบร้อย", status_code=303)


# ---------------- ป้ายลิ้นชัก / Export ----------------

def lan_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))  # ไม่ได้ส่งข้อมูลจริง แค่ให้ OS เลือก interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


@app.get("/labels", response_class=HTMLResponse)
def labels(request: Request, mode: str = "drawer", cabinet: str = "", only_used: str = ""):
    """mode=drawer: ป้ายหน้าลิ้นชัก (QR + รูปไอซีในลิ้นชัก) ; mode=part: ป้ายติดถุง (รูปไอซี + QR + ตำแหน่ง)"""
    # QR ต้องชี้ IP เครื่องในวง LAN (ไม่ใช่ localhost) มือถือถึงจะเปิดได้
    base = f"http://{lan_ip()}:{request.url.port or 80}"
    with connect() as con:
        cats = {c["code"]: c for c in categories(con)}
        grid = _drawer_grid(con)
        parts = con.execute(
            """SELECT p.*, (SELECT GROUP_CONCAT(DISTINCT COALESCE(pl.code, loc.code)) FROM stock_lots l
                              JOIN locations loc ON loc.id=l.location_id LEFT JOIN locations pl ON pl.id=l.planned_location_id
                              WHERE l.part_id=p.id AND (l.qty>0 OR l.planned_location_id IS NOT NULL)) AS locs
               FROM parts p ORDER BY p.category, p.mpn_norm""").fetchall() if mode == "part" else []
    drawers = [d for name, cab in grid.items() if not cabinet or name == cabinet
               for row in cab["rows"].values() for d in row if not only_used or d["items"]]
    return page(request, "labels.html", drawers=drawers, parts=[p for p in parts if p["locs"]], cats=cats,
                base=base, mode=mode, cabinet=cabinet, only_used=only_used, cabinets=list(grid.keys()))


@app.get("/qr")
def qr(data: str):
    import qrcode
    img = qrcode.make(data, box_size=6, border=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


@app.get("/export.xlsx")
def export_xlsx():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Stock"
    ws.append(["ตำแหน่ง", "แผนจะย้ายไป", "MPN", "หมวด", "ผู้ผลิต", "แพ็กเกจ", "คำอธิบาย", "จำนวน",
               "ผู้ขาย", "เบอร์ผู้ขาย", "Lot", "Date code", "เลขออเดอร์", "วันที่รับ"])
    with connect() as con:
        for r in con.execute(
                """SELECT loc.code, pl.code AS plan, p.mpn, p.category, p.manufacturer, p.package, p.description,
                          l.qty, l.supplier, l.supplier_pn, l.lot, l.date_code, l.order_no, l.received_at
                   FROM stock_lots l JOIN parts p ON p.id=l.part_id JOIN locations loc ON loc.id=l.location_id
                   LEFT JOIN locations pl ON pl.id=l.planned_location_id
                   ORDER BY loc.cabinet, loc.row, loc.col, p.mpn_norm"""):
            ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": "attachment; filename=ic_storage_stock.xlsx"})
