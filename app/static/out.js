// หน้าต่างเบิกใช้ (ตัดสต๊อก) — ใช้ร่วมกันระหว่างหน้าแผนผังตู้และหน้าเบิกใช้
// OutSheet.open(parts, {loc, title, note, useNote, onDone})  parts = [{part_id, mpn, image, qty, locs|loc, level?}]
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const JOB_KEY = "ic_out_job";
  let dlg, parts = [], opts = {}, sel = -1, busy = false, reqId = null, reqSig = "", autoNote = false;

  function build() {
    dlg = document.createElement("dialog");
    dlg.className = "sheet outsheet";
    dlg.innerHTML = `
      <div class="sheethead"><b id="oTitle">เบิกใช้ (ตัดสต๊อก)</b><button type="button" class="mini secondary" id="oClose">✕</button></div>
      <div id="oNote" class="muted"></div>
      <div id="oParts"></div>
      <div id="oForm">
        <div class="qtyrow">
          <button type="button" class="step" id="oMinus">−</button>
          <input type="number" id="oQty" min="1" value="1" inputmode="numeric" class="bigin">
          <button type="button" class="step" id="oPlus">＋</button>
        </div>
        <div class="quick" id="oQuick"></div>
        <div class="grid2">
          <div><label>JOB</label><input type="text" id="oJob" placeholder="JOB-..."></div>
          <div><label>ใช้ที่ / หมายเหตุ</label><input type="text" id="oNoteIn" placeholder="เช่น U12"></div>
        </div>
        <div id="oLeft" class="outleft"></div>
        <button type="button" class="big" id="oOk" style="width:100%; margin-top:8px" disabled>เลือกเบอร์ก่อน</button>
      </div>
      <div id="oLinks" class="olinks"></div>`;
    document.body.appendChild(dlg);
    const $ = (id) => dlg.querySelector("#" + id);
    $("oClose").onclick = () => dlg.close();
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });  // แตะพื้นหลังเพื่อปิด
    $("oMinus").onclick = () => setQty(qty() - 1);
    $("oPlus").onclick = () => setQty(qty() + 1);
    $("oQty").addEventListener("input", update);
    $("oOk").onclick = submit;
    try { $("oJob").value = localStorage.getItem(JOB_KEY) || ""; } catch (e) { /* โหมดส่วนตัว */ }
  }
  const el = (id) => dlg.querySelector("#" + id);
  const qty = () => parseInt(el("oQty").value, 10) || 0;
  const avail = () => (sel >= 0 ? stockOf(parts[sel]) : 0);
  // เบิกจากแผนผัง = ยอดในช่องนั้น ; หน้าเบิกใช้ = ยอดรวมทุกช่อง
  const stockOf = (p) => p.qty || 0;
  const whereOf = (p) => opts.loc ? opts.loc : (p.locs || []).map((l) => l.name).join(", ") || "ยังไม่ระบุช่อง";

  function setQty(n) {
    el("oQty").value = Math.max(1, Math.min(n, Math.max(1, avail())));
    update();
  }

  function render() {
    const box = el("oParts");
    box.innerHTML = parts.map((p, i) => `<div class="orow">
      <button type="button" class="idc ${i === sel ? "on" : ""} ${p.level === "weak" ? "weak" : ""}" data-i="${i}" ${stockOf(p) ? "" : "disabled"}>
        ${p.image ? `<img src="/images/t_${esc(p.image)}" alt="">` : `<span class="noimg">ไม่มีรูป</span>`}
        <span class="idt"><b class="mono">${esc(p.mpn)}</b>${p.pct ? ` <span class="pct">${p.pct}%</span>` : ""}<br>
          📍 <b>${esc(whereOf(p))}</b> · คงเหลือ <b>${stockOf(p)}</b>${stockOf(p) ? "" : " (หมด)"}
          ${p.reasons ? `<br><span class="muted">${esc(p.reasons.join(" · "))}</span>` : ""}</span>
      </button>
      <a class="btn secondary oinfo" href="/part/${p.part_id}" title="ดูข้อมูล / รูป / ประวัติ ${esc(p.mpn)}">ℹ️<span>ข้อมูล</span></a></div>`).join("");
    box.querySelectorAll(".idc").forEach((b) => b.onclick = () => { sel = +b.dataset.i; render(); setQty(qty()); });
    const p = parts[sel];
    el("oQuick").innerHTML = p ? [1, 2, 5, 10].filter((n) => n < stockOf(p))
      .map((n) => `<button type="button" class="mini secondary" data-n="${n}">${n}</button>`).join("")
      + `<button type="button" class="mini secondary" data-n="${stockOf(p)}">ทั้งหมด ${stockOf(p)}</button>` : "";
    el("oQuick").querySelectorAll("button").forEach((b) => b.onclick = () => setQty(+b.dataset.n));
    const links = [];
    if (p) links.push(`<a class="btn secondary mini" href="/part/${p.part_id}">ℹ️ ข้อมูลไอซี ${esc(p.mpn)}</a>`);
    if (opts.loc) links.push(`<a class="btn secondary mini" href="/loc/${esc(opts.loc)}">📂 เปิดช่อง ${esc(opts.loc)}</a>`);
    el("oLinks").innerHTML = links.join(" ");
    update();
  }

  function update() {
    const p = parts[sel], n = qty(), ok = el("oOk");
    if (!p) { ok.disabled = true; ok.textContent = "เลือกเบอร์ก่อน"; el("oLeft").textContent = ""; return; }
    const left = stockOf(p) - n;
    el("oLeft").innerHTML = n > 0 && left >= 0
      ? `คงเหลือ ${stockOf(p)} − ${n} = <b>${left}</b> ชิ้น${left === 0 ? " <span class='warnchip'>หมด</span>" : ""}`
      : `<span class="bad">เบิกได้ไม่เกิน ${stockOf(p)} ชิ้น</span>`;
    ok.disabled = busy || !(n > 0 && left >= 0);
    ok.textContent = `✔ เบิก ${p.mpn} ${n > 0 ? n : ""} ชิ้น`;
  }

  async function submit() {
    const p = parts[sel];
    if (!p || busy) return;
    busy = true; update();
    const fd = new FormData();
    fd.append("part_id", p.part_id);
    fd.append("qty", qty());
    fd.append("job_ref", el("oJob").value);
    fd.append("note", el("oNoteIn").value);
    if (opts.loc) fd.append("loc", opts.loc);
    // รหัสคำขอเดิมตลอดการลองส่งซ้ำของรายการเดียวกัน: ถ้า Wi-Fi หลุดหลังเซิร์ฟเวอร์ตัดสต๊อกไปแล้ว ส่งซ้ำก็ไม่ตัดซ้ำ
    const sig = [p.part_id, qty(), opts.loc || ""].join("|");
    if (!reqId || reqSig !== sig) { reqId = Date.now().toString(36) + Math.random().toString(36).slice(2, 10); reqSig = sig; }
    fd.append("req", reqId);
    try { localStorage.setItem(JOB_KEY, el("oJob").value); } catch (e) { /* ไม่เป็นไร */ }
    try {
      let r;
      for (let i = 0; ; i++) {
        try { r = await (await fetch("/api/out", { method: "POST", body: fd })).json(); break; }
        catch (e) { if (i >= 1) throw e; await new Promise((ok) => setTimeout(ok, 1500)); }   // สายสะดุด: ลองซ้ำเอง 1 ครั้ง
      }
      if (!r.ok) { toast("❌ " + r.msg, null, true); return; }
      reqId = null;
      dlg.close();
      toast("✔ " + r.msg, r.tx);
      opts.onDone && opts.onDone(r);
    } catch (e) {
      toast("❌ ส่งไม่สำเร็จ — เช็ก Wi-Fi แล้วกดอีกครั้ง", null, true);
    } finally {
      busy = false; update();
    }
  }

  // แจ้งผลด้านล่างจอ + ปุ่มยกเลิก (กันกดผิด)
  let tBox, tTimer;
  function toast(text, tx, bad) {
    if (!tBox) { tBox = document.createElement("div"); tBox.className = "toast"; document.body.appendChild(tBox); }
    tBox.className = "toast show" + (bad ? " bad" : "");
    tBox.innerHTML = `<span>${esc(text)}</span>` + (tx && tx.length ? `<button type="button" class="mini">↩ ยกเลิก</button>` : "");
    clearTimeout(tTimer);
    tTimer = setTimeout(() => tBox.classList.remove("show"), tx ? 15000 : 6000);
    const b = tBox.querySelector("button");
    if (b) b.onclick = async () => {
      if (!confirm("ต้องการยกเลิกการเบิกนี้จริง ๆ ใช่ไหม?\n(" + text.replace(/^✔ /, "") + ")")) return;
      b.disabled = true;
      const fd = new FormData(); fd.append("tx", tx.join(","));
      const r = await (await fetch("/api/out/undo", { method: "POST", body: fd })).json();
      toast((r.ok ? "↩ " : "❌ ") + r.msg, null, !r.ok);
      if (r.ok && opts.onUndo) opts.onUndo(r);
    };
  }

  window.OutSheet = {
    open(list, o = {}) {
      if (!dlg) build();
      parts = list || []; opts = o;
      // เลือกให้อัตโนมัติเฉพาะกรณีชัดเจน: มีตัวเดียว หรือมีตัวมั่นใจ (strong) ตัวเดียว — ไม่เดาแทนผู้ใช้
      const strong = parts.filter((p) => p.level === "strong" && stockOf(p));
      sel = parts.length === 1 && stockOf(parts[0]) ? 0 : (strong.length === 1 ? parts.indexOf(strong[0]) : -1);
      const want = parts.findIndex((p) => o.pid && p.part_id === o.pid && stockOf(p));  // แตะรูปไอซีบนแผนผัง
      if (want >= 0) sel = want;
      el("oTitle").textContent = o.title || "เบิกใช้ (ตัดสต๊อก)";
      el("oNote").innerHTML = o.note || "";
      // หมายเหตุอัตโนมัติ (เช่น "แทน TJA1051T" ตอนเบิกตัวแทน) — ครั้งถัดไปที่ไม่ได้ส่งมา ล้างทิ้ง ไม่ให้ติดไปรายการอื่น
      if (o.useNote !== undefined) el("oNoteIn").value = o.useNote;
      else if (autoNote) el("oNoteIn").value = "";
      autoNote = o.useNote !== undefined;
      el("oQty").value = 1;
      render();
      dlg.showModal();
    },
    toast,
  };
})();
