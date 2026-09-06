/** Equivalent orientation within half a turn, for a jump-free return to rest. */
export function settleAngle(degrees: number): number {
  return ((((degrees + 180) % 360) + 360) % 360) - 180;
}
