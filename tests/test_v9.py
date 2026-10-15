"""ทดสอบ v9: ตัดกรอบรูปไอซีให้ตัวชิปเด่นอยู่กลางภาพ (รูปจริงจากคลัง อ่านอย่างเดียว)
- รูปถ่ายใกล้ด้วยแหนบ ฉากหลังเบลอ/มืด/หลายสี: ต้องจับตัวชิปที่อยู่ในโฟกัส ไม่ใช่ฉากหลังมืด
- ตัดแขนแหนบออกจากกรอบ ; ชิปอยู่กลางภาพ ; ไม่มีขอบขาวเมื่อชิปอยู่ในรูปครบ"""
import os, sys, tempfile
os.environ["IC_STORAGE_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from PIL import Image
from app import images as A

SRC = r"D:\IC_Storage\data\part_images"
# ตัวชิปจริงรวมขา (วัดจากรูป 1200x1600): x0, y0, x1, y1
BODY = {
    "2026-10-15_31b77a2f.jpg": (305, 505, 825, 860),    # ฉากหลังมืดเบลอ: วิธีเดิมจับฉากหลัง
    "2026-10-15_da9973de.jpg": (195, 370, 940, 905),    # SOIC-14 คีบแหนบ ฉากหลังหลายสี
    "2026-10-14_d577c3ec.jpg": (300, 640, 800, 1010),   # พื้นฟ้าเรียบ + แหนบยาวถึงขอบรูป
}


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


for name, body in BODY.items():
    im = Image.open(os.path.join(SRC, name)).convert("RGB")
    x, y, w, h = A.find_chip_box(np.asarray(im))
    got = (x, y, x + w, y + h)
    assert iou(got, body) > 0.6, (name, got, body, iou(got, body))
    # กรอบจัตุรัส: ชิปอยู่กลาง (คลาดได้ไม่เกิน 8% ของด้าน) และไม่มีขอบขาว
    crop = np.asarray(A._square_crop(im, (x, y, w, h), 0.2))
    side_px = int(max(max(w, h) * 1.4, min(w, h) * 1.75))
    cx, cy = (body[0] + body[2]) / 2, (body[1] + body[3]) / 2
    left = min(max(0, round(x + w / 2 - side_px / 2)), im.width - side_px)
    top = min(max(0, round(y + h / 2 - side_px / 2)), im.height - side_px)
    assert abs((cx - left) / side_px - 0.5) < 0.08 and abs((cy - top) / side_px - 0.5) < 0.08, (name, cx - left, cy - top, side_px)
    assert not (crop[:6, :6] > 250).all() and not (crop[-6:, -6:] > 250).all(), f"{name}: มีขอบขาว"

# auto_crop ทั้งไฟล์: c_ = 600px จัตุรัส, t_ = thumbnail
from app.db import IMAGE_DIR
import shutil
IMAGE_DIR.mkdir(parents=True, exist_ok=True)
shutil.copy(os.path.join(SRC, "2026-10-15_31b77a2f.jpg"), IMAGE_DIR / "t.jpg")
assert A.auto_crop("t.jpg")
assert Image.open(IMAGE_DIR / "c_t.jpg").size == (600, 600) and max(Image.open(IMAGE_DIR / "t_t.jpg").size) == 240
print("V9 ALL OK")
