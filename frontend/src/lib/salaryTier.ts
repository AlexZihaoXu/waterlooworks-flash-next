import type { components } from './api.generated';
import { scalePay, type FxRates } from './hourlyPay';

export type SalaryTierRate = Pick<
  components['schemas']['CompensationRate'],
  | 'bound_type'
  | 'minimum'
  | 'maximum'
  | 'currency'
  | 'period'
  | 'hours_per_week'
>;
export type SalaryTier =
  'neutral' | 'green' | 'blue' | 'gold' | 'purple' | 'red' | 'top';

/** Highest CAD/hour equivalent; unspecified currency defaults to CAD for styling only. */
export function salaryTier(
  rates: readonly SalaryTierRate[],
  fx: FxRates | null = null,
  needsReview = false,
): SalaryTier {
  if (needsReview || !rates.length) return 'neutral';
  let highest = 0n;
  for (const rate of rates) {
    const maximum = rate.bound_type === 'fixed' ? rate.minimum : rate.maximum;
    if (
      rate.bound_type === 'minimum' ||
      maximum === null ||
      !/^\d+(\.\d+)?$/.test(maximum)
    )
      return 'neutral';
    const currency =
      rate.currency === 'CAD' || rate.currency === null
        ? '1'
        : rate.currency
          ? fx?.cad_per_unit[rate.currency]
          : null;
    if (!currency || !/^\d+(\.\d+)?$/.test(currency) || Number(currency) <= 0)
      return 'neutral';
    const multiply = [currency],
      divide: string[] = [];
    const hours = rate.hours_per_week ?? '40';
    if (
      rate.period !== 'hour' &&
      (!/^\d+(\.\d+)?$/.test(hours) || Number(hours) <= 0)
    )
      return 'neutral';
    if (rate.period === 'week') divide.push(hours);
    else if (rate.period === 'month') {
      multiply.push('12');
      divide.push('52', hours);
    } else if (rate.period === 'year') divide.push('52', hours);
    else if (rate.period !== 'hour') return 'neutral';
    // All thresholds are integers; compare unrounded decimal values exactly.
    const whole = BigInt(scalePay(maximum, multiply, divide)!.split('.')[0]!);
    if (whole > highest) highest = whole;
  }
  if (highest >= 80n) return 'top';
  if (highest >= 65n) return 'red';
  if (highest >= 50n) return 'purple';
  if (highest >= 40n) return 'gold';
  if (highest >= 30n) return 'blue';
  if (highest >= 20n) return 'green';
  return 'neutral';
}
