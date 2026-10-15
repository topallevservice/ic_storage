"""ระบุไอซีที่ไม่มีถุงฉลาก จากรูปถ่ายตัวชิป
1) อ่าน marking บนตัวชิป (OCR) แล้วเทียบกับ MPN / ชื่อเรียกอื่น / marking ที่เคยบันทึก
2) เทียบรูปร่าง/ลวดลายกับรูปไอซีในคลัง (ORB จับลวดลายตัวหนังสือ-โลโก้, HOG จับทรงตัวถัง-ขา, สีตัวถัง)
คืนรายการเบอร์ในคลังที่น่าจะเป็นตัวเดียวกัน ให้ผู้ใช้ยืนยันเองเสมอ (ระบบไม่สรุปเอง)"""
import os
import re
import threading
from difflib import SequenceMatcher

import cv2
import numpy as np
from PIL import Image

from .db import IMAGE_DIR
from .parts import MARK_STOP, is_date_code, marking_tokens_of, norm  # noqa: F401 (is_date_code ใช้ที่อื่นผ่านโมดูลนี้)

# ตัวที่ OCR อ่านสลับกันบ่อยบนตัวชิป -> แปลงทั้งสองฝั่งให้เป็นตัวเดียวกันก่อนเทียบ
_CONFUSE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "B": "8", "S": "5", "Z": "2", "G": "6"})


def canon(s: str) -> str:
    return norm(s).translate(_CONFUSE)


# ---------------- เบอร์ / marking ----------------

def _ocr_chars(arr: np.ndarray) -> list[dict]:
    """OCR 1 ครั้ง: คืนบรรทัด พร้อมตำแหน่ง/ความมั่นใจของแต่ละตัวอักษร (ใช้แยกโลโก้ออกจากตัวหนังสือ)"""
    from . import barcodes
    with barcodes._OCR_LOCK:
        res = barcodes._ocr_engine()(arr, return_word_box=True, return_single_char_box=True)
    out = []
    for txt, sc, wr, box in zip(res.txts or [], res.scores or [], res.word_results or [], res.boxes if res.boxes is not None else []):
        chars = [(c[0], float(c[1]), float(c[2][0][0]), float(c[2][1][0])) for c in wr if c and c[0]]
        ys = [p[1] for p in box]
        out.append({"text": txt, "score": float(sc), "chars": chars, "y": (min(ys) + max(ys)) / 2, "h": max(ys) - min(ys)})
    return out


