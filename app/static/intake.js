// หน้า สแกนรับของ: ถ่ายรูปฉลาก -> ระบบเติมข้อมูลเอง, ผู้รับเข้าใส่แค่จำนวน + ตำแหน่ง
const $ = (id) => document.getElementById(id);
const MAX_IMAGES = 6;
// รูปไอซีเรียงตามลำดับที่ผู้ใช้ลาก (ตัวแรก = รูปหลัก) ; isNew = ถ่ายในรอบนี้, ไม่ใช่ = มีในระบบแล้ว
let images = [];
const FIELDS = ["mpn", "manufacturer", "description", "package", "supplier", "supplier_pn", "lot", "date_code", "order_no", "coo", "marking"];

let current = null;  // ข้อมูลถุงที่กำลังรับเข้า (รวมผลตรวจซ้ำจากเซิร์ฟเวอร์)
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- ตรวจซ้ำ: รู้ตั้งแต่สแกนฉลาก ----------
function renderDup(item) {
  const box = $("dupBox");
  let html = "";
  if (item.duplicate) {
    const d = item.dup_info || {};
    html += '<div class="dup bag"><div class="duph">⛔ ถุงนี้บันทึกแล้ว</div>' +
      "<div>บันทึกเมื่อ " + esc(d.at) + " · " + esc(d.qty) + " ชิ้น · " + esc(d.loc) + "</div>" +
            '<label class="dupok"><input type="checkbox" name="dup_ok" value="1" id="f_dup_ok"> เป็นถุงใหม่จริง — บันทึกเพิ่ม</label></div>';
  }
  if (item.existing) {
    const where = item.locked_loc ? '<span class="loc">📍 ' + esc(item.locked_loc) + "</span>"
      : (item.waiting ? '<span class="loc wait">⏳ รอระบุช่อง</span>' : "ยังไม่มีช่อง");
    let note = "";
    if (item.matched_by === "supplier_pn") note = "จากเบอร์ผู้ขาย " + esc(item.supplier_pn);
    if (item.matched_by === "alias") note = "ฉลาก: " + esc(item.scanned_mpn);
    html += '<div class="dup same"><div class="duph">🔁 เบอร์ซ้ำ — มี <b>' + esc(item.mpn) + "</b> ในคลังแล้ว</div>" +
      "<div>คงเหลือ <b>" + esc(item.stock_qty) + "</b> ชิ้น" + (item.stock_bags ? " (" + item.stock_bags + " ถุง)" : "") + " · อยู่ที่ " + where + "</div>" +
      '<div class="dupgo">➡ ' + (item.locked_loc ? "ช่องเดิม <b>" + esc(item.locked_loc) + "</b> · " : "") +
      'รวม <b id="dupTotal"></b></div>' + (note ? '<div class="muted">' + note + "</div>" : "") + "</div>";
  } else if (item.similar && item.similar.length) {
    html += '<div class="dup near"><div class="duph">⚠ เบอร์ใกล้เคียงในคลัง</div>' +
      '<div class="muted">ใช่ตัวเดียวกัน = แตะเลือก</div><div class="simlist">';
    for (const s of item.similar) {
      html += '<button type="button" class="secondary sim" data-mpn="' + esc(s.mpn) + '">' +
        (s.image ? '<img src="/images/t_' + esc(s.image) + '" alt="">' : "") +
        "<span><b>" + esc(s.mpn) + "</b><br><small>" + esc(s.loc ? (s.loc === "STAGING" ? "รอระบุช่อง" : s.loc) : "-") + " · " + esc(s.qty) + " ชิ้น</small></span></button>";
    }
    html += "</div></div>";
  }
  box.innerHTML = html;
  box.querySelectorAll(".sim").forEach((b) => (b.onclick = () => { $("f_mpn").value = b.dataset.mpn; lookup(b.dataset.mpn); }));
  const ok = $("f_dup_ok");
  if (ok) ok.onchange = updateSaveBtn;
  updateDupTotal();
  updateSaveBtn();
}

