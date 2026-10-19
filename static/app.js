const $ = (id) => document.getElementById(id);

async function api(path, opts) {
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// ── Tabs ─────────────────────────────────────────────────────────

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $(`tab-${btn.dataset.tab}`).classList.add("active");
    if (btn.dataset.tab === "location") initObserverMap();
    if (btn.dataset.tab === "track") initTrackMap();
  });
});

// ── Line chart ───────────────────────────────────────────────────

// Single-series canvas line chart: 2px line, hairline grid, crosshair +
// tooltip on hover. setData(points, [xMin, xMax]) with points sorted by x,
// each {x, y, ...}; the extra fields are handed to opts.tooltip(point).
function niceTicks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const raw = (max - min) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + step / 2; v += step) ticks.push(Number(v.toFixed(10)));
  return { lo, hi, ticks };
}

function createLineChart(container, opts) {
  const canvas = document.createElement("canvas");
  const tip = document.createElement("div");
  tip.className = "chart-tooltip";
  tip.hidden = true;
  const empty = document.createElement("div");
  empty.className = "chart-empty";
  empty.textContent = opts.emptyText;
  container.append(canvas, tip, empty);

  const pad = { l: 52, r: 14, t: 18, b: 30 };
  let points = [];
  let domain = [0, 1];
  let hoverIdx = null;

  function draw() {
    const w = container.clientWidth;
    const h = container.clientHeight;
    if (!w || !h) return; // hidden tab - redrawn when it's shown (ResizeObserver)
    const css = getComputedStyle(document.documentElement);
    const ink = css.getPropertyValue("--text").trim();
    const accent = css.getPropertyValue("--accent").trim();
    const surface = css.getPropertyValue("--panel").trim();
    const grid = "#45475a";
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    empty.hidden = points.length > 0;
    if (!points.length) { tip.hidden = true; return; }

    let [x0, x1] = domain;
    if (x0 === x1) { x0 -= 1; x1 += 1; }
    const ys = points.map((p) => p.y);
    const { lo, hi, ticks } = niceTicks(Math.min(...ys), Math.max(...ys));
    const plotW = w - pad.l - pad.r;
    const plotH = h - pad.t - pad.b;
    const sx = (x) => pad.l + ((x - x0) / (x1 - x0)) * plotW;
    const sy = (y) => pad.t + (1 - (y - lo) / (hi - lo)) * plotH;

    // Recessive hairline grid + y labels in muted ink.
    ctx.font = "11px system-ui, sans-serif";
    ctx.lineWidth = 1;
    ctx.strokeStyle = grid;
    ctx.fillStyle = ink;
    ctx.globalAlpha = 0.7;
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    ticks.forEach((t) => {
      const y = Math.round(sy(t)) + 0.5;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
      ctx.fillText(t.toLocaleString(), pad.l - 8, y);
    });
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    opts.xTicks(x0, x1).forEach((t) => {
      ctx.fillText(opts.xTickLabel(t, x0, x1), sx(t), h - pad.b + 10);
    });
    ctx.globalAlpha = 1;

    // The series.
    ctx.strokeStyle = accent;
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    points.forEach((p, i) => (i ? ctx.lineTo(sx(p.x), sy(p.y)) : ctx.moveTo(sx(p.x), sy(p.y))));
    ctx.stroke();
    if (points.length === 1) dot(ctx, sx(points[0].x), sy(points[0].y), accent, surface);

    // Selective direct label: the peak only.
    if (opts.markPeak) {
      const peak = points.reduce((a, b) => (b.y > a.y ? b : a));
      const px = sx(peak.x);
      const py = sy(peak.y);
      dot(ctx, px, py, accent, surface);
      const label = `peak ${peak.y.toLocaleString()}`;
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = ink;
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      ctx.fillText(label, Math.min(Math.max(px - tw / 2, pad.l), w - pad.r - tw), py - 7);
    }

    // Crosshair + tooltip for the hovered point.
    if (hoverIdx !== null && points[hoverIdx]) {
      const p = points[hoverIdx];
      const px = Math.round(sx(p.x)) + 0.5;
      ctx.strokeStyle = ink;
      ctx.globalAlpha = 0.4;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px, pad.t); ctx.lineTo(px, h - pad.b); ctx.stroke();
      ctx.globalAlpha = 1;
      dot(ctx, sx(p.x), sy(p.y), accent, surface);

      const [value, ...rest] = opts.tooltip(p);
      const strong = document.createElement("strong");
      strong.textContent = value;
      tip.replaceChildren(strong, ...rest.map((line) => {
        const d = document.createElement("div");
        d.textContent = line;
        return d;
      }));
      tip.hidden = false;
      const left = px + 12 + tip.offsetWidth > w ? px - 12 - tip.offsetWidth : px + 12;
      tip.style.left = `${Math.max(0, left)}px`;
      tip.style.top = `${pad.t}px`;
    } else {
      tip.hidden = true;
    }

    chart.toX = (offsetX) => x0 + ((offsetX - pad.l) / plotW) * (x1 - x0);
  }

  // >= 8px marker with a 2px surface ring.
  function dot(ctx, x, y, fill, ring) {
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = ring;
    ctx.stroke();
  }

  // The crosshair snaps to the nearest point by x, so the whole plot is
  // the hit target, not the 2px line.
  canvas.addEventListener("pointermove", (e) => {
    if (!points.length || !chart.toX) return;
    const x = chart.toX(e.offsetX);
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(p.x - x) < Math.abs(points[best].x - x)) best = i; });
    hoverIdx = best;
    draw();
  });
  canvas.addEventListener("pointerleave", () => { hoverIdx = null; draw(); });
  new ResizeObserver(draw).observe(container);

  const chart = {
    setData(newPoints, newDomain) {
      points = newPoints;
      domain = newDomain;
      if (hoverIdx !== null && hoverIdx >= points.length) hoverIdx = null;
      draw();
    },
  };
  return chart;
}

