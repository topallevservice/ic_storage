"""ทดสอบ v16: เทียบตัวแทน — ชื่อขาหลายหน้าที่/คำพ้อง, เลือกรายการตัวแทนที่ชื่อตรงที่สุด, ค่าของไดโอด/ทรานซิสเตอร์/C"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
os.environ["IC_DATASHEETS"] = r"D:\IC_Datasheets"   # คลัง datasheet จริง (อ่านอย่างเดียว)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import matcher

# 1) ชื่อขา: ขาหลายหน้าที่ / คำพ้อง = ขาเดียวกัน ; คนละหน้าที่ = ต่าง
assert matcher.pin_same("nINT/SDO", "SDO") and matcher.pin_same("INH/LIMP", "INH")
assert matcher.pin_same("SCLK", "SCK") and matcher.pin_same("VSUP", "BAT") and matcher.pin_same("nCS", "SCSN")
assert matcher.pin_same("A (แอโนด)", "ANODE")
assert not matcher.pin_same("SDO", "EN") and not matcher.pin_same("SCLK", "ERR_N")

# 2) ตัวแทนที่ประกาศไว้: หลายรายการตรง -> เอาที่ชื่อตรงที่สุด (ไม่ใช่รายการแรกที่ตรงแบบหลวม)
spec = {"substitutes": [{"mpn": "BZX84-B16 / BZX84-A16", "level": "drop-in"},
                        {"mpn": "BZX84-C2V4,215", "level": "none"}]}
d = matcher._declared(spec, [matcher.norm("BZX84-C2V4,215")] if hasattr(matcher, "norm") else ["BZX84C2V4"])
assert d and d["level"] == "none", d

# 3) ค่าไดโอด/ทรานซิสเตอร์/C: ต้องตรง (ขั้ว, ความจุ, ขนาด) / สูงกว่าได้ (Vrrm, If)
rows = {r["key"]: r["flag"] for r in matcher.param_rows(
    {"params": {"polarity": "PNP", "vrrm_v": 1000, "if_a": 8, "cap_uf": 4.7, "size": "1206"}},
    {"params": {"polarity": "NPN", "vrrm_v": 30, "if_a": 10, "cap_uf": 4.7, "size": "0805"}})}
assert rows == {"polarity": "bad", "vrrm_v": "warn", "if_a": "ok", "cap_uf": "ok", "size": "bad"}, rows

# 4) คลัง datasheet จริง: ทุกเบอร์ในคลังมี datasheet (ยกเว้น OSFQ ที่ยังระบุเบอร์ไม่ได้) + ผลเทียบสำคัญ
lib = matcher.library()
names = {it["mpn"]: it for it in lib["items"] if not it["ref"]}
for m in ["M95640-WMN6P", "TJ50S06M3L,LXHQ", "BQ79616PAPRQ1", "BQ79656PAPRQ1", "GRM31CR71H475KA12L", "MAX5024LASAT",
          "MAX8860EUA33T", "TCAN1146DRQ1", "LM3478QMM/NOPB", "BAT54S", "BZX84-C16,215", "NCV33172DR2G", "MC33172VDR2G",
          "TLV70033DCKRG4", "PMST3906,115", "MC34063EBD-TR", "NCP1117ST33T3G", "GS8MJ"]:
    assert m in names, m
t = names["TCAN1146DRQ1"]["spec"]
assert matcher.pin_diff(t, names["TJA1145T/FDJ"]["spec"]) == []          # ขาตรง ...
assert matcher._declared(t, names["TJA1145T/FDJ"]["names"])["level"] == "none"   # ... แต่รีจิสเตอร์คนละแบบ = ห้ามแทน
a = names["NCV33172DR2G"]["spec"]
assert matcher._declared(a, names["MC33172VDR2G"]["names"])["level"] == "drop-in"
print("V16 ALL OK")

# 5) หน้าเปิดในมือถือ + manifest (ไอคอนบนหน้าจอโฮม) ; ตอนทดสอบไม่ประกาศชื่อ mDNS
from fastapi.testclient import TestClient
from app.main import app
from app import lan
with TestClient(app) as c:
    h = c.get("/connect").text
    assert "/qr?data=http" in h and "เพิ่มลงในหน้าจอหลัก" in h
    m = c.get("/manifest.webmanifest").json()
    assert m["start_url"] == "/" and m["display"] == "standalone" and len(m["icons"]) == 2
    assert 'rel="manifest"' in c.get("/").text
assert lan.PORT == 0 and not lan._state["mdns"]
print("V16 CONNECT OK")