function updateDupTotal() {
  const el = $("dupTotal");
  if (!el || !current) return;
  const have = Number(current.stock_qty) || 0, add = parseInt($("f_qty").value, 10) || 0;
  el.textContent = have + " + " + add + " = " + (have + add) + " ชิ้น";
}

function updateSaveBtn() {
  // ถุงซ้ำต้องติ๊กยืนยันก่อน ถึงจะบันทึกได้
  const ok = $("f_dup_ok");
  const blocked = !!(current && current.duplicate) && !(ok && ok.checked);
  $("btnSave").disabled = blocked;
  $("btnSave").textContent = blocked ? "⛔ ถุงซ้ำ — ติ๊กยืนยันก่อน"
    : (current && current.existing ? "✔ บันทึก (เพิ่มเข้าเบอร์เดิม)" : "✔ บันทึกรับเข้า");
}

function dupStatus(item) {
  if (item.duplicate) return " — ⛔ ถุงนี้บันทึกไปแล้ว!";
  if (item.existing) return " — 🔁 เบอร์ซ้ำ มีในคลังแล้ว" + (item.locked_loc ? " (ช่อง " + item.locked_loc + ")" : "");
  if (item.similar && item.similar.length) return " — ⚠ มีเบอร์ใกล้เคียงในคลัง";
  return " — 🆕 เบอร์ใหม่";
}

function resetForm() {
  for (const k of FIELDS) { const el = $("f_" + k); if (el) el.value = ""; }
  $("f_qty").value = "";
  $("f_raw").value = "";
  images = [];
  $("icStatus").textContent = "";
}

function setLocation(item) {
  const sel = $("f_location"), box = $("lockedBox");
  if (item.locked_loc) {
    // มีตำแหน่งเดิมแล้ว: แสดงตำแหน่งเดิมอย่างเดียว ห้ามเลือกที่อื่น
    sel.value = item.locked_loc;
    sel.hidden = true;
    box.hidden = false;
    box.innerHTML = '<span class="loc" style="font-size:22px">📍 ' + item.locked_loc + "</span><div>" + item.locked_note + "</div>";
    $("sugg").textContent = "";
  } else {
    sel.hidden = false;
    box.hidden = true;
    $("sugg").textContent = item.waiting
      ? "มีถุงรอระบุช่อง " + item.waiting + " ถุง (จะย้ายตาม)"
      : "";
    sel.value = "STAGING";
  }
}

function renderImages() {
  const main = images[0];
  $("icBox").innerHTML = main ? '<img src="/images/t_' + main.file + '" alt="">' : '<span class="muted">ยังไม่มีรูปไอซี</span>';
  const g = $("icGallery");
  g.innerHTML = "";
  images.forEach((im, i) => {
    const d = document.createElement("div");
    d.className = "gimg" + (i === 0 ? " primary" : "");
    d.dataset.file = im.file;
    d.innerHTML = '<span class="order">' + (i + 1) + '</span><img src="/images/t_' + im.file + '" alt="" draggable="false">' +
      '<div class="gbtns">' + (i === 0 ? '<span class="chip" style="background:var(--brand)">รูปหลัก</span>' : "") +
      (im.isNew ? '<button type="button" class="mini secondary danger">เอาออก</button>' : '') + "</div>";
    const rm = d.querySelector("button");
    if (rm) rm.onclick = () => { if (confirm("ต้องการเอารูปนี้ออกจริง ๆ ใช่ไหม?")) { images.splice(i, 1); renderImages(); } };
    g.appendChild(d);
  });
  $("f_ic_images").value = images.filter((im) => im.isNew).map((im) => im.file).join(",");
  $("f_ic_order").value = images.map((im) => im.file).join(",");
  $("icBtn").hidden = images.length >= MAX_IMAGES;
  $("icBtn").textContent = images.length ? "📷 เพิ่มรูป (" + images.length + "/" + MAX_IMAGES + ")" : "📷 ถ่ายรูปตัวไอซี";
  $("icHint").hidden = images.length < 2;
}

