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
const $ = id => document.getElementById(id);
function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function setStatus(msg, err) { const s = $("status"); s.textContent = msg || ""; s.className = err ? "err" : ""; }

async function api(path, opts) {
  opts = opts || {};
  opts.headers = Object.assign({ "X-Session-Token": token }, opts.headers || {});
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch (e) { /* non-JSON */ }
  if (r.status === 401) { await newSession(); throw new Error("Session expired. Please retry."); }
  if (!r.ok) throw new Error((body && typeof body.detail === "string" && body.detail) || "Request failed.");
  return body;
}
async function newSession() { token = (await (await fetch("/api/session", { method: "POST" })).json()).token; }

function showDesc() {
  const d = state && state.databases.find(x => x.id === $("db").value);
  $("dbdesc").textContent = d && d.description ? d.description : "";
}

async function loadSchema() {
  const ul = $("schema"); ul.textContent = "";
  const id = $("db").value; if (!id) return;
  try {
    const s = await api("/api/schema?db_id=" + encodeURIComponent(id));
    for (const t of s.tables) {
      ul.appendChild(el("li", t.name + (t.kind === "view" ? " (view)" : ""), "t"));
      for (const c of t.columns) ul.appendChild(el("li", c.name + " " + (c.type || "") + (c.pk ? " PK" : ""), "c"));
      for (const f of t.foreign_keys) ul.appendChild(el("li", "FK " + f.from.join(",") + " -> " + f.table + "(" + f.to.join(",") + ")", "c"));
    }
    if (s.warnings && s.warnings.length) setStatus(s.warnings.join(" "));
  } catch (e) { setStatus(e.message, true); }
}

async function refresh() {
  try {
    state = await api("/api/state");
    const db = $("db"), prev = db.value; db.textContent = "";
    for (const d of state.databases) { const o = el("option", d.name); o.value = d.id; db.appendChild(o); }
    if (prev && state.databases.some(d => d.id === prev)) db.value = prev;
    const m = $("model"), pm = m.value; m.textContent = "";
    for (const x of state.models) { const o = el("option", x.name + (x.ram_gb_est ? " (~" + x.ram_gb_est + " GB RAM)" : "")); o.value = x.id; m.appendChild(o); }
    m.value = pm || state.default_model;
    $("allowed").textContent = "Allowed: " + state.limits.allowed.join(", ") + ". Max " + state.limits.max_db_mb + " MB.";
    const c = state.changes, n = [];
    if (c.new.length) n.push(c.new.length + " new"); if (c.modified.length) n.push(c.modified.length + " changed");
    if (c.removed.length) n.push(c.removed.length + " removed"); if (c.invalid.length) n.push("unreadable: " + c.invalid.join(", "));
    setStatus(state.databases.length ? (n.length ? "Databases: " + n.join("; ") : "") : "No databases yet. Add a .sqlite file or copy one into the databases folder, then Refresh.");
    await loadSchema();
  } catch (e) { setStatus(e.message, true); }
}

let generated = "";
function renderTable(out, r) {
  out.appendChild(el("div", r.rows.length + " row(s) in " + r.elapsed_ms + " ms" + (r.truncated ? " (truncated to the limit)" : ""), "label"));
  if (!r.rows.length) { out.appendChild(el("div", "The query returned no rows.", "msg")); return; }
  const wrap = el("div", undefined, "tablewrap"), t = el("table"), h = el("tr");
  for (const c of r.columns) h.appendChild(el("th", c)); t.appendChild(h);
  for (const row of r.rows) { const tr = el("tr"); for (const v of row) tr.appendChild(el("td", v === null ? "NULL" : String(v))); t.appendChild(tr); }
  wrap.appendChild(t); out.appendChild(wrap);
}
function showResult(r) {
  const out = $("result"); out.textContent = "";
  $("sqlcard").hidden = !r.sql;
  if (r.sql) { $("sql").value = r.sql; generated = r.sql; $("sqlwarn").hidden = true;
    $("dbname").textContent = r.database || ""; $("sqlstate").textContent = r.executed ? "Ran read-only" : "Not run yet"; }
  if (r.status === "cannot_answer") out.appendChild(el("div", "The database schema cannot answer this question.", "msg gen"));
  else if (r.status === "rejected") out.appendChild(el("div", "Generation failed: " + r.reason, "msg rej"));
  else if (r.status === "execution_failed") out.appendChild(el("div", "The SQL is valid but running it failed: " + r.reason, "msg exec"));
  else if (r.executed) renderTable(out, r);
}

async function ask(path) {
  const q = $("q").value.trim();
  if (!q) { setStatus("Enter a question.", true); return; }
  if (!$("db").value) { setStatus("Choose a database.", true); return; }
  $("gen").disabled = $("prev").disabled = true; setStatus("Working (the first request loads the model and can take a minute) ...");
  try {
    const r = await api(path, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ db_id: $("db").value, question: q, model_id: $("model").value || null }) });
    showResult(r); setStatus("");
  } catch (e) { setStatus(e.message, true); }
  finally { $("gen").disabled = $("prev").disabled = false; }
}

$("refresh").onclick = refresh; $("db").onchange = loadSchema;
$("gen").onclick = () => ask("/api/generate"); $("prev").onclick = () => ask("/api/preview");
$("upload").onchange = async () => {
  const f = $("upload").files[0]; if (!f) return;
  const fd = new FormData(); fd.append("file", f);
  try { const r = await api("/api/upload-db", { method: "POST", body: fd }); setStatus("Saved as " + r.saved_as); await refresh(); }
  catch (e) { setStatus(e.message, true); }
  $("upload").value = "";
};

async function runSql() {
  const sql = $("sql").value.trim(), out = $("result");
  if (!sql) { setStatus("There is no SQL to run.", true); return; }
  if (!$("db").value) { setStatus("Choose a database.", true); return; }
  $("run").disabled = true; setStatus("Running ...");
  try {
    const r = await api("/api/run-sql", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ db_id: $("db").value, sql }) });
    out.textContent = "";
    if (r.status === "ok") { $("sqlstate").textContent = "Ran read-only"; renderTable(out, r); }
    else { $("sqlstate").textContent = "Not run";
      out.appendChild(el("div", (r.status === "rejected" ? "Not allowed: " : "Running failed: ") + r.reason, "msg exec")); }
    setStatus("");
  } catch (e) { setStatus(e.message, true); }
  finally { $("run").disabled = false; }
}
$("run").onclick = runSql;
$("sql").oninput = () => { $("sqlwarn").hidden = !generated || $("sql").value.trim() === generated.trim(); };
$("reset").onclick = () => { $("sql").value = generated; $("sqlwarn").hidden = true; };
$("copy").onclick = async () => {
  const b = $("copy");
  try { await navigator.clipboard.writeText($("sql").value); b.textContent = "Copied"; } catch (e) { b.textContent = "Copy manually"; }
  setTimeout(() => { b.textContent = "Copy"; }, 1500);
};

(async () => { await newSession(); await refresh(); })();