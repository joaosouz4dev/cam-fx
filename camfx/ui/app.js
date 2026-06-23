// Ponte com o Python via pywebview (window.pywebview.api.*).
let state = null;
let previewOn = false;
let previewTimer = null;

function api() { return window.pywebview.api; }

async function init() {
  state = await api().get_state();
  // logo
  const logo = await api().get_logo();
  if (logo) document.getElementById("logo").src = logo;

  // camera
  const camSel = document.getElementById("camera");
  camSel.innerHTML = "";
  state.cameras.forEach((c, i) => {
    const o = document.createElement("option");
    o.value = i; o.textContent = c.name;
    if (i === state.camera_index_pos) o.selected = true;
    camSel.appendChild(o);
  });

  // device (processamento)
  const devSel = document.getElementById("device");
  devSel.innerHTML = "";
  state.devices.forEach((d) => {
    const o = document.createElement("option");
    o.value = d.value; o.textContent = d.label;
    if (d.value === state.compute_device) o.selected = true;
    devSel.appendChild(o);
  });

  setToggle("tg-blur", state.blur_enabled);
  setToggle("tg-framing", state.framing_enabled);
  setToggle("tg-autostart", state.autostart);
  setToggle("tg-preview", false);

  document.getElementById("blur").value = state.blur_strength;
  document.getElementById("blur-val").textContent = pct(state.blur_strength, 3, 75);
  document.getElementById("zoom").value = Math.round(state.framing_zoom * 10);
  document.getElementById("zoom-val").textContent =
    pctZoom(Math.round(state.framing_zoom * 10));

  applyDisabled("blur-sub", state.blur_enabled);
  applyDisabled("framing-sub", state.framing_enabled);

  pollStatus();
}

function pct(v, lo, hi) { return Math.round(((v - lo) / (hi - lo)) * 100) + "%"; }
function pctZoom(v10) { return Math.round(((v10 - 10) / (25 - 10)) * 100) + "%"; }

function setToggle(id, on) {
  document.getElementById(id).classList.toggle("on", !!on);
}
function applyDisabled(id, enabled) {
  document.getElementById(id).classList.toggle("disabled", !enabled);
}

// ---- handlers ----
function onCamera() {
  api().set_camera(parseInt(document.getElementById("camera").value));
}
function onDevice() {
  api().set_device(document.getElementById("device").value);
}
function toggleBlur() {
  state.blur_enabled = !state.blur_enabled;
  setToggle("tg-blur", state.blur_enabled);
  applyDisabled("blur-sub", state.blur_enabled);
  api().set_blur_enabled(state.blur_enabled);
}
function onBlur(v) {
  document.getElementById("blur-val").textContent = pct(+v, 3, 75);
  api().set_blur_strength(parseInt(v));
}
function toggleFraming() {
  state.framing_enabled = !state.framing_enabled;
  setToggle("tg-framing", state.framing_enabled);
  applyDisabled("framing-sub", state.framing_enabled);
  api().set_framing_enabled(state.framing_enabled);
}
function onZoom(v) {
  document.getElementById("zoom-val").textContent = pctZoom(+v);
  api().set_zoom(parseInt(v));
}
function toggleAutostart() {
  state.autostart = !state.autostart;
  setToggle("tg-autostart", state.autostart);
  api().set_autostart(state.autostart);
}
function minimize() { api().minimize(); }

function togglePreview() {
  previewOn = !previewOn;
  setToggle("tg-preview", previewOn);
  api().set_preview(previewOn);
  if (previewOn) {
    document.getElementById("placeholder").style.display = "none";
    document.getElementById("preview").style.display = "block";
    tickPreview();
  } else {
    if (previewTimer) clearTimeout(previewTimer);
    document.getElementById("preview").style.display = "none";
    document.getElementById("placeholder").style.display = "flex";
  }
}

async function tickPreview() {
  if (!previewOn) return;
  try {
    const data = await api().get_preview_frame();
    if (data) document.getElementById("preview").src = data;
  } catch (e) {}
  previewTimer = setTimeout(tickPreview, 120);
}

async function pollStatus() {
  try {
    const s = await api().get_status();
    if (s) document.getElementById("status-main").textContent = s;
  } catch (e) {}
  setTimeout(pollStatus, 1000);
}

window.addEventListener("pywebviewready", init);
