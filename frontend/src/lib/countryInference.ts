import type { components } from './api.generated';

type CountryInference = components['schemas']['CountryInference'];
// MIT flag-icons SVG assets are bundled locally. No component CSS or remote requests.
const flags = import.meta.glob<string>(
  '../../../node_modules/flag-icons/flags/4x3/*.svg',
  { query: '?url&no-inline', import: 'default', eager: true },
);
export function countryFlagUrl(
  code: string | null | undefined,
): string | undefined {
  if (!code || !/^[A-Z]{2}$/.test(code)) return undefined;
  return flags[
    `../../../node_modules/flag-icons/flags/4x3/${code.toLowerCase()}.svg`
  ];
}
export function inferredCountryName(
  inference: CountryInference | undefined,
): string | null {
  return inference?.state === 'accepted' &&
    inference.country_code &&
    countryFlagUrl(inference.country_code)
    ? inference.country_name
    : null;
}