def _groups(line: dict) -> list[list[tuple]]:
    """แบ่งตัวอักษรในบรรทัดเป็นกลุ่มตามช่องว่างจริงบนชิป (ระยะห่าง > 45% ของความกว้างตัวอักษร)
    ตัวอักษรเล็กมาก OCR ให้กรอบเดียวกันทั้งคำ -> แบ่งตามช่องว่างในข้อความแทน"""
    chars = line["chars"]
    if not chars:
        return [[(c, line["score"], 0, 0) for c in w] for w in line["text"].split()]
    widths = sorted(c[3] - c[2] for c in chars if c[3] > c[2])
    if not widths or len({(c[2], c[3]) for c in chars}) <= 1:
        return [[(c, line["score"], 0, 0) for c in w] for w in line["text"].split()]
    med = widths[len(widths) // 2]
    groups, cur = [], [chars[0]]
    for a, b in zip(chars, chars[1:]):
        if b[2] - a[3] > 0.45 * med:
            groups.append(cur)
            cur = []
        cur.append(b)
    groups.append(cur)
    return groups


def clean_line(line: dict) -> tuple[str, str, list[dict]]:
    """แยกบรรทัดเป็น: ข้อความ (ตัดโลโก้ทิ้ง), ผู้ผลิตจากโลโก้, รายการคำพร้อมชนิด
    - โลโก้ที่ OCR อ่านเป็นตัวอักษร: 1–2 ตัวแรกของบรรทัดที่มีช่องว่างคั่นจากข้อความ และความมั่นใจต่ำ/เป็นสัญลักษณ์
      (เช่น "4·EB5", "@·423") — ใช้ระยะห่างจริงบนชิปเฉพาะตรงนี้ (ช่องไฟในเบอร์ไม่สม่ำเสมอ ห้ามใช้ตัดคำ)
    - สัญลักษณ์/ตัวอักษรนอกภาษาอังกฤษ = ลายโลโก้ (เช่น "OLML立") ; ชื่อผู้ผลิต (TI, NXP, Infineon) = โลโก้ผู้ผลิต"""
    from .ocrtext import junk, kind, mfr_fuzzy
    text = line["text"].strip()
    items, logo = [], ""
    gs = _groups(line)
    if len(gs) > 1 and gs[0] and gs[0][0][2] != gs[0][0][3]:
        head = "".join(c[0] for c in gs[0])
        mean = sum(c[1] for c in gs[0]) / len(gs[0])
        core = re.sub(r"[^A-Za-z0-9]", "", head)
        m = mfr_fuzzy(core) if core.isalpha() else None
        if m and text.startswith(head) and not text.startswith(head + " "):
            text = head + " " + text[len(head):]   # โลโก้ผู้ผลิตติดกับข้อความ (TI57A1LGM) -> แยกคำ
        elif len(core) <= 2 and (mean < 0.9 or core != head) and not m and text.startswith(head):
            items.append({"text": head, "kind": "logo"})   # สัญลักษณ์โลโก้หน้าข้อความ
            text = text[len(head):].strip()
    words = []
    for w in text.split():
        core = re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9/]+$", "", w)   # ตัดสัญลักษณ์ที่ติดหัว/ท้าย (โลโก้ @ ฯลฯ)
        m = mfr_fuzzy(core) if core.isalpha() else None
        if m:
            logo = logo or m[0]
            words.append(m[1])
            items.append({"text": m[1], "kind": "mfr", "mfr": m[0]})
        elif not core or re.search(r"[^\x20-\x7e]", w) or (junk(w) and not re.fullmatch(r"[A-Za-z0-9/\-.]+", core)):
            # ตัวอักษรนอกภาษาอังกฤษ (立 口 豆) = OCR อ่านลายโลโก้/ลายพื้น -> ไม่ใช่ข้อความบนชิป
            items.append({"text": w, "kind": "logo"})
        else:
            words.append(core)
            items.append({"text": core, "kind": kind(core)})
    return " ".join(words), logo, items


def _quality(lines: list[dict]) -> float:
    """คุณภาพผลอ่าน: ผลรวม ความมั่นใจ × จำนวนตัวอักษร/ตัวเลข ของบรรทัดที่เชื่อได้"""
    return sum(ln["score"] * len(re.sub(r"[^A-Za-z0-9]", "", ln["text"])) for ln in lines if ln["score"] >= 0.6)


def _enhance(img: Image.Image) -> np.ndarray:
    """ตัวหนังสือเลเซอร์บนชิปสีเทาจาง: เพิ่มคอนทราสต์เฉพาะที่ (CLAHE) แล้วคืนเป็นภาพสี 3 ช่อง"""
    g = cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    g = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(g)
    return cv2.cvtColor(g, cv2.COLOR_GRAY2RGB)


def read_chip(img: Image.Image) -> dict:
    """อ่านตัวชิป: หาทิศที่อ่านได้ดีที่สุด (0/90/180/270°) + ภาพเพิ่มคอนทราสต์ แล้วแยก ตัวหนังสือ / ตัวเลข / โลโก้ผู้ผลิต
    คืน {lines: บรรทัดที่ตัดโลโก้แล้ว, tokens: คำที่ใช้ค้นหา, logo: ผู้ผลิตจากโลโก้, items: คำพร้อมชนิด, rotation}"""
    best = None
    for rot in (0, 90, 180, 270):
        r = img.rotate(rot, expand=True)
        lines = [ln for ln in _ocr_chars(np.asarray(r.convert("RGB"))) if ln["score"] >= 0.6]
        q = _quality(lines)
        if best is None or q > best[0]:
            best = (q, rot, lines, r)
        # อ่านได้ชัดแล้ว (มีคำยาว ≥ 4 ที่มั่นใจ ≥ 0.9) ไม่ต้องหมุนต่อ
        if any(ln["score"] >= 0.9 and len(re.sub(r"[^A-Za-z0-9]", "", ln["text"])) >= 4 for ln in lines):
            break
    q, rot, lines, r = best
    if lines and min(ln["score"] for ln in lines) < 0.9 and not os.environ.get("IC_OCR_NOCLAHE"):
        # บางบรรทัดอ่านไม่ชัด: ลองภาพเพิ่มคอนทราสต์ แล้วเลือกผลที่มั่นใจกว่าทีละบรรทัด (เทียบตำแหน่งแนวตั้ง)
        for ln2 in (x for x in _ocr_chars(_enhance(r)) if x["score"] >= 0.6):
            same = next((ln for ln in lines if abs(ln["y"] - ln2["y"]) < max(ln["h"], ln2["h"]) * 0.5), None)
            if same is None:
                lines.append(ln2)
            elif ln2["score"] > same["score"] + 0.03:
                lines[lines.index(same)] = ln2
        lines.sort(key=lambda ln: ln["y"])
    out_lines, logo, items = [], "", []
    for ln in lines:
        text, lg, its = clean_line(ln)
        logo = logo or lg
        items += its
        if text and text not in out_lines:
            out_lines.append(text)
    return {"lines": out_lines, "tokens": marking_tokens(out_lines), "logo": logo, "items": items, "rotation": rot,
            "raw": [ln["text"] for ln in lines]}


