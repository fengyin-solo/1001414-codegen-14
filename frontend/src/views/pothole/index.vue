<template>
  <section class="page" data-module="pothole">
    <header class="page-head">
      <div>
        <h2>坑槽修补管理</h2>
        <p class="page-desc">维护修补单，围绕修补单号、所在路段、修补面积、修补材料做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记修补单</button>
        <button class="btn" type="button" :disabled="exporting" @click="exportRows">
          {{ exporting ? '正在生成…' : '导出坑槽修补清单' }}
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div v-if="missingNumbers.length" class="notice warning">
      以下 {{ missingNumbers.length }} 条修补单所在路段为空，导出时会保留并标记，不会被丢掉：{{ missingNumbers.join('、') }}
    </div>
    <div v-if="exportNotice" :class="['notice', exportNotice.tone]">
      {{ exportNotice.text }}
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
          <td v-for="column in columns" :key="column">
            <span v-if="column === '所在路段' && isRoadMissing(row)" class="cell-warning">
              路段为空 ⚠
            </span>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
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
      <span>共 {{ total }} 条坑槽修补记录<template v-if="filterActive">（已按条件筛选，全量为 {{ stats.total ?? '—' }} 条；导出始终按全量）</template></span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

interface StatsPayload {
  total: number
  pending: number
  month_area: number
  canceled: number
  missing_count: number
  missing_numbers: string[]
}

interface ExportResult {
  filename: string
  signature: string
  generated_at: string
  total: number
  missing_count: number
  missing_numbers: string[]
}

interface ExportStatus extends ExportResult {
  generated: boolean
  current_signature: string
  current_total: number
  fresh: boolean
}

const ENDPOINT = '/api/pothole'
const columns = ["修补单号", "所在路段", "修补面积", "修补材料", "用料数量", "作业班组", "完成日期", "修补状态"]
const actions = ["安排修补", "确认完成", "取消修补"]
const statuses = ["待安排", "修补中", "已完成", "已取消"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const exporting = ref(false)
const exportNotice = ref<{ text: string; tone: 'success' | 'warning' | 'error-text' } | null>(null)
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const stats = ref<Partial<StatsPayload>>({})

const statCards = computed(() => [
  { label: '修补单总数', value: stats.value.total ?? 0 },
  { label: '待安排修补', value: stats.value.pending ?? 0 },
  { label: '本月修补面积', value: stats.value.month_area ?? 0 },
  { label: '取消单数', value: stats.value.canceled ?? 0 },
])

const missingNumbers = computed(() => stats.value.missing_numbers ?? [])
const filterActive = computed(() =>
  Object.values(filters.value).some((value) => String(value ?? '').trim() !== ''),
)

function isRoadMissing(row: Row): boolean {
  return String(row['所在路段'] ?? '').trim() === ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function openCreate() {
  errorMessage.value = '修补单登记入口尚未接入审批流'
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const [listResponse, statsResponse] = await Promise.all([
      request(`${ENDPOINT}?${query}`),
      request(`${ENDPOINT}/stats`),
    ])
    if (!listResponse.ok) {
      throw new Error('修补单列表读取失败')
    }
    if (!statsResponse.ok) {
      throw new Error('修补单统计卡读取失败')
    }
    const payload = await listResponse.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats.value = await statsResponse.json()
    await checkExportFreshness()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '坑槽修补列表读取失败'
  }
}

async function checkExportFreshness() {
  // 刷新后核对已生成文件与当前页面是否同一批；不一致就静默重生成，
  // 保证“文件与页面口径不能两样”，并把差异点出来。
  try {
    const response = await request(`${ENDPOINT}/export/status`)
    if (!response.ok) {
      return
    }
    const status: ExportStatus = await response.json()
    if (!status.generated) {
      exportNotice.value = null
      return
    }
    if (!status.fresh) {
      await syncExport(status)
      return
    }
    reconcile(status)
  } catch {
    // 状态核对失败不挡住列表本身。
  }
}

async function syncExport(status: ExportStatus) {
  try {
    const response = await request(`${ENDPOINT}/export`, { method: 'POST' })
    if (!response.ok) {
      throw new Error()
    }
    const latest: ExportResult = await response.json()
    exportNotice.value = {
      tone: 'warning',
      text: `已生成清单（${status.generated_at}）与当前数据不是同一批，已自动重新生成最新一份；请重新下载，旧文件作废。`,
    }
    reconcile(latest)
  } catch {
    exportNotice.value = {
      tone: 'error-text',
      text: '已生成清单与当前数据不一致，请重新点“导出坑槽修补清单”获取最新文件。',
    }
  }
}

function reconcile(result: ExportResult) {
  const listTotal = stats.value.total
  const problems: string[] = []
  if (result.total !== listTotal) {
    problems.push(`文件 ${result.total} 条，统计卡/全量列表 ${listTotal} 条`)
  }
  if (filterActive.value && total.value !== result.total) {
    problems.push(`当前筛选列表 ${total.value} 条（导出按全量 ${result.total} 条，月底汇报口径）`)
  }
  const missingText = result.missing_count
    ? `；其中所在路段为空 ${result.missing_count} 条（${result.missing_numbers.join('、')}）已标记保留`
    : '；无路段为空记录'
  if (problems.length) {
    exportNotice.value = {
      tone: 'warning',
      text: `清单已就绪（${result.generated_at} 生成，共 ${result.total} 条），核对提示：${problems.join('；')}${missingText}。`,
    }
  } else {
    exportNotice.value = {
      tone: 'success',
      text: `清单已就绪（${result.generated_at} 生成，共 ${result.total} 条），与列表条数、统计卡总数一致${missingText}。`,
    }
  }
}

async function exportRows() {
  errorMessage.value = ''
  exporting.value = true
  try {
    const response = await request(`${ENDPOINT}/export`, { method: 'POST' })
    if (!response.ok) {
      throw new Error('清单生成失败，请稍后重试')
    }
    const result: ExportResult = await response.json()
    // 生成后立刻把清单条数与屏幕上的两处数字（列表全量条数、统计卡总数）核一遍。
    reconcile(result)
    window.location.assign(`${ENDPOINT}/export/download`)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '坑槽修补清单导出失败'
  } finally {
    exporting.value = false
  }
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
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '坑槽修补操作失败'
  }
}

onMounted(reload)
</script>