// กดค้างแล้วลากเพื่อเรียงรูป — รูปแรกเป็นรูปหลัก
makeSortable($("icGallery"), ".gimg[data-file]", (els) => {
  const byFile = Object.fromEntries(images.map((im) => [im.file, im]));
  images = els.map((el) => byFile[el.dataset.file]);
  renderImages();
});

function updateSummary() {
  const v = (k) => $("f_" + k).value;
  $("sumMpn").textContent = v("mpn") || "—";
  const c = CATS[$("f_category").value];
  $("sumCat").innerHTML = c ? '<span class="chip" style="background:' + c.color + '">' + $("f_category").value + " " + c.name + "</span>" : "";
  $("sumDesc").textContent = [v("manufacturer"), v("package"), v("description")].filter(Boolean).join(" · ");
  $("sumSup").textContent = [v("supplier"), v("supplier_pn"), v("lot") && "Lot " + v("lot"), v("date_code") && "D/C " + v("date_code")]
    .filter(Boolean).join(" · ");
}

function fill(item) {
  resetForm();
  $("form").hidden = false;
  for (const k of FIELDS) {
    const el = $("f_" + k);
    if (el && item[k] !== undefined && item[k] !== null) el.value = item[k];
  }
  if (item.qty !== null && item.qty !== undefined) $("f_qty").value = item.qty;
  if (item.category) $("f_category").value = item.category;
  $("f_raw").value = item.source === "LCSC-QR" || item.source === "ECIA-DM" ? item.raw : "";
  current = item;
  $("editSum").textContent = "✏️ แก้ไขข้อมูล";
  renderDup(item);
  $("sumNew").textContent = item.existing ? "🔁 มีในคลังแล้ว" : "🆕 เบอร์ใหม่";
  if ((item.duplicate || item.existing) && navigator.vibrate) navigator.vibrate([60, 60, 60]);
  images = (item.images || []).map((f) => ({ file: f, isNew: false }));
  renderImages();
  setLocation(item);
  updateSummary();
  showCandidates(item);
  // ไม่มี MPN (อ่านฉลากไม่ออก) -> เปิดช่องแก้ไขให้กรอก
  $("editBox").open = !item.mpn;
  if (!item.mpn) $("f_mpn").focus();
  else $("f_qty").focus();
  $("form").scrollIntoView({ behavior: "smooth", block: "start" });
}

function showCandidates(item) {
  const box = $("choices");
  box.innerHTML = "";
  if (!item.candidates || !item.candidates.length || !["OCR", "CHIP"].includes(item.source)) return;
  const div = document.createElement("div");
  div.className = "cands";
  div.innerHTML = item.source === "CHIP" ? "<label>แตะเพื่อใช้เป็นเบอร์</label>"
    : "<label>MPN ไม่ใช่? แตะคำที่ถูก</label>";
  for (const c of item.candidates) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "secondary"; b.textContent = c;
    b.onclick = () => { $("f_mpn").value = c; lookup(c); };
    div.appendChild(b);
  }
  box.appendChild(div);
}

function chooseAmong(items) {
  // รูปเดียวมีหลายถุง -> ให้เลือกถุง
  const box = $("choices");
  box.innerHTML = "<label>พบ " + items.length + " ถุง — เลือก</label>";
  items.forEach((it) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "secondary"; b.style.margin = "3px";
    b.textContent = (it.mpn || it.supplier_pn || "?") + " x" + (it.qty ?? "?") +
      (it.duplicate ? " ⛔ถุงซ้ำ" : it.existing ? " 🔁มีในคลัง" + (it.locked_loc ? " " + it.locked_loc : "") : "");
    b.onclick = () => fill(it);
    box.appendChild(b);
  });
}

