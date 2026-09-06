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
