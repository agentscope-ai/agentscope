<template>
  <button @click="enqueue">递交报销</button>
  <section v-if="sheet.stage === 'queued'">
    <button @click="accept">复核</button>
    <button @click="sendBack">撤回申请</button>
  </section>
</template>
<script setup lang="ts">
import { fileClaim as enqueue, endorse as accept, reopen as sendBack } from './gateway';
defineProps<{ sheet: { stage: string } }>();
</script>
