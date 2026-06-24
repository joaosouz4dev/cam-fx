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

  // face swap (o sub fica sempre ativo: o usuario pode escolher a foto antes
  // de ligar o efeito; o toggle controla apenas se o swap roda)
  setToggle("tg-faceswap", state.faceswap_enabled);
  setToggle("tg-enhance", state.faceswap_enhance);
  if (state.has_source_face) {
    try {
      const thumb = await api().get_source_face_thumb();
      if (thumb) {
        document.getElementById("src-face-img").src = thumb;
        document.getElementById("src-face").classList.add("show");
      }
    } catch (e) {}
  }

  // versao no rodape
  try {
    const v = await api().get_app_version();
    if (v) document.getElementById("app-version").textContent = "CamFX " + v;
  } catch (e) {}

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

// ---- atualizacao ----
let updateInfo = null;
let updating = false;

// Chamado pelo Python quando uma versao nova e encontrada.
window.camfxUpdateAvailable = function (version, notes) {
  updateInfo = { version: version, notes: notes };
  document.getElementById("update-main").textContent =
    "Nova versão disponível: CamFX " + version;
  document.getElementById("update-sub").textContent =
    "Clique em Atualizar para baixar e instalar.";
  resetUpdateButtons();
  document.getElementById("update-banner").classList.add("show");
};

// Progresso do download (got/total em bytes; -1,-1 = erro; 100,100 = pronto).
window.camfxUpdateProgress = function (got, total) {
  const sub = document.getElementById("update-sub");
  if (got === -1) {
    updating = false;
    resetUpdateButtons();
    sub.textContent = "Falha ao baixar. Tente novamente.";
    return;
  }
  if (got >= 100 && total === 100) {
    sub.textContent = "Concluído. Iniciando o instalador...";
    return;
  }
  if (total > 0) {
    const p = Math.round((got / total) * 100);
    sub.textContent = "Baixando... " + p + "%";
  } else {
    sub.textContent = "Baixando...";
  }
};

function resetUpdateButtons() {
  const go = document.getElementById("update-go");
  const later = document.getElementById("update-later");
  go.disabled = false; later.disabled = false;
  go.textContent = "Atualizar";
}

async function updateNow() {
  if (updating) return;
  updating = true;
  document.getElementById("update-go").disabled = true;
  document.getElementById("update-later").disabled = true;
  document.getElementById("update-go").textContent = "Baixando...";
  document.getElementById("update-sub").textContent = "Iniciando download...";
  try {
    const r = await api().download_and_install_update();
    if (r && r.ok === false) {
      updating = false;
      resetUpdateButtons();
      document.getElementById("update-sub").textContent =
        r.error || "Não foi possível atualizar.";
    }
  } catch (e) {
    updating = false;
    resetUpdateButtons();
  }
}

function updateLater() {
  document.getElementById("update-banner").classList.remove("show");
}

async function checkUpdate() {
  const link = document.getElementById("check-update");
  link.textContent = "Verificando...";
  try {
    const info = await api().check_update_now();
    if (info && info.version) {
      window.camfxUpdateAvailable(info.version, info.notes || "");
      link.textContent = "Verificar atualizações";
    } else {
      link.textContent = "Você está na versão mais recente";
      setTimeout(() => { link.textContent = "Verificar atualizações"; }, 4000);
    }
  } catch (e) {
    link.textContent = "Verificar atualizações";
  }
}

// ---- face swap ----
async function toggleFaceswap() {
  // Liga so apos aceitar os termos.
  if (!state.faceswap_enabled && !state.terms_accepted) {
    showTerms("faceswap");
    return;
  }
  state.faceswap_enabled = !state.faceswap_enabled;
  setToggle("tg-faceswap", state.faceswap_enabled);
  applyDisabled("faceswap-sub", state.faceswap_enabled);
  api().set_faceswap_enabled(state.faceswap_enabled);
}

function toggleEnhance() {
  state.faceswap_enhance = !state.faceswap_enhance;
  setToggle("tg-enhance", state.faceswap_enhance);
  api().set_faceswap_enhance(state.faceswap_enhance);
}

async function chooseSourceFace() {
  try {
    const r = await api().choose_source_face();
    if (r && r.thumb) {
      document.getElementById("src-face-img").src = r.thumb;
      document.getElementById("src-face").classList.add("show");
      state.has_source_face = true;
    } else if (r && r.error) {
      alert(r.error);
    }
  } catch (e) {}
}

// ---- termos de uso ----
let termsContext = null;   // o que fazer apos aceitar

async function showTerms(context) {
  termsContext = context || null;
  try {
    const t = await api().get_terms();
    if (t && t.text) document.getElementById("terms-text").textContent = t.text;
  } catch (e) {}
  document.getElementById("terms-overlay").classList.add("show");
}

async function acceptTerms() {
  try { await api().accept_terms(); } catch (e) {}
  state.terms_accepted = true;
  document.getElementById("terms-overlay").classList.remove("show");
  // Se o usuario veio do toggle de face swap, liga agora.
  if (termsContext === "faceswap") {
    state.faceswap_enabled = true;
    setToggle("tg-faceswap", true);
    applyDisabled("faceswap-sub", true);
    api().set_faceswap_enabled(true);
  }
  termsContext = null;
}

function declineTerms() {
  document.getElementById("terms-overlay").classList.remove("show");
  termsContext = null;
}

// O Python pode pedir para exibir os termos no startup.
window.camfxShowTerms = function () { showTerms(null); };

window.addEventListener("pywebviewready", init);
