"""รูปตัวไอซี: เก็บต้นฉบับ + ตัดกรอบเฉพาะตัวชิปอัตโนมัติ (c_) + thumbnail (t_)
ไฟล์ X.jpg  -> ต้นฉบับย่อ 1600px
          c_X.jpg -> สี่เหลี่ยมจัตุรัสเฉพาะตัวชิป 600px (ใช้บนป้าย)
          t_X.jpg -> thumbnail 240px จาก c_ (ใช้ในหน้าเว็บ)"""
import uuid
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from .db import IMAGE_DIR

CROP_PX = 600
THUMB_PX = 240


def new_name() -> str:
    return f"{datetime.now():%Y-%m-%d}_{uuid.uuid4().hex[:8]}.jpg"


def save_upload(fileobj) -> str:
    im = ImageOps.exif_transpose(Image.open(fileobj)).convert("RGB")
    im.thumbnail((1600, 1600))
    name = new_name()
    im.save(IMAGE_DIR / name, quality=88)
    auto_crop(name)
    return name


def _find_chip_box(rgb: np.ndarray) -> tuple[int, int, int, int] | None:
    """หา bounding box ของตัวชิปบนพื้นหลัง: ใช้ความ 'ต่างจากสีพื้น' ที่ขอบรูป
    (ชิปส่วนใหญ่สีดำบนพื้นสว่าง แต่รองรับชิ้นงานสีอ่อนด้วย) ตัดแสงสะท้อนสีขาวทิ้ง"""
    h, w = rgb.shape[:2]
    scale = 500 / max(h, w)
    small = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab = cv2.GaussianBlur(lab, (5, 5), 0)
    sh, sw = lab.shape[:2]
    b = max(4, int(min(sh, sw) * 0.04))
    border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3),
                             lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    bg = np.median(border, axis=0)

    candidates = []
    L = lab[:, :, 0]
    masks = []
    # 1) ตัวถังไอซีดำ: มืดกว่ากึ่งกลางระหว่างจุดมืดสุดกับพื้น (เงาบนพื้นจะไม่ผ่าน)
    low, med = np.percentile(L, 2), np.median(L)
    if med - low > 50:
        masks.append(L < low + (med - low) * 0.38)
    # 2) ชิ้นงานสี/สีอ่อน: สีต่างจากพื้นที่ขอบรูปชัดเจน
    colordiff = np.linalg.norm(lab[:, :, 1:] - bg[1:], axis=2)
    if colordiff.max() > 15:
        masks.append(colordiff > max(12, colordiff.max() * 0.5))
    for m in masks:
        mask = m.astype(np.uint8) * 255
        k = max(3, int(min(sh, sw) * 0.02)) | 1
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8), iterations=2)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, _, stats, cent = cv2.connectedComponentsWithStats(mask)
        for i in range(1, n):
            x, y, ww, hh, area = stats[i]
            frac = area / (sh * sw)
            if frac < 0.005 or frac > 0.85:
                continue
            touches = (x <= 1) + (y <= 1) + (x + ww >= sw - 1) + (y + hh >= sh - 1)
            if touches >= 2:
                continue  # เงา/ขอบโต๊ะ
            cx, cy = cent[i]
            center = 1 - (abs(cx - sw / 2) / sw + abs(cy - sh / 2) / sh)
            candidates.append((area * center * (0.5 if touches else 1), (x, y, ww, hh)))
        if candidates:
            break
    if not candidates:
        return None
    x, y, ww, hh = max(candidates)[1]
    return int(x / scale), int(y / scale), int(ww / scale), int(hh / scale)


# ---------- หาชิปจาก "ความคมชัด" (ส่วนที่อยู่ในโฟกัส) ----------
# รูปถ่ายใกล้ด้วยแหนบ: ฉากหลังเบลอแต่มีสีเข้ม/ยุ่ง -> วิธีสีพื้นจับฉากหลังผิด
# ตัวถังไอซีที่อยู่ในโฟกัสมีผิวลายละเอียด = ความคมสูง + สีเข้ม ; แขนแหนบ = แถบบาง ตัดทิ้งด้วย opening/ความหนา

