"""ค้นหาไอซีแบบเทียบหลายแหล่ง + หาตัวแทนจาก datasheet (ทำงานหลังบ้านทั้งหมด หน้าเว็บแสดงผลให้ผู้ใช้เลือกเอง)

ผู้ใช้ค้นด้วย: พิมพ์เบอร์ที่อ่านจากตัวชิปเอง / ถ่ายรูปตัวชิป (OCR) / สแกนฉลากถุง
1) เทียบกับคลัง: เบอร์หน้าซอง (MPN + ชื่อเรียกอื่น + เบอร์ผู้ขาย LCSC) และข้อความบนตัวชิป (marking ที่บันทึกไว้)
2) เทียบกับ datasheet: marking ที่ datasheet ระบุ, ชื่อตระกูล, เบอร์ในตารางสั่งซื้อ/marking ในไฟล์ข้อความ datasheet
   - datasheet ของเบอร์ในคลัง (D:\\IC_Datasheets\\NN_หมวด\\...) = ยืนยันว่าเจอถูกตัว
   - datasheet อ้างอิง (D:\\IC_Datasheets\\_อ้างอิง_ไม่มีในคลัง\\...) = รู้ว่าเบอร์ที่ค้นคืออะไร แม้ไม่มีในคลัง
3) ไม่มีในคลัง / ของหมด / ไม่แน่ใจ -> หาตัวแทนที่มีในคลัง:
   ตัวแทนที่ datasheet ระบุ (ทั้งสองทิศ) + เทียบขาต่อขา + เทียบค่าไฟ/คุณสมบัติ ; ไม่มี datasheet -> เทียบชื่อตระกูล/หมวด (ต้องตรวจเอง)
ระบบไม่สรุปแทนผู้ใช้: ทุกผลมีเหตุผลประกอบ ระดับความมั่นใจ และคำเตือน"""
import json
import re
import threading
from urllib.parse import quote

from .db import DATASHEET_DIR, STAGING
from .identify import canon
from .parts import guess_category, marking_tokens_of, norm

REF_DIR = "_อ้างอิง_ไม่มีในคลัง"
FOUND, LIKELY, MIN_SHOW = 0.85, 0.6, 0.4   # เกณฑ์: ตรงชัด / น่าจะใช่ (ให้ผู้ใช้ตรวจ) / ต่ำสุดที่แสดง

LEVEL_TH = {"drop-in": "ใส่แทนได้ทันที", "conditional": "แทนได้แบบมีเงื่อนไข", "pin-same": "ขาตรงกันทุกขา — ตรวจค่าก่อนใช้",
            "pin-diff": "ขาไม่ตรง — ห้ามใส่แทนตรง ๆ",
            "param-bad": "ขาตรงกัน แต่ค่าสำคัญไม่ผ่าน/ด้อยกว่า (ดูตาราง)", "name": "ชื่อตระกูลคล้าย — ไม่มี datasheet ให้เทียบ",
            "none": "ห้ามใช้แทน", "criteria": "เกณฑ์เลือกตัวแทน"}
LEVEL_ORDER = {"drop-in": 0, "conditional": 1, "pin-same": 2, "criteria": 3, "param-bad": 4, "name": 5, "pin-diff": 6, "none": 7}
SOURCE_TH = {"datasheet": "ยืนยันจาก datasheet", "engineering-verify": "ความรู้วิศวกรรม — ต้องตรวจ datasheet ตัวแทน",
             "auto": "ระบบเทียบขา/ค่าจาก datasheet อัตโนมัติ", "name": "เทียบจากชื่อเบอร์เท่านั้น"}

# ---------------- คลัง datasheet (โหลดใหม่เมื่อไฟล์เปลี่ยน) ----------------
_LIB: dict = {"sig": None, "items": [], "df": {}}
_LIB_LOCK = threading.Lock()


