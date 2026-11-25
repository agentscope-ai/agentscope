import axios from 'axios';

export function postReceipt(body) {
  return axios.post('/purchase/order', body);
}
