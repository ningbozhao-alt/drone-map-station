const canvas = document.getElementById("map");
const ctx = canvas.getContext("2d");
const frameImg = document.getElementById("frame");
const drapeImg = new Image();
const preImg = new Image();
const diffImg = new Image();
let state = null;
let follow = true;
let pickMode = false;
let observed = null;
let center = { lat: 30.274084, lon: 120.15507 };
let metersPerPixel = 3;
let baidu = null;
let baiduOverlays = {};
let drapeCanvas = document.createElement("canvas");
let preCanvas = document.createElement("canvas");
let diffCanvas = document.createElement("canvas");
let lastDrawnSeq = -1;

const M_PER_DEG_LAT = 111320;

function mPerDegLon(lat) {
  return M_PER_DEG_LAT * Math.cos((lat * Math.PI) / 180);
}

function resize() {
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = Math.max(2, rect.width);
  canvas.height = Math.max(2, rect.height);
  draw();
}
window.addEventListener("resize", resize);

function project(lat, lon) {
  const x = canvas.width / 2 + ((lon - center.lon) * mPerDegLon(center.lat)) / metersPerPixel;
  const y = canvas.height / 2 - ((lat - center.lat) * M_PER_DEG_LAT) / metersPerPixel;
  return [x, y];
}

function unproject(x, y) {
  const lon = center.lon + ((x - canvas.width / 2) * metersPerPixel) / mPerDegLon(center.lat);
  const lat = center.lat - ((y - canvas.height / 2) * metersPerPixel) / M_PER_DEG_LAT;
  return { lat, lon };
}

function boundsRect(b) {
  if (!b) return null;
  const nw = project(b.north, b.west);
  const se = project(b.south, b.east);
  return { x: nw[0], y: nw[1], w: se[0] - nw[0], h: se[1] - nw[1] };
}

function drawImageBounds(img, bounds, alpha, clipRatio) {
  if (!img.complete || !img.naturalWidth || !bounds) return;
  const rect = boundsRect(bounds);
  if (!rect || rect.w < 2 || rect.h < 2) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  if (clipRatio > 0) {
    ctx.beginPath();
    ctx.rect(rect.x + rect.w * clipRatio, rect.y, rect.w * (1 - clipRatio), rect.h);
    ctx.clip();
  }
  ctx.drawImage(img, rect.x, rect.y, rect.w, rect.h);
  ctx.restore();
}

const tileCache = new Map();

function chooseZoom() {
  const cos = Math.cos((center.lat * Math.PI) / 180);
  const z = Math.log2((156543.03392 * Math.max(cos, 0.01)) / metersPerPixel);
  return Math.max(4, Math.min(18, Math.round(z)));
}

function tileLon(x, z) {
  return (x / 2 ** z) * 360 - 180;
}

function tileLat(y, z) {
  const n = Math.PI - (2 * Math.PI * y) / 2 ** z;
  return (180 / Math.PI) * Math.atan(Math.sinh(n));
}