def _run(prof: np.ndarray) -> tuple[int, int]:
    """ช่วงต่อเนื่องที่ยาวที่สุดที่ 'หนา' >= 40% ของค่าสูงสุด"""
    ok = list(prof >= prof.max() * 0.4) + [False]
    runs, start = [], None
    for i, v in enumerate(ok):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((i - start, start, i))
            start = None
    _, a, b = max(runs)
    return a, b


def _trim(m: np.ndarray, x: int, y: int) -> tuple[int, int, int, int]:
    """กรอบของก้อน หลังตัดส่วนที่บางกว่าตัวถัง (แขนแหนบ/ขอบโต๊ะ) ออก"""
    m = m.astype(np.uint8)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(m, cs, -1, 1, thickness=cv2.FILLED)   # อุดรู: ตัวหนังสือ/แผ่นระบายความร้อนสีอ่อน
    c0, c1 = _run(m.sum(axis=0))
    r0, r1 = _run(m[:, c0:c1].sum(axis=1))
    return x + c0, y + r0, c1 - c0, r1 - r0


def _components(mask: np.ndarray) -> list:
    sh, sw = mask.shape
    out = []
    n, lbl, stats, cent = cv2.connectedComponentsWithStats(mask)
    for i in range(1, n):
        x, y, ww, hh, area = stats[i]
        if not 0.004 < area / (sh * sw) < 0.7:
            continue
        touches = (x <= 1) + (y <= 1) + (x + ww >= sw - 1) + (y + hh >= sh - 1)
        if touches >= 2:
            continue
        fill = area / (ww * hh)
        cx, cy = cent[i]
        center = 1 - (abs(cx - sw / 2) / sw + abs(cy - sh / 2) / sh)
        if max(ww, hh) > 3 * min(ww, hh):
            fill *= 0.3                  # แถบยาว (แขนแหนบ/ขอบโต๊ะ) ไม่ใช่ตัวถังไอซี
        out.append((area * center * fill * (0.4 if touches else 1),
                    _trim(lbl[y:y + hh, x:x + ww] == i, x, y)))
    return out


