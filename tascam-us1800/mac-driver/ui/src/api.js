const API = "/api";

export async function getStatus() {
  const r = await fetch(`${API}/status`);
  return r.json();
}

/** Same bullets prepended to capture.log on each passthrough start. */
export async function getAudioTips() {
  const r = await fetch(`${API}/audio-tips`);
  return r.json();
}

/** Passthrough POST defaults + TASCAM_* tuning reference (from passthrough_config). */
export async function getPassthroughConfig() {
  const r = await fetch(`${API}/passthrough-config`);
  return r.json();
}

export async function getSampleRates() {
  const r = await fetch(`${API}/sample-rates`);
  return r.json();
}

export async function startRecording(payload = {}) {
  const r = await fetch(`${API}/record/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return r.json();
}

export async function stopRecording() {
  const r = await fetch(`${API}/record/stop`, { method: "POST" });
  return r.json();
}

export async function getRecordings() {
  const r = await fetch(`${API}/recordings`);
  return r.json();
}

export async function deleteRecording(filename) {
  const r = await fetch(`${API}/recordings/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
  return r.json();
}

export async function stopStream() {
  const r = await fetch(`${API}/stop`, { method: "POST" });
  return r.json();
}

/** @param {number | { volume?: number, sample_rate?: number, channels?: number }} payload */
export async function testTone(payload = 0.1) {
  const body =
    typeof payload === "number"
      ? { volume: payload }
      : { volume: 0.1, ...payload };
  const r = await fetch(`${API}/test-tone`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

/** @param {number | Record<string, unknown>} payload — number = volume only; object = { volume, ...labFields } */
export async function startPassthrough(payload = { volume: 0.01 }) {
  const body =
    typeof payload === "number"
      ? { volume: payload }
      : { volume: 0.01, ...(payload || {}) };
  const r = await fetch(`${API}/passthrough`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return r.json();
}

const WS_URL =
  import.meta.env.DEV
    ? `ws://${window.location.hostname}:8420/ws/stats`
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws/stats`;

export function connectWS(onMessage) {
  let stopped = false;
  let ws = null;
  let timer = null;

  function open() {
    if (stopped) return;
    ws = new WebSocket(WS_URL);
    ws.onmessage = (e) => {
      let data;
      try {
        data = JSON.parse(e.data);
      } catch {
        return;
      }
      if (data.ack != null) return;
      onMessage(data);
    };
    ws.onclose = () => {
      if (!stopped) timer = setTimeout(open, 2000);
    };
  }

  open();

  return {
    close() {
      stopped = true;
      clearTimeout(timer);
      ws?.close();
    },
    /** Live software gain while routing macOS audio (passthrough). 0–1 */
    sendVolume(level) {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ cmd: "set_volume", volume: level }));
      }
    },
  };
}
