"use strict";
/* ---- UI kit: theme, sidebar, busy timer, drop zone, example chips (identical in all three apps) ---- */
(function () {
  const root = document.documentElement, g = id => document.getElementById(id);
  try { root.dataset.theme = localStorage.getItem("theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"); }
  catch (e) { root.dataset.theme = "light"; }
  const tb = g("themeBtn"), paint = () => { if (tb) tb.textContent = root.dataset.theme === "dark" ? "☀" : "☾"; };
  paint();
  if (tb) tb.onclick = () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("theme", root.dataset.theme); } catch (e) { /* storage unavailable */ }
    paint(); window.dispatchEvent(new Event("themechange"));
  };
  const shell = g("shell"), sb = g("sideBtn");
  if (shell && sb) sb.onclick = () => shell.classList.toggle(innerWidth < 860 ? "open" : "collapsed");

  const busy = g("busy"), bt = g("busyT");
  let active = 0, t0 = 0, delay = null, tick = null, on = false;
  const secs = () => Math.round((Date.now() - t0) / 1000);
  function lightUp() {
    on = true; busy.classList.add("on"); busy.setAttribute("aria-label", "Working");
    const u = () => { bt.textContent = secs() + " s"; }; u(); tick = setInterval(u, 500);
  }
  function dim() {
    clearTimeout(delay); clearInterval(tick);
    if (on) { on = false; busy.classList.remove("on"); bt.textContent = "Last " + secs() + " s";
      busy.setAttribute("aria-label", "Idle, last request took " + secs() + " seconds"); }
  }
  if (busy && bt) {
    const f0 = window.fetch.bind(window);
    window.fetch = function () {
      if (active++ === 0) { t0 = Date.now(); delay = setTimeout(lightUp, 400); }
      return f0.apply(null, arguments).finally(() => { if (--active === 0) dim(); });
    };
  }

  document.querySelectorAll(".drop").forEach(dz => {
    const inp = dz.querySelector("input"), txt = dz.querySelector(".dt"), def = txt ? txt.textContent : "";
    ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("over"); }));
    dz.addEventListener("dragleave", () => dz.classList.remove("over"));
    dz.addEventListener("drop", e => {
      e.preventDefault(); dz.classList.remove("over");
      if (e.dataTransfer.files.length) { inp.files = e.dataTransfer.files; inp.dispatchEvent(new Event("change")); }
    });
    if (dz.dataset.show && txt) {
      inp.addEventListener("change", () => { txt.textContent = inp.files.length
        ? inp.files.length + " file(s) chosen: " + Array.from(inp.files).map(f => f.name).join(", ") : def; });
      ["start", "add"].forEach(id => { const b = g(id); if (b) b.addEventListener("click", () => { txt.textContent = def; }); });
    }
  });
  document.querySelectorAll("[data-fill]").forEach(b => {
    b.onclick = () => { const t = g(b.dataset.to || "q"); if (t) { t.value = b.dataset.fill; t.focus(); } };
  });
})();

let token = "", state = null;
const selected = new Set();
const $ = id => document.getElementById(id);

function setStatus(msg, isErr) { const s = $("status"); s.textContent = msg || ""; s.className = isErr ? "err" : ""; }
function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function fmtSize(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }

async function api(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign({ "X-Session-Token": token }, opts.headers || {});
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch (e) { /* non-JSON */ }
  if (r.status === 401) { await newSession(); throw new Error("Session expired. Your temporary uploads were cleared; please retry."); }
  if (!r.ok) throw new Error((body && (typeof body.detail === "string" ? body.detail : "Invalid request.")) || "Request failed.");
  return body;
}

async function newSession() {
  const r = await fetch("/api/session", { method: "POST" });
  token = (await r.json()).token;
  selected.clear();
}

function allFiles() { return state ? state.session.concat(state.repository) : []; }
function filtered() {
  const q = $("search").value.trim().toLowerCase(), t = $("type").value;
  return allFiles().filter(f => (!t || f.kind === t) && (!q || f.name.toLowerCase().includes(q)));
}

function render() {
  const list = $("files"); list.textContent = "";
  const items = filtered();
  if (!allFiles().length) list.appendChild(el("li", "No files yet. Upload one, or copy files into the repository folder and press Refresh.", "empty"));
  else if (!items.length) list.appendChild(el("li", "No files match your search.", "empty"));
  for (const f of items) {
    const li = el("li"); if (selected.has(f.id)) li.className = "selected";
    const cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = selected.has(f.id);
    cb.setAttribute("aria-label", "Select " + f.name);
    cb.onchange = () => { cb.checked ? selected.add(f.id) : selected.delete(f.id); render(); };
    li.appendChild(cb);
    const nm = el("span", f.name, "name"); nm.title = f.name + " (" + f.kind.toUpperCase() + ", " + fmtSize(f.size) + ")"; li.appendChild(nm);
    li.appendChild(el("span", f.source === "session" ? "temporary" : "saved", "tag " + (f.source === "session" ? "tmp" : "saved")));
    if (f.source === "session") {
      const b = el("button", "Save"); b.title = "Copy into the permanent repository";
      b.onclick = () => saveFile(f); li.appendChild(b);
    } else {
      const b = el("button", "Delete", "danger"); b.title = "Permanently delete this repository file";
      b.onclick = () => deleteFile(f); li.appendChild(b);
    }
    list.appendChild(li);
  }
  $("count").textContent = allFiles().length + " file(s), " + items.length + " shown, " + selected.size + " selected";
  $("selInfo").textContent = selected.size ? selected.size + " file(s) selected" : "Select at least one file";
}