// ── Manual ───────────────────────────────────────────────────────

let signalPeak = 0;
let signalPolling = false;
let manualMoving = false;

// Live signal chart: the last LIVE_WINDOW seconds of readings, from the
// idle poll below or, during a sweep, from the sweep's own readings.
const LIVE_WINDOW = 120;
let liveSamples = [];
const liveChart = createLineChart($("live-chart"), {
  emptyText: "Waiting for readings…",
  xTicks: (x0, x1) => [0, 30, 60, 90, 120].map((s) => x1 - s).filter((t) => t >= x0),
  xTickLabel: (t, x0, x1) => (Math.round(x1 - t) === 0 ? "now" : `-${Math.round(x1 - t)}s`),
  tooltip: (p) => [p.y.toLocaleString(), `${Math.max(0, Math.round(Date.now() / 1000 - p.x))} s ago`],
});

function redrawLiveChart() {
  const now = Date.now() / 1000;
  liveSamples = liveSamples.filter((s) => s.x >= now - LIVE_WINDOW);
  liveChart.setData(liveSamples, [now - LIVE_WINDOW, now]);
}

function showSignal(value, t) {
  signalPeak = Math.max(signalPeak, value);
  $("signal-value").textContent = value;
  $("signal-peak").textContent = signalPeak;
  // Raw units are undocumented, so scale the bar against the peak seen.
  $("signal-bar").style.width = `${signalPeak ? Math.min(100, (value / signalPeak) * 100) : 0}%`;
  liveSamples.push({ x: t, y: value });
}

$("manual-go-btn").addEventListener("click", async () => {
  const az = parseFloat($("manual-az").value);
  const el = parseFloat($("manual-el").value);
  const status = $("manual-status");
  if (isNaN(az) || isNaN(el) || az < 0 || az > 360 || el < 5 || el > 70) {
    status.textContent = "Az must be 0–360 and El 5–70.";
    return;
  }
  const btn = $("manual-go-btn");
  manualMoving = true;
  btn.disabled = true;
  status.textContent = "Moving…";
  try {
    const post = (axis, degrees) => api("/api/move", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ axis, degrees }),
    });
    await post("az", az);
    await post("el", el);
    status.textContent = `Arrived at az ${az}°, el ${el}°.`;
  } catch (e) {
    status.textContent = "Move failed: " + e.message;
  }
  manualMoving = false;
  btn.disabled = false;
  refreshStatus();
});

$("signal-reset-btn").addEventListener("click", () => {
  signalPeak = 0;
  $("signal-peak").textContent = "—";
});

async function pollSignal() {
  if (signalPolling || !$("tab-manual").classList.contains("active")
      || !$("conn-status").classList.contains("connected")) return;
  signalPolling = true;
  try {
    // A sweep owns the dish and feeds the meter itself - see pollSweep.
    if (!sweepPolling) {
      const r = await api("/api/signal");
      if (r.signal !== null) {
        showSignal(r.signal, Date.now() / 1000);
      } else if (r.busy) {
        $("signal-value").textContent = "busy…";
      }
    }
  } catch (e) {
    /* transient - try again next tick */
  }
  redrawLiveChart();
  signalPolling = false;
}
setInterval(pollSignal, 1000);

// ── Manual: point-to-point sweep ─────────────────────────────────

let sweepPolling = false;   // true from a sweep's start until its last samples are in
let sweepSamples = [];
let sweepLength = 0;        // degrees along the line, for the chart's x domain

const sweepChart = createLineChart($("sweep-chart"), {
  emptyText: "Run a sweep to see signal along its path.",
  xTicks: (x0, x1) => niceTicks(x0, x1).ticks.filter((t) => t >= x0 && t <= x1),
  xTickLabel: (t) => `${t}°`,
  markPeak: true,
  tooltip: (p) => [p.y.toLocaleString(), `az ${p.az}°, el ${p.el}°`, `${p.x}° along the line`],
});

function sweepInputs() {
  return ["az-start", "el-start", "az-end", "el-end", "step"].map((k) => parseFloat($(`sweep-${k}`).value));
}

function updateSweepEstimate() {
  const [az0, el0, az1, el1, step] = sweepInputs();
  const out = $("sweep-estimate");
  if ([az0, el0, az1, el1, step].some(isNaN) || step <= 0) { out.textContent = ""; return; }
  const n = Math.max(1, Math.ceil(Math.hypot(az1 - az0, el1 - el0) / step)) + 1;
  // Rough per-point cost: a signal reading, plus an az and/or el hop
  // (elacc's slow final approach makes el the expensive one).
  const perPoint = 1 + (az1 !== az0 ? 2 : 0) + (el1 !== el0 ? 5 : 0);
  out.textContent = `${n} points, roughly ${formatAgo(n * perPoint)} (plus the move to the start).`;
}
["az-start", "el-start", "az-end", "el-end", "step"].forEach((k) =>
  $(`sweep-${k}`).addEventListener("input", updateSweepEstimate));
updateSweepEstimate();

$("sweep-start-btn").addEventListener("click", async () => {
  const [az0, el0, az1, el1, step] = sweepInputs();
  const status = $("sweep-status");
  if ([az0, el0, az1, el1, step].some(isNaN)) {
    status.textContent = "Fill in every field.";
    return;
  }
  try {
    await api("/api/sweep/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ az_start: az0, el_start: el0, az_end: az1, el_end: el1, step }),
    });
  } catch (e) {
    status.textContent = "Couldn't start: " + e.message;
    return;
  }
  sweepSamples = [];
  sweepLength = Math.hypot(az1 - az0, el1 - el0);
  sweepChart.setData([], [0, sweepLength]);
  $("sweep-download").hidden = true;
  status.textContent = "Moving to the start…";
  sweepPolling = true;
  refreshStatus();
});