def _grow(dark: np.ndarray, S: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """ขยายกรอบให้คลุมตัวถังทั้งก้อน (ส่วนผิวเรียบ/ถูกแผ่นเงินแบ่ง อาจหลุดจากกรอบแรก)
    ไม่ขยายถ้าลามออกไปฉากหลัง: ชนขอบรูป, รูปร่างไม่เป็นสี่เหลี่ยม หรือส่วนที่เพิ่มมาเบลอ (นอกโฟกัส)"""
    x, y, w, h = box
    H, W = dark.shape
    m = max(w, h)
    for _ in range(4):                      # หน้าต่างค้นหาขยายทีละเท่าจนคลุมทั้งก้อน
        x0, y0, x1, y1 = max(0, x - m), max(0, y - m), min(W, x + w + m), min(H, y + h + m)
        win = cv2.morphologyEx(dark[y0:y1, x0:x1].astype(np.uint8), cv2.MORPH_OPEN,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
        _, lbl = cv2.connectedComponents(win)
        seed = lbl[y - y0 + h // 4:y - y0 + 3 * h // 4 + 1, x - x0 + w // 4:x - x0 + 3 * w // 4 + 1]
        ids, cnt = np.unique(seed[seed > 0], return_counts=True)
        if not len(ids):
            return box
        comp = lbl == ids[np.argmax(cnt)]
        nx, ny, nw, nh = _trim(comp, x0, y0)
        if (nx <= x0 and x0 > 0) or (ny <= y0 and y0 > 0) or (nx + nw >= x1 and x1 < W) or (ny + nh >= y1 and y1 < H):
            m *= 2
            continue
        break
    else:
        return box
    if nw < w * 0.9 or nh < h * 0.9 or nw * nh > w * h * 15:
        return box
    if nx <= 0 or ny <= 0 or nx + nw >= W or ny + nh >= H:
        return box
    filled = comp[ny - y0:ny - y0 + nh, nx - x0:nx - x0 + nw].astype(np.uint8)
    cs, _ = cv2.findContours(filled, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(filled, cs, -1, 1, thickness=cv2.FILLED)
    if filled.mean() < 0.6:
        return box
    r = S[ny:ny + nh, nx:nx + nw]
    if (r < 0.05).mean() > 0.1 or r.mean() < S[y:y + h, x:x + w].mean() * 0.5:
        return box
    return nx, ny, nw, nh


def find_chip_box(rgb: np.ndarray) -> tuple[int, int, int, int] | None:
    """กรอบตัวชิปในรูปเต็ม: ใช้วิธีสีพื้นเดิมถ้ากรอบนั้นอยู่ในโฟกัส ; ถ้าเบลอ (จับฉากหลังผิด) ใช้กรอบจากความคมชัด
    แล้วขยายให้คลุมตัวถังทั้งก้อน"""
    h, w = rgb.shape[:2]
    scale = 500 / max(h, w)
    small = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    sh, sw = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    lap = np.abs(cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F, ksize=3))
    k = max(9, int(min(sh, sw) * 0.06)) | 1
    S = cv2.blur(lap, (k, k))
    S = S / (np.percentile(S, 99.5) + 1e-6)
    sharp = S > 0.2
    src = gray[sharp] if 0.01 < sharp.mean() < 0.6 else gray.ravel()
    t1, _ = cv2.threshold(src.reshape(-1, 1), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    t2, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = gray < min(t1, t2)    # แผ่นเงิน/ขาสว่างมาก ทำให้เกณฑ์เดียวสูงเกิน -> ใช้ค่าต่ำกว่า

    # ฉากหลังยุ่ง (ขอบรูปมีหลายสี/มืดสว่างปน) = วิธีสีพื้นเชื่อไม่ได้ -> ต้องผ่านเกณฑ์โฟกัสด้วย
    lab = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2LAB).astype(np.float32), (5, 5), 0)
    b = max(4, int(min(sh, sw) * 0.04))
    border = np.concatenate([lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3),
                             lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3)])
    busy = np.percentile(np.linalg.norm(border - np.median(border, axis=0), axis=1), 90) > 90

    box = None
    ob = _find_chip_box(rgb)
    if ob:
        bx, by, bw, bh = [int(v * scale) for v in ob]
        bw, bh = max(1, bw), max(1, bh)
        d = dark[by:by + bh, bx:bx + bw]
        if d.mean() > 0.4:
            # กรอบติดแขนแหนบมาด้วย -> ตัดส่วนที่บางกว่าตัวถังออก (ชิปสีอ่อน: ส่วนเข้มคือแหนบ ไม่ตัด)
            tb = _trim(d, bx, by)
            if tb[2] * tb[3] >= bw * bh * 0.35 and max(tb[2], tb[3]) <= 3.5 * min(tb[2], tb[3]):
                bx, by, bw, bh = tb
        touches = (bx <= 1) + (by <= 1) + (bx + bw >= sw - 1) + (by + bh >= sh - 1)
        touches = 0 if touches == 1 and not busy else touches   # พื้นเรียบ+ชิดขอบเดียว: กรอบจัตุรัสเลื่อนเข้าในรูปได้
        r = S[by:by + bh, bx:bx + bw]
        ok = touches == 0 and bw * bh < sh * sw * 0.4 and max(bw, bh) <= 4.5 * min(bw, bh)
        if ok and (not busy or (r.mean() >= 0.2 and (r < 0.05).mean() < 0.3)):
            box = (bx, by, bw, bh)
    if box is None and 0.01 < sharp.mean() < 0.6:
        mask = (sharp & dark).astype(np.uint8) * 255
        kc = max(3, int(min(sh, sw) * 0.02)) | 1
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((kc, kc), np.uint8), iterations=2)
        for ko in (0.06, 0.035, 0.0):
            ko = max(5, int(min(sh, sw) * ko)) | 1
            cands = _components(cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                                 cv2.getStructuringElement(cv2.MORPH_RECT, (ko, ko))))
            if cands:
                box = max(cands)[1]
                break
    if box is None:
        return ob
    # ขยายเฉพาะบริเวณที่อยู่ในโฟกัส (พื้น/ฉากหลังเรียบหรือเบลอ ไม่นับ)
    kf = max(3, int(min(sh, sw) * 0.015)) | 1
    focus = cv2.dilate((S > 0.05).astype(np.uint8), np.ones((kf, kf), np.uint8)) > 0
    x, y, ww, hh = _grow(dark & focus, S, box)
    return int(x / scale), int(y / scale), int(ww / scale), int(hh / scale)


