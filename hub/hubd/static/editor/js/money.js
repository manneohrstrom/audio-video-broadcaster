// Costs for people: Google prices in US dollars; a rough pound figure beside it.
const POUNDS_PER_DOLLAR = 0.79;          // for the "≈ £" only (2026-11-28)

/** "$0.089 (≈ 7p)", or "price not known" for a model missing from the hub's price table. */
export function money(dollars) {
  if (dollars == null) return "price not known";
  const pounds = dollars * POUNDS_PER_DOLLAR;
  const rough = pounds < 1 ? `${Math.max(1, Math.round(pounds * 100))}p` : `£${pounds.toFixed(2)}`;
  return `$${dollars.toFixed(dollars < 1 ? 3 : 2)} (≈ ${rough})`;
}
