<template>
  <button v-if="claim.status === 'draft'" @click="submit">提交</button>
  <button v-if="claim.status === 'pending'" @click="approve">审批</button>
  <button v-if="claim.status === 'pending'" @click="withdraw">撤回</button>
</template>
<script setup lang="ts">
import { submit, approve, withdraw } from './requests';
defineProps<{ claim: { status: string } }>();
</script>
