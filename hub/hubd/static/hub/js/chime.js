// A short two-note chime when a camera finishes calibrating, for whoever is holding the
// card in front of it (calibration.seq goes up by one per finished calibration).
import { useEffect, useRef } from "preact/hooks";

let audio = null;
// Browsers only allow sound after a click: open the audio context on the first one.
document.addEventListener("click", () => {
  try { audio = audio || new AudioContext(); audio.resume(); } catch (error) { /* no audio */ }
}, { once: true });

function playChime() {
  if (!audio) return;
  [880, 1320].forEach((frequency, i) => {
    const oscillator = audio.createOscillator(), gain = audio.createGain(), at = audio.currentTime + i * 0.15;
    oscillator.frequency.value = frequency;
    gain.gain.setValueAtTime(0.25, at);
    gain.gain.exponentialRampToValueAtTime(0.001, at + 0.4);
    oscillator.connect(gain).connect(audio.destination);
    oscillator.start(at);
    oscillator.stop(at + 0.4);
  });
}

export function useCalibrationChime(status) {
  const lastSeq = useRef(null);
  const seq = status && (status.calibration || {}).seq;
  useEffect(() => {
    if (seq == null) return;
    if (lastSeq.current !== null && seq > lastSeq.current) playChime();
    lastSeq.current = seq;
  }, [seq]);
}
