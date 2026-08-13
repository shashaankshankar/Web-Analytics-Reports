export const AGGREGATION = Object.freeze({ SUM: 'SUM', RATIO: 'RATIO', WINDOWED_UNIQUE: 'WINDOWED_UNIQUE', SNAPSHOT: 'SNAPSHOT', FUNNEL: 'FUNNEL', EXTERNAL: 'EXTERNAL' });
export const FRESHNESS = Object.freeze({ REALTIME: 'realtime', PROVISIONAL: 'provisional', RECONCILING: 'reconciling', STABLE: 'stable' });
export const QUALITY = Object.freeze({ OK: 'ok', WARNING: 'warning', RESTRICTED: 'restricted', INCOMPLETE: 'incomplete', FAILED: 'failed', DISABLED: 'disabled' });
export const PERIODS = Object.freeze(['7d', '28d', 'this_month', 'last_month', '90d']);

export function assertSupportedPeriod(period) {
  if (!PERIODS.includes(period)) throw new Error(`unsupported_period:${period}`);
  return period;
}

export function freshnessForAgeDays(ageDays) {
  if (ageDays <= 0) return FRESHNESS.REALTIME;
  if (ageDays === 1) return FRESHNESS.PROVISIONAL;
  if (ageDays <= 14) return FRESHNESS.RECONCILING;
  return FRESHNESS.STABLE;
}

export function calculateMetric({ aggregation, numerator, denominator, value }) {
  if (aggregation === AGGREGATION.RATIO) return denominator ? numerator / denominator : null;
  if ([AGGREGATION.SUM, AGGREGATION.WINDOWED_UNIQUE, AGGREGATION.SNAPSHOT, AGGREGATION.FUNNEL, AGGREGATION.EXTERNAL].includes(aggregation)) return value;
  throw new Error(`unsupported_aggregation:${aggregation}`);
}
