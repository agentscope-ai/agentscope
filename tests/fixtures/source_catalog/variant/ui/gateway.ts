export function fileClaim() {
  return fetch('/v2/expense/file', { method: 'POST' });
}
export function endorse() {
  return fetch('/v2/expense/endorse', { method: 'POST' });
}
export function reopen() {
  return fetch('/v2/expense/reopen', { method: 'PATCH' });
}
