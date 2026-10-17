<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="not-found">
    <el-result icon="warning" title="404" sub-title="这个页面不存在，或者已经被删除了">
      <template #extra>
        <el-button type="primary" @click="router.push('/dashboard')">回到概览</el-button>
        <el-button @click="router.push('/kbs')">我的知识库</el-button>
      </template>
    </el-result>
  </div>
</template>

<style scoped>
.not-found {
  display: grid;
  min-height: 60vh;
  place-items: center;
}
</style>