async function lookup(mpn) {
  // แก้ MPN เอง -> เช็คตำแหน่งเดิม/หมวดใหม่
  if (!mpn.trim()) return;
  const r = await fetch("/api/lookup?mpn=" + encodeURIComponent(mpn));
  const it = await r.json();
  if (it.category) $("f_category").value = it.category;
  if (it.mpn && it.mpn !== $("f_mpn").value) $("f_mpn").value = it.mpn;  // ชื่อเรียกอื่น -> ใช้เบอร์ในคลัง
  images = images.filter((im) => im.isNew).concat((it.images || []).map((f) => ({ file: f, isNew: false })));
  renderImages();
  setLocation(it);
  // ผลตรวจถุงซ้ำมาจากโค้ดบนฉลากตอนสแกน -> เก็บไว้เหมือนเดิม
  current = Object.assign({}, current || {}, it, { duplicate: current?.duplicate, dup_info: current?.dup_info });
  renderDup(current);
  $("sumNew").textContent = it.existing ? "🔁 มีในคลังแล้ว" : "🆕 เบอร์ใหม่";
  updateSummary();
}

async function parseText() {
  const text = $("scan").value;
  if (!text.trim()) return;
  const fd = new FormData();
  fd.append("text", text);
  const r = await fetch("/api/parse", { method: "POST", body: fd });
  const data = await r.json();
  fill(data.items[0]);
  $("status").textContent = "✅" + dupStatus(data.items[0]);
  $("scan").value = "";
}

$("scan").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); parseText(); }
});
$("btnParse").onclick = parseText;
$("f_mpn").addEventListener("change", () => lookup($("f_mpn").value));
for (const k of FIELDS.concat(["category"])) $("f_" + k).addEventListener("input", updateSummary);
$("f_qty").addEventListener("input", updateDupTotal);

// ส่งรูปขึ้นเซิร์ฟเวอร์: มีเวลาจำกัด (ไม่ค้างรอตลอดไป) + ล็อกปุ่มระหว่างทำงาน (กันกดซ้ำจนคิวยาว)
async function postPhoto(url, file, btn, ms = 60000) {
  const fd = new FormData();
  fd.append("photo", file);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  btn.classList.add("busy");
  try {
    let r;
    for (let i = 0; ; i++) {
      try { r = await fetch(url, { method: "POST", body: fd, signal: ctrl.signal }); break; }
      catch (err) {
        // สายสะดุด/Wi-Fi หลุดชั่วครู่: ลองส่งซ้ำเอง 1 ครั้ง (รูปยังอยู่ในเครื่อง ไม่ต้องถ่ายใหม่)
        if (err.name === "AbortError" || i >= 1) throw err;
        await new Promise((ok) => setTimeout(ok, 1500));
      }
    }
    if (!r.ok) throw new Error("เซิร์ฟเวอร์ตอบ " + r.status);
    return await r.json();
  } catch (err) {
    if (err.name === "AbortError") throw new Error("หมดเวลา — เช็ก Wi-Fi");
    if (err instanceof TypeError) throw new Error("ส่งไม่ถึงเซิร์ฟเวอร์ — เช็ก Wi-Fi แล้วถ่ายใหม่");
    throw err;
  } finally {
    clearTimeout(timer);
    btn.classList.remove("busy");
  }
}

$("photo").addEventListener("change", async () => {
  const file = $("photo").files[0];
  if (!file) return;
  const t0 = Date.now();
  $("preview").innerHTML = '<img src="' + URL.createObjectURL(file) + '">';
  $("status").textContent = "⏳ กำลังอ่านฉลาก...";
  $("choices").innerHTML = "";
  try {
    const data = await postPhoto("/api/decode-image", file, document.querySelector('label[for="photo"]'));
    $("f_photo").value = data.photo || "";
    if (!data.items.length) {
      $("status").textContent = "❌ อ่านไม่ได้ — ถ่ายใกล้ขึ้น หรือพิมพ์เบอร์";
    } else if (data.items.length === 1) {
      $("status").textContent = (data.items[0].source === "OCR" ? "⚠ อ่านจากตัวหนังสือ — ตรวจ MPN" : "✅") + dupStatus(data.items[0]);
      fill(data.items[0]);
      $("f_photo").value = data.photo || "";
    } else {
      $("status").textContent = "อ่านได้หลายถุง";
      chooseAmong(data.items);
    }
  } catch (err) {
    $("status").textContent = "ผิดพลาด: " + err;
  }
  $("photo").value = "";
});

