<template>
  <button @click="save(10, 9, 3, 2)">保存</button>
</template>
<script setup lang="ts">
import { payable } from './amount';
import { postReceipt } from './service';

function save(expected: number, received: number, price: number, loading: number = 0) {
  if (received < 0) throw new Error('Negative received quantity');
  const amount = payable(expected, price, loading);
  return postReceipt({ details: [{ expectPcs: expected, inPcs: received, payableAmount: amount }] });
}
</script>
