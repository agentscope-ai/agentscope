import axios from 'axios';
export const purchases = {
  page: () => axios.get('/purchase/order/page'),
  detail: (key) => axios.get(`/purchase/order/${key}`),
};
export const helpdesk = {
  search: () => axios.get('/desk/inbox'),
  read: (lookup) => axios.get(`/desk/cases/${lookup}`),
};