$("sweep-stop-btn").addEventListener("click", () => api("/api/sweep/stop", { method: "POST" }));

async function pollSweep() {
  if (!sweepPolling) return;
  let r;
  try {
    r = await api(`/api/sweep?since=${sweepSamples.length}`);
  } catch (e) {
    return;
  }
  r.samples.forEach((s) => {
    sweepSamples.push({ x: s.dist, y: s.signal, az: s.az, el: s.el });
    showSignal(s.signal, s.t);
  });
  if (sweepLength === 0 && r.samples.length) {
    // Page was reloaded mid-sweep - recover the line length from the data.
    sweepLength = Math.max(...sweepSamples.map((p) => p.x));
  }
  sweepChart.setData(sweepSamples, [0, sweepLength]);

  const status = $("sweep-status");
  if (r.running) {
    if (r.done) status.textContent = `Point ${r.done} of ${r.total}…`;
    return;
  }
  sweepPolling = false;
  if (r.error) status.textContent = "Sweep failed: " + r.error;
  else if (r.done < r.total) status.textContent = `Stopped after ${r.done} of ${r.total} points.`;
  else status.textContent = `Done - ${r.total} points.`;
  if (r.last_file) {
    const link = $("sweep-download");
    const url = new URL("/api/sweep/download", window.location.origin);
    url.searchParams.set("file", r.last_file);
    link.href = url.toString();
    link.download = r.last_file;
    link.hidden = false;
  }
}
setInterval(pollSweep, 1000);

// ── Connection ───────────────────────────────────────────────────

$("connect-btn").addEventListener("click", async () => {
  const pill = $("conn-status");
  if (pill.classList.contains("connected")) {
    await api("/api/disconnect", { method: "POST" });
  } else {
    try {
      await api("/api/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ port: $("port").value }),
      });
    } catch (e) {
      alert("Connect failed: " + e.message);
    }
  }
  refreshStatus();
});

$("reboot-btn").addEventListener("click", async () => {
  if (!confirm("Reboot the server? Any running scan or tracking will stop.")) return;
  $("reboot-btn").disabled = true;
  $("reboot-btn").textContent = "Rebooting…";
  try {
    await api("/api/system/reboot", { method: "POST" });
  } catch (e) {
    /* the process is restarting out from under this request - expected */
  }
  waitForServerBackUp();
});

async function waitForServerBackUp() {
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    try {
      await api("/api/status");
      location.reload();
      return;
    } catch (e) {
      /* still restarting */
    }
  }
  $("reboot-btn").textContent = "Reboot Server";
  $("reboot-btn").disabled = false;
  alert("Server did not come back up within 60 seconds.");
}

function setConnected(connected) {
  const pill = $("conn-status");
  pill.textContent = connected ? "Connected" : "Disconnected";
  pill.classList.toggle("connected", connected);
  pill.classList.toggle("disconnected", !connected);
  $("connect-btn").textContent = connected ? "Disconnect" : "Connect";
}

// ── Scan ─────────────────────────────────────────────────────────

function scanParams() {
  return {
    az_start: $("az_start").value,
    az_end: $("az_end").value,
    el_start: $("el_start").value,
    el_end: $("el_end").value,
    resolution: $("resolution").value,
  };
}

async function updateEstimate() {
  try {
    const { minutes } = await api("/api/scan/estimate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(scanParams()),
    });
    $("estimate").textContent = `Estimated time: ${minutes} minutes`;
  } catch (e) {
    $("estimate").textContent = "";
  }
}

["az_start", "az_end", "el_start", "el_end", "resolution"].forEach((id) =>
  $(id).addEventListener("change", updateEstimate)
);

$("scan-start-btn").addEventListener("click", async () => {
  try {
    await api("/api/scan/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(scanParams()),
    });
  } catch (e) {
    alert("Could not start scan: " + e.message);
  }
});

$("scan-stop-btn").addEventListener("click", () => api("/api/scan/stop", { method: "POST" }));

async function refreshScanLog() {
  try {
    const { log } = await api("/api/scan/log");
    const el = $("scan-log");
    el.textContent = log.join("\n");
    el.scrollTop = el.scrollHeight;
  } catch (e) {
    /* ignore */
  }
}

// ── Saved scans ──────────────────────────────────────────────────

// selectNewest: pick the most recent file (the list is newest-first)
// instead of keeping the current selection - used right after a scan
// finishes, so its result is ready to render.
async function refreshFiles(selectNewest = false) {
  const { files } = await api("/api/files");
  const select = $("file-select");
  const current = select.value;
  select.replaceChildren();
  files.forEach((f) => {
    const opt = document.createElement("option");
    opt.value = f;
    opt.textContent = f;
    select.appendChild(opt);
  });
  if (!selectNewest && files.includes(current)) select.value = current;
}

$("refresh-files-btn").addEventListener("click", () => refreshFiles());

$("render-btn").addEventListener("click", () => {
  const file = $("file-select").value;
  if (!file) return alert("Pick a scan file first.");
  const factor = $("interp-factor").value;
  const img = $("rendered-image");
  // Built via the URL API rather than raw template-string interpolation -
  // URLSearchParams handles encoding itself, so there's no way for a
  // value read from the DOM to end up somewhere other than a query
  // parameter value, however it's selected/loaded from /api/files.
  const url = new URL("/api/image.png", window.location.origin);
  url.searchParams.set("file", file);
  url.searchParams.set("factor", factor);
  url.searchParams.set("t", Date.now());
  img.src = url.toString();
  img.hidden = false;
});

// ── Track ────────────────────────────────────────────────────────

