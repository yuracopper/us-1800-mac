/** Single localStorage blob for panel settings. */

export const UI_PREFS_KEY = "tascam-ui-prefs-v1";

/** Volume only; USB is always 4ch (see passthrough_config). */

export const STREAM_DEFAULTS = {
  /** Slider 0–100 */
  volumePercent: 75,
};

export function loadUiPrefs() {
  try {
    const raw = localStorage.getItem(UI_PREFS_KEY);
    let parsed = raw ? JSON.parse(raw) : {};

    const stream = {
      ...STREAM_DEFAULTS,
      ...(parsed.stream || {}),
    };
    stream.volumePercent = Math.max(
      0,
      Math.min(100, Number(stream.volumePercent) || STREAM_DEFAULTS.volumePercent)
    );

    return { stream };
  } catch {
    return { stream: { ...STREAM_DEFAULTS } };
  }
}

export function saveUiPrefs(prefs) {
  try {
    localStorage.setItem(UI_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* quota */
  }
}
