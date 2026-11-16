// One requestAnimationFrame loop for the whole editor. Canvases and live labels register
// a callback; it runs once per frame, in registration order.
const callbacks = new Set();
let running = false;

function frame(now) {
  for (const callback of callbacks) callback(now);
  if (callbacks.size) requestAnimationFrame(frame); else running = false;
}

/** Run `callback(now)` every frame until the returned function is called. */
export function everyFrame(callback) {
  callbacks.add(callback);
  if (!running) { running = true; requestAnimationFrame(frame); }
  return () => callbacks.delete(callback);
}
