// ใช้ทุกหน้า: ปุ่มย้อนกลับ + ถามก่อนลบข้อมูล + ส่งรูปแบบทนสายหลุด
(function () {
  // ---------- ส่งข้อมูลแบบทนสายหลุด ----------
  // iPhone: ระหว่างเปิดกล้อง/สลับแอป หน้าเว็บถูกพัก สายเดิมอาจถูกตัด -> ส่งครั้งแรก "การเชื่อมต่อขาดไป"
  // -> ลองส่งซ้ำเอง 1 ครั้ง + มีเวลาจำกัด ; ทุกคำขอมีรหัส (X-Req-Id) เซิร์ฟเวอร์เห็นรหัสซ้ำ = ตอบผลเดิม ไม่บันทึกซ้ำ
  const newId = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  const fnv = (str) => { let h = 0x811c9dc5; for (let i = 0; i < str.length; i++) h = Math.imul(h ^ str.charCodeAt(i), 16777619); return (h >>> 0).toString(36); };
  window.Net = {
    newId,
    async send(url, body, { btn = null, ms = 60000, rid = newId() } = {}) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), ms);
      btn && btn.classList.add("busy");
      try {
        for (let i = 0; ; i++) {
          try { return await fetch(url, { method: "POST", body, signal: ctrl.signal, headers: { "X-Req-Id": rid } }); }
          catch (err) {
            if (err.name === "AbortError") throw new Error("หมดเวลา — เช็ก Wi-Fi");
            if (i >= 2) throw new Error("ส่งไม่ถึงเซิร์ฟเวอร์ — เช็ก Wi-Fi แล้วลองใหม่");
            await new Promise((ok) => setTimeout(ok, 1500 * (i + 1)));
          }
        }
      } finally {
        clearTimeout(timer);
        btn && btn.classList.remove("busy");
      }
    },
    async json(url, body, opt) {
      const r = await Net.send(url, body, opt);
      if (!r.ok) throw new Error("เซิร์ฟเวอร์ตอบ " + r.status);
      return r.json();
    },
  };

  // ฟอร์มบันทึกทุกฟอร์ม (method=post) -> ส่งผ่าน Net แทนการส่งฟอร์มตรง
  // ส่งตรงแล้วสายหลุด = Safari ขึ้นหน้า "การเชื่อมต่อขาดไป" ข้อมูลที่กรอกหายหมด ; แบบนี้ข้อมูลยังอยู่บนจอ กดบันทึกซ้ำได้
  // รหัสคำขอ = หน้านี้ (หรือ data-rid ของงานที่จำไว้) + ข้อมูลในฟอร์ม -> กดซ้ำด้วยข้อมูลเดิม ไม่บันทึกซ้ำ
  const pageId = newId();
  let sending = false;
  window.addEventListener("submit", async (e) => {
    const form = e.target;
    if (e.defaultPrevented || (form.method || "").toLowerCase() !== "post" || form.dataset.native !== undefined || form.target) return;
    e.preventDefault();
    if (sending) return;
    const fd = new FormData(form);
    if (e.submitter && e.submitter.name) fd.append(e.submitter.name, e.submitter.value);
    let sig = form.action;
    for (const [k, v] of fd.entries()) sig += "\n" + k + "=" + (typeof v === "string" ? v : v.name + ":" + v.size + ":" + v.lastModified);
    const btns = [...form.querySelectorAll("button, input[type=submit]")].filter((b) => !b.disabled);
    const box = (e.submitter && !e.submitter.hidden) ? e.submitter : form.querySelector("label") || null;
    sending = true;
    btns.forEach((b) => (b.disabled = true));
    try {
      const r = await Net.send(form.action, fd, { btn: box, ms: 90000, rid: (form.dataset.rid || pageId) + "-" + fnv(sig) });
      if (!r.ok) throw new Error("เซิร์ฟเวอร์ตอบ " + r.status);
      // บันทึกแล้ว -> ลืมงานที่จำไว้ (ยกเว้นเซิร์ฟเวอร์ไม่ยอมบันทึก = มี warn)
      if (form.dataset.draft && !/[?&]warn=/.test(r.url)) {
        form.dataset.saved = "1";   // หน้านี้ห้ามจำงานซ้ำตอนปิดหน้า
        try { localStorage.removeItem(form.dataset.draft); } catch (err) { /* */ }
      }
      if (r.redirected) { location.href = r.url; return; }
      const html = await r.text();
      document.open(); document.write(html); document.close();
    } catch (err) {
      btns.forEach((b) => (b.disabled = false));
      if (form.enctype === "multipart/form-data") form.reset();
      alert("❌ บันทึกไม่สำเร็จ: " + err.message + "\n\nข้อมูลยังอยู่บนจอ — กดบันทึกอีกครั้งได้เลย (ไม่บันทึกซ้ำ)");
    } finally {
      sending = false;
    }
  });

  // ---------- จำหน้าที่ทำค้าง ----------
  // แอปถูกปิด/หน้า error แล้วเปิดใหม่ (เริ่มที่หน้าแรก) ภายใน 30 นาที -> พากลับไปหน้าล่าสุดเอง
  const LAST = "ic_last";
  const freshOpen = (() => { try { return !sessionStorage.getItem("ic_nav"); } catch (e) { return false; } })();
  try {
    const last = JSON.parse(localStorage.getItem(LAST) || "null");
    if (freshOpen && location.pathname === "/" && !location.search && last && last.url !== "/" &&
        Date.now() - last.t < 30 * 60e3) {
      try { sessionStorage.setItem("ic_nav", JSON.stringify(["/"])); } catch (e) { /* */ }
      location.replace(last.url);
      return;
    }
  } catch (e) { /* ไม่เป็นไร */ }
  try {
    const u = new URL(location.href); u.searchParams.delete("msg"); u.searchParams.delete("warn");
    localStorage.setItem(LAST, JSON.stringify({ url: u.pathname + u.search, t: Date.now() }));
  } catch (e) { /* โหมดส่วนตัว */ }

  // งานรับของที่ค้างไว้ -> แถบเตือนทุกหน้า (แตะเพื่อทำต่อ)
  document.addEventListener("DOMContentLoaded", () => {
    if (location.pathname === "/intake") return;
    let d = null;
    try { d = JSON.parse(localStorage.getItem("ic_intake_draft") || "null"); } catch (e) { /* */ }
    if (!d || !d.current || Date.now() - d.t > 12 * 3600e3) return;
    const a = document.createElement("a");
    a.href = "/intake"; a.className = "pendbar";
    a.textContent = "📝 มีงานรับของค้าง: " + (d.current.mpn || "ยังไม่มี MPN") + " — แตะเพื่อทำต่อ";
    document.body.appendChild(a);
  });

  // ---------- ปุ่มย้อนกลับ ----------
  // จำเส้นทางที่เปิดมาในแท็บนี้ (ไม่นับการบันทึกที่เด้งกลับหน้าเดิม) -> ย้อนไปหน้าก่อนหน้าจริง ๆ
  const KEY = "ic_nav";
  const clean = (u) => {
    const x = new URL(u, location.origin);
    x.searchParams.delete("msg"); x.searchParams.delete("warn");
    return x.pathname + x.search;
  };
  const load = () => { try { return JSON.parse(sessionStorage.getItem(KEY) || "[]"); } catch (e) { return []; } };
  const save = (st) => { try { sessionStorage.setItem(KEY, JSON.stringify(st.slice(-30))); } catch (e) { /* โหมดส่วนตัว */ } };
  let st = load();
  const i = st.findIndex((u) => new URL(u, location.origin).pathname === location.pathname);
  if (i >= 0) st = st.slice(0, i);
  st.push(clean(location.href));
  save(st);

  window.Nav = {
    // หน้าที่เปลี่ยน URL เอง (เช่น คำค้นในหน้าค้นหา) -> จำ URL ล่าสุดไว้ ย้อนกลับมาแล้วผลยังอยู่
    update() { const s = load(); if (s.length) { s[s.length - 1] = clean(location.href); save(s); } },
  };

  document.addEventListener("DOMContentLoaded", () => {
    const b = document.getElementById("backBtn");
    if (!b) return;
    b.onclick = () => {
      const s = load();
      location.href = s.length >= 2 ? s[s.length - 2] : (b.dataset.fallback || "/");
    };
  });

  // ---------- ถามก่อนลบ ----------
  // 1) ปุ่ม/ฟอร์มที่มี data-confirm  2) ช่องที่เคยมีข้อมูลแล้วถูกล้างจนว่าง
  const nameOf = (f) => {
    const lab = (f.labels && f.labels[0]) || (f.closest("div") && f.closest("div").querySelector("label"));
    const t = lab ? lab.textContent : (f.dataset.name || f.placeholder || f.name);
    return t.replace(/\s+/g, " ").trim().split(" — ")[0];
  };
  document.addEventListener("submit", (e) => {
    const form = e.target;
    const btn = e.submitter;
    const msg = (btn && btn.dataset.confirm) || form.dataset.confirm;
    if (msg && !confirm(msg)) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if ((form.method || "").toLowerCase() !== "post" || msg) return;
    const cleared = [...form.querySelectorAll("input:not([type]), input[type=text], input[type=search], textarea")]
      .filter((f) => !f.disabled && f.defaultValue.trim() && !f.value.trim());
    if (cleared.length && !confirm("ข้อมูลต่อไปนี้จะถูกลบ:\n" + cleared.map((f) => "• " + nameOf(f)).join("\n")
      + "\n\nต้องการลบจริง ๆ ใช่ไหม?")) {
      e.preventDefault(); e.stopImmediatePropagation();
    }
  }, true);
})();
