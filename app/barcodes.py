"""อ่าน/แปลงโค้ดบนถุงอะไหล่: LCSC QR, Digikey/Mouser DataMatrix (ECIA MH10.8.2) และ OCR ฉลาก"""
import re
import threading
from functools import lru_cache

GS_CHARS = "\x1d␝"   # Group separator (zxing คืนเป็น ␝ บางครั้ง)
RS_CHARS = "\x1e␞"
EOT_CHARS = "\x04␄"

# Data identifiers ของ ECIA เรียงยาวไปสั้น เพื่อจับ prefix ที่ยาวที่สุดก่อน
ECIA_IDS = ["30P", "20Z", "13Z", "12Z", "11Z", "14K", "11K", "10K", "10D", "1P", "1T", "1K", "9D",
            "4L", "1V", "P", "Q", "K"]


def empty_result(raw: str = "", source: str = "") -> dict:
    return {"mpn": "", "qty": None, "manufacturer": "", "supplier": "", "supplier_pn": "",
            "lot": "", "date_code": "", "order_no": "", "coo": "", "description": "", "package": "",
            "raw": raw, "source": source, "candidates": []}


def parse_lcsc(text: str) -> dict | None:
    """{pbn:PICK..,on:GB..,pc:C82656,pm:BTS724G,qty:20,mc:,cc:1,...}"""
    t = text.strip()
    if not (t.startswith("{") and "pc:" in t and "pm:" in t):
        return None
    fields = dict(re.findall(r"(\w+):([^,}]*)", t))
    r = empty_result(text, "LCSC-QR")
    r["supplier"] = "LCSC"
    r["supplier_pn"] = fields.get("pc", "").strip()
    r["mpn"] = fields.get("pm", "").strip()
    r["order_no"] = fields.get("on", "").strip()
    r["lot"] = fields.get("pbn", "").strip()
    q = fields.get("qty", "").strip()
    r["qty"] = int(q) if q.isdigit() else None
    return r


def _split_ecia(text: str) -> list[str]:
    t = text
    for ch in RS_CHARS + EOT_CHARS:
        t = t.replace(ch, "\x1d")
    for ch in GS_CHARS:
        t = t.replace(ch, "\x1d")
    t = re.sub(r"^\s*>?\[\)>\x1d?06", "", t)
    return [p for p in t.split("\x1d") if p]


def parse_ecia(text: str) -> dict | None:
    """DataMatrix มาตรฐาน ECIA ที่ Digikey / Mouser / Farnell / TTI ใช้"""
    if "[)>" not in text:
        return None
    fields: dict[str, str] = {}
    for part in _split_ecia(text):
        for ident in ECIA_IDS:
            if part.startswith(ident):
                fields.setdefault(ident, part[len(ident):].strip())
                break
    if "1P" not in fields:
        return None
    r = empty_result(text, "ECIA-DM")
    r["mpn"] = fields.get("1P", "")
    q = fields.get("Q", "")
    r["qty"] = int(q) if q.isdigit() else None
    r["lot"] = fields.get("1T", "")
    r["date_code"] = fields.get("9D") or fields.get("10D", "")
    r["coo"] = fields.get("4L", "")
    r["manufacturer"] = fields.get("1V", "")
    cust = fields.get("P", "") or fields.get("30P", "")
    if cust.endswith("-ND") or fields.get("11Z") == "PICK" or "30P" in fields:
        r["supplier"] = "Digikey"
        r["supplier_pn"] = fields.get("30P") or cust
        r["order_no"] = fields.get("1K", "")
    elif "1V" in fields or "14K" in fields or re.match(r"^\d{2,3}-", cust):
        r["supplier"] = "Mouser"
        r["supplier_pn"] = cust
        r["order_no"] = fields.get("K", "")
    else:
        r["supplier_pn"] = cust
        r["order_no"] = fields.get("K", "")
    return r


def parse_text(text: str) -> dict:
    """รับข้อความจากเครื่องสแกน/กล้อง แล้วแปลงเป็นข้อมูลอะไหล่"""
    text = text.strip()
    for parser in (parse_lcsc, parse_ecia):
        r = parser(text)
        if r:
            return r
    r = empty_result(text, "TEXT")
    if re.fullmatch(r"C\d{3,9}", text.upper()):
        r["supplier"] = "LCSC"
        r["supplier_pn"] = text.upper()
    else:
        r["mpn"] = text  # ถือว่าพิมพ์เบอร์มาเอง
    return r


