// Sounds for whoever is holding the calibration card in front of a camera, so they can
// follow along without watching the screen:
//   start:   one short blip when a device finds the card and starts calibrating (hold still);
//   done:    a rising two-note chime when it finishes cleanly;
//   problem: a low falling buzz when something is wrong (card too far, glare, card moved out
//            of view, camera error, or a calibration that finished with a warning).
// Works from the per-device calibration status the page polls (Pi cameras and phones).
import { useEffect, useRef } from "preact/hooks";

let audio = null;
// Browsers only allow sound after a click: open the audio context on the first one.
document.addEventListener("click", () => {
  try { audio = audio || new AudioContext(); audio.resume(); } catch (error) { /* no audio */ }
}, { once: true });

/**
 * Play a short sequence of notes.
 * @param {number[]} frequencies  one note each, in Hz
 * @param {{ type?: OscillatorType, step?: number, length?: number, volume?: number }} [options]
 */
function playNotes(frequencies, { type = "sine", step = 0.15, length = 0.4, volume = 0.25 } = {}) {
  if (!audio) return;
  frequencies.forEach((frequency, i) => {
    const oscillator = audio.createOscillator(), gain = audio.createGain(), at = audio.currentTime + i * step;
    oscillator.type = type;
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(volume, at);
    gain.gain.exponentialRampToValueAtTime(0.001, at + length);
    oscillator.connect(gain).connect(audio.destination);
    oscillator.start(at);
    oscillator.stop(at + length);
  });
}

const SOUNDS = {
  start: () => playNotes([660], { length: 0.25 }),
  done: () => playNotes([880, 1320]),
  problem: () => playNotes([392, 262], { type: "triangle", step: 0.22, length: 0.3, volume: 0.35 }),
};

/** Every device's calibration entry, keyed so Pis and phones can't clash. */
function devicesOf(calibration) {
  const out = {};
  Object.entries(calibration.devices || {}).forEach(([name, entry]) => { out["cam:" + name] = entry; });
  Object.entries(calibration.phones || {}).forEach(([name, entry]) => { out["phone:" + name] = entry; });
  return out;
}

/**
 * Which sound a device's change between two polls calls for, or null.
 * A finished calibration shows as a new `at` (the record's time); one that finished with a
 * warning carries a message. A problem otherwise is a new message while not calibrating.
 */
export function soundFor(before, now) {
  if (now.at && now.at !== before.at) return now.message ? "problem" : "done";
  if (now.status === "calibrating" && before.status !== "calibrating") return "start";
  const changed = now.status !== before.status || now.message !== before.message;
  if (changed && now.message && now.status !== "calibrating" && now.status !== "calibrated") return "problem";
  return null;
}

export function useCalibrationChime(status) {
  const last = useRef(null);       // device key -> { status, message, at } from the previous poll
  const calibration = status && status.calibration;
  useEffect(() => {
    if (!calibration) return;
    const now = devicesOf(calibration);
    if (last.current) {
      // Several devices changing in the same poll: one sound, the most important one.
      const sounds = new Set(Object.entries(now)
        .filter(([key]) => last.current[key])          // new devices (a phone joining) stay quiet
        .map(([key, entry]) => soundFor(last.current[key], entry)));
      const pick = ["problem", "done", "start"].find(name => sounds.has(name));
      if (pick) SOUNDS[pick]();
    }
    last.current = now;
  }, [calibration]);
}
