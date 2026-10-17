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

// ── View ─────────────────────────────────────────────────────────

async function refreshFiles() {
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
  if (files.includes(current)) select.value = current;
}

$("refresh-files-btn").addEventListener("click", refreshFiles);

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

async function loadTleSources() {
  const { sources } = await api("/api/track/sources");
  const select = $("tle-source-select");
  select.replaceChildren();
  sources.forEach((name) => {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  });
}

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

function updateSatInfo(live) {
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

setInterval(refreshTrackLive, 3000);

$("load-tles-btn").addEventListener("click", async () => {
  const btn = $("load-tles-btn");
  const requested = $("tle-source-select").value;
  btn.disabled = true;
  btn.textContent = "Loading…";
  try {
    const { satellites, source, ranked, cached_fallback } = await api("/api/track/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: requested }),
    });
    renderSatelliteList(satellites, ranked);
    if (source !== requested || cached_fallback) {
      $("tle-source-select").value = source;
      const note = cached_fallback ? " (from local cache, live fetch failed)" : "";
      alert(`${requested} didn't respond - loaded ${satellites.length} satellites from ${source}${note} instead.`);
    }
  } catch (e) {
    alert("Failed to load satellites: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Load Satellites";
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

async function refreshStatus() {
  let status;
  try {
    status = await api("/api/status");
  } catch (e) {
    return;
  }

  setConnected(status.connected);

  const busy = status.scan.running || status.track.running || status.calibrate.running;
  updateMotionControls(busy);

  $("scan-start-btn").disabled = !status.connected || status.scan.running;
  $("scan-stop-btn").disabled = !status.scan.running;
  if (status.scan.running) {
    $("scan-preview").src = `/api/scan/preview.png?t=${Date.now()}`;
    $("scan-preview").hidden = false;
    refreshScanLog();
  }

  $("track-start-btn").disabled = !status.connected || status.track.running;
  $("track-stop-btn").disabled = !status.track.running;
  const trackPill = $("track-status");
  trackPill.textContent = status.track.message;
  trackPill.classList.toggle("connected", status.track.running && !status.track.below_horizon);
  trackPill.classList.toggle("disconnected", status.track.below_horizon);

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
  $("dish-stat-az").textContent = pos.az === null ? "unknown" : `${pos.az.toFixed(1)}°`;
  $("dish-stat-el").textContent = pos.el === null ? "unknown" : `${pos.el.toFixed(1)}°`;
  $("dish-stat-step").textContent = `${status.motion.step_size}° / ${status.motion.tolerance} counts`;
}

setInterval(refreshStatus, 1500);
refreshStatus();
refreshFiles();
updateEstimate();
loadTleSources();
loadMotionSettings();
loadObserver();
loadTrackSettings();
loadCalibrateTargets();
loadCorrection();
