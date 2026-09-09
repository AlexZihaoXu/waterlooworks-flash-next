export function matchesSelection(
  value: string | null,
  selected: readonly string[],
): boolean {
  return (
    selected.length === 0 ||
    (value !== null &&
      selected.some(
        (item) => item.toLocaleLowerCase() === value.toLocaleLowerCase(),
      ))
  );
}

// Targeted source identities are exact, unlike case-insensitive scalar filters.
export function matchesAnySelection(
  values: readonly string[],
  selected: readonly string[],
): boolean {
  return (
    selected.length === 0 || selected.some((value) => values.includes(value))
  );
}
