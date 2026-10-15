"""Normalize เบอร์อะไหล่, เดาหมวด, ค้นหา"""
import re
import sqlite3

from .db import now

# suffix บรรจุภัณฑ์ที่ไม่เปลี่ยนตัวชิป (ตัดทิ้งตอนค้นหา)
PACKING_SUFFIX = re.compile(r"(,215|,115|,135|,235|,315|-TR|/TR|-T1|-REEL7?|/R7|/RL|-13-F|-7-F|-ND)$")

# เดาหมวดจาก prefix เบอร์ (ใช้ตอนรับของ ผู้ใช้แก้ได้)
CATEGORY_RULES = [
    ("GD", r"^(1ED|2ED|UCC2[1-9]|UCC5|ISO5|ISO7|ADUM|SI82|SI86|ACPL|HCPL|FOD|TLP|PC817|NSI|BM6[0-9]|MC33GD|STGAP|IX[DE]|IR2[0-9]|EiceDRIVER)"),
    ("AFE", r"^(BQ7[0-9]|BQ76|BQ79|LTC68|ADBMS|MC3377|L999|TLE9012|ISL94)"),
    ("IF", r"^(TJA|TCAN|MCP25|MCP20|SN65HVD|SN75|ISO10|ATA6|NCV7|TLE72|TLE92[5-9]|UJA|MAX3485|MAX485|SIT10|ADM2)"),
    ("SW", r"^(BTS|BTT|BSP7|VN|VND|VNQ|TPS1H|TPS2H|TPS4H|TPS27|NCV8|IPS|ITS|BTF|TLE7|DRV8|L99)"),
    ("PM", r"^(B[0-2][0-9]{3}|F[0-2][0-9]{3}|IB[0-2][0-9]|WR[AB]|UR[AB]|VR[AB]|R1S|R05P|R12P|TMR|TEN|TPS|LM5|LM2|LM1117|LM317|LMR|LT[0-9]|LTC3|LTM|MP[0-9]|AMS1117|TLE42|TLE4[0-9]|TLF|TL431|TL432|REF|LP29|LP5|NCV4|NCP|L78|L79|78M|79M|XL|SY8|RT[0-9]|MIC|AP2|AP7|ME6|HT7)"),
    ("MCU", r"^(STM32|STM8|SPC5|S32K|MPC5|TC2|TC3|SAK|R5F|RH850|PIC|ATMEGA|ATSAM|GD32|CY|MSP430|AT24|24C|25LC|M24|W25|MX25|S25|IS2|SN74|74HC|74LV|74AHC|CD40|NC7)"),
    ("AN", r"^(LM358|LM2904|LM393|LM339|LMV|OPA|TLV|TL07|TL08|AD8|ADS|ADC|INA|MCP60|MCP3|LT1|AD7|MAX4|LMC|TSV|ACS7|ACS)"),
    ("SEN", r"^(TLE49|TLI|TLV49|A13|AH|DRV5|SS49|LM35|TMP|NTC|KTY|AD2S|PGA4|AS5)"),
    ("IGBT", r"^(IKW|IGW|IHW|FF[0-9]|FS[0-9]|IKQ|STGW|FGH|FGA|RGT|HGT|IXGH|IGBT)"),
    ("MOS", r"^(IRF|IRL|IPD|IPB|IPP|IPW|IPT|BSC|BSZ|BSS|BSO|SI[0-9]|SIS|SIR|SQ|AO[0-9D]|AON|NTD|NTM|NVM|NVD|FDS|FDN|FDD|STP|STD|STB|STW|DMN|DMP|RQ|RJK|TK[0-9]|2N7002|BUK|PSMN)"),
    ("DIO", r"^(BZX|BZT|MMSZ|SMBJ|SMAJ|SMCJ|P6KE|1.5KE|ESD|PESD|TPD|SM712|PRTR|BAS|BAT|BAV|1N4|1N5|SS[0-9]|ES[0-9]|US[0-9]|MBR|STPS|RB|SMF|MURS|S[0-9]M|M[0-9]$|LL4|SB[0-9])"),
    ("BJT", r"^(BC[0-9]|MMBT|2N[0-9]|2SA|2SC|PBSS|MJD|TIP|BCP|BCX|PMBT|DTC|DTA)"),
    ("XTL", r"^(ABM|ABLS|FA-|TSX|ECS|NX[0-9]|SIT[0-9]|ASE|XRCGB|HC49)"),
    ("RES", r"^(CRCW|RC[0-9]{4}|ERJ|RMCF|CR[0-9]{4}|WSL|CSR|RL[0-9]|ERA|TNPW|PAT|RT0|CRG|MCR|ESR|LR[0-9])"),
    ("CAP", r"^(GRM|GCM|GCJ|CL[0-9]{2}|C[0-9]{4}[A-Z]|CGA|CGJ|EMK|UMK|TMK|EEE|EEU|UWT|T491|T495|TAJ|TPS[A-Z]|KEMET|CC[0-9]{4}|C0G|X7R|ECA|ECJ|FK|UPW)"),
    ("IND", r"^(SRR|SRN|SRP|SDR|MSS|XAL|XFL|LQH|LQM|BLM|MPZ|NR[0-9]|CDR|IHLP|SLF|VLS|WE-|744|ACM|DLW|ACT)"),
    ("CON", r"^(TE|MOLEX|JST|B[0-9]B-|S[0-9]B-|DF|FTSH|G5|G6|HF|ALQ|0154|0451|0446|OMRON|FUSE|MF-|NANO|RELAY)"),
]


