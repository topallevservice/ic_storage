"""แยกชนิดตัวหนังสือที่ OCR อ่านได้ (ใช้ร่วมกันทั้งฉลากถุงและตัวชิป)
- ผู้ผลิต/โลโก้ (TI, NXP, Infineon, Toshiba ...) แยกออกจากเบอร์ — ไม่ให้โลโก้ปนเข้าไปในเบอร์/marking
- ช่องที่ต้องเป็นตัวเลขล้วน (จำนวน, LCSC C-number) แก้ตัวอักษรที่หน้าตาเหมือนตัวเลข (O→0, I/l→1, S→5, B→8)
- token: เบอร์ (ตัวอักษร+ตัวเลข) / ตัวเลขล้วน / ตัวอักษรล้วน / date code / ผู้ผลิต"""
import re

from .parts import is_date_code, norm

# ชื่อ/อักษรย่อผู้ผลิตที่พิมพ์บนฉลากหรือเป็นโลโก้บนตัวชิป -> ชื่อมาตรฐาน
# (key = ตัวพิมพ์ใหญ่ ไม่มีช่องว่าง ; เทียบแบบ "ขึ้นต้นด้วย" สำหรับชื่อยาว ≥ 5 ตัว เพราะ OCR มักอ่านโลโก้ต่อท้ายเป็นขยะ)
MFR = {
    "TEXASINSTRUMENTS": "Texas Instruments", "TI": "Texas Instruments", "NXP": "NXP", "PHILIPS": "NXP",
    "STMICROELECTRONICS": "STMicroelectronics", "STMICRO": "STMicroelectronics", "ST": "STMicroelectronics",
    "INFINEON": "Infineon", "MICROCHIP": "Microchip", "ATMEL": "Microchip (Atmel)", "ONSEMI": "onsemi",
    "ONSEMICONDUCTOR": "onsemi", "NEXPERIA": "Nexperia", "DIODES": "Diodes Inc.", "TOSHIBA": "Toshiba",
    "RENESAS": "Renesas", "ANALOGDEVICES": "Analog Devices", "ADI": "Analog Devices", "MAXIM": "Maxim (ADI)",
    "ROHM": "ROHM", "VISHAY": "Vishay", "MURATA": "Murata", "TDK": "TDK",
    "EPCOS": "TDK (EPCOS)", "WURTH": "Würth Elektronik", "WÜRTH": "Würth Elektronik", "BROADCOM": "Broadcom",
    "AVAGO": "Broadcom (Avago)", "NOVOSENSE": "Novosense", "SGMICRO": "SG Micro", "SLKOR": "Slkor",
    "MORNSUN": "Mornsun", "EVISUN": "EVISUN", "FITIPOWER": "Fitipower", "SITCORES": "SIT", "BOURNS": "Bourns",
    "LITTELFUSE": "Littelfuse", "YAGEO": "Yageo", "SAMSUNG": "Samsung", "KEMET": "KEMET", "ALPHAOMEGA": "Alpha & Omega",
    "AOS": "Alpha & Omega", "LITEON": "Lite-On", "EVERLIGHT": "Everlight", "SILAN": "Silan", "3PEAK": "3PEAK",
    "MELEXIS": "Melexis", "ALLEGRO": "Allegro", "AMS": "ams OSRAM", "BOSCH": "Bosch", "MPS": "MPS",
    "RICHTEK": "Richtek", "GIGADEVICE": "GigaDevice", "WINBOND": "Winbond", "ISSI": "ISSI", "LRC": "LRC",
}
_MFR_LONG = sorted((k for k in MFR if len(k) >= 5), key=len, reverse=True)

