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
  img.src = `/api/image.png?file=${encodeURIComponent(file)}&factor=${factor}&t=${Date.now()}`;
  img.hidden = false;
});

// ── Track ────────────────────────────────────────────────────────

$("load-tles-btn").addEventListener("click", async () => {
  $("load-tles-btn").disabled = true;
  try {
    const { satellites } = await api("/api/track/load", { method: "POST" });
    const select = $("satellite-select");
    select.replaceChildren();
    satellites.forEach((name, i) => {
      const opt = document.createElement("option");
      opt.value = i;
      opt.textContent = name;
      select.appendChild(opt);
    });
  } catch (e) {
    alert("Failed to load satellites: " + e.message);
  } finally {
    $("load-tles-btn").disabled = false;
  }
});

$("track-start-btn").addEventListener("click", async () => {
  const select = $("satellite-select");
  if (!select.value) return alert("Select a satellite first.");
  try {
    await api("/api/track/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: select.value }),
    });
  } catch (e) {
    alert("Could not start tracking: " + e.message);
  }
});

$("track-stop-btn").addEventListener("click", () => api("/api/track/stop", { method: "POST" }));

// ── Status polling ───────────────────────────────────────────────

async function refreshStatus() {
  let status;
  try {
    status = await api("/api/status");
  } catch (e) {
    return;
  }

  setConnected(status.connected);

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
}

setInterval(refreshStatus, 1500);
refreshStatus();
refreshFiles();
updateEstimate();
