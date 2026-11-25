<template>
  <Grid :row-data="tableData">
    <template #bodyCell="{ record }">
      <button @click="handleEdit(record)">{{ record.billNo }}</button>
    </template>
  </Grid>
</template>
<script setup>
import { purchases } from './service';
const tableData = ref([]);
useInfiniteTable({
  tableData,
  async loadPage() {
    const res = await purchases.page();
    return { records: res.records };
  }
});
function handleEdit(record) {
  const orderId = Number(record?.id);
  const loadFromServer = () => loadCached(orderId);
  return loadFromServer();
}
function loadCached(orderId) {
  return purchases.detail(orderId);
}
</script>