function mercatorRow(lat, n) {
  const clamped = Math.max(-85, Math.min(85, lat));
  const rad = (clamped * Math.PI) / 180;
  return Math.floor((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2 * n);
}

function getTile(z, x, y) {
  const key = `${z}/${y}/${x}`;
  let img = tileCache.get(key);
  if (img) return img;
  img = new Image();
  img.onload = () => draw();
  img.onerror = () => { img._failed = true; };
  img.src = `/tiles/${z}/${y}/${x}.jpg`;
  tileCache.set(key, img);
  if (tileCache.size > 400) {
    const oldest = tileCache.keys().next().value;
    tileCache.delete(oldest);
  }
  return img;
}

function drawTiles() {
  if (canvas.hidden) return false;
  let z = chooseZoom();
  let box = null;
  for (let guard = 0; guard < 6; guard += 1) {
    const n = 2 ** z;
    const nw = unproject(-32, -32);
    const se = unproject(canvas.width + 32, canvas.height + 32);
    const x0 = Math.max(0, Math.floor(((nw.lon + 180) / 360) * n));
    const x1 = Math.min(n - 1, Math.floor(((se.lon + 180) / 360) * n));
    const y0 = Math.max(0, mercatorRow(nw.lat, n));
    const y1 = Math.min(n - 1, mercatorRow(se.lat, n));
    const count = Math.max(0, x1 - x0 + 1) * Math.max(0, y1 - y0 + 1);
    box = { x0, x1, y0, y1 };
    if (count <= 48 || z <= 4) break;
    z -= 1;
  }
  if (!box) return false;
  let painted = 0;
  for (let x = box.x0; x <= box.x1; x += 1) {
    for (let y = box.y0; y <= box.y1; y += 1) {
      const img = getTile(z, x, y);
      if (!img.complete || !img.naturalWidth) continue;
      const west = tileLon(x, z);
      const east = tileLon(x + 1, z);
      const north = tileLat(y, z);
      const south = tileLat(y + 1, z);
      const nw = project(north, west);
      const se = project(south, east);
      ctx.drawImage(img, nw[0], nw[1], se[0] - nw[0], se[1] - nw[1]);
      painted += 1;
    }
  }
  if (!baidu && document.getElementById("legend")) {
    document.getElementById("legend").textContent = painted
      ? "卫星影像 · WGS84 · © Esri"
      : "正在加载卫星瓦片";
  }
  return painted > 0;
}

function draw() {
  if (canvas.hidden || !canvas.width) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#1a2420";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  const hasTiles = drawTiles();
  if (!hasTiles) {
    ctx.strokeStyle = "#2a3830";
    ctx.lineWidth = 1;
    for (let x = 0; x < canvas.width; x += 80) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, canvas.height);
      ctx.stroke();
    }
  }
  const swipe = Number(document.getElementById("swipe").value) / 100;
  const showPre = document.getElementById("showPre").checked || swipe > 0;
  if (showPre && state && state.pre) drawImageBounds(preImg, state.pre, 1, 0);
  const opacity = Number(document.getElementById("opacity").value) / 100;
  if (state && state.bounds_wgs) drawImageBounds(drapeImg, state.bounds_wgs, opacity, swipe);
  if (document.getElementById("diff").checked && state && state.bounds_wgs) {
    drawImageBounds(diffImg, state.bounds_wgs, 0.9, 0);
  }
  if (state && state.track_wgs && state.track_wgs.length > 1) {
    ctx.beginPath();
    state.track_wgs.forEach((p, i) => {
      const [x, y] = project(p.lat, p.lon);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = "#8fd0ff";
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  if (state && state.corners_wgs && state.corners_wgs.length === 4) {
    ctx.beginPath();
    state.corners_wgs.forEach((p, i) => {
      const [x, y] = project(p.lat, p.lon);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
    const oblique = state.view === "oblique";
    ctx.fillStyle = oblique ? "rgba(224,177,90,0.05)" : "rgba(125,206,160,0.04)";
    ctx.strokeStyle = oblique ? "rgba(224,177,90,0.28)" : "rgba(125,206,160,0.28)";
    ctx.lineWidth = 1;
    ctx.fill();
    ctx.stroke();
  }
  if (state && state.wgs) {
    const [x, y] = project(state.wgs.lat, state.wgs.lon);
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(((state.yaw || 0) * Math.PI) / 180);
    ctx.fillStyle = "#f2f6f3";
    ctx.beginPath();
    ctx.moveTo(0, -10);
    ctx.lineTo(6, 8);
    ctx.lineTo(-6, 8);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
  ctx.fillStyle = "#d5e6db";
  ctx.fillRect(16, canvas.height - 28, 100 / metersPerPixel, 3);
  ctx.font = "12px sans-serif";
  ctx.fillText("100 m", 16, canvas.height - 34);
}

function fmt(n, digits) {
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

function renderReadout() {
  const pills = document.getElementById("pills");
  if (!state) {
    pills.innerHTML = "";
    return;
  }
  const viewClass = state.view === "oblique" ? "oblique" : state.view === "nadir" ? "nadir" : "";
  const srcLabel = {
    srt: "姿态来自录像",
    mqtt: "姿态来自 MQTT",
    ocr: "姿态来自 OCR",
    manual: "姿态来自手动",
    "ocr+mix": "姿态 OCR+补齐",
    "manual+mix": "姿态手动+其他",
    mixed: "姿态混合",
  };
  const att = state.wgs
    ? (srcLabel[state.telemetry_source] || `姿态 ${state.telemetry_source || ""}`)
    : "无姿态";
  pills.innerHTML = [
    `<span class="pill ${viewClass}">${state.view || "等待"}</span>`,
    `<span class="pill">${state.mode === "rtmp" ? "直播" : "录像"}</span>`,
    `<span class="pill ${state.wgs ? "nadir" : ""}">${att}</span>`,
    `<span class="pill">解码 ${state.decoder || "—"}</span>`,
  ].join("");
  const rows = [
    ["时间", `${fmt(state.time_s, 1)} / ${fmt(state.duration_s, 1)} s`],
    ["机型", state.aircraft_label || state.aircraft_id || "—"],
    ["纬度 WGS84", fmt(state.wgs && state.wgs.lat, 6)],
    ["经度 WGS84", fmt(state.wgs && state.wgs.lon, 6)],
    ["BD09 纬度", fmt(state.bd09 && state.bd09.lat, 6)],
    ["BD09 经度", fmt(state.bd09 && state.bd09.lon, 6)],
    ["坐标偏移", `${fmt(state.wgs_bd_offset_m, 1)} m`],
    ["相对高度", `${fmt(state.height_agl, 1)} m`],
    ["航向", `${fmt(state.yaw, 1)}°`],
    ["云台俯仰", `${fmt(state.pitch, 1)}°`],
    ["云台横滚", `${fmt(state.roll, 1)}°`],
    ["水平视场", `${fmt(state.hfov, 1)}°`],
    ["说明", state.message || ""],
  ];
  document.getElementById("telem").innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
  const miss = document.getElementById("missingHint");
  if (miss) {
    if (state.missing_fields && state.missing_fields.length) {
      miss.hidden = false;
      miss.textContent = `缺项：${state.missing_fields.join("、")}（可在下方手动锁定）`;
    } else {
      miss.hidden = true;
      miss.textContent = "";
    }
  }
  const aircraftPick = document.getElementById("aircraftPick");
  if (aircraftPick && state.aircraft_id && [...aircraftPick.options].some((o) => o.value === state.aircraft_id)) {
    aircraftPick.value = state.aircraft_id;
  }
  const aircraftHint = document.getElementById("aircraftHint");
  if (aircraftHint) {
    aircraftHint.textContent = state.ocr_enabled === false
      ? "直播 OCR 已关闭，可在设置页打开。"
      : (state.mode === "rtmp" ? "直播优先 OCR，缺项用手动或 MQTT。" : "");
  }
  const hud = document.getElementById("hud");
  if (state.wgs) {
    hud.innerHTML = `俯仰 ${fmt(state.pitch, 1)}°　航向 ${fmt(state.yaw, 1)}°　高度 ${fmt(state.height_agl, 0)} m<br>${fmt(state.wgs.lat, 6)}, ${fmt(state.wgs.lon, 6)}`;
  } else {
    hud.textContent = state.mode === "file"
      ? "这条样例录像没有读到姿态"
      : (state.message || "直播还没有完整姿态");
  }
  const ratio = state.diff_ratio || 0;
  document.getElementById("diffText").textContent = `相对灾前影像的变化像素约占视场 ${Math.round(ratio * 100)}%。`;
  const bias = `改正 东 ${fmt(state.bias_east_m, 1)} m，北 ${fmt(state.bias_north_m, 1)} m`;
  const picked = observed ? `；点选 ${observed.lat.toFixed(6)}, ${observed.lon.toFixed(6)}` : "";
  document.getElementById("biasText").textContent = bias + picked;
  document.getElementById("holdNadir").classList.toggle("on", !!(state.paused && state.view === "nadir"));
  const obliqueBtn = document.getElementById("holdOblique");
  const noOblique = state.pitch_max != null && state.pitch_max <= -75;
  obliqueBtn.disabled = !!noOblique;
  obliqueBtn.classList.toggle("on", !!(state.paused && state.view === "oblique"));
  const pick = document.getElementById("sourcePick");
  const hint = document.getElementById("sourceHint");
  if (pick && hint) {
    if (state.loading) {
      pick.disabled = true;
      hint.textContent = "正在解码这段录像，请稍等。";
    } else {
      pick.disabled = false;
      const want = state.mode === "rtmp" ? "rtmp" : `file:${state.clip_id || ""}`;
      if ([...pick.options].some((opt) => opt.value === want) && pick.value !== want) pick.value = want;
      hint.textContent = state.mode === "rtmp"
        ? "直播来自推流。推流工具请到「设置」复制 RTMP 推流地址（不要填 RTSP/预览页）。姿态可用 OCR / MQTT / 手动补齐。"
        : (state.clip_label || "选择一段已经放在本机的录像。");
    }
  }
  document.getElementById("play").classList.toggle("on", !state.paused);
  if (state.pre && !document.getElementById("trueLat").value) {
    document.getElementById("trueLat").value = state.pre.center_lat;
    document.getElementById("trueLon").value = state.pre.center_lon;
  }
}

let lastFrameAt = 0;
let lastDrapeAt = 0;

function refreshImages() {
  if (!state) return;
  const now = performance.now();
  if (now - lastFrameAt > 250) {
    lastFrameAt = now;
    frameImg.src = `/api/frame.jpg?s=${state.seq}`;
  }
  if (now - lastDrapeAt > 700) {
    lastDrapeAt = now;
    drapeImg.src = `/api/drape.png?s=${state.seq}`;
    if (document.getElementById("diff").checked) diffImg.src = `/api/diff.png?s=${state.seq}`;
  }
}

frameImg.onload = () => {};
drapeImg.onload = () => {
  if (mapStyle === "offline") draw();
  else updateGroundTexture();
};
preImg.onload = () => draw();
diffImg.onload = () => draw();
preImg.src = "/api/pre.png";

const events = new EventSource("/api/events");
events.onmessage = (ev) => {
  state = JSON.parse(ev.data);
  if (follow && state.wgs) center = { lat: state.wgs.lat, lon: state.wgs.lon };
  renderReadout();
  refreshImages();
  if (mapStyle === "offline") draw();
  else syncBaidu();
};

document.getElementById("opacity").oninput = draw;
document.getElementById("swipe").oninput = draw;
document.getElementById("showPre").onchange = () => { draw(); syncBaidu(true); };
document.getElementById("diff").onchange = () => { draw(); syncBaidu(true); };
document.getElementById("follow").onclick = () => {
  follow = !follow;
  document.getElementById("follow").classList.toggle("on", follow);
};
document.getElementById("play").onclick = () => postJson("/api/playback", { paused: false });
document.getElementById("holdNadir").onclick = () => {
  const t = state && Number.isFinite(state.nadir_t) ? state.nadir_t : 0;
  postJson("/api/seek", { t, pause: true });
};
document.getElementById("holdOblique").onclick = () => {
  const t = state && Number.isFinite(state.oblique_t) ? state.oblique_t : 0;
  postJson("/api/seek", { t, pause: true });
};
document.getElementById("styleStreet").onclick = () => applyMapStyle("street");
document.getElementById("styleSat").onclick = () => applyMapStyle("satellite");
document.getElementById("styleOffline").onclick = () => applyMapStyle("offline");
document.getElementById("sourcePick").onchange = async () => {
  const pick = document.getElementById("sourcePick");
  const hint = document.getElementById("sourceHint");
  follow = true;
  document.getElementById("follow").classList.add("on");
  pick.disabled = true;
  const live = pick.value === "rtmp";
  hint.textContent = live ? "正在切到直播…" : "正在解码这段录像，请稍等。";
  const body = live ? { mode: "rtmp" } : { mode: "file", id: pick.value.slice(5) };
  const res = await postJson("/api/source", body);
  if (!res.ok) {
    pick.disabled = false;
    hint.textContent = "这段录像打不开。";
    return;
  }
  reloadPreview();
};

function reloadPreview() {
  const frame = document.getElementById("webrtc");
  if (!frame) return;
  const base = frame.dataset.base || frame.src.split("?")[0];
  frame.dataset.base = base;
  window.setTimeout(() => {
    frame.src = `${base}?t=${Date.now()}`;
  }, 800);
}

function postJson(url, body) {
  return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

async function loadSources() {
  const data = await (await fetch("/api/sources")).json();
  const pick = document.getElementById("sourcePick");
  pick.innerHTML = "";
  for (const clip of data.clips) {
    if (!clip.ready) continue;
    const opt = document.createElement("option");
    opt.value = `file:${clip.id}`;
    opt.textContent = clip.label;
    pick.appendChild(opt);
  }
  const live = document.createElement("option");
  live.value = "rtmp";
  live.textContent = "RTMP 直播";
  pick.appendChild(live);
  const want = data.mode === "rtmp" ? "rtmp" : `file:${data.active || ""}`;
  if ([...pick.options].some((opt) => opt.value === want)) pick.value = want;
}

async function loadAircraft() {
  const data = await (await fetch("/api/aircraft")).json();
  const pick = document.getElementById("aircraftPick");
  if (!pick) return;
  pick.innerHTML = "";
  for (const model of data.models || []) {
    const opt = document.createElement("option");
    opt.value = model.id;
    opt.textContent = model.label;
    pick.appendChild(opt);
  }
  if (data.active && [...pick.options].some((o) => o.value === data.active)) pick.value = data.active;
}

document.getElementById("aircraftPick")?.addEventListener("change", async () => {
  const pick = document.getElementById("aircraftPick");
  await postJson("/api/aircraft", { id: pick.value });
});

document.getElementById("applyOverride")?.addEventListener("click", async () => {
  const values = {};
  const locked = [];
  const miss = document.getElementById("missingHint");
  let err = "";
  document.querySelectorAll("#overrideGrid [data-field]").forEach((input) => {
    const key = input.getAttribute("data-field");
    const lock = document.querySelector(`#overrideGrid [data-lock="${key}"]`);
    const raw = (input.value || "").trim();
    const num = raw === "" ? NaN : Number(raw);
    if (lock && lock.checked) {
      if (raw === "" || Number.isNaN(num)) {
        err = `勾选锁定后必须填写数值（${key}）`;
        return;
      }
      if ((key === "hfov" || key === "vfov") && num <= 1) {
        err = "水平/垂直视场需大于 1°，或不要勾选锁定以使用机型默认值";
        return;
      }
      values[key] = num;
      locked.push(key);
    } else if (raw !== "" && !Number.isNaN(num)) {
      values[key] = num;
    }
  });
  if (err) {
    if (miss) {
      miss.hidden = false;
      miss.textContent = err;
    }
    return;
  }
  if (!locked.length) {
    if (miss) {
      miss.hidden = false;
      miss.textContent = "请至少勾选并填写一项后再点「应用锁定」。俯仰向下为负（垂直约 -90）。";
    }
    return;
  }
  const res = await postJson("/api/telemetry_override", { values, locked });
  if (!res.ok) {
    if (miss) {
      miss.hidden = false;
      miss.textContent = "应用锁定失败，请重试。";
    }
    return;
  }
  if (miss) {
    miss.hidden = false;
    miss.textContent = `已锁定 ${locked.join("、")}。俯仰向下请填负数（如 -90）；视场可不锁，用机型默认。`;
  }
});

document.getElementById("clearOverride")?.addEventListener("click", async () => {
  await postJson("/api/telemetry_override", { clear: true });
  document.querySelectorAll("#overrideGrid [data-field]").forEach((input) => { input.value = ""; });
  document.querySelectorAll("#overrideGrid [data-lock]").forEach((box) => { box.checked = false; });
});

let mapStyle = localStorage.getItem("mapStyle") || "street";
let lastPan = 0;
let lastGround = 0;

function applyMapStyle(style) {
  mapStyle = style;
  localStorage.setItem("mapStyle", style);
  document.getElementById("styleStreet").classList.toggle("on", style === "street");
  document.getElementById("styleSat").classList.toggle("on", style === "satellite");
  document.getElementById("styleOffline").classList.toggle("on", style === "offline");
  const holder = document.getElementById("bmap");
  if (style === "offline" || !baidu) {
    holder.hidden = style !== "offline" ? holder.hidden : true;
    if (style === "offline") {
      holder.hidden = true;
      canvas.hidden = false;
      document.getElementById("legend").textContent = "离线卫星 · 本机缓存 · © Esri";
      resize();
      return;
    }
  }
  if (!baidu) {
    document.getElementById("legend").textContent = "百度地图还在加载";
    return;
  }
  canvas.hidden = true;
  holder.hidden = false;
  const type = style === "satellite"
    ? (window.BMAP_SATELLITE_MAP || window.BMAP_EARTH_MAP)
    : window.BMAP_NORMAL_MAP;
  if (type) baidu.setMapType(type);
  document.getElementById("legend").textContent = style === "satellite" ? "百度卫星图" : "百度平面图";
}

canvas.addEventListener("wheel", (ev) => {
  ev.preventDefault();
  metersPerPixel *= ev.deltaY > 0 ? 1.12 : 0.89;
  metersPerPixel = Math.min(30, Math.max(0.4, metersPerPixel));
  follow = false;
  document.getElementById("follow").classList.remove("on");
  draw();
}, { passive: false });

let drag = null;
canvas.addEventListener("mousedown", (ev) => {
  drag = { x: ev.clientX, y: ev.clientY, lat: center.lat, lon: center.lon };
});
window.addEventListener("mousemove", (ev) => {
  if (!drag) return;
  const dx = ev.clientX - drag.x;
  const dy = ev.clientY - drag.y;
  center = {
    lon: drag.lon - (dx * metersPerPixel) / mPerDegLon(drag.lat),
    lat: drag.lat + (dy * metersPerPixel) / M_PER_DEG_LAT,
  };
  follow = false;
  document.getElementById("follow").classList.remove("on");
  draw();
});
window.addEventListener("mouseup", () => { drag = null; });

canvas.addEventListener("click", (ev) => {
  if (!pickMode) return;
  const rect = canvas.getBoundingClientRect();
  observed = unproject(ev.clientX - rect.left, ev.clientY - rect.top);
  pickMode = false;
  document.getElementById("pick").classList.remove("on");
  renderReadout();
});

document.getElementById("pick").onclick = () => {
  pickMode = !pickMode;
  document.getElementById("pick").classList.toggle("on", pickMode);
};
document.getElementById("applyBias").onclick = async () => {
  if (!observed) return;
  await fetch("/api/calibrate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      true_lat: Number(document.getElementById("trueLat").value),
      true_lon: Number(document.getElementById("trueLon").value),
      observed_lat: observed.lat,
      observed_lon: observed.lon,
      observed_crs: baidu ? "bd09" : "wgs84",
    }),
  });
};
document.getElementById("resetBias").onclick = () => {
  observed = null;
  fetch("/api/calibrate/reset", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
};

function loadBaidu(ak) {
  if (!ak || window.BMapGL || document.getElementById("bmap-script")) return;
  const script = document.createElement("script");
  script.id = "bmap-script";
  script.src = `https://api.map.baidu.com/api?v=1.0&type=webgl&ak=${encodeURIComponent(ak)}&callback=onBMapReady`;
  document.head.appendChild(script);
}

async function boot() {
  const cfg = await (await fetch("/api/config")).json();
  document.getElementById("webrtc").src = cfg.webrtc_page;
  document.getElementById("webrtcHint").textContent = cfg.webrtc_page;
  const ak = (cfg.baidu_ak || localStorage.getItem("baidu_ak") || "").trim();
  if (ak) {
    document.getElementById("ak").value = ak;
    localStorage.setItem("baidu_ak", ak);
    loadBaidu(ak);
  }
  resize();
  await loadSources();
  await loadAircraft();
}
boot();

function loadImageToCanvas(img, target) {
  if (!img.complete || !img.naturalWidth) return false;
  target.width = img.naturalWidth;
  target.height = img.naturalHeight;
  target.getContext("2d").clearRect(0, 0, target.width, target.height);
  target.getContext("2d").drawImage(img, 0, 0);
  return true;
}

function syncBaidu(force) {
  if (mapStyle === "offline" || !baidu || !state || !window.BMapGL) return;
  const B = window.BMapGL;
  const now = performance.now();
  if (state.bd09 && follow && now - lastPan > 800) {
    lastPan = now;
    baidu.panTo(new B.Point(state.bd09.lon, state.bd09.lat));
  }
  if (state.track_wgs && state.track_wgs.length > 1) {
    const pts = state.track_wgs.map((p) => {
      const [lon, lat] = wgsToBd(p.lon, p.lat);
      return new B.Point(lon, lat);
    });
    if (baiduOverlays.line && baiduOverlays.line.setPath) baiduOverlays.line.setPath(pts);
    else {
      if (baiduOverlays.line) baidu.removeOverlay(baiduOverlays.line);
      baiduOverlays.line = new B.Polyline(pts, { strokeColor: "#8fd0ff", strokeWeight: 3 });
      baidu.addOverlay(baiduOverlays.line);
    }
  }
  if (state.bd09) {
    const craft = new B.Point(state.bd09.lon, state.bd09.lat);
    if (baiduOverlays.craft && baiduOverlays.craft.setPosition) baiduOverlays.craft.setPosition(craft);
    else {
      if (baiduOverlays.craft) baidu.removeOverlay(baiduOverlays.craft);
      baiduOverlays.craft = new B.Marker(craft);
      baidu.addOverlay(baiduOverlays.craft);
    }
  }
  if (state.corners_bd && state.corners_bd.length === 4) {
    const color = state.view === "oblique" ? "#e0b15a" : "#7dcea0";
    const pts = state.corners_bd.map((p) => new B.Point(p.lon, p.lat));
    if (baiduOverlays.poly && baiduOverlays.polyView === state.view && baiduOverlays.poly.setPath) {
      baiduOverlays.poly.setPath(pts);
    } else {
      if (baiduOverlays.poly) baidu.removeOverlay(baiduOverlays.poly);
      baiduOverlays.poly = new B.Polygon(pts, {
        strokeColor: color, strokeWeight: 1, strokeOpacity: 0.28, fillColor: color, fillOpacity: 0.04,
      });
      baiduOverlays.polyView = state.view;
      baidu.addOverlay(baiduOverlays.poly);
    }
  } else if (baiduOverlays.poly) {
    baidu.removeOverlay(baiduOverlays.poly);
    baiduOverlays.poly = null;
  }
  if (force || now - lastGround > 700) updateGroundTexture();
}

function updateGroundTexture() {
  if (mapStyle === "offline" || !baidu || !window.BMapGL || !state) return;
  lastGround = performance.now();
  const B = window.BMapGL;
  refreshGround(B, "drape", drapeCanvas, drapeImg, state.bounds_bd, Number(document.getElementById("opacity").value) / 100);
  if (document.getElementById("showPre").checked && state.pre) {
    refreshGround(B, "pre", preCanvas, preImg, wgsBoundsToBd(state.pre), 0.9);
  } else if (baiduOverlays.pre) {
    baidu.removeOverlay(baiduOverlays.pre);
    baiduOverlays.pre = null;
  }
  if (document.getElementById("diff").checked) refreshGround(B, "diff", diffCanvas, diffImg, state.bounds_bd, 0.7);
  else if (baiduOverlays.diff) {
    baidu.removeOverlay(baiduOverlays.diff);
    baiduOverlays.diff = null;
  }
}

function refreshGround(B, key, target, img, bounds, opacity) {
  if (!bounds || !loadImageToCanvas(img, target)) return;
  if (baiduOverlays[key]) baidu.removeOverlay(baiduOverlays[key]);
  const sw = new B.Point(bounds.west, bounds.south);
  const ne = new B.Point(bounds.east, bounds.north);
  baiduOverlays[key] = new B.GroundOverlay(new B.Bounds(sw, ne), { type: "canvas", url: target, opacity });
  baidu.addOverlay(baiduOverlays[key]);
}

function wgsToBd(lon, lat) {
  if (!state || !state.wgs || !state.bd09) return [lon, lat];
  return [lon + (state.bd09.lon - state.wgs.lon), lat + (state.bd09.lat - state.wgs.lat)];
}

function wgsBoundsToBd(b) {
  const [w, s] = wgsToBd(b.west, b.south);
  const [e, n] = wgsToBd(b.east, b.north);
  return { west: w, south: s, east: e, north: n };
}

document.getElementById("useAk").onclick = () => {
  const ak = document.getElementById("ak").value.trim();
  if (!ak) return;
  localStorage.setItem("baidu_ak", ak);
  loadBaidu(ak);
};

window.onBMapReady = () => {
  const holder = document.getElementById("bmap");
  baidu = new window.BMapGL.Map(holder);
  const lat = state && state.bd09 ? state.bd09.lat : 30.28;
  const lon = state && state.bd09 ? state.bd09.lon : 120.17;
  baidu.centerAndZoom(new window.BMapGL.Point(lon, lat), 17);
  baidu.enableScrollWheelZoom(true);
  applyMapStyle(mapStyle);
  baidu.addEventListener("click", (ev) => {
    if (!pickMode || !ev.latlng) return;
    observed = { lat: ev.latlng.lat, lon: ev.latlng.lng };
    pickMode = false;
    document.getElementById("pick").classList.remove("on");
    renderReadout();
  });
};

