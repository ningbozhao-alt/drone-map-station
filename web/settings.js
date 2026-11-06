let cachedLanRtmp = "";

async function loadSettings() {
  const data = await (await fetch("/api/settings")).json();
  document.getElementById("rtmpIngest").value = data.stream.rtmp_ingest;
  document.getElementById("rtspUrl").value = data.stream.rtsp_url;
  document.getElementById("webrtcPage").value = data.stream.webrtc_page;
  cachedLanRtmp = data.stream.rtmp_ingest_lan || data.stream.rtmp_ingest;
  const localHint = document.getElementById("rtmpLocalHint");
  if (localHint) {
    localHint.textContent =
      `同机推流也可用：${data.stream.rtmp_ingest_localhost}（仅本机推流工具填这个；外机请用上方带板子 IP 的地址）`;
  }

  document.getElementById("mqttEnabled").checked = !!data.mqtt.mqtt_enabled;
  document.getElementById("mqttHost").value = data.mqtt.mqtt_host;
  document.getElementById("mqttPort").value = data.mqtt.mqtt_port;
  document.getElementById("mqttTopic").value = data.mqtt.mqtt_topic;

  const pick = document.getElementById("aircraftId");
  pick.innerHTML = "";
  for (const model of data.aircraft.models || []) {
    const opt = document.createElement("option");
    opt.value = model.id;
    opt.textContent = model.label;
    pick.appendChild(opt);
  }
  pick.value = data.aircraft.aircraft_id;
  document.getElementById("ocrEnabled").checked = !!data.aircraft.ocr_enabled;
  document.getElementById("hfov").value = data.aircraft.hfov_deg;
  document.getElementById("vfov").value = data.aircraft.vfov_deg;
  document.getElementById("aircraftNotes").textContent = data.aircraft.notes || "";
  return data;
}

async function putSection(section, body, msgId) {
  const msg = document.getElementById(msgId);
  msg.textContent = "正在保存…";
  const res = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ section, ...body }),
  });
  const data = await res.json();
  if (!data.ok) {
    msg.textContent = data.error || "保存失败";
    return;
  }
  msg.textContent = "已保存并应用。";
  await loadSettings();
}

document.getElementById("saveStream").onclick = () => putSection("stream", {
  rtmp_ingest: document.getElementById("rtmpIngest").value.trim(),
  rtsp_url: document.getElementById("rtspUrl").value.trim(),
  webrtc_page: document.getElementById("webrtcPage").value.trim(),
}, "streamMsg");

document.getElementById("copyRtmp").onclick = async () => {
  const text = document.getElementById("rtmpIngest").value.trim();
  try {
    await navigator.clipboard.writeText(text);
    document.getElementById("streamMsg").textContent = "已复制，可粘贴到桌面推流工具的 RTMP 地址框。";
  } catch (_) {
    document.getElementById("streamMsg").textContent = text;
  }
};

document.getElementById("useLocalRtmp").onclick = () => {
  const cur = document.getElementById("rtmpIngest").value.trim();
  const path = cur.replace(/^rtmp:\/\/[^/]+/i, "") || "/live";
  document.getElementById("rtmpIngest").value = `rtmp://127.0.0.1:1935${path.startsWith("/") ? path : `/${path}`}`;
  document.getElementById("streamMsg").textContent = "已改为本机地址，记得点「保存并应用」。";
};

document.getElementById("useLanRtmp").onclick = () => {
  if (cachedLanRtmp) {
    document.getElementById("rtmpIngest").value = cachedLanRtmp;
    document.getElementById("streamMsg").textContent = "已改为当前板子 IP 地址，记得点「保存并应用」。";
  }
};

document.getElementById("saveMqtt").onclick = () => putSection("mqtt", {
  mqtt_enabled: document.getElementById("mqttEnabled").checked,
  mqtt_host: document.getElementById("mqttHost").value.trim(),
  mqtt_port: Number(document.getElementById("mqttPort").value),
  mqtt_topic: document.getElementById("mqttTopic").value.trim(),
}, "mqttMsg");

document.getElementById("saveAircraft").onclick = () => putSection("aircraft", {
  aircraft_id: document.getElementById("aircraftId").value,
  ocr_enabled: document.getElementById("ocrEnabled").checked,
  hfov_deg: Number(document.getElementById("hfov").value),
  vfov_deg: Number(document.getElementById("vfov").value),
}, "aircraftMsg");

document.getElementById("aircraftId").onchange = () => {
  document.getElementById("aircraftNotes").textContent = "保存后显示该机型说明。";
};

loadSettings().catch((err) => {
  document.getElementById("streamMsg").textContent = String(err);
});