// Fills the source dropdown, labelling each entry with the age of its saved
// copy. On first load (initial = true) it also selects the source of the
// satellites the server already has loaded, and shows them straight away
// rather than waiting for the first 30s auto-resort.
async function loadTleSources(initial = false) {
  const { sources, current, cached_at, space_track } = await api("/api/track/sources");
  showSpaceTrackNote(space_track);
  const select = $("tle-source-select");
  const selected = initial ? current : select.value;
  select.replaceChildren();
  const now = Date.now() / 1000;
  sources.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = cached_at[name]
      ? `${name} · saved ${formatAgo(now - cached_at[name])} ago`
      : `${name} · not saved yet`;
    select.appendChild(opt);
  });
  if (selected && sources.includes(selected)) select.value = selected;
  if (initial && current) {
    try {
      const { satellites, ranked } = await api("/api/track/resort", { method: "POST" });
      renderSatelliteList(satellites, ranked);
    } catch (e) {
      /* nothing loaded server-side after all */
    }
  }
}

// Space-Track sources are only listed once a credentials file exists -
// say so, and where the app looked, instead of leaving them silently absent.
function showSpaceTrackNote(spaceTrack) {
  const note = $("space-track-note");
  note.hidden = !spaceTrack || spaceTrack.available;
  if (note.hidden) return;
  const code = (text) => {
    const c = document.createElement("code");
    c.textContent = text;
    return c;
  };
  note.replaceChildren(
    "Space-Track sources are hidden: no valid credentials file at ",
    code(spaceTrack.credentials_path),
    ". Create it containing ",
    code('{"username": "…", "password": "…"}'),
    " (a free account from space-track.org), then reload this page.",
  );
}

function showSourceNote(text) {
  const note = $("source-note");
  note.textContent = text;
  note.hidden = !text;
}

