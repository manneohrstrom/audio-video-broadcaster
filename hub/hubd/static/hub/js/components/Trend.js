// Small charts of the last 30 minutes (user, 2026-11-16/17), one point per 10 s from
// /api/history. uPlot (vendored and pinned: /uPlot.iife.min.js 1.6.30) draws each one:
// a thin line with a soft fill on a FIXED scale (so normal wiggles stay small), a dot on
// the latest point, and on hover the value and how long ago. Gaps (offline) break the line.
import { useEffect, useRef, useState } from "preact/hooks";
import { html } from "shared/html.js";

const DEFINITIONS = {
  hub: [
    { key: "temp_c", label: "temperature", unit: " °C", min: 30, max: 85, warnAbove: 75, digits: 1 },
    { key: "mem_available_mb", label: "memory free", unit: " MB", min: 0, max: "total", warnBelow: 150, digits: 0 },
    { key: "swap_used_mb", label: "swap used", unit: " MB", min: 0, max: 1000, warnAbove: 500, digits: 0 },
    { key: "load", label: "CPU load", unit: "", min: 0, max: 4, warnAbove: 3.5, digits: 2 },
  ],
  camera: [
    { key: "temp_c", label: "temperature", unit: " °C", min: 30, max: 85, warnAbove: 70, digits: 1 },
    { key: "wifi_dbm", label: "Wi-Fi", unit: " dBm", min: -90, max: -40, warnBelow: -65, digits: 0 },
  ],
};

/** Line and fill colours from the tokens: steel blue normally, the warning yellow when out of range. */
function chartColours(warn) {
  const style = getComputedStyle(document.documentElement);
  const colour = style.getPropertyValue(warn ? "--color-warning" : "--color-camera-2").trim();
  return [colour, colour + "2a"];
}

const format = (definition, value) => (value == null ? "–" : value.toFixed(definition.digits) + definition.unit);
const ago = seconds => (seconds < 60 ? "now" : `${Math.round(seconds / 60)} min ago`);

function Trend({ definition, values, interval, points, max }) {
  const placeRef = useRef(null);
  const chartRef = useRef(null);
  const [hovered, setHovered] = useState(null);         // { value, ago } while the pointer is on the chart
  const latestIndex = values.reduce((last, value, i) => (value === null ? last : i), -1);
  const latest = latestIndex >= 0 ? values[latestIndex] : null;
  const warn = latest !== null && ((definition.warnAbove != null && latest > definition.warnAbove) ||
                                   (definition.warnBelow != null && latest < definition.warnBelow));
  const xs = values.map((_, i) => -(values.length - 1 - i) * interval);

  // A new chart when its colour or scale changes; otherwise only new data.
  useEffect(() => {
    if (typeof uPlot === "undefined" || !placeRef.current) return undefined;
    const [stroke, fill] = chartColours(warn);
    const chart = new uPlot({
      width: Math.max(80, placeRef.current.clientWidth || 110), height: 34, legend: { show: false },
      cursor: { y: false, drag: { x: false, y: false }, points: { size: 6, fill: stroke } },
      select: { show: false },
      // A fixed time axis: the right edge is now, the left 30 minutes ago, so a short
      // history (after a restart) draws on the right instead of stretching.
      scales: { x: { time: false, range: [-points * interval, 0] }, y: { range: [definition.min, max] } },
      axes: [{ show: false }, { show: false }],
      series: [{}, { stroke, fill, width: 1.5, spanGaps: false, points: { show: true, size: 5, fill: stroke, filter: u => {
        const ys = u.data[1];
        for (let i = ys.length - 1; i >= 0; i--) if (ys[i] != null) return [i];
        return null;
      } } }],
      hooks: { setCursor: [u => {
        const i = u.cursor.idx;
        setHovered(i == null || u.data[1][i] == null ? null : { value: u.data[1][i], ago: ago(-u.data[0][i]) });
      }] },
    }, [xs, values], placeRef.current);
    chartRef.current = chart;
    return () => { chart.destroy(); chartRef.current = null; };
  }, [warn, max, points, interval]);

  useEffect(() => { if (chartRef.current) chartRef.current.setData([xs, values]); }, [values]);

  return html`
    <div class="trend">
      <div class="trend-head"><span>${definition.label}</span>
        <span class=${"trend-value" + (warn && !hovered ? " warn" : "")}>
          ${hovered ? `${format(definition, hovered.value)} · ${hovered.ago}` : format(definition, latest)}</span></div>
      <div class="trend-chart" ref=${placeRef}></div>
    </div>`;
}

/** All of one device's charts. scope "hub" or "camera"; id the camera's name. */
export function Trends({ scope, id, history, totalMemory }) {
  if (!history) return null;
  const series = scope === "hub" ? history.hub || {} : (history.cameras || {})[id] || {};
  return html`
    <div class="trends">
      ${DEFINITIONS[scope].map(definition => html`
        <${Trend} key=${definition.key} definition=${definition}
                  values=${(series[definition.key] || []).map(v => (typeof v === "number" ? v : null))}
                  interval=${history.interval_s || 10} points=${history.points || 180}
                  max=${definition.max === "total" ? totalMemory || 1000 : definition.max} />`)}
    </div>`;
}