# ---------- อ่านจากรูป ----------

def _load_image(path: str):
    from PIL import Image, ImageOps
    im = Image.open(path)
    return ImageOps.exif_transpose(im).convert("RGB")


def decode_codes(path: str) -> list[dict]:
    """คืนรายการโค้ดที่อ่านได้ทั้งหมดในรูป [{format, text}]"""
    import zxingcpp
    im = _load_image(path)
    found: dict[str, str] = {}

    def scan(img):
        for b in zxingcpp.read_barcodes(img, try_rotate=True, try_downscale=True):
            if b.text and b.text not in found:
                found[b.text] = str(b.format)

    scan(im)
    if not any(_is_2d(f) for f in found.values()):
        # โค้ดเล็กในรูปใหญ่: ลองตัดเป็นส่วนๆ ซ้อนกัน
        w, h = im.size
        for fx in (0, 0.25, 0.5):
            for fy in (0, 0.25, 0.5):
                box = (int(w * fx), int(h * fy), int(w * (fx + 0.5)), int(h * (fy + 0.5)))
                scan(im.crop(box))
    return [{"format": f, "text": t} for t, f in found.items()]


def _is_2d(fmt: str) -> bool:
    return any(k in fmt for k in ("QR", "Matrix", "Aztec", "PDF"))


_OCR_LOCK = threading.Lock()


@lru_cache(maxsize=1)
def _ocr_engine():
    from rapidocr import RapidOCR
    return RapidOCR()


LABEL_KEY = re.compile(r"(?:\b(?:MPN|NPM|MNP|PMN|MFR\.?\s*P/?N|MFG\.?\s*P/?N|MANUFACTURER\s+PART(?:\s+(?:NO\.?|NUMBER))?)\b|型号|型號)"
                       r"\s*[:：]?\s*([A-Z0-9][A-Z0-9\-.,/#+_()]*)?", re.I)
MOUSER_PN = re.compile(r"(?<![A-Z0-9])\d{2,3}-([A-Z][A-Z0-9\-./#+,()]{3,})", re.I)   # Mouser/Arrow: 595-TCAN1051VDRQ1
_NOT_PN = re.compile(r"^(?:WM\d|GB\d|PICK|ROHS|COO|MSL|HTTP|WWW|LCSC|MOUSER|D/?C)|\.COM$|^C\d{3,9}$|^\d{4}-[A-Z]-[A-Z]\d{2}-\d{3}$"
                     r"|^\d{4}-\d{4}$|^\d{2,3}-|[@~]")
_VALUE = re.compile(r"^[\d.]+(?:V|MV|A|MA|UA|NA|W|MW|HZ|KHZ|MHZ|DB|UH|NH|MH|PF|NF|UF|NS|MS|US|MBPS|KBPS|OHM|Ω|%|MIL|MM|°C|C)$")
_PKG = re.compile(r"^(?:P?DSO|SOIC|SOP|SSOP|TSSOP|HTSSOP|MSOP|QFN|VQFN|DFN|LQFP|TQFP|SOT|TO|D2PAK|DPAK|SC|PG-[A-Z]+)-?\d+", re.I)


# คำที่เป็นค่าทางไฟฟ้า/ขนาด/วัสดุ ของ C R L (ใช้รับบรรทัดค่าล้วนเข้าคำอธิบาย)
_SPEC = re.compile(r"(±?\d+(\.\d+)?\s*(%|[pnuµμm]?F|[kKmM]?Ω|[pnuµμm]H|k?V|m?A|m?W|[kKMG]?Hz|ppm)|\d+/\d+W|X7R|X5R|X7S|X6S|X8R|C0G|COG|NP0|Y5V"
                   r"|01005|0201|0402|0603|0805|1206|1210|1812|2010|2220|2512)", re.I)
_SIZE_CODE = re.compile(r"(?<![\d.x×])(01005|0201|0402|0603|0805|1206|1210|1812|2010|2220|2512)(?![\d.x×])")


