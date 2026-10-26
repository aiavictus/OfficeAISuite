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

let token = "", batch = "", files = [], limits = null, current = null;
const $ = id => document.getElementById(id);
function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function setStatus(msg, err) { const s = $("status"); s.textContent = msg || ""; s.className = err ? "err" : ""; }
function fmtSize(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
function fmtNum(v) { return typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : (v === null ? "(empty)" : String(v)); }

async function api(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign({ "X-Session-Token": token }, opts.headers || {});
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch (e) { /* non-JSON */ }
  if (r.status === 401) { await newSession(); throw new Error("Session expired. Please upload again."); }
  if (!r.ok) throw new Error((body && typeof body.detail === "string" && body.detail) || "Request failed.");
  return body;
}
const post = (p, o) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(o) });
async function newSession() { token = (await (await fetch("/api/session", { method: "POST" })).json()).token; batch = ""; files = []; }

function sources() { const out = []; for (const f of files) for (const t of f.tables) out.push({ file: f, table: t, key: f.id + "|" + t.sheet,
  label: f.name + (f.tables.length > 1 ? " / " + t.sheet : "") }); return out; }
function selected() { return sources().find(s => s.key === $("src").value); }

async function uploadFiles(newBatch) {
  const list = Array.from($("upload").files);
  if (!list.length) { setStatus("Choose one or more files first.", true); return; }
  try {
    if (newBatch || !batch) { batch = (await api("/api/batch", { method: "POST" })).batch; files = []; $("rsec").hidden = true; }
    let ok = 0;
    for (const f of list) {
      setStatus("Uploading " + f.name + " ...");
      const fd = new FormData(); fd.append("file", f);
      try { await api("/api/upload?batch=" + batch, { method: "POST", body: fd }); ok++; }
      catch (e) { setStatus(f.name + ": " + e.message, true); }
    }
    $("upload").value = ""; await loadFiles();
    if (ok) setStatus(ok + " file(s) ready.");
  } catch (e) { setStatus(e.message, true); }
}

async function loadFiles() {
  files = batch ? (await api("/api/files?batch=" + batch)).files : [];
  renderFiles(); fillSources();
}

function renderFiles() {
  const box = $("files"); box.textContent = "";
  if (!files.length) { box.appendChild(el("div", "No data yet. Upload an Excel or CSV file.", "hint")); return; }
  for (const f of files) {
    const d = el("div", undefined, "file"); d.appendChild(el("b", f.name)); d.appendChild(el("span", "  " + f.kind.toUpperCase() + ", " + fmtSize(f.size), "q"));
    for (const t of f.tables) {
      const det = document.createElement("details"); const sm = document.createElement("summary");
      sm.textContent = (f.tables.length > 1 || f.kind === "xlsx" ? "Sheet " + t.sheet + ": " : "") + t.rows + " rows, " + t.columns.length + " columns";
      det.appendChild(sm);
      for (const c of t.columns) det.appendChild(el("div", c.name + " (" + c.kind + ")" + (c.nulls ? ", " + c.null_pct + "% empty" : ""), "colrow"));
      const q = t.quality, issues = [];
      if (q.duplicate_rows) issues.push(q.duplicate_rows + " duplicate row(s)");
      if (q.empty_columns.length) issues.push("empty: " + q.empty_columns.join(", "));
      if (q.constant_columns.length) issues.push("one value only: " + q.constant_columns.join(", "));
      if (q.high_null_columns.length) issues.push("mostly empty: " + q.high_null_columns.join(", "));
      if (q.mixed_type_columns.length) issues.push("mixed text and numbers: " + q.mixed_type_columns.join(", "));
      det.appendChild(el("div", issues.length ? "Data quality: " + issues.join("; ") : "Data quality: no problems found", issues.length ? "note" : "colrow"));
      d.appendChild(det);
    }
    if (f.skipped_sheets.length) d.appendChild(el("div", "Skipped empty sheets: " + f.skipped_sheets.join(", "), "colrow"));
    box.appendChild(d);
  }
}

function fillSources() {
  const s = $("src"), prev = s.value; s.textContent = "";
  for (const x of sources()) { const o = el("option", x.label); o.value = x.key; s.appendChild(o); }
  if (prev && sources().some(x => x.key === prev)) s.value = prev;
  const c = $("cmplist"); c.textContent = "";
  for (const x of sources()) { const l = document.createElement("label"); l.className = "chip"; const cb = document.createElement("input");
    cb.type = "checkbox"; cb.value = x.key; cb.className = "cmpcb"; cb.onchange = onCompareChange; l.appendChild(cb); l.appendChild(document.createTextNode(" " + x.label)); c.appendChild(l); }
  onSourceChange();
}

