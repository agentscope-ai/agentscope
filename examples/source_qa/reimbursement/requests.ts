export function submit() {
  return fetch('/expenses/submit', { method: 'POST' });
}
export function approve() {
  return fetch('/expenses/approve', { method: 'POST' });
}
export function withdraw() {
  return fetch('/expenses/withdraw', { method: 'POST' });
}