def _pn_ok(tok: str) -> bool:
    """หน้าตาเป็นเบอร์อะไหล่ไหม: มีทั้งตัวอักษรและตัวเลข (หรือตัวเลขล้วนยาว ≥ 6 เช่น Würth 760301302), ไม่ใช่ค่าไฟ/แพ็กเกจ/เลขล็อต"""
    from .ocrtext import junk
    t = tok.upper()
    if not 4 <= len(t) <= 32 or _NOT_PN.search(t) or _VALUE.match(t) or _PKG.match(t) or junk(tok):
        return False
    has_d, has_a = bool(re.search(r"\d", t)), bool(re.search(r"[A-Z]", t))
    return (has_d and has_a) or (has_d and len(re.sub(r"\D", "", t)) >= 6)


def ocr_label(path: str, vocab: dict[str, str] | None = None) -> dict:
    """อ่านตัวหนังสือบนฉลากเมื่อไม่มีโค้ด 2 มิติ — แยกช่องตามชนิดข้อมูล:
    เบอร์ (ช่อง MPN/型号, เลข Mouser 595-xxx, คำที่ซ้ำหลายครั้ง, ตรงกับเบอร์ที่รู้จัก) / ตัวเลขล้วน (จำนวน, LCSC C-number)
    / ผู้ผลิต (ชื่อที่รู้จัก) / คำอธิบาย (ตัดคำขยะที่ OCR อ่านโลโก้/ลายพื้นผิด)
    vocab = {canon: เบอร์ที่สะกดถูก} จากคลัง+datasheet — ใช้แก้ O/0, S/5 ในเบอร์ที่รู้จักแล้วเท่านั้น"""
    import numpy as np
    from .identify import canon
    from .ocrtext import digits, find_mfr, junk, snap
    im = _load_image(path)
    im.thumbnail((1200, 1200))   # รูปมือถือ 12MP ทำ OCR ช้ามาก (ทดสอบกับฉลากจริงแล้วอ่านได้เท่าเดิม)
    with _OCR_LOCK:   # OCR ทีละรูป ไม่ให้แย่ง CPU กันจนช้าทั้งหมด
        res = _ocr_engine()(np.asarray(im))
    lines = [(t.strip(), float(s)) for t, s in zip(res.txts or [], res.scores or []) if t and t.strip()]
    texts = [t for t, _ in lines]
    joined = "\n".join(texts)
    r = empty_result(joined, "OCR")

    # ---- เบอร์อะไหล่: ให้คะแนนจากหลายหลักฐาน ----
    score: dict[str, float] = {}

    def add(tok: str, pts: float):
        tok = tok.strip(" :：.,;").upper()
        tok = re.sub(r"\($", "", tok)
        if _pn_ok(tok):
            tok = snap(tok, vocab or {})
            score[tok] = score.get(tok, 0) + pts

    key_next = False
    for t, s in lines:
        if key_next:   # ฉลากจีน: "型号" อยู่บรรทัดหนึ่ง ค่าอยู่บรรทัดถัดไป
            m = re.match(r"[:：]?\s*([A-Z0-9][A-Z0-9\-.,/#+_()]*)", t, re.I)
            if m:
                add(m.group(1), 6)
        key_next = False
        for m in LABEL_KEY.finditer(t):
            if m.group(1):
                add(m.group(1), 6)
            else:
                key_next = True
        for m in MOUSER_PN.finditer(t):
            add(m.group(1), 4)
        for tok in re.findall(r"[A-Z0-9][A-Z0-9\-.,/#+()]{3,}", t.upper()):
            add(tok, 1)   # ทุกครั้งที่เห็น (ฉลาก Mouser/Digikey พิมพ์เบอร์ซ้ำ 2 ที่)
    for tok in list(score):
        if vocab and canon(tok) in vocab:
            score[tok] += 5   # ตรงกับเบอร์ที่รู้จักแล้ว
    ranked = sorted(score, key=lambda k: (-score[k], -len(k)))
    if ranked and score[ranked[0]] >= 2:
        r["mpn"] = ranked[0]
    r["candidates"] = ranked[:15]

    # ---- ช่องตัวเลขล้วน ----
    m = re.search(r"(?:QTY|Q'TY|QUANTITY|数量|數量|จำนวน)\s*[:：]?\s*([0-9OoIlSB]{1,7})\b", joined, re.I)
    if m and digits(m.group(1)):
        r["qty"] = int(digits(m.group(1)))
    m = re.search(r"(?<![A-Z0-9])C([0-9OoIl]{3,9})(?![A-Z0-9])", joined)
    if m and len(digits(m.group(1))) == len(m.group(1)):
        r["supplier_pn"] = "C" + digits(m.group(1))
        r["supplier"] = "LCSC"
    up = joined.upper()
    if "LCSC" in up:
        r["supplier"] = "LCSC"
    elif "MOUSER" in up:
        r["supplier"] = "Mouser"
        m = MOUSER_PN.search(joined)
        if m:
            r["supplier_pn"] = m.group(0).upper()
    elif "DIGI" in up or "-ND" in joined:
        r["supplier"] = "Digikey"
    m = re.search(r"(?<![A-Z])D[/I1l|]?C\s*[:：]\s*([0-9A-Z+]+?)(?=COO|\s|$)", joined, re.I)   # OCR อ่าน D/C เป็น DIC
    if m:
        r["date_code"] = m.group(1)
    m = re.search(r"COO\s*[:：]\s*([A-Z]{2,})", joined, re.I)
    if m:
        r["coo"] = m.group(1).upper()

    # ---- ผู้ผลิต ----
    r["manufacturer"] = find_mfr(texts)

    # ---- คำอธิบาย: บรรทัดที่อ่านได้ชัด ไม่ใช่หัวฉลาก/เลขรหัส และตัดคำขยะออก ----
    skip = ("MPN", "NPM", "P/N", "QTY", "LCSC", "D/C", "COO", "MOUSER", "DIGI", "INV", "SO#", "PO#", "BROOKS", "RIVER",
            "MANUFACT", "ROHS", "LOT", "MSL", "WWW", ".COM", "OBSERVE", "PRECAUTION", "ELECTROSTATIC", "SENSITIVE",
            "ATTENTION", "HANDLING", "CUST", "QUANTITY", "BARCODE", "ELECTRONICS", "型号")
    desc = []
    for t, s in lines:
        if s < 0.8 or any(k in t.upper() for k in skip) or (r["mpn"] and r["mpn"] in t.upper()):
            continue
        words = [w for w in t.strip(" :：").split() if not junk(w)]
        if len([w for w in words if re.search(r"[A-Za-z]{3,}", w)]) >= 2 or (words and all(_SPEC.fullmatch(w) for w in words)):
            desc.append(" ".join(words))   # บรรทัดค่าล้วน เช่น "X7R 1206" (ฉลาก LCSC ขึ้นบรรทัดใหม่ต่อจากคำอธิบาย)
    r["description"] = " ".join(desc)[:200]
    m = re.search(r"\b((?:P?DSO|SOIC|SOP|SSOP|TSSOP|HTSSOP|QFN|VQFN|DFN|LQFP|TQFP|SOT|TO|D2PAK|DPAK|PG-[A-Z]+)-?\d+[A-Z0-9\-]*)", joined, re.I)
    if m:
        r["package"] = m.group(1).upper()
    elif _SIZE_CODE.search(r["description"]):   # C/R/L ขนาดตัว 0603/1206 ...
        r["package"] = _SIZE_CODE.search(r["description"]).group(1)
    return r


def read_image(path: str, vocab: dict[str, str] | None = None) -> list[dict]:
    """รูปถุง 1 รูป -> รายการข้อมูลอะไหล่ (อาจหลายถุงในรูปเดียว)"""
    results = []
    other_codes = []
    for c in decode_codes(path):
        r = parse_text(c["text"])
        if r["source"] in ("LCSC-QR", "ECIA-DM"):
            results.append(r)
        else:
            other_codes.append(c["text"])
    if len(results) == 1:
        # โค้ด 2 มิติไม่มีคำอธิบาย/แพ็กเกจ -> อ่านตัวหนังสือบนฉลากมาเติม (ค่าจากโค้ดถือเป็นหลัก)
        try:
            o = ocr_label(path, vocab)
            for k in ("description", "package", "date_code", "coo", "manufacturer"):
                if not results[0].get(k) and o.get(k):
                    results[0][k] = o[k]
        except Exception:
            pass
    if not results:
        r = ocr_label(path, vocab)
        r["candidates"] = r["candidates"] + [t for t in other_codes if t not in r["candidates"]]
        if r["mpn"] or r["candidates"] or r["supplier_pn"]:
            results.append(r)
    return results