// ข้อความบนตัวชิปจาก OCR: เติมต่อท้ายช่อง marking เฉพาะคำที่ยังไม่มี (ผู้ใช้ตรวจ/แก้เองก่อนบันทึก)
function addChipText(text, tokens) {
  const box = $("f_marking");
  const norm = (s) => s.toUpperCase().replace(/[\s\-_/.,#()]/g, "");
  const have = norm(box.value);
  if (!text) return "";
  if (!box.value.trim()) { box.value = text; return text; }
  const fresh = tokens.filter((t) => !have.includes(t));
  if (!fresh.length) return "";
  box.value = box.value.trim() + " · " + fresh.join(" ");
  return fresh.join(" ");
}

$("icPhoto").addEventListener("change", async () => {
  const file = $("icPhoto").files[0];
  if (!file) return;
  $("icStatus").textContent = "⏳ กำลังอัปโหลด...";
  try {
    const data = await postPhoto("/api/ic-photo", file, $("icBtn"));
    images.push({ file: data.image, isNew: true });
    renderImages();
    const added = addChipText(data.chip_text || "", data.tokens || []);
    // โลโก้ผู้ผลิตบนชิป -> เติมช่องผู้ผลิตให้ (เฉพาะที่ยังว่าง)
    if (data.logo && !$("f_manufacturer").value) { $("f_manufacturer").value = data.logo; updateSummary(); }
    $("icStatus").textContent = (added ? "✅ อ่านได้: " + added : data.chip_text ? "✅" : "✅ อ่านตัวหนังสือไม่ได้ — พิมพ์เอง")
      + (data.logo ? " · 🏭 " + data.logo : "");
  } catch (err) {
    $("icStatus").textContent = "ผิดพลาด: " + err;
  }
  $("icPhoto").value = "";
});

// ---------- ไอซีไม่มีถุงฉลาก: ถ่ายรูปตัวชิป -> หาเบอร์ซ้ำจาก marking + รูปร่าง -> ป๊อปอัพให้ยืนยัน ----------
let idRes = null, idPick = null;

$("chipPhoto").addEventListener("change", async () => {
  const file = $("chipPhoto").files[0];
  if (!file) return;
  const t0 = Date.now();
  $("preview").innerHTML = '<img src="' + URL.createObjectURL(file) + '">';
  $("status").textContent = "⏳ กำลังค้นจากรูป...";
  $("choices").innerHTML = "";
  try {
    idRes = await postPhoto("/api/identify-ic", file, document.querySelector('label[for="chipPhoto"]'));
    const secs = " (" + ((Date.now() - t0) / 1000).toFixed(1) + " วิ)";
    if (idRes.candidates.length) {
      $("status").textContent = "🔍 พบ " + idRes.candidates.length + " รายการ" + secs;
      openIdDialog();
    } else {
      $("status").textContent = "ℹ ไม่พบในคลัง" + secs;
      manualFromChip("none");
    }
  } catch (err) {
    $("status").textContent = "ผิดพลาด: " + err;
  }
  $("chipPhoto").value = "";
});

function openIdDialog() {
  idPick = null;
  $("idImg").src = "/images/t_" + idRes.image;
  $("idLines").textContent = (idRes.lines.length ? idRes.lines.join(" · ") : "(อ่านไม่ได้)") + (idRes.logo ? "  🏭 " + idRes.logo : "");
  $("idQty").value = "";
  const box = $("idCands");
  box.innerHTML = "";
  idRes.candidates.forEach((c, i) => {
    const pic = c.match_image || c.image;
    const where = c.locked_loc ? '<span class="loc">📍 ' + esc(c.locked_loc) + "</span>"
      : '<span class="loc wait">⏳ รอระบุช่อง</span>';
    const b = document.createElement("button");
    b.type = "button";
    b.className = "idc " + c.level;
    b.innerHTML = (pic ? '<img src="/images/t_' + esc(pic) + '" alt="">' : '<span class="noimg">ไม่มีรูป</span>') +
      '<span class="idt"><b>' + esc(c.mpn) + '</b> <span class="pct">' + Math.round(c.score * 100) + "%</span>" +
      (c.level === "weak" ? ' <span class="chip weakchip">ไม่แน่ใจ</span>' : "") +
      "<br><small>" + c.reasons.map(esc).join(" · ") + "</small>" +
      "<br>" + where + " <small>คงเหลือ " + esc(c.qty) + " ชิ้น</small></span>";
    b.onclick = () => pickId(i);
    box.appendChild(b);
  });
  // ตัวแรกที่มั่นใจ (เบอร์/ลวดลายตรง) เลือกไว้ให้ก่อน ; ไม่แน่ใจ = ผู้ใช้ต้องแตะเลือกเอง
  if (idRes.candidates[0].level === "strong") pickId(0);
  else updateIdOk();
  $("idDlg").showModal();
  if (navigator.vibrate) navigator.vibrate([60, 60, 60]);
}

function pickId(i) {
  idPick = idRes.candidates[i];
  [...$("idCands").children].forEach((el, k) => el.classList.toggle("on", k === i));
  $("idMpn").value = idPick.mpn;
  $("idMarking").value = idRes.tokens.join(" ");
  $("idKeepBox").hidden = !idPick.can_add_image;
  updateIdOk();
  $("idQty").focus();
}

function updateIdOk() {
  const ok = $("idOk"), q = parseInt($("idQty").value, 10) || 0;
  $("idImages").value = idPick && idPick.can_add_image && $("idKeep").checked ? idRes.image : "";
  if (!idPick) { ok.disabled = true; ok.textContent = "เลือกเบอร์ก่อน"; $("idTotal").textContent = ""; return; }
  const have = Number(idPick.qty) || 0;
  $("idTotal").innerHTML = (idPick.locked_loc ? "📍 <b>" + esc(idPick.locked_loc) + "</b> · " : "⏳ รอระบุช่อง · ") +
    "รวม <b>" + have + " + " + q + " = " + (have + q) + "</b>";
  ok.disabled = q < 1;
  ok.textContent = q < 1 ? "ใส่จำนวนก่อน" : "✔ บันทึก " + idPick.mpn + " +" + q;
}

$("idQty").addEventListener("input", updateIdOk);
$("idKeep").addEventListener("change", updateIdOk);
$("idClose").onclick = () => $("idDlg").close();
$("idNone").onclick = () => { $("idDlg").close(); manualFromChip("rejected"); };

function manualFromChip(why) {
  // ไม่พบในคลัง / ผู้ใช้บอกว่าไม่ใช่ -> เปิดฟอร์มให้กรอกเองเท่าที่ทราบ พร้อมแนบรูปไอซีที่ถ่ายไว้แล้ว
  const item = Object.assign(barcodesEmpty(), { source: "CHIP", candidates: idRes.tokens, marking: idRes.tokens.join(" ") });
  fill(item);
  $("f_source").value = "nolabel";
  images = [{ file: idRes.image, isNew: true }];
  renderImages();
  $("dupBox").innerHTML = '<div class="dup near"><div class="duph">🆕 เบอร์ใหม่ — กรอก MPN</div></div>';
  $("sumNew").textContent = "🆕 เบอร์ใหม่";
  $("editSum").textContent = "✏️ กรอกข้อมูล (MPN จำเป็น)";
}

function barcodesEmpty() {
  return { mpn: "", qty: null, manufacturer: "", supplier: "", supplier_pn: "", lot: "", date_code: "", order_no: "",
    coo: "", description: "", package: "", raw: "", source: "CHIP", candidates: [], existing: false, images: [], similar: [],
    category: "OTH" };
}
