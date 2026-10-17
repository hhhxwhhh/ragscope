import { createRouter, createWebHistory } from 'vue-router'

import { tokenStorage } from '@/api/client'
import AppLayout from '@/layouts/AppLayout.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/views/RegisterView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: AppLayout,
      children: [
        { path: '', redirect: '/dashboard' },
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
        },
        {
          path: 'kbs',
          name: 'kbs',
          component: () => import('@/views/KbListView.vue'),
        },
        {
          path: 'kbs/:id',
          name: 'kb-detail',
          component: () => import('@/views/KbDetailView.vue'),
          props: true,
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
  ],
})

router.beforeEach((to) => {
  const hasToken = Boolean(tokenStorage.get())
  if (!to.meta.public && !hasToken) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.public && hasToken) {
    return { name: 'dashboard' }
  }
  return true
})

export default router
