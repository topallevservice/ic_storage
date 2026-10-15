// ป๊อปอัพผลการค้นหาแบบเทียบทุกแหล่ง (เบอร์หน้าซอง / ข้อความบนชิป / datasheet) + ตัวแทนที่มีในคลัง
// MatchSheet.open(result, {source, photo})  result = JSON จาก /api/match หรือ /api/identify-ic (.match)
// ระบบแสดงเหตุผลและระดับความมั่นใจเท่านั้น — ผู้ใช้เป็นคนเลือกเอง (เบิก / ดูข้อมูล / เปิด datasheet)
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const KIND = { label: "🏷 หน้าซอง", marking: "🔤 บนชิป", datasheet: "📘 datasheet", photo: "📷 รูปถ่าย", partial: "… บางส่วน" };
  const FLAG = { ok: "✅", warn: "⚠️", bad: "❌", info: "·" };
  let dlg, res, opts = {};

  function build() {
    dlg = document.createElement("dialog");
    dlg.className = "sheet matchsheet";
    dlg.innerHTML = `<div class="sheethead"><b>🔎 ผลการค้นหา</b>
      <button type="button" class="mini secondary" data-x>✕</button></div><div class="mbody"></div>`;
    document.body.appendChild(dlg);
    dlg.querySelector("[data-x]").onclick = () => dlg.close();
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  }

  const where = (p) => (p.locs || []).length
    ? p.locs.map((l) => l.code === "STAGING" ? `<span class="loc wait">รอระบุช่อง</span>` : `<span class="loc">${esc(l.code)}</span>`).join(" ")
    : `<span class="muted">ของหมด</span>`;
  const img = (p) => p.image ? `<img src="/images/t_${esc(p.image)}" alt="">` : `<span class="noimg">ไม่มีรูป</span>`;
  const dsLink = (d) => d && d.pdf_url ? `<a class="btn secondary mini" href="${esc(d.pdf_url)}" target="_blank" rel="noopener">📄 datasheet</a>` : "";

  function verdict(r) {
    const top = r.matches[0], q = `<b class="mono">${esc(r.query)}</b>`;
    const nSub = r.substitutes.filter((s) => s.qty > 0 && !["pin-diff", "none"].includes(s.level)).length;
    switch (r.status) {
      case "found": return ["ok", `✅ <b class="mono">${esc(top.mpn)}</b> (${top.pct}%)` +
        (top.ds_confirm ? " · datasheet ยืนยัน" : "")];
      case "multiple": return ["ok", `✅ ตรงหลายเบอร์ — เลือกเอง`];
      case "out_of_stock": return ["warn", `⚠️ <b class="mono">${esc(top.mpn)}</b> ของหมด` + (nSub ? ` · ตัวแทน ${nSub} เบอร์` : "")];
      case "uncertain": return ["warn", `❓ ${q} ไม่ตรงชัด — ตรวจรุ่นย่อย` + (nSub ? ` · ตัวแทน ${nSub} เบอร์` : "")];
      default: return ["bad", `❌ ไม่มี ${q}` + (nSub ? ` · ตัวแทน ${nSub} เบอร์` : r.substitutes.length ? " · ไม่มีตัวแทนที่ใช้ได้" : "")];
    }
  }

  function matchCard(m, i) {
    const ev = m.evidence.filter((e, j, a) => a.findIndex((x) => x.text === e.text) === j).sort((x, y) => y.score - x.score);
    return `<div class="mcard ${m.level}">
      <div class="mtop">${img(m)}
        <div class="minfo"><div><b class="mono">${esc(m.mpn)}</b> <span class="pct">${m.pct}%</span>
          ${m.level === "strong" ? `<span class="lv ok">ตรง</span>` : m.level === "likely" ? `<span class="lv warn">ตรวจ</span>` : ""}</div>
          ${m.marking ? `<div class="mk">🔤 บนชิป: <span class="mono">${esc(m.marking)}</span></div>` : ""}
          <div class="rloc">📍 ${where(m)} · คงเหลือ <b>${m.qty}</b></div></div></div>
      <div class="evs">${[...new Set(ev.map((e) => e.kind))].map((k) => `<span class="ev ${k}">${KIND[k] || k}</span>`).join("")}</div>
      ${ev.length ? `<div class="muted small">${esc(ev[0].text)}</div>` : ""}
      <div class="mact"><button type="button" data-out="m${i}" ${m.qty ? "" : "disabled"}>🛒 เบิก</button>
        <a class="btn secondary" href="/part/${m.part_id}">ℹ️ ข้อมูล</a>${dsLink(m.datasheet)}</div></div>`;
  }

  function compare(s, target) {
    const tn = target ? esc(target.mpn) : "เบอร์ที่ค้น";
    let pins;
    if (s.pins === null || s.pins === undefined) pins = `<span class="muted">เทียบขาไม่ได้</span>`;
    else if (!s.pins.length) pins = `✅ ตรงทุกขา`;
    else pins = s.pins.map((d) => `❌ ขา ${esc(d.pin)}: <span class="mono">${esc(d.a)}</span> ↔ <span class="mono">${esc(d.b)}</span>`).join("<br>");
    const rows = (s.params || []).map((r) => `<tr class="f-${r.flag}"><td>${FLAG[r.flag]} ${esc(r.name)}</td>
      <td class="mono">${esc(r.a)}</td><td class="mono">${esc(r.b)}</td></tr>`).join("");
    return `<details class="cmp" ${s.level === "pin-diff" || s.level === "none" ? "open" : ""}><summary>เทียบขา/ค่า</summary>
      <div class="cpins"><b>ขา:</b><br>${pins}</div>
      ${rows ? `<div class="tablewrap"><table><tr><th>ค่า</th><th>${tn}</th><th>${esc(s.mpn)}</th></tr>${rows}</table></div>` : ""}
    </details>`;
  }

  function subCard(s, i, target) {
    const tone = { "drop-in": "ok", conditional: "warn", "pin-same": "info", "param-bad": "warn", name: "gray", "pin-diff": "bad", none: "bad", criteria: "gray" }[s.level] || "gray";
    const usable = s.qty > 0 && !["pin-diff", "none"].includes(s.level);
    return `<div class="mcard sub ${tone}">
      <div class="mtop">${img(s)}
        <div class="minfo"><div><b class="mono">${esc(s.mpn)}</b> <span class="lv ${tone}">${esc(s.level_th)}</span></div>
          ${s.marking ? `<div class="mk">🔤 บนชิป: <span class="mono">${esc(s.marking)}</span></div>` : ""}
          <div class="rloc">📍 ${where(s)} · คงเหลือ <b>${s.qty}</b></div></div></div>
      ${s.note ? `<div class="snote">${esc(s.note)}</div>` : ""}
      ${s.level === "name" ? "" : compare(s, target)}
      <div class="mact"><button type="button" data-out="s${i}" ${usable ? "" : "disabled"}>🛒 เบิก</button>
        <a class="btn secondary" href="/part/${s.part_id}">ℹ️ ข้อมูล</a>${dsLink(s.datasheet)}</div></div>`;
  }

  function render() {
    const r = res, [tone, text] = verdict(r);
    const showM = r.matches.filter((m) => r.status === "not_found" ? m.level !== "weak" || r.matches.length <= 3 : true);
    const t = r.target, idRef = t && t.ref;
    const src = { photo: "จากรูปตัวชิป", label: "จากฉลากถุง", text: "พิมพ์ค้น" }[opts.source] || "";
    let h = `<div class="mq">${opts.photo ? `<img src="${esc(opts.photo)}" alt="">` : ""}
        <div>ค้น: <b class="mono">${esc(r.query || "-")}</b> <span class="muted">${src}</span>
        ${r.logo ? `<span class="ev label">🏭 ${esc(r.logo)}</span>` : ""}
</div></div>
      <div class="verdict ${tone}">${text}</div>`;
    if (idRef) h += `<div class="ident">📘 <b class="mono">${esc(t.mpn)}</b> ${t.package ? `· ${esc(t.package)}` : ""}<br>${esc(t.function_th)} ${dsLink(t)}</div>`;
    if (showM.length) h += `<h3>📦 ในคลัง</h3>` + showM.map((m) => matchCard(m, r.matches.indexOf(m))).join("");
    if (r.substitutes.length) {
      h += `<h3>🔁 ตัวแทนในคลัง${t ? "" : " (เทียบจากชื่อเท่านั้น)"}</h3>`
        + r.substitutes.map((s, i) => subCard(s, i, t)).join("");
    } else if (r.status !== "found" && r.status !== "multiple") {
      h += `<p class="muted">ไม่พบตัวแทนในคลัง${t ? "" : " (ไม่มี datasheet ของเบอร์นี้)"}</p>`;
    }
    if (r.substitutes.length) h += `<p class="muted small mfoot">ตรวจขา/ไฟเลี้ยงกับบอร์ดจริงก่อนใส่</p>`;
    const body = dlg.querySelector(".mbody");
    body.innerHTML = h;
    body.querySelectorAll("[data-out]").forEach((b) => b.onclick = () => {
      const k = b.dataset.out, p = k[0] === "m" ? r.matches[+k.slice(1)] : r.substitutes[+k.slice(1)];
      const isSub = k[0] === "s";
      dlg.close();
      OutSheet.open([{ part_id: p.part_id, mpn: p.mpn, image: p.image, qty: p.qty, locs: p.locs }], {
        note: isSub ? `🔁 แทน <b class="mono">${esc(r.query)}</b>` : "",
        useNote: isSub ? `แทน ${r.query}` : undefined,
        onDone: opts.onDone, onUndo: opts.onUndo,
      });
    });
  }

  window.MatchSheet = {
    open(result, o = {}) {
      if (!dlg) build();
      res = result; opts = o;
      render();
      if (!dlg.open) dlg.showModal();
      dlg.querySelector(".mbody").scrollTop = 0;
    },
    // ข้อความสรุปสั้นสำหรับแถบสถานะ (ไม่เปิดป๊อปอัพเอง ระหว่างพิมพ์)
    summary(r) { return verdict(r); },
    isOpen: () => !!(dlg && dlg.open),
  };
})();