def read_marking(img: Image.Image) -> tuple[list[str], list[str]]:
    """OCR ตัวหนังสือบนตัวชิป คืน (บรรทัดที่อ่านได้ ตัดโลโก้แล้ว, token ที่ใช้ค้นหา)"""
    r = read_chip(img)
    return r["lines"], r["tokens"]


def marking_tokens(lines: list[str]) -> list[str]:
    """token ที่ OCR อ่านได้จากตัวชิป: ≥ 3 ตัว (โค้ดสั้นบน SOT-23 เช่น 721, WT3, SDO ก็ใช้ได้), ไม่ใช่ date code
    ตัวเลขล้วน 4 หลักข้าม (มักเป็นปี/สัปดาห์ หรือเลขล็อต) ; 3 หลักใช้ได้ (เช่น 721)"""
    toks: list[str] = []
    for ln in lines:
        for t in re.split(r"\s+", ln.upper()):
            n = norm(t)
            if len(n) < 3 or is_date_code(n) or n in MARK_STOP:
                continue
            if n.isdigit() and len(n) == 4:
                continue
            if n not in toks:
                toks.append(n)
    return toks


def text_sim(token: str, target: str) -> float:
    """token จากตัวชิป เทียบกับเบอร์ในคลัง: เหมือนกันทั้งหมด = 1 ; marking เป็นส่วนหนึ่งของ MPN (เช่น 50P03 ใน TM50P03NF) ได้คะแนนสูง"""
    t, m = canon(token), canon(target)
    if not t or not m:
        return 0.0
    if t == m:
        return 1.0
    L = SequenceMatcher(None, t, m, autojunk=False).find_longest_match(0, len(t), 0, len(m)).size
    if L < 4 or L / len(t) < 0.7:
        return 0.0
    return round(min(0.97, 0.7 * L / len(t) + 0.3 * L / len(m)), 3)


# ---------------- รูปร่าง ----------------

_ORB = cv2.ORB_create(nfeatures=1200)
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()


def _hog(gray: np.ndarray, cells: int = 8, bins: int = 9) -> np.ndarray:
    """HOG แบบย่อ (ทิศทางขอบในแต่ละช่อง 8x8) — จับทรงตัวถัง แถวขา และตำแหน่งตัวหนังสือ"""
    g = gray.astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag, ang = cv2.cartToPolar(gx, gy, angleInDegrees=True)
    b = (np.mod(ang, 180) / (180 / bins)).astype(np.int32) % bins
    n = gray.shape[0] // cells
    out = np.zeros((cells, cells, bins), np.float32)
    for i in range(cells):
        for j in range(cells):
            sl = (slice(i * n, (i + 1) * n), slice(j * n, (j + 1) * n))
            out[i, j] = np.bincount(b[sl].ravel(), weights=mag[sl].ravel(), minlength=bins)[:bins]
    out /= np.linalg.norm(out, axis=2, keepdims=True) + 1e-6
    v = out.ravel()
    return v / (np.linalg.norm(v) + 1e-6)