function fillBuilder(t) {
  const gb = $("gb"), me = $("me"), fc = $("fc");
  gb.textContent = ""; me.textContent = ""; fc.textContent = "";
  const none = el("option", "(no grouping)"); none.value = ""; gb.appendChild(none);
  const nf = el("option", "(no filter)"); nf.value = ""; fc.appendChild(nf);
  const rows = el("option", "number of rows"); rows.value = "*"; me.appendChild(rows);
  for (const c of t.columns) {
    if (c.kind === "categorical" || c.kind === "datetime") { const o = el("option", c.name); o.value = c.name; o.dataset.kind = c.kind; gb.appendChild(o); }
    if (c.kind === "numeric") { const o = el("option", c.name); o.value = c.name; me.appendChild(o); }
    if (c.kind === "categorical") { const o = el("option", c.name); o.value = c.name; fc.appendChild(o); }
  }
  onFilterColumn();
}
function onFilterColumn() {
  const fv = $("fv"); fv.textContent = ""; const s = selected(); if (!s || !$("fc").value) { fv.hidden = true; return; }
  fv.hidden = false; const col = s.table.columns.find(c => c.name === $("fc").value);
  for (const v of (col && col.top) || []) { const o = el("option", v.value); o.value = v.value; fv.appendChild(o); }
}

function onSourceChange() {
  const s = selected(); $("sugg").textContent = "";
  if (!s || $("cmp").checked) { if (s) fillBuilder(s.table); return; }
  fillBuilder(s.table);
  for (const g of s.table.suggestions) {
    const b = el("button", g.title, "chip"); b.onclick = () => runPlan(g.plan); $("sugg").appendChild(b);
  }
}

function builtPlan() {
  const plan = {}, gcol = $("gb").value, m = $("me").value, fn = $("ag").value;
  if (gcol) { const k = $("gb").selectedOptions[0].dataset.kind; plan.group_by = [k === "datetime" ? { column: gcol, bucket: $("bk").value } : gcol]; }
  if (m === "*") plan.aggregations = [{ column: "*", agg: "count" }];
  else if (m) plan.aggregations = [{ column: m, agg: fn }];
  if ($("fc").value) plan.filters = [{ column: $("fc").value, op: "eq", value: $("fv").value }];
  if ($("tn").value && gcol) plan.top_n = parseInt($("tn").value, 10);
  plan.chart = $("ct").value;
  return plan;
}

async function runPlan(plan) {
  setStatus("Working ...");
  try {
    let r;
    if ($("cmp").checked) {
      const t = checkedTargets(); if (t.length < 2) { setStatus("Tick at least two sheets to compare.", true); return; }
      r = await post("/api/compare", { batch, targets: t, plan });
    } else {
      const s = selected(); if (!s) { setStatus("Upload data first.", true); return; }
      r = await post("/api/run", { batch, file_id: s.file.id, sheet: s.table.sheet, plan });
    }
    show(r); setStatus("");
  } catch (e) { setStatus(e.message, true); }
}

function checkedTargets() {
  return Array.from(document.querySelectorAll(".cmpcb:checked")).map(c => { const [file_id, sheet] = c.value.split("|"); return { file_id, sheet }; });
}
async function onCompareChange() {
  const on = $("cmp").checked; $("cmpbox").hidden = !on;
  if (!on) { onSourceChange(); return; }
  const t = checkedTargets(), box = $("sugg"); box.textContent = "";
  if (t.length > limits.max_compare) { setStatus("Compare at most " + limits.max_compare + " sheets.", true); return; }
  if (t.length < 2) { box.appendChild(el("div", "Tick two or more sheets.", "hint")); return; }
  try {
    const r = await post("/api/compare-suggestions", { batch, targets: t, plan: {} });
    if (!r.suggestions.length) box.appendChild(el("div", "These sheets share no comparable columns, so there is nothing to compare.", "note"));
    for (const g of r.suggestions) { const b = el("button", g.title, "chip"); b.onclick = () => runPlan(g.plan); box.appendChild(b); }
    setStatus("");
  } catch (e) { setStatus(e.message, true); }
}

function show(r) {
  window.__last = r;
  $("rsec").hidden = false; $("desc").textContent = r.description;
  const nb = $("notes"); nb.textContent = ""; for (const n of r.notes || []) nb.appendChild(el("div", n, "note"));
  const mb = $("metrics"); mb.textContent = "";
  for (const m of r.metrics || []) { const d = el("div", undefined, "metric"); d.appendChild(el("div", fmtNum(m.value) + (m.of !== undefined ? " of " + fmtNum(m.of) : ""), "v")); d.appendChild(el("div", m.label, "l")); mb.appendChild(d); }
  const t = $("tbl"); t.textContent = ""; const h = el("tr"); for (const c of r.columns) h.appendChild(el("th", c)); t.appendChild(h);
  for (const row of r.rows) { const tr = el("tr"); for (const v of row) tr.appendChild(el("td", fmtNum(v))); t.appendChild(tr); }
  if (!r.rows.length) { const tr = el("tr"); tr.appendChild(el("td", "No rows.")); t.appendChild(tr); }
  draw(r);
}

