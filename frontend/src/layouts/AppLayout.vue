<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Collection, DataAnalysis, SwitchButton } from '@element-plus/icons-vue'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

const activeMenu = computed(() =>
  route.path.startsWith('/kbs') || route.path.startsWith('/eval') ? '/kbs' : route.path,
)
const displayName = computed(() => auth.user?.display_name || auth.user?.email || '用户')

onMounted(() => {
  auth.loadProfile().catch(() => {
    // The axios interceptor handles expired sessions and redirects to /login.
  })
})

async function handleLogout(): Promise<void> {
  await auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <el-container class="shell">
    <el-aside width="220px" class="sidebar">
      <div class="brand">
        <span class="brand-mark">R</span>
        <div>
          <div class="brand-name">RAGScope</div>
          <div class="brand-sub">knowledge · retrieval · eval</div>
        </div>
      </div>

      <el-menu :default-active="activeMenu" router class="menu">
        <el-menu-item index="/dashboard">
          <el-icon><DataAnalysis /></el-icon>
          <span>概览</span>
        </el-menu-item>
        <el-menu-item index="/kbs">
          <el-icon><Collection /></el-icon>
          <span>知识库</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-foot muted">v0.4 · Week 4</div>
    </el-aside>

    <el-container>
      <el-header class="header">
        <div class="header-title">知识库问答与检索 Agent</div>
        <div class="header-right">
          <span class="muted">{{ displayName }}</span>
          <el-button text :icon="SwitchButton" @click="handleLogout">退出</el-button>
        </div>
      </el-header>

      <el-main class="main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.shell {
  min-height: 100%;
}

.sidebar {
  display: flex;
  flex-direction: column;
  background: #0f172a;
  color: #e2e8f0;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 22px 20px;
}

.brand-mark {
  display: grid;
  width: 38px;
  height: 38px;
  place-items: center;
  border-radius: 10px;
  background: linear-gradient(135deg, #6366f1, #22d3ee);
  color: #0f172a;
  font-weight: 800;
  font-size: 20px;
}

.brand-name {
  font-weight: 700;
  font-size: 17px;
  letter-spacing: 0.3px;
}

.brand-sub {
  font-size: 11px;
  color: #94a3b8;
}

.menu {
  flex: 1;
  border-right: none;
  background: transparent;
  --el-menu-text-color: #cbd5e1;
  --el-menu-hover-bg-color: rgba(148, 163, 184, 0.12);
  --el-menu-active-color: #22d3ee;
}

.sidebar-foot {
  padding: 16px 20px;
  font-size: 12px;
  color: #64748b;
}

.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 60px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
}

.header-title {
  font-size: 14px;
  color: #64748b;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.main {
  padding: 0;
  background: var(--app-bg);
}

.menu :deep(.el-menu-item.is-active) {
  background: rgba(34, 211, 238, 0.12);
}
</style>
