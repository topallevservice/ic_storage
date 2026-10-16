// อัปเดตเรียลไทม์: ฟังการเปลี่ยนแปลงของ Database (/api/events) แล้วอัปเดตหน้าจอเอง
// - หน้าที่ลงทะเบียน Live.on(fn) (แผนผัง, ค้นหา/เบิก) = อัปเดตเฉพาะตัวเลข/รายการ ไม่โหลดหน้าใหม่
// - หน้าอื่น = โหลดใหม่เองเมื่อผู้ใช้ไม่ได้พิมพ์/ไม่มีหน้าต่างเปิดอยู่ ; ถ้ากำลังทำงานอยู่ แสดงแถบ "มีข้อมูลใหม่" ให้แตะเอง
// - <body data-live="manual"> (เช่น หน้ารับของ) = แสดงแถบอย่างเดียว ไม่โหลดเอง
// การเชื่อมต่อ:
// - เซิร์ฟเวอร์ปิดสายทุก ~25 วิ แล้วเบราว์เซอร์ต่อใหม่เอง = ปกติ ไม่แจ้ง "ขาดการเชื่อมต่อ" (แจ้งเมื่อต่อไม่ได้เกิน 5 วิ)
// - แท็บที่ไม่ได้เปิดดู ปิดสายทิ้ง (Safari ให้ต่อเซิร์ฟเวอร์เดียวกันได้ ~6 สาย — หลายแท็บค้างสายจนหน้าอื่นโหลดไม่ขึ้น)
// - ไม่ได้ยินเสียงจากเซิร์ฟเวอร์เกิน 30 วิ (Wi-Fi มือถือตัดเงียบ) -> ต่อใหม่เอง
(function () {
  const body = document.body;
  let known = parseInt(body.dataset.rev || "0", 10) || null;   // ไม่รู้รุ่น -> ใช้ข้อความแรกเป็นจุดเริ่ม (กันโหลดวน)
  const handlers = [];
  let dirty = false, pending = false, bar = null;
  let es = null, offTimer = null, lastHeard = Date.now();

  document.addEventListener("input", (e) => { if (e.target.closest("form")) dirty = true; }, true);

  function busy() {
    const a = document.activeElement;
    return dirty || document.querySelector("dialog[open]") || document.querySelector(".toast.show")
      || document.querySelector(".cabcard.editing") || document.querySelector(".busy")
      || (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && a.type !== "search");
  }

  function showBar() {
    if (bar) return;
    bar = document.createElement("button");
    bar.type = "button";
    bar.className = "livebar";
    bar.textContent = "🔄 มีข้อมูลใหม่จากอีกเครื่อง — แตะเพื่ออัปเดต";
    bar.onclick = () => location.reload();
    body.appendChild(bar);
  }

  // หน้าที่ไม่ได้ลงทะเบียนตัวอัปเดตเอง: โหลดใหม่เมื่อว่าง
  function reloadWhenIdle() {
    if (body.dataset.live === "manual") { showBar(); return; }
    if (busy()) { pending = true; showBar(); return; }
    location.reload();
  }
  setInterval(() => { if (pending && body.dataset.live !== "manual" && !busy()) location.reload(); }, 1500);

  async function changed(rev) {
    if (!handlers.length) return reloadWhenIdle();
    for (const fn of handlers) {
      try { await fn(rev); } catch (e) { reloadWhenIdle(); return; }
    }
  }

  function online() {
    clearTimeout(offTimer); offTimer = null;
    body.classList.remove("offline");
  }

  function heard(rev) {
    lastHeard = Date.now();
    online();
    if (!rev) return;
    if (known === null) { known = rev; return; }
    if (rev <= known) return;   // รุ่นเดียวกับที่หน้านี้แสดงอยู่แล้ว (หรือเลขเก่าที่มาช้า) = ไม่ต้องทำอะไร
    known = rev;
    changed(rev);
  }

  // ถามเลขรุ่นตรง ๆ ครั้งเดียว (ไม่พึ่งสายค้าง): iPhone ตัดสาย /api/events เงียบ ๆ ตอนสลับแอป/ล็อกจอ
  // -> กลับมาที่หน้านี้ / สายเงียบ -> ถามเอง แผนผังจะได้ไม่ค้างข้อมูลเก่า
  let polling = false;
  async function poll() {
    if (polling || document.hidden) return;
    polling = true;
    try {
      const t = await (await fetch("/api/events?lifetime=0.2", { cache: "no-store" })).text();
      const m = t.match(/data: (\d+)/);
      if (m) heard(parseInt(m[1], 10));
    } catch (e) { /* ออฟไลน์ — รอบหน้าค่อยถามใหม่ */ }
    finally { polling = false; }
  }

  function connect() {
    if (!window.EventSource || es || document.hidden) return;
    es = new EventSource("/api/events");
    lastHeard = Date.now();
    es.onmessage = (e) => heard(parseInt(e.data, 10));
    es.onopen = () => { lastHeard = Date.now(); online(); };
    es.onerror = () => {
      // สายหลุด/เซิร์ฟเวอร์ปิดตามรอบ: เบราว์เซอร์ต่อใหม่เองใน ~1.5 วิ — แจ้งเฉพาะเมื่อต่อไม่ได้นานจริง
      if (es && es.readyState === EventSource.CLOSED) { es = null; setTimeout(connect, 2000); }
      if (!offTimer) offTimer = setTimeout(() => body.classList.add("offline"), 5000);
    };
  }

  function disconnect() {
    if (es) { es.close(); es = null; }
    clearTimeout(offTimer); offTimer = null;
  }

  // แท็บถูกซ่อน (สลับแอป/แท็บ, ล็อกจอ) -> ปิดสาย ; กลับมา -> ต่อใหม่ (ข้อความแรกบอกว่าพลาดอะไรไปหรือไม่)
  document.addEventListener("visibilitychange", () => (document.hidden ? disconnect() : (connect(), poll())));
  window.addEventListener("pagehide", disconnect);
  window.addEventListener("pageshow", () => { connect(); poll(); });
  window.addEventListener("focus", poll);
  window.addEventListener("online", () => { disconnect(); connect(); });
  // สายเงียบผิดปกติ (เซิร์ฟเวอร์ส่งสัญญาณทุก 10 วิ) -> ตัดแล้วต่อใหม่
  setInterval(() => {
    if (document.hidden) return;
    if (Date.now() - lastHeard > 12000) poll();                 // สายเงียบเกินรอบสัญญาณชีพ (10 วิ) -> ถามเอง
    if (es && Date.now() - lastHeard > 30000) { disconnect(); connect(); }
  }, 5000);

  window.Live = {
    on(fn) { handlers.push(fn); },
    reloadWhenIdle,
    // บอกว่าหน้านี้อัปเดตถึงรุ่นไหนแล้ว (เช่น หลังเบิกเองแล้วอัปเดตจอเรียบร้อย)
    seen(rev) { if (rev > known) known = rev; },
  };
  connect();
})();