def _square_crop(im: Image.Image, box: tuple[int, int, int, int], pad: float) -> Image.Image:
    x, y, w, h = box
    side = int(max(w, h) * (1 + 2 * pad))
    if pad:
        # ขาไอซีอยู่ด้านแคบของตัวถัง (SOIC: บน/ล่าง) -> เผื่อด้านแคบให้พอ
        side = max(side, int(min(w, h) * 1.75))
        side = min(side, max(min(im.size), int(max(w, h) * 1.08)))
    cx, cy = x + w / 2, y + h / 2
    left, top = int(round(cx - side / 2)), int(round(cy - side / 2))
    # ชิปอยู่ใกล้ขอบรูป -> เลื่อนกรอบเข้าในรูปแทนการเติมขอบขาว (ชิปยังอยู่ในกรอบครบ)
    if side <= im.width:
        left = min(max(0, left), im.width - side)
    if side <= im.height:
        top = min(max(0, top), im.height - side)
    canvas = Image.new("RGB", (side, side), "white")
    src = im.crop((max(0, left), max(0, top), min(im.width, left + side), min(im.height, top + side)))
    canvas.paste(src, (max(0, -left), max(0, -top)))
    return canvas.resize((CROP_PX, CROP_PX), Image.LANCZOS)


def _save_derivatives(name: str, crop: Image.Image) -> None:
    crop.save(IMAGE_DIR / f"c_{name}", quality=88)
    t = crop.copy()
    t.thumbnail((THUMB_PX, THUMB_PX))
    t.save(IMAGE_DIR / f"t_{name}", quality=82)


def auto_crop(name: str) -> bool:
    """ตัดเฉพาะตัวชิป; หาไม่เจอ -> ตัดจัตุรัสกลางรูป. คืน True ถ้าเจอชิป"""
    im = Image.open(IMAGE_DIR / name).convert("RGB")
    box = find_chip_box(np.asarray(im))
    if box:
        # กรอบ = ตัวถัง ; ขยาย 20% เพื่อให้ติดขาไอซี (ขาสีเงินมักกลืนกับพื้น) ชิปอยู่กลางภาพ
        crop = _square_crop(im, box, pad=0.2)
    else:
        side = min(im.size)
        crop = _square_crop(im, ((im.width - side) // 2, (im.height - side) // 2, side, side), pad=0)
    _save_derivatives(name, crop)
    return box is not None


def manual_crop(name: str, x: float, y: float, w: float, h: float) -> None:
    """ผู้ใช้ลากกรอบเอง (พิกัดเป็นสัดส่วน 0..1 ของรูปต้นฉบับ)"""
    im = Image.open(IMAGE_DIR / name).convert("RGB")
    box = (int(x * im.width), int(y * im.height), int(w * im.width), int(h * im.height))
    _save_derivatives(name, _square_crop(im, box, pad=0))


def ensure_derivatives(names: list[str]) -> None:
    for n in names:
        if n and (IMAGE_DIR / n).exists() and not (IMAGE_DIR / f"c_{n}").exists():
            auto_crop(n)


def delete_files(name: str) -> None:
    for prefix in ("", "c_", "t_"):
        p = IMAGE_DIR / f"{prefix}{name}"
        if p.exists():
            p.unlink()