def norm(s: str) -> str:
    """BTS724G -> BTS724G ; bzx84-c3v3,215 -> BZX84C3V3"""
    s = (s or "").upper().strip()
    s = PACKING_SUFFIX.sub("", s)
    return re.sub(r"[\s\-_/\.,#()]", "", s)


# ---------------- ข้อความบนตัวชิป (marking) ----------------
# parts.marking = ข้อความตามที่พิมพ์บนตัวชิป อ่านง่าย เช่น "TI OPA / 2348A / 53ERZ (ใต้ตัว: KA608)"
# ใช้ค้นหา + ระบุไอซีจากรูป: แยกเป็น token (ตัวพิมพ์ใหญ่ ไม่มีช่องว่าง/ขีด) เก็บใน part_aliases kind='MARKING'

# date code ปี/สัปดาห์ผลิต (YYWW เช่น 2531 หรือมีตัวอักษรนำ 1 ตัว เช่น G2527) ซ้ำกันข้ามเบอร์ -> ไม่ใช้จำ/เทียบ
# ต้องเข้มงวด: 1042V, 2348A, LP2904 เป็น marking จริง ไม่ใช่ date code
_DATE_RE = re.compile(r"[A-Z]?(\d{2})(\d{2})")

# คำที่พิมพ์บนชิปหลายเบอร์เหมือนกัน (โลโก้/ประเทศ) — ไม่ใช้แยกเบอร์
MARK_STOP = {"NXP", "CHN", "CHINA", "PHI", "PHL", "MYS", "MAL", "KOR", "TWN", "TAIWAN", "USA", "JPN", "JAPAN", "THA",
             "MEX", "INFINEON", "ATMEL", "MICROCHIP"}


def is_date_code(token: str) -> bool:
    m = _DATE_RE.fullmatch((token or "").upper())
    return bool(m) and 1 <= int(m.group(2)) <= 53


def marking_tokens_of(text: str) -> list[str]:
    """แยกข้อความบนชิปเป็น token สำหรับค้นหา/เทียบ: ≥ 3 ตัวอักษร, ไม่ใช่ date code, ไม่ใช่คำโลโก้/ประเทศ"""
    out: list[str] = []
    # "(โลโก้ TI)" = โลโก้รูปภาพบนชิป (ไม่ใช่ตัวหนังสือที่พิมพ์) -> ไม่ใช้เป็นคำค้น
    text = re.sub(r"\(\s*โลโก้[^)]*\)", " ", text or "")
    # คั่นคำด้วยช่องว่าง (" / " = ขึ้นบรรทัดใหม่) — "/" ที่ติดกับคำเป็นส่วนของ marking เช่น TJA1145T/FD, SIT1044T/3
    for raw in re.split(r"[\s,·|;]+", (text or "").upper()):
        t = norm(re.sub(r"[^\x21-\x7e]", "", raw))   # ตัดภาษาไทย/สัญลักษณ์ (เช่น "ใต้ตัว")
        if len(t) >= 3 and not is_date_code(t) and t not in MARK_STOP and t not in out:
            out.append(t)
    return out


def set_marking(con: sqlite3.Connection, part_id: int, text: str) -> None:
    """บันทึกข้อความบนตัวชิป + สร้างดัชนีค้นหาใหม่ (token ทีละคำ และทั้งข้อความต่อกัน เช่น OPA2348A)"""
    text = re.sub(r"\s+", " ", (text or "").strip())[:300]
    con.execute("UPDATE parts SET marking=?, updated_at=? WHERE id=?", (text, now(), part_id))
    con.execute("DELETE FROM part_aliases WHERE part_id=? AND kind='MARKING'", (part_id,))
    toks = marking_tokens_of(text)
    for t in toks + (["".join(toks)] if len(toks) > 1 else []):
        con.execute("INSERT OR IGNORE INTO part_aliases(part_id,alias_norm,kind) VALUES(?,?,?)", (part_id, t[:80], "MARKING"))