def split_names(text: str) -> list[str]:
    """'LM2904 / LM358', 'TJA1145T/FDJ, ATA6570-GNQW0', 'TCAN1042-Q1 (ไม่มี V)' -> ชื่อเบอร์ทีละตัว (norm แล้ว)
    '/' ที่ไม่มีช่องว่างเป็นส่วนของเบอร์ (TJA1145T/FD) ; ข้อความในวงเล็บ/ภาษาไทย = คำอธิบาย ไม่ใช่เบอร์"""
    out = []
    for part in re.split(r"\s+/\s+|,|;", re.sub(r"\([^)]*\)", " ", text or "")):
        w = part.strip().split(" ")[0] if part.strip() else ""
        n = norm(w)
        if len(n) >= 3 and re.search(r"\d", n) and not re.search(r"[฀-๿]", w) and n not in out:
            out.append(n)
    return out


def _family_names(fam: str) -> list[str]:
    """'LP358 / LP2904' -> [LP358, LP2904] ; 'OPAx348' -> [OPA348] (x = ตัวเลขจำนวนช่อง)"""
    out = []
    for f in re.split(r"\s*/\s*|,", fam or ""):
        f = re.sub(r"(?<=[A-Z])x(?=\d)", "", f.strip())
        n = norm(f.split(" ")[0])
        if len(n) >= 3 and re.search(r"\d", n):
            out.append(n)
    return out


