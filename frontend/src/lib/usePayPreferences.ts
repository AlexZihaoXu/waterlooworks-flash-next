import { useEffect, useState } from 'react';
import {
  defaultPayPreferences,
  type PayPreferences,
  type FxRates,
} from './hourlyPay';

export function parsePayPreferences(value: unknown): PayPreferences {
  if (typeof value !== 'object' || value === null) return defaultPayPreferences;
  const p = value as Record<string, unknown>;
  return {
    format: p.format === 'month' ? 'month' : 'hour',
    currency:
      p.currency === 'CAD' || p.currency === 'USD' ? p.currency : 'original',
  };
}
export function usePayPreferences(needsTierFx = false, enabled = true) {
  const [preferences, setPreferences] = useState<PayPreferences>(() => {
    try {
      return parsePayPreferences(
        JSON.parse(localStorage.getItem('ww-pay-preferences') ?? 'null'),
      );
    } catch {
      return defaultPayPreferences;
    }
  });
  const [fx, setFx] = useState<FxRates | null>(null);
  const [fxStatus, setFxStatus] = useState<
    'idle' | 'loading' | 'ready' | 'unavailable'
  >('idle');
  useEffect(() => {
    try {
      localStorage.setItem('ww-pay-preferences', JSON.stringify(preferences));
    } catch {
      /* Optional preference persistence. */
    }
  }, [preferences]);
  useEffect(() => {
    if (!enabled || (preferences.currency === 'original' && !needsTierFx))
      return;
    const controller = new AbortController();
    let active = true;
    const timer = window.setTimeout(() => controller.abort(), 12000);
    void Promise.resolve().then(async () => {
      if (!active) return;
      setFxStatus('loading');
      try {
        const response = await fetch('/api/exchange-rates', {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('Rates unavailable');
        const data: unknown = await response.json();
        if (typeof data !== 'object' || data === null)
          throw new Error('Invalid rates');
        const v = data as Record<string, unknown>;
        if (
          typeof v.as_of !== 'string' ||
          !/^\d{4}-\d{2}-\d{2}$/.test(v.as_of) ||
          typeof v.cad_per_unit !== 'object' ||
          v.cad_per_unit === null ||
          !Object.entries(v.cad_per_unit).every(
            ([key, value]) =>
              /^[A-Z]{3}$/.test(key) &&
              typeof value === 'string' &&
              value.length <= 30 &&
              /^\d+(?:\.\d+)?$/.test(value) &&
              Number(value) > 0,
          )
        )
          throw new Error('Invalid rates');
        if (active) {
          setFx(v as FxRates);
          setFxStatus('ready');
        }
      } catch {
        if (active) {
          setFx(null);
          setFxStatus('unavailable');
        }
      } finally {
        clearTimeout(timer);
      }
    });
    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [preferences.currency, needsTierFx, enabled]);
  return { preferences, setPreferences, fx, fxStatus };
}