# ---------------- ค่าจากฉลาก (C / R / L ที่ตัวไม่มีพิมพ์) ----------------
# ตัวเก็บประจุ/ตัวต้านทาน/ตัวเหนี่ยวนำ SMD ส่วนใหญ่ไม่มีตัวหนังสือบนตัว -> ใช้ค่าบนฉลากถุงเป็น "ข้อความบนตัว"
# เช่น "4.7uF ±10% 50V Ceramic Capacitor X7R 1206" -> "4.7uF / 50V / ±10% / X7R / 1206 (ค่าจากฉลาก)"
PASSIVE = {"CAP", "RES", "IND"}
LABEL_TAG = "(ค่าจากฉลาก)"
_NUM = r"(?<![\w.])(\d+(?:\.\d+)?)\s*"
_MAIN = {
    "CAP": re.compile(_NUM + r"([pnuµμm]F)(?![a-z])"),
    "RES": re.compile(_NUM + r"([kKmM]?)\s*(?:Ω|ohms?\b|R\b)", re.I),
    "IND": re.compile(_NUM + r"([pnuµμm]H)(?![a-z])"),
    "XTL": re.compile(_NUM + r"([kKMG]Hz)\b"),
}
_IMP = re.compile(_NUM + r"([kKmM]?)\s*Ω")                    # ferrite bead / common-mode: 600Ω@100MHz
_AT = re.compile(r"\s*@\s*(\d+(?:\.\d+)?\s*[kKMG]?Hz)")
_VOLT = re.compile(_NUM + r"(k?V)(?:DC|AC)?(?![a-zA-Z])")
_AMP = re.compile(_NUM + r"(m?A)(?![a-zA-Z])")
_TOL = re.compile(r"±\s*\d+(?:\.\d+)?\s*%|(?<![\w.±])\d+(?:\.\d+)?%")
_WATT = re.compile(r"(?<![\w./])(\d+/\d+|\d+(?:\.\d+)?)\s*(m?W)(?![a-zA-Z])")
_DIEL = re.compile(r"\b(X7R|X5R|X7S|X6S|X8R|X7T|C0G|COG|NP0|Y5V|Z5U)\b", re.I)
_SIZE = re.compile(r"(?<![\d.x×])(01005|0201|0402|0603|0805|1206|1210|1812|2010|2220|2512)(?![\d.x×])")
_RATIO = re.compile(r"(?<![\d:])(\d+(?:\.\d+)?:\d+(?:\.\d+)?)(?![\d:])")


def _unit(u: str) -> str:
    return u.replace("µ", "u").replace("μ", "u")


def label_value(category: str, description: str, package: str = "") -> str:
    """ดึงค่าทางไฟฟ้าจากคำอธิบายบนฉลาก -> ข้อความสั้นสำหรับช่อง "ข้อความบนตัว" (ว่าง = หาค่าไม่ได้)"""
    d = re.sub(r"\s+", " ", description or "")
    out: list[str] = []
    m = _MAIN.get(category)
    hit = m.search(d) if m else None
    if not hit and category == "IND":
        hit = _IMP.search(d)
    if not hit and category in ("CON", "OTH"):   # ไอซี (AN/PM/...) มีตัวหนังสือบนตัวเสมอ -> ไม่ดึงค่า
        hit = next((x for x in (rx.search(d) for rx in (_MAIN["CAP"], _MAIN["RES"], _MAIN["IND"], _MAIN["XTL"])) if x), None)
    if hit:
        v, u = hit.group(1), hit.group(2)
        main = v + (_unit(u) + "Ω" if hit.re in (_MAIN["RES"], _IMP) else _unit(u))
        at = _AT.match(d, hit.end())
        out.append(main + (" @" + at.group(1).replace(" ", "") if at else ""))
    for rx in (_VOLT, _AMP):
        x = rx.search(d)
        if x:
            out.append(x.group(1) + x.group(2))
    x = _TOL.search(d)
    if x:
        out.append(x.group(0).replace(" ", ""))
    x = _DIEL.search(d)
    if x:
        out.append(x.group(1).upper().replace("COG", "C0G"))
    x = _WATT.search(d)
    if x:
        out.append(x.group(1) + x.group(2))
    x = _RATIO.search(d)
    if x and category == "IND":
        out.append(x.group(1))
    x = _SIZE.search(package or "") or _SIZE.search(d)
    if x:
        out.append(x.group(1))
    if not hit:   # ไม่มีค่าหลัก (F/Ω/H/Hz) = ไม่ใช่ค่าของ passive ที่เชื่อถือได้
        return ""
    return " / ".join(out) + " " + LABEL_TAG