def _text_words(path) -> set[str]:
    try:
        t = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    return {w for w in (norm(x) for x in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/.]{2,30}", t)) if len(w) >= 3}


def library() -> dict:
    """{items: [...], df: {คำ: จำนวน datasheet ที่มีคำนี้}} — แคชไว้จนกว่าไฟล์ spec.json จะเปลี่ยน"""
    files = sorted(DATASHEET_DIR.glob("[0-9][0-9]_*/*/spec.json")) + sorted((DATASHEET_DIR / REF_DIR).glob("*/spec.json"))
    sig = []
    for f in files:
        try:
            sig.append((str(f), f.stat().st_mtime))
        except OSError:
            pass
    sig = tuple(sig)
    with _LIB_LOCK:
        if sig == _LIB["sig"]:
            return _LIB
        items, df = [], {}
        for f in files:
            try:
                s = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            folder = f.parent.relative_to(DATASHEET_DIR).as_posix()
            ds = s.get("datasheet") or {}
            words = _text_words(f.parent / ds["text"]) if ds.get("text") else set()
            for w in words:
                df[w] = df.get(w, 0) + 1
            n = norm(s.get("mpn", ""))
            names = [n] + [x for x in _family_names(s.get("family", "")) if x != n]
            items.append({"mpn": s.get("mpn", ""), "n": n, "spec": s, "ref": folder.startswith("_"), "folder": folder,
                          "names": names, "marks": [norm(m) for m in s.get("marking") or [] if len(norm(m)) >= 3],
                          "words": words,
                          "pdf_url": "/datasheets/" + quote(f"{folder}/{ds['file']}") if ds.get("file") else ""})
        _LIB.update(sig=sig, items=items, df=df)
        return _LIB


# ---------------- เทียบชื่อ ----------------

def name_rel(q: str, n: str) -> tuple[float, str]:
    """q = คำค้น, n = ชื่อเบอร์ (norm แล้วทั้งคู่) -> (คะแนน, ชนิดความสัมพันธ์)"""
    if not q or not n:
        return 0.0, ""
    if q == n:
        return 1.0, "exact"
    if len(n) >= 5 and q.startswith(n) and re.search(r"\d", n):
        return 0.88, "suffix"           # ค้นเบอร์เต็มรหัสสั่งซื้อ เช่น BTS724GXUMA1 -> BTS724G
    if len(q) >= 4 and n.startswith(q) and re.search(r"\d", q):
        return round(0.7 + 0.12 * len(q) / len(n), 3), "base"   # ค้นเบอร์หลัก เช่น TJA1042 -> TJA1042T/1J (รุ่นย่อยต้องตรวจ)
    cq, cn = canon(q), canon(n)
    if len(q) >= 3 and cq == cn:
        return 0.86, "ocr"              # ต่างแค่ตัวที่อ่านสลับกันบ่อย O/0 B/8 S/5
    if len(q) >= 5 and len(cn) >= 5 and (cn.startswith(cq) or cq.startswith(cn)):
        return 0.66, "ocr-base"
    return 0.0, ""


REL_TH = {"exact": "ตรง", "suffix": "ตรง (ต่างรหัสสั่งซื้อ/บรรจุ)", "base": "ตรงเบอร์หลัก (รุ่นย่อยต้องตรวจ)",
          "ocr": "ใกล้เคียง (ตัวอักษรคล้าย O/0, B/8, S/5)", "ocr-base": "คล้ายบางส่วน (ตัวอักษรคล้าย)"}


def query_tokens(text: str = "", tokens: list[str] | None = None) -> list[str]:
    """คำค้นทั้งข้อความ (ไม่สนช่องว่าง/ขีด) + แยกคำตามที่พิมพ์บนชิป (TI OPA 2348A -> OPA, 2348A)"""
    out = []
    whole = norm(text)
    if whole:
        out.append(whole)
    for t in marking_tokens_of(text) + list(tokens or []):
        t = norm(t)
        if t and t not in out:
            out.append(t)
    return out


# ---------------- เทียบกับคลัง ----------------

def _parts(con) -> list[dict]:
    rows = con.execute(
        """SELECT p.id, p.mpn, p.mpn_norm, p.marking, p.category, p.package, p.image, p.description, p.manufacturer,
                  COALESCE((SELECT SUM(qty) FROM stock_lots l WHERE l.part_id=p.id),0) AS qty
           FROM parts p""").fetchall()
    al: dict[int, list] = {}
    for a in con.execute("SELECT part_id, alias_norm, kind FROM part_aliases WHERE kind<>'MARKING'"):
        al.setdefault(a["part_id"], []).append((a["alias_norm"], a["kind"]))
    out = []
    for r in rows:
        d = dict(r)
        d["labels"] = [(r["mpn_norm"], "MPN")] + al.get(r["id"], [])
        d["marks"] = marking_tokens_of(r["marking"] or "")
        out.append(d)
    return out


def _stock_item(lib: dict, part: dict) -> dict | None:
    """datasheet ของเบอร์ในคลัง (ตามชื่อหน้าซอง)"""
    names = {n for n, _ in part["labels"]}
    return next((it for it in lib["items"] if not it["ref"] and it["n"] in names), None)


def _score_part(p: dict, toks: list[str], shared: dict, item: dict | None, df: dict,
                ocr: set[str] | None = None) -> tuple[float, list[dict]]:
    best, ev = 0.0, []
    hits = set()

    def add(score, kind, text):
        nonlocal best
        best = max(best, score)
        ev.append({"kind": kind, "text": text, "score": round(score, 2)})

    for t in toks:
        # เบอร์หน้าซอง / ชื่อเรียกอื่น / เบอร์ผู้ขาย
        s_l, k_l, how = 0.0, "", ""
        for n, kind in p["labels"]:
            s, k = name_rel(t, n)
            if s > s_l:
                s_l, k_l, how = s, k, kind
        if s_l:
            src = {"MPN": "เบอร์หน้าซอง", "LCSC": "เบอร์ LCSC"}.get(how, "ชื่อเรียกอื่น")
            add(s_l, "label", f"{src} {REL_TH[k_l]}: {t}")
            hits.add(t)
        # ข้อความบนตัวชิปที่บันทึกไว้ (คำที่ซ้ำหลายเบอร์ แยกเบอร์ไม่ได้ -> คะแนนต่ำ)
        s_m, k_m, mk = 0.0, "", ""
        for m in p["marks"] + (["".join(p["marks"])] if len(p["marks"]) > 1 else []):
            s, k = name_rel(t, m)
            if s and shared.get(canon(m), 0) > 1:
                s = min(s, 0.55)
            if s > s_m:
                s_m, k_m, mk = min(s, 0.97), k, m
        if s_m:
            add(s_m, "marking", f"ข้อความบนชิป{REL_TH[k_m]}: {t}" + (" (มีหลายเบอร์ใช้คำนี้)" if s_m <= 0.55 else ""))
            hits.add(t)
        elif ocr and t in ocr and len(t) >= 5:
            # คำที่ OCR อ่านจากรูป ต่างจากข้อความบนชิปที่บันทึกไว้ 1 ตัว (เช่น 2133CU9 / 2133CUS) -> น่าจะใช่ ให้ผู้ใช้ตรวจ
            from .ocrtext import _lev
            near = next((m for m in p["marks"] if len(m) >= 5 and _lev(canon(t), canon(m), 1) == 1), None)
            if near and shared.get(canon(near), 0) <= 1:
                add(0.68, "marking", f"ข้อความบนชิปคล้าย (ต่าง 1 ตัว): {t} ≈ {near}")
        # datasheet ของเบอร์นี้
        if item:
            if t in item["marks"]:
                add(min(best + 0.05, 0.99) if best >= FOUND else 0.9, "datasheet", f"datasheet ระบุ marking {t}")
                hits.add(t)
            elif any(name_rel(t, n)[1] in ("exact", "suffix") or (name_rel(t, n)[1] == "base" and len(n) - len(t) <= 1)
                     for n in item["names"][1:]):
                add(0.75, "datasheet", f"ชื่อตระกูลตาม datasheet: {t}")
            elif len(t) >= 4 and t in item["words"] and df.get(t, 0) <= 2:
                add(max(0.55, best), "datasheet", f"พบ {t} ใน datasheet ของเบอร์นี้ (เช่น ตารางสั่งซื้อ/marking)")
    # ข้อความบางส่วนตรง (พิมพ์ค้นไปเรื่อย ๆ)
    if not ev:
        for t in toks[:1]:
            if len(t) >= 2 and (t in p["mpn_norm"] or any(t in n for n, _ in p["labels"])
                                or t in (p["marking"] or "").upper().replace(" ", "")):
                add(MIN_SHOW, "partial", f"มี “{t}” อยู่ในเบอร์/ข้อความบนชิป")
    if len(hits) > 1 and best >= LIKELY:
        best = min(1.0, best + 0.03 * (len(hits) - 1))   # หลายคำตรงเบอร์เดียวกัน (เช่น OCR อ่านได้ทั้ง 2 บรรทัด)
    if best >= FOUND and any(e["kind"] == "datasheet" for e in ev):
        best = min(1.0, best + 0.02)
    return round(best, 3), ev


# ---------------- เทียบขา / ค่า ----------------
_PIN_SYN = {"VS": "BAT", "VBAT": "BAT", "NCS": "SCSN", "CS": "SCSN", "VCC1": "VDD1", "VCC2": "VDD2", "N.C.": "NC",
            "VIN": "IN", "VOUT": "OUT", "CE": "EN", "VSS": "GND", "STBY": "STB", "SHTDN": "SHDN",
            "SCLK": "SCK", "VSUP": "BAT", "A": "ANODE", "K": "CATHODE"}


def pin_name(s: str) -> str:
    s = re.sub(r"\s*\(.*\)$", "", (s or "").strip()).upper().replace(" ", "")
    if s.startswith("NC/"):
        s = s[3:]
    return _PIN_SYN.get(s, s)


def pin_diff(a: dict, b: dict):
    """None = เทียบไม่ได้ (ไม่มีข้อมูลขา/จำนวนขาไม่เท่ากัน) ; [] = เหมือนทุกขา ; [{pin, a, b}] = ขาที่ต่าง"""
    pa, pb = a.get("pinout") or {}, b.get("pinout") or {}
    if not pa or set(pa) != set(pb):
        return None
    return [{"pin": k, "a": pa[k], "b": pb[k]} for k in pa if not pin_same(pa[k], pb[k])]


def pin_same(x: str, y: str) -> bool:
    """ชื่อขาเดียวกัน — รวมขาหลายหน้าที่ เช่น "nINT/SDO" = "SDO", "INH/LIMP" = "INH" (หน้าที่หลักตรงกัน)"""
    a, b = pin_name(x), pin_name(y)
    if a == b:
        return True
    sa, sb = ({_PIN_SYN.get(t, t) for t in n.split("/") if t} for n in (a, b))
    return (len(sa) > 1 or len(sb) > 1) and bool(sa & sb)


_P_SYN = {"vs_v": "supply_v", "vcc_v": "supply_v", "vdd_v": "supply_v", "vin_v": "supply_v", "vbb_v": "supply_v"}
PARAM_TH = {"supply_v": "ไฟเลี้ยง (V)", "vio_v": "ไฟ VIO (V)", "data_rate_mbps": "ความเร็ว (Mbps)", "bus_fault_v": "ทนบัสลัด (±V)",
            "ta_c": "อุณหภูมิใช้งาน (°C)", "tvj_c": "อุณหภูมิรอยต่อ (°C)", "tj_c": "อุณหภูมิรอยต่อ (°C)",
            "vio_pin": "มีขา VIO", "mode_pin8": "หน้าที่ขา 8", "gbw_mhz": "GBW (MHz)", "iq_per_ch_ua": "กินไฟ/ช่อง (µA)",
            "rail_to_rail": "Rail-to-rail", "channels": "จำนวนช่อง", "vout_v": "แรงดันออก (V)", "iout_ma": "กระแสออก (mA)",
            "iq_ua": "กินไฟ (µA)", "isw_a": "กระแสสวิตช์ (A)", "fsw_khz": "ความถี่ (kHz)", "vref_v": "แรงดันอ้างอิง (V)",
            "vref_tol_pct": "ความแม่น (±%)", "vka_max_v": "แรงดัน K-A สูงสุด (V)", "ik_ma": "กระแส K (mA)", "vz_v": "แรงดันซีเนอร์ (V)",
            "vz_tol_pct": "คลาดเคลื่อน (±%)", "ptot_mw": "กำลัง (mW)", "iopk_a": "กระแสขับยอด (A)", "peak_current_a": "กระแสขับยอด (A)",
            "cmti_kv_us": "CMTI (kV/µs)", "iso_vrms": "ฉนวน (Vrms)", "grade": "เกรด", "spi": "คุมด้วย SPI", "vds_v": "Vds (V)",
            "forward": "ช่องไปข้างหน้า", "reverse": "ช่องย้อนกลับ", "fail_safe": "fail-safe", "logic_family": "ตระกูลลอจิก",
            "output_drive_ma": "กระแสขาออก (mA)", "standard": "มาตรฐาน", "type": "ชนิด",
            # ไดโอด / ทรานซิสเตอร์ / มอสเฟต / C / หน่วยความจำ
            "polarity": "ขั้ว/ชนิด", "id_a": "กระแส Id (A)", "rds_on_max_mohm": "Rds(on) สูงสุด (mΩ)", "vgs_max_v": "Vgs สูงสุด (V)",
            "vceo_v": "Vceo (V)", "ic_a": "กระแส Ic (A)", "vrrm_v": "ทนแรงดันย้อน (V)", "if_a": "กระแสไปหน้า (A)",
            "vf_v": "แรงดันตกไปหน้า (V)", "config": "การต่อภายใน", "cap_uf": "ความจุ (µF)", "rated_v": "ทนแรงดัน (V)",
            "dielectric": "ชนิดเนื้อ C", "size": "ขนาดตัว", "memory_kbit": "ความจุหน่วยความจำ (kbit)", "cells": "จำนวนเซลล์"}
_MUST_EQUAL = {"vout_v", "vz_v", "vref_v", "channels", "vio_pin", "spi", "type", "forward", "reverse", "mode_pin8",
               "polarity", "config", "cap_uf", "size", "memory_kbit"}
_HIGHER_OK = {"data_rate_mbps", "bus_fault_v", "iout_ma", "isw_a", "iopk_a", "peak_current_a", "vka_max_v", "ptot_mw",
              "iso_vrms", "cmti_kv_us", "output_drive_ma", "id_a", "vceo_v", "ic_a", "vrrm_v", "if_a", "rated_v", "vds_v",
              "cells"}
_RANGE_COVER = {"supply_v", "ta_c", "tvj_c", "tj_c", "vio_v", "ik_ma"}


def _params(spec: dict) -> dict:
    p = {}
    for k, v in (spec.get("params") or {}).items():
        p.setdefault(_P_SYN.get(k, k), v)
    if "iq_per_ch_ua" not in p and isinstance(p.get("iq_total_ua"), (int, float)) and p.get("channels"):
        p["iq_per_ch_ua"] = round(p["iq_total_ua"] / p["channels"], 1)
    return p


def _fmt(v) -> str:
    if isinstance(v, list) and len(v) == 2:
        return f"{'' if v[0] is None else v[0]} – {'' if v[1] is None else v[1]}"
    if isinstance(v, bool):
        return "มี" if v else "ไม่มี"
    return str(v)


def param_rows(target: dict, cand: dict) -> list[dict]:
    """เทียบค่าที่มีทั้งสองเบอร์: ok = ใช้ได้, warn = ด้อยกว่า/ต้องตรวจ, bad = ไม่ตรง (ห้าม), info = แค่แสดง"""
    a, b = _params(target), _params(cand)
    rows = []
    for k in a:
        if k not in b or k not in PARAM_TH:
            continue
        x, y, flag = a[k], b[k], "info"
        if k in _RANGE_COVER and isinstance(x, list) and isinstance(y, list) and len(x) == len(y) == 2:
            lo_ok = x[0] is None or (y[0] is not None and y[0] <= x[0])
            hi_ok = x[1] is None or (y[1] is not None and y[1] >= x[1])
            overlap = (y[1] is None or x[0] is None or y[1] >= x[0]) and (y[0] is None or x[1] is None or y[0] <= x[1])
            flag = "ok" if lo_ok and hi_ok else ("warn" if overlap else "bad")
        elif k in _MUST_EQUAL:
            flag = "ok" if (str(x).split(" ")[0] == str(y).split(" ")[0]) else "bad"
        elif k in _HIGHER_OK and isinstance(x, (int, float)) and isinstance(y, (int, float)):
            flag = "ok" if abs(y) >= abs(x) else "warn"
        elif x == y:
            flag = "ok"
        rows.append({"key": k, "name": PARAM_TH[k], "a": _fmt(x), "b": _fmt(y), "flag": flag})
    return rows


# ---------------- ตัวแทน ----------------

def _declared(spec: dict, names: list[str]) -> dict | None:
    """รายการตัวแทนใน spec ที่เอ่ยถึงเบอร์ใดเบอร์หนึ่งใน names"""
    best, best_sc = None, 0.0
    for x in spec.get("substitutes") or []:
        for sn in split_names(x.get("mpn", "")):
            for n in names:
                sc = max(name_rel(sn, n)[0], name_rel(n, sn)[0])
                if sc >= 0.8 and sc > best_sc:   # หลายรายการตรง -> เอาที่ตรงที่สุด (BZX84-C2V4 ตรงกว่า "BZX84-B16")
                    best, best_sc = x, sc
    return best


def substitutes(lib: dict, stock: dict[str, dict], target: dict | None, query: str, exclude: set[int]) -> list[dict]:
    """ตัวแทนที่มีในคลัง สำหรับ target (datasheet ของเบอร์ที่ค้น) ; target = None -> เทียบจากชื่อ/หมวดเท่านั้น
    stock = {mpn_norm ของ datasheet: แถวเบอร์ในคลัง}"""
    out = []
    tnames = (target["names"] if target else []) + ([query] if query else [])
    for it in lib["items"]:
        part = stock.get(it["n"])
        if it["ref"] or not part or part["id"] in exclude or (target and it["n"] == target["n"]):
            continue
        c = {"part": part, "item": it, "level": "", "source": "", "note": "", "from": "", "pins": None, "params": []}
        fwd = _declared(target["spec"], it["names"] + [n for n, _ in part["labels"]]) if target else None
        rev = _declared(it["spec"], tnames)
        decl = fwd or rev
        if decl:
            note = decl.get("note", "")
            if not fwd and target:
                # หมายเหตุจาก datasheet ของตัวแทน เขียนไว้สำหรับกรณีกลับกัน (เอาเบอร์ที่ค้นไปใส่แทนตัวแทน) -> บอกให้ชัด
                note = f"มุมกลับ — datasheet ของ {it['mpn']} เขียนกรณีใช้ {target['mpn']} แทน {it['mpn']}: {note}"
            c.update(level=decl.get("level", "conditional"), source=decl.get("source", ""), note=note,
                     **{"from": f"datasheet ของ {(target['mpn'] if fwd else it['mpn'])}"})
        if target:
            c["pins"] = pin_diff(target["spec"], it["spec"])
            c["params"] = param_rows(target["spec"], it["spec"])
            same_cat = it["spec"].get("category") == target["spec"].get("category")
            if not decl:
                if c["pins"] == [] and same_cat:
                    c.update(level="pin-same", source="auto")
                elif c["pins"] and same_cat and len(c["pins"]) <= max(2, len(target["spec"].get("pinout") or {}) // 3):
                    c.update(level="pin-diff", source="auto")
            elif c["pins"] and decl.get("level") == "drop-in":
                c["level"] = "conditional"   # datasheet บอกแทนได้ แต่ขาต่าง -> ลดระดับ ให้ผู้ใช้อ่านหมายเหตุ
            # ค่าที่ไม่ผ่าน หรือไฟเลี้ยงที่รับได้ไม่ครอบคลุมช่วงของเบอร์เดิม (บอร์ดอาจใช้ไฟสูงกว่าที่ตัวแทนทนได้)
            if any(r["flag"] == "bad" or (r["key"] == "supply_v" and r["flag"] == "warn") for r in c["params"]):
                if c["level"] == "drop-in":
                    c["level"] = "conditional"
                elif c["level"] == "pin-same":
                    c["level"] = "param-bad"
        if not c["level"]:
            continue
        out.append(c)
    if not target:   # ไม่รู้จักเบอร์นี้: เทียบจากเลขตระกูล/หมวด (อ่อนมาก ต้องเทียบ datasheet เอง)
        digits = max(re.findall(r"\d{3,}", query) or [""], key=len)
        cat = guess_category(query)
        have = {c["part"]["id"] for c in out}
        for it in lib["items"]:
            part = stock.get(it["n"])
            if it["ref"] or not part or part["id"] in have or part["id"] in exclude:
                continue
            why = []
            if digits and any(digits in n for n in it["names"] + [norm(m) for m in part["marks"]]):
                why.append(f"เลขตระกูล {digits} เหมือนกัน")
            pre = re.match(r"[A-Z]+\d{2}", query)
            if pre and any(n.startswith(pre.group(0)) for n in it["names"]):
                why.append(f"ขึ้นต้น {pre.group(0)} เหมือนกัน")
            if why and cat != "OTH" and it["spec"].get("category") == cat:
                why.append("หมวดเดียวกัน")
            if why:
                out.append({"part": part, "item": it, "level": "name", "source": "name", "note": " · ".join(why),
                            "from": "", "pins": None, "params": []})
    out.sort(key=lambda c: (LEVEL_ORDER.get(c["level"], 9), c["part"]["qty"] <= 0, c["part"]["mpn"]))
    return out


# ---------------- รวม ----------------

def _identify_lib(lib: dict, toks: list[str]) -> list[tuple[float, dict, str]]:
    """เบอร์ที่ค้นคืออะไรตามคลัง datasheet (รวม datasheet อ้างอิงที่ไม่มีในคลัง)"""
    res = []
    for it in lib["items"]:
        best, why = 0.0, ""
        for t in toks:
            for i, n in enumerate(it["names"]):
                s, k = name_rel(t, n)
                if i and k not in ("exact", "suffix") and not (k == "base" and len(n) - len(t) <= 1):
                    continue   # ชื่อตระกูล: ต้องตรง (ยอมขาดท้าย 1 ตัว เช่น MC34063 = MC34063A) ป้องกันจับผิดตัว
                if s > best:
                    best, why = s, ("เบอร์" if i == 0 else "ตระกูล") + f" {REL_TH.get(k, k)}"
            if t in it["marks"] and 0.9 > best:
                best, why = 0.9, f"datasheet ระบุ marking {t}"
            if best < 0.6 and len(t) >= 5 and t in it["words"] and lib["df"].get(t, 0) <= 3:
                best, why = 0.6, f"พบ {t} ใน datasheet"
        if best >= LIKELY:
            res.append((best, it, why))
    res.sort(key=lambda x: -x[0])
    return res


def ds_info(it: dict | None) -> dict | None:
    if not it:
        return None
    s = it["spec"]
    return {"mpn": it["mpn"], "ref": it["ref"], "function_th": s.get("function_th", ""), "manufacturer": s.get("manufacturer", ""),
            "package": (s.get("package") or {}).get("name", ""), "pdf_url": it["pdf_url"],
            "summary_url": "", "has_pinout": bool(s.get("pinout")),
            "revision": (s.get("datasheet") or {}).get("revision", "")}


def match(con, text: str = "", tokens: list[str] | None = None, extra: dict[int, dict] | None = None,
          limit: int = 8, logo: str = "") -> dict:
    """text = ที่ผู้ใช้พิมพ์/สแกนได้ ; tokens = คำที่ OCR อ่านจากตัวชิป ;
    extra = {part_id: {score, reasons}} ผลเทียบรูปร่างจาก identify (รูปชิป)"""
    lib = library()
    toks = query_tokens(text, tokens)
    query = toks[0] if toks else ""
    parts = _parts(con)
    shared: dict[str, int] = {}
    for p in parts:
        for m in {canon(x) for x in p["marks"]}:
            shared[m] = shared.get(m, 0) + 1
    stock: dict[str, dict] = {}
    for p in parts:
        it = _stock_item(lib, p)
        p["item"] = it
        if it:
            stock[it["n"]] = p

    found = []
    for p in parts:
        score, ev = _score_part(p, toks, shared, p["item"], lib["df"], {norm(t) for t in tokens or []}) if toks else (0.0, [])
        # โลโก้ผู้ผลิตบนชิปตรงกับผู้ผลิตของเบอร์นี้ (หลักฐานเสริม ไม่ใช้ตัดสินลำพัง)
        brand = (p.get("manufacturer") or ((p["item"] or {}).get("spec") or {}).get("manufacturer") or "").lower()
        if logo and score >= 0.5 and brand and logo.split()[0].lower() in brand:
            ev.append({"kind": "photo", "text": f"โลโก้ผู้ผลิตตรง ({logo})", "score": 0.0})
            score = round(min(1.0, score + 0.05), 3)
        x = (extra or {}).get(p["id"])
        if x and x.get("score", 0) > 0:
            for r in x.get("reasons") or []:
                ev.append({"kind": "photo", "text": r, "score": x["score"]})
            score = max(score, x["score"]) + (0.05 if score >= 0.5 and x["score"] >= 0.4 else 0)
            score = round(min(1.0, score), 3)
        if score >= MIN_SHOW:
            found.append({"part": p, "score": score, "evidence": ev})
    found.sort(key=lambda m: (-m["score"], m["part"]["mpn"]))
    found = found[:limit]

    ident = _identify_lib(lib, toks) if toks else []
    best = found[0] if found else None
    if best and best["score"] >= FOUND:
        status = "found" if best["part"]["qty"] > 0 else "out_of_stock"
    elif best and best["score"] >= LIKELY:
        status = "uncertain"
    else:
        status = "not_found"
    if status == "found" and sum(1 for m in found if m["score"] >= FOUND) > 1:
        status = "multiple"

    # เบอร์ที่ค้น ตาม datasheet: ของในคลังที่ตรงชัด หรือ datasheet อ้างอิง
    target = None
    if best and best["score"] >= FOUND and best["part"]["item"]:
        target = best["part"]["item"]
    elif ident and ident[0][0] >= 0.75:
        target = ident[0][1]
    subs = []
    if status != "found":
        exclude = {m["part"]["id"] for m in found if m["score"] >= FOUND}
        if target and not target["ref"] and stock.get(target["n"]):
            exclude.add(stock[target["n"]]["id"])
        subs = substitutes(lib, stock, target, query, exclude)[:8]

    def card(p):
        return {"part_id": p["id"], "mpn": p["mpn"], "image": p["image"] or "", "category": p["category"],
                "package": p["package"] or "", "marking": p["marking"] or "", "qty": p["qty"]}

    return {
        "query": text.strip() or " ".join(tokens or []), "tokens": toks, "status": status, "logo": logo,
        "matches": [card(m["part"]) | {"score": m["score"], "pct": round(m["score"] * 100),
                                       "level": "strong" if m["score"] >= FOUND else ("likely" if m["score"] >= LIKELY else "weak"),
                                       "evidence": m["evidence"],
                                       "ds_confirm": any(e["kind"] == "datasheet" for e in m["evidence"]),
                                       "datasheet": ds_info(m["part"]["item"])} for m in found],
        "identified": [{"score": s, "why": w} | ds_info(it) for s, it, w in ident[:3]],
        "target": ds_info(target),
        "substitutes": [card(c["part"]) | {"level": c["level"], "level_th": LEVEL_TH.get(c["level"], c["level"]),
                                           "source_th": SOURCE_TH.get(c["source"], c["source"]), "note": c["note"],
                                           "from": c["from"], "pins": c["pins"], "params": c["params"],
                                           "datasheet": ds_info(c["item"])} for c in subs],
    }