// Picking a source shows its last saved pull - no download. "Pull New
// Data" is what fetches.
$("tle-source-select").addEventListener("change", async () => {
  const source = $("tle-source-select").value;
  try {
    const r = await api("/api/track/cached", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    if (r.cached) {
      renderSatelliteList(r.satellites, r.ranked);
      showSourceNote("");
      refreshStatus();
    } else {
      showSourceNote(`No saved data for ${source} yet - click Pull New Data to download it. `
        + "The list below is still the previous source.");
    }
  } catch (e) {
    showSourceNote("Couldn't load saved data: " + e.message);
  }
});

function formatAgo(seconds) {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function formatOverhead(seconds_until) {
  if (seconds_until === null || seconds_until === undefined) return "not in next 24h";
  if (seconds_until <= 0) return "overhead now";
  const totalMinutes = Math.round(seconds_until / 60);
  const h = Math.floor(totalMinutes / 60);
  const m = totalMinutes % 60;
  return h > 0 ? `in ${h}h ${m}m` : `in ${m}m`;
}

let lastSatellites = [];
let selectedSatName = null;
let lastListWasRanked = true;

function renderSatelliteList(satellites, ranked = true) {
  lastSatellites = satellites;
  lastListWasRanked = ranked;

  const note = $("rank-note");
  if (ranked) {
    note.hidden = true;
  } else {
    note.hidden = false;
    note.textContent =
      `${satellites.length} satellites is too many to rank by pass time - showing alphabetically instead.`;
  }

  const select = $("satellite-select");
  select.replaceChildren();
  satellites.forEach((sat, i) => {
    const opt = document.createElement("option");
    opt.value = i;
    opt.textContent = ranked ? `${sat.name} — ${formatOverhead(sat.seconds_until)}` : sat.name;
    select.appendChild(opt);
  });

  if (satellites.length === 0) {
    selectedSatIndex = null;
    selectedSatName = null;
    return;
  }

  // Re-rankings (auto or manual) reorder the list, so re-find whatever was
  // selected by name rather than keeping the same index - otherwise a
  // periodic refresh would silently yank the user's selection back to
  // whatever is now in slot 0.
  let newIndex = selectedSatName === null ? -1 : satellites.findIndex((s) => s.name === selectedSatName);
  if (newIndex === -1) newIndex = 0;

  select.value = String(newIndex);
  selectedSatIndex = newIndex;
  selectedSatName = satellites[newIndex].name;
  refreshTrackLive();
}

let resortInFlight = false;

async function autoResort() {
  // An unranked list is alphabetical and stays that way regardless of
  // time passing - nothing to refresh, so don't bother with the round trip.
  if (resortInFlight || !lastListWasRanked) return;
  resortInFlight = true;
  try {
    const { satellites, ranked } = await api("/api/track/resort", { method: "POST" });
    renderSatelliteList(satellites, ranked);
  } catch (e) {
    /* nothing loaded yet, or a transient error - just skip this tick */
  } finally {
    resortInFlight = false;
  }
}

setInterval(autoResort, 30000);

// ── Track map + live satellite info ─────────────────────────────

let trackMap = null;
let trackMarker = null;
let trackPathLines = [];
let selectedSatIndex = null;
let dishLocationMarker = null;
let smallPinIcon = null;

async function initTrackMap() {
  if (trackMap) {
    trackMap.invalidateSize();
    return;
  }

  // Built lazily, on first actual use, rather than at script load (top
  // level) - L.icon() needs the Leaflet global, and if that CDN script
  // fails to load (offline, blocked, CDN down), a top-level call would
  // throw before this script even finishes registering the rest of the
  // page's button handlers, breaking everything instead of just maps.
  if (!smallPinIcon) {
    // Leaflet's stock pin at ~60% size - small enough not to dominate the
    // map at low zoom, where the satellite's ground track can span continents.
    smallPinIcon = L.icon({
      iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
      shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
      iconSize: [15, 25],
      iconAnchor: [7, 25],
      popupAnchor: [1, -20],
      shadowSize: [25, 25],
    });
  }

  // Read straight from the API rather than the Location tab's input
  // fields - those are populated by their own async loadObserver() call,
  // and depending on it having already resolved by the time this tab is
  // clicked would be a race.
  let lat = 0, lon = 0;
  try {
    const observer = await api("/api/observer");
    lat = observer.lat;
    lon = observer.lon;
  } catch (e) {
    /* fall back to 0,0 */
  }

  trackMap = L.map("track-map").setView([lat, lon], 3);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(trackMap);

  dishLocationMarker = L.marker([lat, lon], { icon: smallPinIcon }).addTo(trackMap).bindPopup("Dish location");
  trackMarker = L.circleMarker([lat, lon], { radius: 6, color: "#f38ba8", fillOpacity: 1 }).addTo(trackMap);
}

// A ground track can cross the antimeridian (lon 180/-180); drawing that
// as one polyline would streak a line clean across the map, so split
// into a new segment wherever consecutive points jump more than 180°.
function splitAntimeridian(points) {
  const segments = [];
  let current = [];
  for (const p of points) {
    if (current.length > 0 && Math.abs(p[1] - current[current.length - 1][1]) > 180) {
      segments.push(current);
      current = [];
    }
    current.push(p);
  }
  if (current.length) segments.push(current);
  return segments;
}

function renderTrackPath(points) {
  trackPathLines.forEach((l) => trackMap.removeLayer(l));
  trackPathLines = splitAntimeridian(points).map((seg) =>
    L.polyline(seg, { color: "#89b4fa", weight: 2, dashArray: "4,6" }).addTo(trackMap)
  );
}

let lastLive = null;
let lastStatusPosition = null;

// Dish az is 360 - compass az (see dish_az_from_compass).
function updateDishLag() {
  const pos = lastStatusPosition;
  if (!pos || pos.az === null || pos.el === null) {
    $("dish-stat-compass").textContent = "—";
    $("dish-stat-lag").textContent = "—";
    return;
  }
  const compass = (360 - pos.az) % 360;
  $("dish-stat-compass").textContent = `${compass.toFixed(1)}°`;
  if (!lastLive) {
    $("dish-stat-lag").textContent = "—";
    return;
  }
  let dAz = lastLive.az - compass;
  dAz = ((dAz + 540) % 360) - 180;
  const dEl = lastLive.el - pos.el;
  $("dish-stat-lag").textContent = `Az ${dAz >= 0 ? "+" : ""}${dAz.toFixed(1)}°, El ${dEl >= 0 ? "+" : ""}${dEl.toFixed(1)}°`;
}

function updateSatInfo(live) {
  lastLive = live;
  updateDishLag();
  $("sat-info-name").textContent = live.name;
  $("sat-info-id").textContent = live.norad_id || "—";
  $("sat-info-az").textContent = live.az.toFixed(1) + "°";
  $("sat-info-el").textContent = live.el.toFixed(1) + "°";
  $("sat-info-dist").textContent = Math.round(live.distance_km) + " km";
  $("sat-info-alt").textContent = Math.round(live.alt_km) + " km";
  $("sat-info-speed").textContent = live.speed_km_s.toFixed(2) + " km/s";
}

async function refreshTrackLive() {
  if (selectedSatName === null) return;
  try {
    const { live, path } = await api(`/api/track/live?name=${encodeURIComponent(selectedSatName)}`);
    updateSatInfo(live);
    if (trackMap) {
      trackMarker.setLatLng([live.lat, live.lon]);
      renderTrackPath(path);
    }
  } catch (e) {
    /* satellite list likely changed under us - wait for the next selection */
  }
}

$("satellite-select").addEventListener("change", () => {
  const val = $("satellite-select").value;
  selectedSatIndex = val === "" ? null : parseInt(val, 10);
  selectedSatName = selectedSatIndex === null ? null : (lastSatellites[selectedSatIndex] || {}).name || null;
  refreshTrackLive();
});

setInterval(refreshTrackLive, 1000);

$("load-tles-btn").addEventListener("click", async () => {
  const btn = $("load-tles-btn");
  const requested = $("tle-source-select").value;
  btn.disabled = true;
  btn.textContent = "Pulling…";
  try {
    const { satellites, source, ranked, cached_fallback, notices } = await api("/api/track/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: requested }),
    });
    renderSatelliteList(satellites, ranked);
    showSourceNote("");
    const details = (notices || []).length ? "\n\n" + notices.join("\n") : "";
    if (source !== requested || cached_fallback) {
      $("tle-source-select").value = source;
      const note = cached_fallback ? " (from local cache, live fetch failed)" : "";
      alert(`${requested} didn't respond - loaded ${satellites.length} satellites from ${source}${note} instead.${details}`);
    } else if (details) {
      alert(`Loaded ${satellites.length} satellites from ${source}.${details}`);
    }
  } catch (e) {
    alert("Failed to load satellites: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Pull New Data";
    loadTleSources().catch(() => {});  // refresh the "saved … ago" labels
  }
});

$("resort-tles-btn").addEventListener("click", async () => {
  $("resort-tles-btn").disabled = true;
  try {
    const { satellites, ranked } = await api("/api/track/resort", { method: "POST" });
    renderSatelliteList(satellites, ranked);
  } catch (e) {
    alert("Could not resort: " + e.message);
  } finally {
    $("resort-tles-btn").disabled = false;
  }
});

$("track-start-btn").addEventListener("click", async () => {
  if (selectedSatName === null) return alert("Select a satellite first.");
  try {
    // Min elevation / max distance only save on the input's blur ("change"
    // event) - if the user typed a value and clicked Start Tracking
    // directly without tabbing away first, that edit could otherwise never
    // reach the server and tracking would silently run under the old
    // (possibly unset) limits. Force a sync of whatever's in the fields now.
    await saveTrackSettings();
    // Sent by name, not list position - resolved server-side against
    // whatever's current there, since an auto-resort between render and
    // this click could otherwise make an index point at a different
    // satellite than the one actually selected.
    await api("/api/track/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: selectedSatName }),
    });
  } catch (e) {
    alert("Could not start tracking: " + e.message);
  }
});

$("track-stop-btn").addEventListener("click", () => api("/api/track/stop", { method: "POST" }));

// ── Track settings (min elevation / max distance) ───────────────

async function loadTrackSettings() {
  try {
    const { min_elevation, max_distance_km, show_constellations } = await api("/api/track/settings");
    $("min-elevation").value = min_elevation;
    $("max-distance").value = max_distance_km === null ? "" : max_distance_km;
    $("show-constellations").checked = show_constellations;
  } catch (e) {
    /* ignore */
  }
}

// Throws on failure - the track-start-btn handler below depends on that to
// avoid silently starting a track session under stale/unsaved limits.
async function saveTrackSettings() {
  await api("/api/track/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      min_elevation: $("min-elevation").value,
      max_distance_km: $("max-distance").value === "" ? null : $("max-distance").value,
      show_constellations: $("show-constellations").checked,
    }),
  });
}

