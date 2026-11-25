export function payable(expected: number, price: number, loading: number) {
  return Math.round((expected * price + loading) * 100) / 100;
}
