// ใช้ทุกหน้า: ปุ่มย้อนกลับ + ถามก่อนลบข้อมูล
(function () {
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