def features(img: Image.Image, rotations: bool = False) -> dict:
    rgb = np.asarray(img.convert("RGB").resize((400, 400)))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    kp, des = _ORB.detectAndCompute(gray, None)
    small = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA)
    hogs = []
    for k in (range(4) if rotations else [0]):
        hogs.append(_hog(np.ascontiguousarray(np.rot90(small, k))))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [18, 8], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return {"pts": np.float32([p.pt for p in kp]) if kp else np.zeros((0, 2), np.float32),
            "des": des, "hogs": hogs, "hist": hist}


def stored_features(file: str) -> dict | None:
    """ฟีเจอร์ของรูปในคลัง (c_ ที่ตัดกรอบแล้ว) เก็บ cache ตามเวลาแก้ไขไฟล์"""
    p = IMAGE_DIR / f"c_{file}"
    if not p.exists():
        return None
    mt = p.stat().st_mtime
    with _CACHE_LOCK:
        hit = _CACHE.get(file)
        if hit and hit[0] == mt:
            return hit[1]
    f = features(Image.open(p))
    with _CACHE_LOCK:
        _CACHE[file] = (mt, f)
    return f


def orb_inliers(a: dict, b: dict) -> int:
    """จำนวนจุดลวดลายที่ตรงกันแบบเข้ารูปทรงเรขาคณิต (หมุน/ย่อขยายได้) — สูง = ลายบนตัวชิปเหมือนกัน"""
    if a["des"] is None or b["des"] is None or len(a["des"]) < 8 or len(b["des"]) < 8:
        return 0
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(a["des"], b["des"], k=2)
    good = [m for m, *rest in pairs if rest and m.distance < 0.78 * rest[0].distance]
    if len(good) < 8:
        return 0
    src = a["pts"][[m.queryIdx for m in good]].reshape(-1, 1, 2)
    dst = b["pts"][[m.trainIdx for m in good]].reshape(-1, 1, 2)
    _, mask = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=6.0)
    return int(mask.sum()) if mask is not None else 0


def shape_sim(q: dict, s: dict) -> dict:
    inl = orb_inliers(q, s)
    hog = max(float(np.dot(h, s["hogs"][0])) for h in q["hogs"])
    col = float(cv2.compareHist(q["hist"], s["hist"], cv2.HISTCMP_CORREL))
    return {"inliers": inl, "hog": round(hog, 3), "color": round(col, 3)}


def shape_score(m: dict) -> float:
    """รวมเป็นคะแนน 0..1
    ทดสอบกับรูปจริง 20 รูปในคลัง (2026-10-14): ชิปตัวเดียวกันถ่ายใหม่ ORB ตรงกัน 116–480 จุด, คนละเบอร์สูงสุด 24 จุด
    -> ORB เชื่อได้ ; HOG/สี แยกเบอร์ในแพ็กเกจเดียวกันไม่ได้ (SOIC-14 ต่างเบอร์ได้ 0.87) -> ใช้แค่บอก 'รูปร่างคล้าย' คะแนนต่ำ"""
    orb = min(1.0, max(0.0, (m["inliers"] - ORB_MIN) / ORB_SPAN))
    look = 0.0
    if m["hog"] >= HOG_MIN:
        look = ((m["hog"] - HOG_MIN) / (1 - HOG_MIN) * 0.6 + 0.4 * max(0.0, m["color"])) * LOOK_CAP
    return round(max(orb, look), 3)


ORB_MIN, ORB_SPAN = 18, 50     # 18 จุด = 0 คะแนน, 68 จุดขึ้นไป = เต็ม
HOG_MIN, LOOK_CAP = 0.78, 0.4  # รูปร่างคล้ายอย่างเดียว ได้ไม่เกิน 0.4 (ต้องให้ผู้ใช้ตรวจ marking)
STRONG, WEAK = 0.5, 0.25       # เกณฑ์ "น่าจะใช่" / "รูปร่างคล้าย"


# ---------------- รวม ----------------