$("min-elevation").addEventListener("change", () => saveTrackSettings().catch(() => {}));
$("max-distance").addEventListener("change", () => saveTrackSettings().catch(() => {}));
$("show-constellations").addEventListener("change", async () => {
  await saveTrackSettings().catch(() => {});
  // Unlike min-elevation/max-distance, this should take effect on the
  // satellites already loaded right away, not just the next load/track -
  // calling resort directly here (not autoResort()) since that skips
  // unranked lists, but this toggle changes an unranked list's contents too.
  try {
    const { satellites, ranked } = await api("/api/track/resort", { method: "POST" });
    renderSatelliteList(satellites, ranked);
  } catch (e) {
    /* nothing loaded yet - ignore */
  }
});

// ── Calibrate ────────────────────────────────────────────────────

let calibrateTargetsCache = [];

async function loadCalibrateTargets() {
  try {
    const { targets } = await api("/api/calibrate/targets");
    calibrateTargetsCache = targets;
    const box = $("calibrate-targets");
    box.replaceChildren();
    const h3 = document.createElement("h3");
    h3.textContent = "Targets";
    box.appendChild(h3);
    targets.forEach((t) => {
      const label = document.createElement("label");
      label.className = "checkbox-label";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.className = "calibrate-target-checkbox";
      input.value = t.lon;
      input.checked = t.visible;
      input.disabled = !t.visible;
      label.appendChild(input);
      label.appendChild(document.createTextNode(
        ` ${t.label} — az ${t.az.toFixed(1)}°, el ${t.el.toFixed(1)}°` +
          (t.visible ? "" : " (not visible from here)")
      ));
      box.appendChild(label);
      input.addEventListener("change", updateCalibrateEstimate);
    });
    updateCalibrateEstimate();
  } catch (e) {
    /* connect/location may not be set up yet */
  }
}

function selectedCalibrateTargets() {
  return Array.from(document.querySelectorAll(".calibrate-target-checkbox:checked")).map((i) => parseFloat(i.value));
}

function updateCalibrateEstimate() {
  const margin = parseFloat($("calibrate-margin").value) || 6;
  const step = parseFloat($("calibrate-step").value) || 2;
  const n = selectedCalibrateTargets().length;
  const perAxis = Math.round((2 * margin) / step) + 1;
  const points = n * perAxis * perAxis;
  const minutes = (points * 1.2) / 60;
  $("calibrate-estimate").textContent = n
    ? `${n} target(s), ~${points} points total, ~${minutes.toFixed(1)} minutes`
    : "Select at least one target.";
}

["calibrate-margin", "calibrate-step"].forEach((id) => $(id).addEventListener("change", updateCalibrateEstimate));

async function startCalibration(targets, margin, step) {
  try {
    await api("/api/calibrate/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ targets, margin, step }),
    });
    $("calibrate-results").hidden = true;
    $("use-average-correction-btn").hidden = true;
  } catch (e) {
    alert("Could not start calibration: " + e.message);
  }
}

$("calibrate-start-btn").addEventListener("click", () => {
  const targets = selectedCalibrateTargets();
  if (targets.length === 0) return alert("Select at least one target first.");
  startCalibration(targets, $("calibrate-margin").value, $("calibrate-step").value);
});

// Just the single best (highest-elevation, currently visible) target, with
// a tighter window - a fast "is this still pointing where it should" check
// instead of the full multi-target sweep.
$("calibrate-quick-btn").addEventListener("click", () => {
  const visible = calibrateTargetsCache.filter((t) => t.visible);
  if (visible.length === 0) return alert("No currently-visible target to check against.");
  const best = visible.reduce((a, b) => (b.el > a.el ? b : a));
  startCalibration([best.lon], 4, 1.5);
});

$("calibrate-stop-btn").addEventListener("click", () => api("/api/calibrate/stop", { method: "POST" }));

async function refreshCalibrateLog() {
  try {
    const { log } = await api("/api/calibrate/log");
    const el = $("calibrate-log");
    el.textContent = log.join("\n");
    el.scrollTop = el.scrollHeight;
  } catch (e) {
    /* ignore */
  }
}

