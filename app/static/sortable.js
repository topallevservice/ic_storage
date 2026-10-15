// ลากเรียงลำดับรูป — ไม่ใช้ไลบรารีภายนอก (ใช้ได้แม้ไม่มีเน็ต)
// มือถือ: กดค้าง ~0.3 วินาที แล้วลาก (ปัดเร็วๆ = เลื่อนหน้าจอตามปกติ) / คอม: กดเมาส์ลากได้เลย
function makeSortable(box, itemSel, onEnd) {
  let item = null, timer = null, sx = 0, sy = 0, active = false, before = "", justDragged = false;
  const items = () => [...box.querySelectorAll(itemSel)];
  box.addEventListener("click", (e) => { if (justDragged) { e.preventDefault(); e.stopPropagation(); } }, true);
  const orderKey = () => items().map((el) => el.dataset.file).join(",");

  function begin() {
    active = true;
    before = orderKey();
    item.classList.add("dragging");
    box.classList.add("sorting");
    if (navigator.vibrate) navigator.vibrate(15);
  }
  function moveTo(x, y) {
    for (const el of items()) {
      if (el === item) continue;
      const r = el.getBoundingClientRect();
      if (x > r.left && x < r.right && y > r.top && y < r.bottom) {
        const list = items();
        if (list.indexOf(item) < list.indexOf(el)) el.after(item); else el.before(item);
        break;
      }
    }
  }
  function end() {
    clearTimeout(timer);
    if (item && active) {
      item.classList.remove("dragging");
      box.classList.remove("sorting");
      // กันไม่ให้การปล่อยนิ้ว/เมาส์ไปเปิดลิงก์รูป (เฉพาะช่วงสั้นๆ หลังลาก)
      justDragged = true;
      setTimeout(() => { justDragged = false; }, 400);
      if (orderKey() !== before) onEnd(items());
    }
    item = null;
    active = false;
  }
  const blocked = (t) => t.closest("button, input, select, textarea, label");

  box.addEventListener("touchstart", (e) => {
    const t = e.target.closest(itemSel);
    if (!t || blocked(e.target)) return;
    item = t;
    sx = e.touches[0].clientX;
    sy = e.touches[0].clientY;
    timer = setTimeout(begin, 300);
  }, { passive: true });
  box.addEventListener("touchmove", (e) => {
    if (!item) return;
    const p = e.touches[0];
    if (!active) {  // ขยับก่อนครบเวลา = ตั้งใจเลื่อนจอ
      if (Math.hypot(p.clientX - sx, p.clientY - sy) > 10) { clearTimeout(timer); item = null; }
      return;
    }
    e.preventDefault();
    moveTo(p.clientX, p.clientY);
  }, { passive: false });
  box.addEventListener("touchend", end);
  box.addEventListener("touchcancel", end);

  box.addEventListener("mousedown", (e) => {
    const t = e.target.closest(itemSel);
    if (!t || e.button !== 0 || blocked(e.target)) return;
    e.preventDefault();
    item = t;
    sx = e.clientX;
    sy = e.clientY;
    const mm = (ev) => {
      if (!active && Math.hypot(ev.clientX - sx, ev.clientY - sy) > 5) begin();
      if (active) moveTo(ev.clientX, ev.clientY);
    };
    const mu = () => {
      document.removeEventListener("mousemove", mm);
      document.removeEventListener("mouseup", mu);
      if (!active) { item = null; return; }  // แค่คลิก ไม่ได้ลาก -> ให้ลิงก์ทำงานปกติ
      end();
    };
    document.addEventListener("mousemove", mm);
    document.addEventListener("mouseup", mu);
  });
}