def identify(con, img: Image.Image, exclude_files: set[str] | None = None, limit: int = 5) -> dict:
    """img = รูปตัวชิปที่ตัดกรอบแล้ว (c_) ; คืน {lines, tokens, candidates:[...]} เรียงจากน่าจะใช่มากสุด"""
    exclude_files = exclude_files or set()
    chip = read_chip(img)
    lines, tokens = chip["lines"], chip["tokens"]
    q = features(img, rotations=True)
    parts = con.execute(
        """SELECT p.id, p.mpn, p.marking, p.package, p.category, p.image,
                  COALESCE((SELECT SUM(qty) FROM stock_lots l WHERE l.part_id=p.id),0) AS qty,
                  (SELECT loc.code FROM stock_lots l JOIN locations loc ON loc.id=l.location_id
                    WHERE l.part_id=p.id ORDER BY loc.kind='drawer' DESC, l.qty>0 DESC, l.id DESC LIMIT 1) AS loc
           FROM parts p""").fetchall()
    aliases: dict[int, list[str]] = {}
    for a in con.execute("SELECT part_id, alias_norm FROM part_aliases"):
        aliases.setdefault(a["part_id"], []).append(a["alias_norm"])
    imgs: dict[int, list[str]] = {}
    for r in con.execute("SELECT part_id, file FROM part_images ORDER BY sort, id"):
        if r["file"] not in exclude_files:
            imgs.setdefault(r["part_id"], []).append(r["file"])

    # ข้อความบนชิปที่บันทึกไว้ของแต่ละเบอร์ ; token ที่พิมพ์อยู่บนหลายเบอร์ (เช่น เลขล็อตซ้ำ/ชื่อตระกูล) แยกเบอร์ไม่ได้ -> คะแนนต่ำ
    marks_of = {p["id"]: marking_tokens_of(p["marking"]) for p in parts}
    shared: dict[str, int] = {}
    for ms in marks_of.values():
        for m in {canon(x) for x in ms}:
            shared[m] = shared.get(m, 0) + 1

    out = []
    for p in parts:
        names = [p["mpn"]] + [a for a in aliases.get(p["id"], []) if a not in marks_of[p["id"]]]
        marks = marks_of[p["id"]]
        text, hit = 0.0, ""
        for t in tokens:
            for n in names:
                s = text_sim(t, n)
                if s > text:
                    text, hit = s, t
            for mk in marks:  # ข้อความบนชิปที่บันทึกไว้ (ผู้ใช้ยืนยันแล้ว)
                c = canon(mk)
                w = 0.95 if shared.get(c, 0) <= 1 else 0.45
                if canon(t) == c and w > text:
                    text, hit = w, t
        best, best_file = None, ""
        for f in imgs.get(p["id"], []):
            sf = stored_features(f)
            if sf is None:
                continue
            m = shape_sim(q, sf)
            m["score"] = shape_score(m)
            if best is None or m["score"] > best["score"]:
                best, best_file = m, f
        shape = best["score"] if best else 0.0
        score = max(text, shape)
        if text >= 0.5 and shape >= 0.4:
            score = min(1.0, score + 0.1)   # ทั้งเบอร์และรูปร่างตรงกัน
        reasons = []
        if text >= 1.0:
            reasons.append(f"เบอร์ตรง ({hit})")
        elif text > 0:
            reasons.append(f"marking {hit} ใกล้เคียงเบอร์")
        if best and best["inliers"] >= ORB_MIN + ORB_SPAN / 2:
            reasons.append("ลวดลาย/ตัวหนังสือบนชิปเหมือนรูปในคลัง")
        elif shape >= WEAK:
            reasons.append("รูปร่าง/แพ็กเกจคล้ายกัน")
        out.append({"part_id": p["id"], "mpn": p["mpn"], "package": p["package"], "category": p["category"],
                    "image": p["image"] or "", "match_image": best_file, "qty": p["qty"], "loc": p["loc"] or "",
                    "score": round(score, 3), "text": text, "shape": shape, "detail": best, "reasons": reasons,
                    "level": "strong" if score >= STRONG else "weak"})
    out.sort(key=lambda c: -c["score"])
    strong = [c for c in out if c["level"] == "strong"]
    weak = [c for c in out if c["level"] == "weak" and c["score"] >= WEAK][:3]
    return {"lines": lines, "tokens": tokens, "logo": chip["logo"], "items": chip["items"],
            "candidates": (strong + weak)[:limit], "all": out}
