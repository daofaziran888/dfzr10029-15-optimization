<template>
  <section class="page" data-module="cache">
    <header class="page-head">
      <div>
        <h2>缓存管理管理</h2>
        <p class="page-desc">维护缓存配置，围绕缓存编号、缓存目标、缓存策略、过期秒数做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记缓存配置</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出缓存管理清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无缓存管理数据，可先登记缓存配置</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记缓存配置</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div class="hit-panel" data-testid="query-hit-panel">
      <div class="page-head">
        <div>
          <h3>查询缓存命中记录（跨模块共享）</h3>
          <p class="page-desc">只读回查 OLAP 查询模块写入的命中记录，与查询页共享同一来源记录；筛选条件在翻页时保持不变。</p>
        </div>
      </div>
      <form class="filter-bar" @submit.prevent="reloadQueryHits(1)">
        <label v-for="field in queryHitFilterFields" :key="field" class="filter-item">
          <span>{{ field }}</span>
          <input v-model="queryHitFilters[field]" :data-testid="`query-hit-filter-${field}`" :placeholder="`按${field}检索`" />
        </label>
        <button class="btn" data-testid="query-hit-search" type="submit">查询</button>
        <button class="btn ghost" data-testid="query-hit-reset" type="button" @click="resetQueryHitFilters">重置条件</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in queryHitColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="hit in queryHitRows" :key="String(hit.id)" :data-testid="`query-hit-row-${hit.id}`">
            <td v-for="column in queryHitColumns" :key="column">
              <span v-if="column === '命中状态'" class="badge" :class="hit[column] === '命中' ? 'badge-hit' : 'badge-miss'">{{ hit[column] }}</span>
              <span v-else-if="column === '核对结果'" class="badge" :class="hit[column] === '核对一致' ? 'badge-ok' : 'badge-diff'">{{ hit[column] }}</span>
              <span v-else>{{ hit[column] ?? '—' }}</span>
            </td>
          </tr>
          <tr v-if="!queryHitRows.length">
            <td :colspan="queryHitColumns.length" class="empty-state">暂无查询缓存命中记录</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ queryHitTotal }} 条命中记录</span>
        <span class="pager">
          <button class="btn" data-testid="query-hit-prev" type="button" :disabled="queryHitPage <= 1" @click="reloadQueryHits(queryHitPage - 1)">上一页</button>
          <span>第 {{ queryHitPage }} 页</span>
          <button class="btn" data-testid="query-hit-next" type="button" :disabled="queryHitRows.length < QUERY_HIT_PAGE_SIZE" @click="reloadQueryHits(queryHitPage + 1)">下一页</button>
        </span>
      </footer>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条缓存管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type HitRow = Record<string, string | number | null>

const ENDPOINT = '/api/cache'
const columns = ["缓存编号", "缓存目标", "缓存策略", "过期秒数", "最大容量", "命中统计", "淘汰策略", "缓存状态"]
const actions = ["保存配置", "启用缓存", "停用缓存"]
const statuses = ["草稿", "已配置", "生效中", "已停用"]
const metricNames = ["生效中缓存", "草稿配置", "缓存命中率"]
const queryHitColumns = ["命中记录编号", "模板编号", "模板版本", "参数范围", "命中状态", "快照编号", "核对结果", "查询时间"]
const queryHitFilterFields = ["模板编号", "模板版本", "命中状态", "核对结果", "快照编号"]
const QUERY_HIT_PAGE_SIZE = 5

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'cache.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
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
      throw new Error('缓存管理动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缓存管理操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('缓存配置列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '缓存管理列表读取失败'
  }
}

const queryHitRows = ref<HitRow[]>([])
const queryHitTotal = ref(0)
const queryHitPage = ref(1)
const queryHitFilters = ref<Record<string, string>>({})

function queryHitQueryString(page: number): string {
  const params = new URLSearchParams(queryHitFilters.value as Record<string, string>)
  params.set('page', String(page))
  params.set('size', String(QUERY_HIT_PAGE_SIZE))
  return params.toString()
}

async function reloadQueryHits(page: number) {
  queryHitPage.value = Math.max(1, page)
  try {
    const response = await request(`${ENDPOINT}/query_hits?${queryHitQueryString(queryHitPage.value)}`)
    if (!response.ok) throw new Error('查询缓存命中记录读取失败')
    const payload = await response.json()
    queryHitRows.value = payload.items ?? []
    queryHitTotal.value = payload.total ?? 0
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '查询缓存命中记录读取失败'
  }
}

function resetQueryHitFilters() {
  queryHitFilters.value = {}
  void reloadQueryHits(1)
}

onMounted(() => {
  void reload()
  void reloadQueryHits(1)
})
</script>
