<template>
  <Grid :items="tickets">
    <template #cell="{ item: selected }">
      <button @click="showTicket(selected)">{{ selected.subject }}</button>
    </template>
  </Grid>
</template>
<script setup>
import { helpdesk as api } from './service';
const tickets = ref([]);
async function refresh() {
  const inbox = await api.search();
  tickets.value = inbox.items;
}
function showTicket(entry) {
  return api.read(entry.ticketKey);
}
</script>
