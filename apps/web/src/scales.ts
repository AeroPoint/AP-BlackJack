/**
 * The two absolute money scales, in the standalone chart page's bands.
 *
 * These are presentation thresholds, not mathematics: they decide which shade
 * a number gets, never the number. They are copied from
 * `scripts/chart_page/page-body.html` (`LEAK_BANDS`, `COST_BANDS`) so a square
 * that reads "orange" there reads orange here. If you move a threshold, move it
 * in both.
 *
 * Both are absolute rather than normalised to the worst square on screen, for
 * the reason the chart page gives: a normalised ramp recolours every square
 * whenever one value moves, and makes a cheap comparison look as alarming as an
 * expensive one. See markdown/DecisionImportance.md#two-questions-one-square for
 * why there are two scales with unlike palettes rather than one.
 */

/** Units per 100 rounds. The leak palette (warm sand to red), class `lk{n}`. */
export const PER_100_BANDS = [0.0005, 0.002, 0.005, 0.01, 0.02] as const;
export const PER_100_TICKS = ["0", "0.0005", "0.002", "0.005", "0.01", "0.02+"] as const;

/** Fraction of a bet, per occurrence. The cost palette (cool blue to magenta), `cg{n}`. */
export const EACH_BANDS = [0.005, 0.02, 0.08, 0.2, 0.5, 1.0] as const;
export const EACH_TICKS = ["0", "0.5%", "2%", "8%", "20%", "50%", "100%"] as const;

/** Index of the band `value` falls in: 0 below the first threshold. */
export function bandOf(bands: readonly number[], value: number): number {
  let band = 0;
  while (band < bands.length && value >= (bands[band] ?? Infinity)) band += 1;
  return band;
}