def no_print(marking: str) -> bool:
    """ช่องข้อความบนตัวว่าง หรือบันทึกไว้ว่าตัวไม่มีตัวหนังสือ"""
    m = (marking or "").strip()
    return not m or m.startswith("(ไม่มีตัวหนังสือ")


def guess_category(mpn: str, description: str = "") -> str:
    m = (mpn or "").upper().strip()
    for code, pattern in CATEGORY_RULES:
        if re.match(pattern, m):
            return code
    d = (description or "").lower()
    for code, words in [
        ("SW", ["high side", "low side", "load driver", "power distribution"]),
        ("GD", ["gate driver", "isolat", "optocoupler"]),
        ("IF", ["can ", "lin ", "transceiver", "rs-485"]),
        ("PM", ["regulator", "ldo", "dc-dc", "buck", "boost", "reference"]),
        ("MOS", ["mosfet"]), ("IGBT", ["igbt"]),
        ("DIO", ["diode", "zener", "tvs", "esd"]),
        ("RES", ["res ", "resistor", "ohm"]), ("CAP", ["cap ", "capacitor", "mlcc"]),
        ("IND", ["inductor", "ferrite"]), ("AN", ["op amp", "comparator", "amplifier"]),
    ]:
        if any(w in d for w in words):
            return code
    return "OTH"


def find_or_create_part(con: sqlite3.Connection, mpn: str, **fields) -> tuple[int, bool]:
    n = norm(mpn)
    row = con.execute("SELECT id FROM parts WHERE mpn_norm=?", (n,)).fetchone()
    if row:
        # เติมข้อมูลที่ยังว่าง ไม่ทับของเดิม
        for k in ("manufacturer", "package", "description"):
            v = (fields.get(k) or "").strip()
            if v:
                con.execute(f"UPDATE parts SET {k}=?, updated_at=? WHERE id=? AND ({k} IS NULL OR {k}='')",
                            (v, now(), row["id"]))
        return row["id"], False
    cur = con.execute(
        "INSERT INTO parts(mpn,mpn_norm,manufacturer,category,package,description,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (mpn.strip(), n, fields.get("manufacturer", ""), fields.get("category") or guess_category(mpn, fields.get("description", "")),
         fields.get("package", ""), fields.get("description", ""), now(), now()))
    return cur.lastrowid, True


def add_alias(con: sqlite3.Connection, part_id: int, alias: str, kind: str) -> None:
    a = norm(alias)
    if a:
        con.execute("INSERT OR IGNORE INTO part_aliases(part_id,alias_norm,kind) VALUES(?,?,?)", (part_id, a, kind))


def search(con: sqlite3.Connection, q: str, limit: int = 50) -> list[sqlite3.Row]:
    """ค้นจาก MPN, เบอร์ผู้ขาย (LCSC/Digikey/Mouser), marking, คำอธิบาย"""
    n = norm(q)
    if not n:
        return []
    like = f"%{n}%"
    return con.execute(
        """
        SELECT p.*, c.name_th AS cat_name, c.color AS cat_color,
               COALESCE((SELECT SUM(qty) FROM stock_lots l WHERE l.part_id=p.id),0) AS total_qty,
               (SELECT GROUP_CONCAT(DISTINCT loc.code) FROM stock_lots l JOIN locations loc ON loc.id=l.location_id
                 WHERE l.part_id=p.id AND l.qty>0) AS locs,
               CASE WHEN p.mpn_norm=? THEN 0
                    WHEN p.mpn_norm LIKE ? THEN 1
                    WHEN EXISTS(SELECT 1 FROM part_aliases a WHERE a.part_id=p.id AND a.alias_norm=?) THEN 0
                    ELSE 2 END AS rank
        FROM parts p JOIN categories c ON c.code=p.category
        WHERE p.mpn_norm LIKE ?
           OR EXISTS(SELECT 1 FROM part_aliases a WHERE a.part_id=p.id AND a.alias_norm LIKE ?)
           OR UPPER(p.description) LIKE ? OR UPPER(p.marking) LIKE ?
        ORDER BY rank, p.mpn LIMIT ?""",
        (n, n + "%", n, like, like, f"%{q.upper()}%", like, limit)).fetchall()