# ตัวที่หน้าตาเหมือนตัวเลข (ใช้กับช่องที่ต้องเป็นตัวเลขล้วนเท่านั้น)
_TO_DIGIT = str.maketrans({"O": "0", "o": "0", "Q": "0", "D": "0", "I": "1", "l": "1", "i": "1", "|": "1",
                           "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "G": "6", "g": "9"})


def digits(s: str) -> str:
    """ช่องตัวเลขล้วน: แก้ตัวอักษรหน้าตาเหมือนตัวเลข แล้วเก็บเฉพาะตัวเลข"""
    return re.sub(r"\D", "", (s or "").translate(_TO_DIGIT))


def mfr_of(word: str) -> str:
    """คำนี้เป็นชื่อ/โลโก้ผู้ผลิตไหม -> ชื่อมาตรฐาน หรือ "" """
    w = re.sub(r"[^A-Z0-9Ü]", "", (word or "").upper())
    if not w:
        return ""
    if w in MFR:
        return MFR[w]
    for k in _MFR_LONG:   # "NEXPERIALNLONMLUAS", "STMICRO..." = ชื่อยาวที่ OCR อ่านโลโก้ต่อท้าย
        if w.startswith(k):
            return MFR[k]
    return ""


def find_mfr(lines: list[str]) -> str:
    """หาชื่อผู้ผลิตจากข้อความทั้งฉลาก: ช่อง Mfr/Manufacturer ก่อน แล้วค่อยหาคำที่เป็นชื่อผู้ผลิต (ไม่นับอักษรย่อสั้น ๆ)"""
    for ln in lines:
        m = re.search(r"(?:MFR|MFG|MANUFACTURER|制造商|厂家|品牌)\.?\s*[:：]\s*([A-Za-zÜü][\w .&-]{1,40})", ln, re.I)
        if m:
            return mfr_of(m.group(1).split()[0]) or m.group(1).strip()
    for ln in lines:
        for w in re.findall(r"[A-Za-zÜü][A-Za-zÜü0-9]{2,}", ln):
            n = mfr_of(w)
            if n and len(w) >= 3 and w.upper() not in ("ST", "TI", "ADI", "AMS", "MPS", "LRC", "AOS"):
                return n
    return ""


def kind(token: str) -> str:
    """ชนิดของ token: mfr / date / digits / letters / code (ตัวอักษร+ตัวเลข = เบอร์/marking)"""
    t = norm(token)
    if not t:
        return ""
    if mfr_of(t) and (len(t) <= 4 or t.isalpha()):
        return "mfr"
    if is_date_code(t):
        return "date"
    if t.isdigit():
        return "digits"
    if t.isalpha():
        return "letters"
    return "code"


def junk(word: str) -> bool:
    """คำขยะจาก OCR (อ่านโลโก้/ลายพื้น/ตัวหนังสือจีนเป็นตัวอักษรมั่ว): มีอักษรนอก ASCII,
    หรือคำยาวที่สลับพิมพ์เล็ก-ใหญ่ไปมา (เช่น OTUInALUUUnNA, luaJTavaJanAn)"""
    if re.search(r"[^\x20-\x7e°µΩ±~–—]", word):
        return True
    letters = re.sub(r"[^A-Za-z]", "", word)
    if len(letters) >= 7:
        flips = sum(1 for a, b in zip(letters, letters[1:]) if a.isupper() != b.isupper())
        if flips >= 3:
            return True
        if word.isalpha() and letters.isupper() and not re.search(r"[AEIOUY]", letters):
            return True   # ตัวอักษรล้วนไม่มีสระ = ขยะ (เบอร์ที่มีตัวเลขปน เช่น SGM721XN5 ไม่นับ)
    return False


def snap(word: str, vocab: dict[str, str]) -> str:
    """ตัวที่อ่านสลับกันบ่อย (O/0, S/5, B/8 ...) แต่ตรงกับเบอร์ที่รู้จักแล้วทุกตัวอักษร -> ใช้การสะกดที่ถูก
    vocab = {canon: การสะกดที่ถูก} ; ไม่เดาเบอร์ใหม่ที่ระบบไม่รู้จัก"""
    from .identify import canon
    return vocab.get(canon(word), word) if vocab else word


def _lev(a: str, b: str, cap: int = 3) -> int:
    """ระยะแก้ไข (Levenshtein) แบบหยุดเร็วเมื่อเกิน cap"""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


# คำโลโก้ที่พิมพ์บนตัวชิป -> การสะกดที่ถูก (แสดงในข้อความบนชิป)
_LOGO_WORD = {"INFINEON": "Infineon", "ATMEL": "Atmel", "MICROCHIP": "Microchip", "NEXPERIA": "Nexperia",
              "TOSHIBA": "Toshiba", "RENESAS": "Renesas", "ONSEMI": "onsemi", "EVISUN": "EVISUN", "SLKOR": "Slkor"}


def mfr_fuzzy(word: str) -> tuple[str, str] | None:
    """คำบนตัวชิปเป็นโลโก้/ชื่อผู้ผลิตไหม (ยอม OCR อ่านผิด 1–2 ตัว เช่น Inrineon) -> (ผู้ผลิต, คำที่สะกดถูก)"""
    w = re.sub(r"[^A-Za-z]", "", word or "").upper()
    if not w:
        return None
    if w in MFR:
        return MFR[w], _LOGO_WORD.get(w, word)
    if len(w) < 5:
        return None
    for k in _MFR_LONG:
        if _lev(w, k) <= (1 if len(k) < 8 else 2):
            return MFR[k], _LOGO_WORD.get(k, k.title())
    return None
