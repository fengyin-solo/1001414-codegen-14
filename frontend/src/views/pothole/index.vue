<template>
  <section class="page" data-module="pothole">
    <header class="page-head">
      <div>
        <h2>坑槽修补管理</h2>
        <p class="page-desc">维护修补单，围绕修补单号、所在路段、修补面积、修补材料做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记修补单</button>
        <button class="btn" type="button" :disabled="exporting" @click="generateExport">
          {{ exporting ? '正在生成…' : '导出坑槽修补清单' }}
        </button>
        <button
          class="btn ghost"
          type="button"
          :disabled="!exportInfo.generated || downloading"
          @click="downloadLatest"
        >
          下载最近一份
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <!-- 最近一次导出的口径说明：有效/过期/空路段提醒都点在这里 -->
    <div v-if="exportMessage" class="export-banner" :class="exportTone">
      <span>{{ exportMessage }}</span>
      <button
        v-if="exportInfo.generated && exportTone !== 'ok'"
        class="link"
        type="button"
        @click="generateExport"
      >
        重新生成
      </button>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td
            v-for="column in columns"
            :key="column"
            :class="{ 'missing-cell': column === '所在路段' && isBlank(row[column]) }"
          >
            {{ column === '修补状态' ? (row.status || '—') : formatCell(row[column]) }}
          </td>
          <td class="row-actions">
            <button
              v-for="action in actionsFor(row.status as string)"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无坑槽修补数据，可先登记修补单</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条坑槽修补记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null> & { status?: string }
type Stats = { total: number; pending: number; cancelled: number; monthArea: number }
type ExportInfo = {
  generated: boolean
  generatedAt?: string
  total?: number
  currentTotal?: number
  totalMatched?: boolean
  stale?: boolean
  missingRoadCount?: number
  missingRoadOrders?: string[]
}

const ENDPOINT = '/api/pothole'
const columns = ["修补单号", "所在路段", "修补面积", "修补材料", "用料数量", "作业班组", "完成日期", "修补状态"]
const actionFlow: Record<string, string[]> = {
  '待安排': ["安排修补", "取消修补"],
  '修补中': ["确认完成", "取消修补"],
  '已完成': [],
  '已取消': [],
}

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const statsData = ref<Stats>({ total: 0, pending: 0, cancelled: 0, monthArea: 0 })
const stats = computed(() => [
  { label: '修补单总数', value: statsData.value.total },
  { label: '待安排修补', value: statsData.value.pending },
  { label: '本月修补面积（㎡）', value: statsData.value.monthArea },
  { label: '取消单数', value: statsData.value.cancelled },
])

const exportInfo = ref<ExportInfo>({ generated: false })
const exporting = ref(false)
const downloading = ref(false)
const exportMessage = ref('')
const exportTone = ref<'ok' | 'warn' | 'error'>('ok')

function isBlank(value: unknown): boolean {
  return value === null || value === undefined || String(value).trim() === ''
}

function formatCell(value: unknown): string {
  return isBlank(value) ? '—' : String(value)
}

function actionsFor(status: string): string[] {
  return actionFlow[status] ?? []
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function openCreate() {
  errorMessage.value = '修补单登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('坑槽修补动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadStats(), loadExportStatus()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '坑槽修补操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  // 后端只认修补单号关键字；其余输入项保留在界面上，不参与查询。
  const params = new URLSearchParams()
  if (filters.value['修补单号']) {
    params.set('keyword', filters.value['修补单号'])
  }
  const query = params.toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('修补单列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '坑槽修补列表读取失败'
  }
}

async function loadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (response.ok) {
      statsData.value = (await response.json()) as Stats
    }
  } catch {
    // 统计卡读不到时保留上次的数字，不打断页面其它操作
  }
}

async function loadExportStatus() {
  try {
    const response = await request(`${ENDPOINT}/export/status`)
    if (!response.ok) {
      return
    }
    exportInfo.value = (await response.json()) as ExportInfo
    if (!exportInfo.value.generated) {
      exportMessage.value = ''
      return
    }
    const info = exportInfo.value
    if (info.stale || !info.totalMatched) {
      exportTone.value = 'warn'
      exportMessage.value =
        `最近一份清单生成于 ${info.generatedAt}，共 ${info.total} 条；当前页面已有 ${info.currentTotal} 条，口径已过期，请重新生成后再下载。`
    } else {
      exportTone.value = 'ok'
      exportMessage.value =
        `最近一份清单生成于 ${info.generatedAt}，共 ${info.total} 条，与列表、统计卡口径一致。${missingRoadText(info)}`
    }
  } catch {
    // 导出状态只是辅助信息，读不到不阻塞主流程
  }
}

function missingRoadText(info: ExportInfo): string {
  if (!info.missingRoadCount) {
    return ''
  }
  const orders = (info.missingRoadOrders ?? []).join('、')
  return `其中 ${info.missingRoadCount} 条所在路段为空（${orders}），已在清单末尾单列，请补录路段。`
}

async function generateExport() {
  errorMessage.value = ''
  exporting.value = true
  try {
    // 导出按全量口径走：先把筛选条件清空并刷新，避免清单条数和屏幕对不上。
    filters.value = {}
    const [response] = await Promise.all([
      request(`${ENDPOINT}/export`, { method: 'POST' }),
      reload(),
      loadStats(),
    ])
    if (!response.ok) {
      throw new Error('清单生成失败，请稍后重试')
    }
    const manifest = (await response.json()) as ExportInfo
    await loadExportStatus()
    // 生成之后把清单条数与屏幕上的两处数字（列表总数、统计卡总数）核一遍。
    if (manifest.total !== total.value || manifest.total !== statsData.value.total) {
      exportTone.value = 'error'
      exportMessage.value =
        `清单条数 ${manifest.total} 与列表总数 ${total.value}、统计卡 ${statsData.value.total} 对不上，请刷新后重试。`
      return
    }
    exportTone.value = manifest.missingRoadCount ? 'warn' : 'ok'
    exportMessage.value =
      `清单已生成：${manifest.total} 条，与列表总数、统计卡核对一致。${missingRoadText(manifest)}`
  } catch (error) {
    exportTone.value = 'error'
    exportMessage.value = error instanceof Error ? error.message : '清单生成失败'
  } finally {
    exporting.value = false
  }
}

async function downloadLatest() {
  errorMessage.value = ''
  if (exportInfo.value.stale || exportInfo.value.totalMatched === false) {
    errorMessage.value = '当前清单已过期，请重新生成后再下载'
    return
  }
  downloading.value = true
  try {
    const response = await request(`${ENDPOINT}/export/latest`)
    if (!response.ok) {
      throw new Error('清单文件不存在，请先生成')
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = '坑槽修补清单.csv'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清单下载失败'
  } finally {
    downloading.value = false
  }
}

onMounted(() => {
  void reload()
  void loadStats()
  void loadExportStatus()
})
</script>

<style scoped>
.page-actions {
  display: flex;
  gap: 8px;
}
.export-banner {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.export-banner.ok {
  background: #ecfdf3;
  border-color: #a6f4c5;
  color: #027a48;
}
.export-banner.warn {
  background: #fffaeb;
  border-color: #fedf89;
  color: #b54708;
}
.export-banner.error {
  background: #fef3f2;
  border-color: #fda29b;
  color: #b42318;
}
.missing-cell {
  color: #b54708;
  font-weight: 600;
}
</style>