function draw(r) {
  const div = $("chart"); Plotly.purge(div); const c = r.chart;
  div.style.display = (c.type === "table" || c.type === "metric") ? "none" : "block"; $("png").disabled = div.style.display === "none";
  if (div.style.display === "none") return;
  const idx = n => r.columns.indexOf(n), xi = idx(c.x), cfg = { displaylogo: false, responsive: true };
  const css = getComputedStyle(document.documentElement), ink = css.getPropertyValue("--text").trim(), grid = css.getPropertyValue("--border").trim();
  const lay = { margin: { t: 20, l: 60, r: 20, b: 80 }, legend: { orientation: "h" }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: ink }, colorway: ["#5E9A6E", "#D9A441", "#5B8DB8", "#B86B5B", "#8C7AB8", "#9BB86B"],
    xaxis: { title: c.x || "", gridcolor: grid }, yaxis: { title: (c.y || []).join(", "), gridcolor: grid } };
  let traces = [];
  if (c.type === "pie") traces = [{ type: "pie", labels: r.rows.map(x => x[xi]), values: r.rows.map(x => x[idx(c.y[0])]) }];
  else if (c.type === "scatter") traces = [{ type: "scatter", mode: "markers", x: r.rows.map(x => x[0]), y: r.rows.map(x => x[1]) }];
  else if (c.series) {
    const si = idx(c.series), names = Array.from(new Set(r.rows.map(x => x[si]))), yi = idx(c.y[0]);
    traces = names.map(n => { const rs = r.rows.filter(x => x[si] === n); return { name: String(n), type: c.type === "line" ? "scatter" : "bar", mode: "lines+markers", x: rs.map(x => x[xi]), y: rs.map(x => x[yi]) }; });
    if (c.type === "bar") lay.barmode = "group";
  } else traces = c.y.map(n => ({ name: n, type: c.type === "line" ? "scatter" : "bar", mode: "lines+markers", x: r.rows.map(x => x[xi]), y: r.rows.map(x => x[idx(n)]) }));
  Plotly.newPlot(div, traces, lay, cfg);
}

$("start").onclick = () => uploadFiles(true);
$("add").onclick = () => uploadFiles(false);
$("clear").onclick = async () => { try { await api("/api/clear", { method: "POST" }); batch = ""; files = []; $("rsec").hidden = true; renderFiles(); fillSources(); setStatus("All uploaded data was removed."); } catch (e) { setStatus(e.message, true); } };
$("src").onchange = onSourceChange; $("fc").onchange = onFilterColumn; $("cmp").onchange = onCompareChange;
$("go").onclick = () => runPlan(builtPlan());
$("png").onclick = () => Plotly.downloadImage($("chart"), { format: "png", filename: "chart" });

async function askText() {
  const s = selected(), q = $("req").value.trim();
  if (!s) { setStatus("Upload data first.", true); return; }
  if (!q) { setStatus("Type what you want to see.", true); return; }
  if ($("cmp").checked) { setStatus("Free-text requests work on one sheet. Untick Compare.", true); return; }
  $("askgo").disabled = true; setStatus("Working (the first request loads the model and can take a minute) ...");
  try {
    const r = await post("/api/ask", { batch, file_id: s.file.id, sheet: s.table.sheet, request: q, model_id: $("model").value || null });
    show(r); setStatus("Plan used: " + JSON.stringify(r.plan));
  } catch (e) { setStatus(e.message, true); }
  finally { $("askgo").disabled = false; }
}
$("askgo").onclick = askText;
async function loadModels() {
  const m = await api("/api/models"), sel = $("model"); sel.textContent = "";
  for (const x of m.models) { const o = el("option", x.name); o.value = x.id; sel.appendChild(o); }
  sel.value = m.default;
}

window.addEventListener("themechange", () => { if (window.__last && !$("rsec").hidden) draw(window.__last); });

(async () => {
  await newSession(); limits = await api("/api/limits"); $("maxc").textContent = limits.max_compare;
  $("limits").textContent = "Allowed: " + limits.allowed.join(", ") + ". Up to " + limits.max_files + " files, " + limits.max_upload_mb + " MB each, " + limits.max_rows.toLocaleString() + " rows per sheet.";
  renderFiles(); await loadModels();
})();