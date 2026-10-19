<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import axios from 'axios'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'

import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()

const formRef = ref<FormInstance>()
const loading = ref(false)
const form = reactive({ display_name: '', email: '', password: '', confirm: '' })

const rules: FormRules = {
  email: [
    { required: true, message: '请输入邮箱', trigger: 'blur' },
    { type: 'email', message: '邮箱格式不正确', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, max: 128, message: '密码长度需在 8 到 128 位之间', trigger: 'blur' },
  ],
  confirm: [{ required: true, message: '请再次输入密码', trigger: 'blur' }],
}

async function submit(): Promise<void> {
  if (!formRef.value) {
    return
  }
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) {
    return
  }
  if (form.password !== form.confirm) {
    ElMessage.error('两次输入的密码不一致')
    return
  }

  loading.value = true
  try {
    await auth.register({
      email: form.email,
      password: form.password,
      display_name: form.display_name || undefined,
    })
    ElMessage.success('注册成功')
    await router.push('/dashboard')
  } catch (error) {
    const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined
    ElMessage.error(detail || '注册失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <div class="auth-panel">
      <div class="auth-brand">
        <span class="mark">R</span>
        <div>
          <h1>创建账号</h1>
          <p>上传文档，搭建自己的知识库问答</p>
        </div>
      </div>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @keyup.enter="submit">
        <el-form-item label="昵称（可选）">
          <el-input v-model="form.display_name" placeholder="怎么称呼你" />
        </el-form-item>
        <el-form-item label="邮箱" prop="email">
          <el-input v-model="form.email" placeholder="you@example.com" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            placeholder="至少 8 位"
            autocomplete="new-password"
          />
        </el-form-item>
        <el-form-item label="确认密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            show-password
            placeholder="再输入一次"
            autocomplete="new-password"
          />
        </el-form-item>
        <el-button type="primary" class="submit" :loading="loading" @click="submit">
          注册并登录
        </el-button>
      </el-form>

      <div class="auth-foot">
        已经有账号？
        <router-link to="/login">去登录</router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.auth-page {
  display: grid;
  min-height: 100%;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(circle at 15% 20%, rgba(99, 102, 241, 0.18), transparent 40%),
    radial-gradient(circle at 85% 80%, rgba(34, 211, 238, 0.16), transparent 45%),
    #f5f7fb;
}

.auth-panel {
  width: min(420px, 100%);
  padding: 36px;
  border: 1px solid #e5e7eb;
  border-radius: 18px;
  background: #fff;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.08);
}

.auth-brand {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 20px;
}

.mark {
  display: grid;
  width: 46px;
  height: 46px;
  place-items: center;
  border-radius: 12px;
  background: linear-gradient(135deg, #6366f1, #22d3ee);
  color: #0f172a;
  font-size: 24px;
  font-weight: 800;
}

.auth-brand h1 {
  margin: 0;
  font-size: 22px;
}

.auth-brand p {
  margin: 4px 0 0;
  color: #6b7280;
  font-size: 13px;
}

.submit {
  width: 100%;
  margin-top: 8px;
}

.auth-foot {
  margin-top: 20px;
  color: #6b7280;
  font-size: 13px;
  text-align: center;
}

.auth-foot a {
  color: #4f46e5;
  font-weight: 600;
}
</style>