function renderCalibrateResults(results) {
  const table = $("calibrate-results");
  const tbody = table.querySelector("tbody");
  const tfoot = table.querySelector("tfoot");
  tbody.replaceChildren();
  tfoot.replaceChildren();

  const found = results.filter((r) => r.found);
  if (found.length === 0) {
    table.hidden = true;
    return;
  }

  const fmtSigned = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}°`;

  found.forEach((r) => {
    const tr = document.createElement("tr");
    [r.label, fmtSigned(r.az_error), fmtSigned(r.el_error), String(r.peak_signal)].forEach((text) => {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });

  const avgAz = found.reduce((s, r) => s + r.az_error, 0) / found.length;
  const avgEl = found.reduce((s, r) => s + r.el_error, 0) / found.length;
  const tr = document.createElement("tr");
  [`Average (${found.length})`, fmtSigned(avgAz), fmtSigned(avgEl), ""].forEach((text) => {
    const td = document.createElement("td");
    td.innerHTML = `<strong>${text}</strong>`;
    tr.appendChild(td);
  });
  tfoot.appendChild(tr);

  table.hidden = false;

  // Offered as a starting point, not applied automatically - the average is
  // only meaningful before any correction is already in effect (this run's
  // "error" is the *residual* once a correction is saved, so stacking
  // another average on top of that would double-correct).
  lastAvgError = { az: avgAz, el: avgEl };
  $("use-average-correction-btn").hidden = false;
}

let lastAvgError = null;

$("use-average-correction-btn").addEventListener("click", () => {
  if (!lastAvgError) return;
  const current = { az: parseFloat($("az-correction").value) || 0, el: parseFloat($("el-correction").value) || 0 };
  $("az-correction").value = (current.az + lastAvgError.az).toFixed(1);
  $("el-correction").value = (current.el + lastAvgError.el).toFixed(1);
});

async function loadCorrection() {
  try {
    const { az, el } = await api("/api/correction");
    $("az-correction").value = az;
    $("el-correction").value = el;
  } catch (e) {
    /* ignore */
  }
}

$("save-correction-btn").addEventListener("click", async () => {
  const az = parseFloat($("az-correction").value);
  const el = parseFloat($("el-correction").value);
  if (Number.isNaN(az) || Number.isNaN(el)) return alert("Enter valid az/el correction values.");
  try {
    await api("/api/correction", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ az, el }),
    });
    const badge = $("correction-saved");
    badge.hidden = false;
    setTimeout(() => (badge.hidden = true), 1500);
  } catch (e) {
    alert("Could not save correction: " + e.message);
  }
});

let calibrateResultsRendered = false;

function refreshCalibrateTab(calibrate, connected) {
  $("calibrate-start-btn").disabled = !connected || calibrate.running;
  $("calibrate-stop-btn").disabled = !calibrate.running;

  const pill = $("calibrate-status");
  if (calibrate.running) {
    pill.textContent = `Running — ${calibrate.current_target || "…"}`;
    pill.classList.add("connected");
    pill.classList.remove("disconnected");
  } else if (calibrate.error) {
    pill.textContent = `Error: ${calibrate.error}`;
    pill.classList.add("disconnected");
    pill.classList.remove("connected");
  } else {
    pill.textContent = "Not running";
    pill.classList.remove("connected", "disconnected");
  }

  // Keep showing the last known progress even after the run ends, not just
  // while running - a status poll can land between the final point's "done"
  // increment and "running" flipping to false, which would otherwise freeze
  // the display short of 100% forever even though the run finished normally.
  if (calibrate.running || calibrate.total > 0) {
    $("calibrate-current").textContent = calibrate.current_target || "—";
    $("calibrate-azel").textContent =
      calibrate.azimuth === null ? "—" : `${calibrate.azimuth.toFixed(1)}° / ${calibrate.elevation.toFixed(1)}°`;
    $("calibrate-signal").textContent = calibrate.signal === null ? "—" : calibrate.signal;
    $("calibrate-progress").textContent = `${calibrate.done} / ${calibrate.total}`;
  }

  if (calibrate.running) {
    refreshCalibrateLog();
    calibrateResultsRendered = false;
  } else if (!calibrateResultsRendered && calibrate.results && calibrate.results.length) {
    renderCalibrateResults(calibrate.results);
    calibrateResultsRendered = true;
  }
}

// ── Location ─────────────────────────────────────────────────────

let observerMap = null;
let observerMarker = null;

function setObserverFields(lat, lon) {
  $("observer-lat").value = lat.toFixed(4);
  $("observer-lon").value = lon.toFixed(4);
}

function initObserverMap() {
  if (observerMap) {
    observerMap.invalidateSize();
    return;
  }

  const lat = parseFloat($("observer-lat").value) || 0;
  const lon = parseFloat($("observer-lon").value) || 0;

  observerMap = L.map("observer-map").setView([lat, lon], 10);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(observerMap);

  observerMarker = L.marker([lat, lon], { draggable: true }).addTo(observerMap);
  observerMarker.on("dragend", () => {
    const { lat, lng } = observerMarker.getLatLng();
    setObserverFields(lat, lng);
  });
  observerMap.on("click", (e) => {
    observerMarker.setLatLng(e.latlng);
    setObserverFields(e.latlng.lat, e.latlng.lng);
  });
}

async function loadObserver() {
  try {
    const { lat, lon } = await api("/api/observer");
    setObserverFields(lat, lon);
  } catch (e) {
    /* ignore */
  }
}

$("save-observer-btn").addEventListener("click", async () => {
  const lat = parseFloat($("observer-lat").value);
  const lon = parseFloat($("observer-lon").value);
  if (Number.isNaN(lat) || Number.isNaN(lon)) return alert("Enter a valid latitude and longitude.");
  try {
    await api("/api/observer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat, lon }),
    });
    if (observerMarker) observerMarker.setLatLng([lat, lon]);
    if (observerMap) observerMap.panTo([lat, lon]);
    // The Track tab's map (if it's been opened already) has its own
    // separate dish-location pin that otherwise never finds out about a
    // location change made from here.
    if (dishLocationMarker) dishLocationMarker.setLatLng([lat, lon]);
    loadCalibrateTargets();  // the slots' theoretical az/el depend on the dish's location
    const badge = $("observer-saved");
    badge.hidden = false;
    setTimeout(() => (badge.hidden = true), 1500);
  } catch (e) {
    alert("Could not save location: " + e.message);
  }
});

// ── Motion monitor ───────────────────────────────────────────────

async function loadMotionSettings() {
  try {
    const { step_size, tolerance } = await api("/api/motion/settings");
    $("step-size").value = step_size;
    $("move-tolerance").value = tolerance;
  } catch (e) {
    /* ignore */
  }
}

async function saveMotionSettings() {
  try {
    await api("/api/motion/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        step_size: $("step-size").value,
        tolerance: $("move-tolerance").value,
      }),
    });
  } catch (e) {
    /* ignore */
  }
}

$("step-size").addEventListener("change", saveMotionSettings);
$("move-tolerance").addEventListener("change", saveMotionSettings);

$("clear-warnings-btn").addEventListener("click", () => api("/api/motion/clear", { method: "POST" }));

function updateMotionControls(busy) {
  const title = busy
    ? "Settings apply to the next scan/track run, not the one in progress"
    : "";
  [$("step-size"), $("move-tolerance"), $("min-elevation"), $("max-distance")].forEach((input) => {
    input.disabled = busy;
    input.title = title;
  });
}

function updateSlipBadge(motion) {
  const badge = $("slip-badge");
  if (motion.slip_active) {
    const last = motion.warnings[motion.warnings.length - 1];
    const detail = last.unverified
      ? "could not verify (no sensor reply)"
      : `off by ${last.diff} counts`;
    badge.textContent = `Movement warning: ${last.axis} target ${last.target_deg}° ${detail}`;
    badge.classList.remove("disconnected");
    badge.classList.add("warning");
  } else {
    badge.textContent = "No movement warnings";
    badge.classList.remove("warning");
    badge.classList.remove("disconnected");
  }
}

// ── Status polling ───────────────────────────────────────────────

let scanWasRunning = false;

async function refreshStatus() {
  let status;
  try {
    status = await api("/api/status");
  } catch (e) {
    return;
  }

  setConnected(status.connected);

  const busy = status.scan.running || status.track.running || status.calibrate.running
    || status.sweep.running;
  updateMotionControls(busy);

  $("sweep-start-btn").disabled = !status.connected || busy || manualMoving;
  $("sweep-stop-btn").disabled = !status.sweep.running;
  if (status.sweep.running && !sweepPolling) {
    sweepPolling = true; // a sweep started before this page loaded - pick it up
  }

  $("scan-start-btn").disabled = !status.connected || status.scan.running;
  $("scan-stop-btn").disabled = !status.scan.running;
  if (status.scan.running) {
    $("scan-preview").src = `/api/scan/preview.png?t=${Date.now()}`;
    $("scan-preview").hidden = false;
    refreshScanLog();
  } else if (scanWasRunning) {
    refreshFiles(true);  // the scan just finished - list its file under Saved scans
  }
  scanWasRunning = status.scan.running;

  $("track-start-btn").disabled = !status.connected || status.track.running;
  $("track-stop-btn").disabled = !status.track.running;
  const trackPill = $("track-status");
  trackPill.textContent = status.track.message;
  trackPill.classList.toggle("connected", status.track.running && !status.track.below_horizon);
  trackPill.classList.toggle("disconnected", status.track.below_horizon);

  $("manual-go-btn").disabled = !status.connected || manualMoving || status.sweep.running;
  const mp = status.motion.position;
  $("manual-heading").textContent = (mp.az === null || mp.el === null)
    ? "unknown" : `az ${mp.az.toFixed(1)}°, el ${mp.el.toFixed(1)}°`;

  updateSlipBadge(status.motion);
  refreshCalibrateTab(status.calibrate, status.connected);

  if (status.track.tle_fetched_at) {
    const agoText = formatAgo(Date.now() / 1000 - status.track.tle_fetched_at);
    $("tle-updated").textContent = `Kepler data: pulled ${agoText} ago from ${status.track.tle_source}`;
  } else {
    $("tle-updated").textContent = "Kepler data: never pulled";
  }

  $("dish-stat-conn").textContent = status.connected ? `Connected (${status.port})` : "Disconnected";
  const pos = status.motion.position;
  lastStatusPosition = pos;
  updateDishLag();
  $("dish-stat-az").textContent = pos.az === null ? "unknown" : `${pos.az.toFixed(1)}°`;
  $("dish-stat-el").textContent = pos.el === null ? "unknown" : `${pos.el.toFixed(1)}°`;
  $("dish-stat-step").textContent = `${status.motion.step_size}° / ${status.motion.tolerance} counts`;
}

setInterval(refreshStatus, 1500);
refreshStatus();
refreshFiles();
updateEstimate();
loadTleSources(true);
loadMotionSettings();
loadObserver();
loadTrackSettings();
loadCalibrateTargets();
loadCorrection();

// ── Clock check (satellite az/el depends on the local clock) ─────

async function checkClock() {
  const el = $("clock-stat");
  try {
    const r = await api("/api/clock");
    if (!r.ok) {
      el.textContent = "check failed";
      el.title = r.error;
      return;
    }
    const ms = Math.round(r.offset_s * 1000);
    el.textContent = `${r.in_sync ? "OK" : "OFF"}: ${ms >= 0 ? "+" : ""}${ms} ms`;
    el.title = `NTS server ${r.server}, round trip ${r.rtt_ms.toFixed(0)} ms. `
      + "Positive means this computer's clock is behind.";
    el.style.color = r.in_sync ? "var(--good)" : "var(--bad)";
  } catch (e) {
    el.textContent = "check failed";
    el.title = e.message;
  }
}
checkClock();
setInterval(checkClock, 5 * 60 * 1000);