async function refresh() {
  try {
    state = await api("/api/state");
    const ids = new Set(allFiles().map(f => f.id));
    for (const id of Array.from(selected)) if (!ids.has(id)) selected.delete(id);
    const sel = $("model"); const prev = sel.value; sel.textContent = "";
    for (const m of state.models) { const o = el("option", m.name + (m.ram_gb_est ? " (~" + m.ram_gb_est + " GB RAM)" : "")); o.value = m.id; sel.appendChild(o); }
    sel.value = prev || state.default_model;
    $("allowed").textContent = "Allowed: " + state.limits.allowed.join(", ") + ". Max " + state.limits.max_upload_mb + " MB each.";
    const c = state.changes, notes = [];
    if (c.new.length) notes.push(c.new.length + " new");
    if (c.modified.length) notes.push(c.modified.length + " changed");
    if (c.removed.length) notes.push(c.removed.length + " removed");
    if (c.invalid.length) notes.push("unreadable: " + c.invalid.join(", "));
    if (c.busy.length) notes.push("busy: " + c.busy.join(", "));
    setStatus(notes.length ? "Repository: " + notes.join("; ") : "");
    render();
  } catch (e) { setStatus(e.message, true); }
}

async function upload() {
  const files = Array.from($("upload").files); if (!files.length) return;
  for (const f of files) {
    setStatus("Uploading " + f.name + " ...");
    const fd = new FormData(); fd.append("file", f);
    try { const r = await api("/api/upload", { method: "POST", body: fd }); if (r.auto_selected) selected.add(r.file.id); }
    catch (e) { setStatus(f.name + ": " + e.message, true); await refresh(); $("upload").value = ""; return; }
  }
  $("upload").value = ""; setStatus("Upload complete. New files are selected."); await refresh();
}

async function saveFile(f) {
  try {
    const r = await api("/api/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ file_id: f.id }) });
    setStatus("Saved to repository as " + r.saved_as + ". It is not selected for questions until you tick it."); await refresh();
  } catch (e) { setStatus(e.message, true); }
}

async function deleteFile(f) {
  if (!confirm("Permanently delete '" + f.name + "' from the repository? This cannot be undone.")) return;
  try {
    await api("/api/delete", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ file_id: f.id, confirm: true }) });
    selected.delete(f.id); setStatus("Deleted " + f.name); await refresh();
  } catch (e) { setStatus(e.message, true); }
}

async function ask() {
  const q = $("q").value.trim();
  if (!q) { setStatus("Enter a question.", true); return; }
  if (!selected.size) { setStatus("Select at least one file.", true); return; }
  $("ask").disabled = true; setStatus("Thinking (the first question loads the model and can take a minute) ...");
  const out = $("result"); out.textContent = "";
  try {
    const r = await api("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, file_ids: Array.from(selected), model_id: $("model").value || null }) });
    const label = { calculated: "Calculated from the data", sourced: "Answer from the selected files", insufficient: "Not found in the selected files", invalid: "Answer rejected" }[r.status] || r.status;
    out.appendChild(el("div", label, "label"));
    out.appendChild(el("div", r.answer || r.detail || "", "answer " + r.status));
    if (r.detail && r.answer) out.appendChild(el("div", r.detail, "label"));
    if (r.citations.length) { out.appendChild(el("div", "Sources", "label")); const ul = el("ul", undefined, "cites"); for (const c of r.citations) ul.appendChild(el("li", c)); out.appendChild(ul); }
    out.appendChild(el("div", "Files used: " + r.used_files.join(", "), "label"));
    setStatus("");
  } catch (e) { setStatus(e.message, true); }
  finally { $("ask").disabled = false; }
}

$("search").oninput = render; $("type").onchange = render;
$("selAll").onclick = () => { allFiles().forEach(f => selected.add(f.id)); render(); };
$("selFiltered").onclick = () => { filtered().forEach(f => selected.add(f.id)); render(); };
$("clearSel").onclick = () => { selected.clear(); render(); };
$("refresh").onclick = refresh;
$("upload").onchange = upload;
$("ask").onclick = ask;
$("clearSession").onclick = async () => {
  try { const r = await api("/api/clear", { method: "POST" }); token = r.token; selected.clear(); setStatus("Session uploads cleared. Saved files were not touched."); await refresh(); }
  catch (e) { setStatus(e.message, true); }
};
(async () => { await newSession(); await refresh(); })();