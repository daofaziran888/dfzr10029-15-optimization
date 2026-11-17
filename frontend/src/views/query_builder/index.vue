<template>
  <section class="page" data-module="query_builder">
    <header class="page-head">
      <div>
        <h2>OLAP查询管理</h2>
        <p class="page-desc">维护查询模板，按模板版本与参数范围复用查询结果：相同版本与筛选口径只执行一次全量扫描，切换日期和门店时优先命中缓存。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记查询模板</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出OLAP查询清单</button>
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
            <button class="link" type="button" :data-testid="`execute-row-${row.id}`" @click="selectTemplate(row)">
              执行查询
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无OLAP查询数据，可先登记查询模板</td>
        </tr>
      </tbody>
    </table>

    <div class="query-panel" data-testid="query-panel">
      <h3>查询执行与结果缓存</h3>
      <p class="page-desc">缓存键 = 模板编号 + 模板版本 + 参数范围；切换日期、门店筛选时，相同口径直接复用结果快照。</p>
      <form class="analysis-form" @submit.prevent="executeQuery">
        <label>
          查询模板
          <select v-model="executeForm.templateId" data-testid="execute-template">
            <option v-for="row in rows" :key="String(row.id)" :value="Number(row.id)">
              {{ row['模板编号'] }} · v{{ row['模板版本'] ?? 1 }} · {{ row['模板名称'] }}
            </option>
          </select>
        </label>
        <label>
          日期筛选
          <input v-model="executeForm.date" data-testid="execute-date" placeholder="如 2026-11-10" />
        </label>
        <label>
          门店筛选
          <input v-model="executeForm.store" data-testid="execute-store" placeholder="如 S001" />
        </label>
        <button class="btn primary" data-testid="execute-submit" type="submit" :disabled="executing">
          {{ executing ? '执行中…' : '执行查询' }}
        </button>
      </form>

      <div v-if="lastResult" class="result-card" data-testid="last-result">
        <div class="result-head">
          <span
            class="badge"
            :class="lastResult['命中状态'] === '命中' ? 'badge-hit' : 'badge-miss'"
            data-testid="result-marker"
          >
            {{ lastResult['命中状态'] }}
          </span>
          <span class="result-meta">模板 {{ lastResult['模板编号'] }} · v{{ lastResult['模板版本'] }}</span>
          <span class="result-meta">参数范围 {{ lastResult['参数指纹'] }}</span>
          <span class="result-meta">快照 {{ lastResult['快照编号'] }}</span>
          <span class="result-meta">耗时 {{ lastResult['执行毫秒'] }}ms（全量扫描 {{ lastResult['全量扫描毫秒'] }}ms）</span>
          <span class="result-meta">命中次数 {{ lastResult['命中次数'] }}</span>
          <span class="badge" :class="lastResult['核对结果'] === '核对一致' ? 'badge-ok' : 'badge-diff'" data-testid="result-check">
            {{ lastResult['核对结果'] }}
          </span>
        </div>
        <table class="data-table">
          <thead>
            <tr><th v-for="column in resultColumns" :key="column">{{ column }}</th></tr>
          </thead>
          <tbody>
            <tr v-for="(resultRow, index) in lastResult['结果行']" :key="index">
              <td v-for="column in resultColumns" :key="column">{{ resultRow[column] ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="hit-panel" data-testid="hit-panel">
      <div class="page-head">
        <div>
          <h3>查询缓存命中记录</h3>
          <p class="page-desc">每次查询结果接口调用写入一条命中记录，保留模板版本与参数范围；分页定位时保持筛选条件。</p>
        </div>
        <div class="page-actions">
          <button class="btn" data-testid="hit-export" type="button" @click="exportHits">导出命中记录</button>
        </div>
      </div>

      <div class="stat-row">
        <article v-for="item in cacheStats" :key="item.label" class="stat-card">
          <span class="stat-label">{{ item.label }}</span>
          <strong class="stat-value">{{ item.value }}</strong>
        </article>
      </div>

      <form class="filter-bar" @submit.prevent="reloadHits(1)">
        <label v-for="field in hitFilterFields" :key="field" class="filter-item">
          <span>{{ field }}</span>
          <input v-model="hitFilters[field]" :data-testid="`hit-filter-${field}`" :placeholder="`按${field}检索`" />
        </label>
        <button class="btn" data-testid="hit-query" type="submit">查询</button>
        <button class="btn ghost" data-testid="hit-reset" type="button" @click="resetHitFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in hitColumns" :key="column">{{ column }}</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="hit in hitRows" :key="String(hit.id)" :data-testid="`hit-row-${hit.id}`">
            <td v-for="column in hitColumns" :key="column">
              <span v-if="column === '命中状态'" class="badge" :class="hit[column] === '命中' ? 'badge-hit' : 'badge-miss'">
                {{ hit[column] }}
              </span>
              <span v-else-if="column === '核对结果'" class="badge" :class="hit[column] === '核对一致' ? 'badge-ok' : 'badge-diff'">
                {{ hit[column] }}
              </span>
              <span v-else>{{ hit[column] ?? '—' }}</span>
            </td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`hit-verify-${hit.id}`" @click="verifyHit(hit)">
                核对命中记录
              </button>
            </td>
          </tr>
          <tr v-if="!hitRows.length">
            <td :colspan="hitColumns.length + 1" class="empty-state">暂无查询缓存命中记录，执行一次查询后自动写入</td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <span>共 {{ hitTotal }} 条命中记录</span>
        <span class="pager">
          <button class="btn" data-testid="hit-prev" type="button" :disabled="hitPage <= 1" @click="reloadHits(hitPage - 1)">上一页</button>
          <span>第 {{ hitPage }} / {{ totalHitPages }} 页</span>
          <button class="btn" data-testid="hit-next" type="button" :disabled="hitPage >= totalHitPages" @click="reloadHits(hitPage + 1)">下一页</button>
        </span>
      </footer>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记查询模板</h3>
        <p class="page-desc">模板版本由服务端在发布时统一维护，新登记模板默认 v1，无需手工填写。</p>
        <label v-for="field in createColumns" :key="field" class="create-field">
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

    <footer class="page-foot">
      <span>共 {{ total }} 条OLAP查询记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type QueryResult = {
  命中: boolean
  命中状态: string
  缓存键: string
  模板编号: string
  模板版本: number
  参数范围: Record<string, string>
  参数指纹: string
  快照编号: string
  结果校验哈希: string
  核对结果: string
  扫描行数: number
  执行毫秒: number
  全量扫描毫秒: number
  命中次数: number
  命中记录编号: string
  命中记录ID: number
  查询时间: string
  结果行: Array<Record<string, string | number>>
}
type HitRow = Record<string, string | number | null>
type StatCard = { label: string; value: number }

const ENDPOINT = '/api/query_builder'
const columns = ["模板编号", "模板名称", "查询语句", "参数定义", "数据集范围", "超时秒数", "缓存策略", "模板版本", "模板状态"]
// 模板版本由服务端在发布动作中维护，登记表单不暴露该字段。
const createColumns = columns.filter((field) => field !== "模板版本")
const actions = ["保存模板", "发布模板", "停用模板"]
const statuses = ["草稿", "已保存", "已发布", "已停用"]
const metricNames = ["已发布模板", "草稿模板", "停用模板"]
const hitColumns = ["命中记录编号", "模板编号", "模板版本", "参数范围", "命中状态", "快照编号", "核对结果", "缓存键", "执行毫秒", "命中次数", "查询时间"]
const hitFilterFields = ["模板编号", "模板版本", "命中状态", "核对结果", "快照编号"]
const resultColumns = ["日期", "门店", "品类", "订单数", "销售金额"]
const HIT_PAGE_SIZE = 5

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

const executeForm = ref({ templateId: 0, date: '', store: '' })
const executing = ref(false)
const lastResult = ref<QueryResult | null>(null)

const hitRows = ref<HitRow[]>([])
const hitTotal = ref(0)
const hitPage = ref(1)
const hitFilters = ref<Record<string, string>>({})
const cacheStats = ref<StatCard[]>([])
const totalHitPages = computed(() => Math.max(1, Math.ceil(hitTotal.value / HIT_PAGE_SIZE)))

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
    link.download = 'query_builder.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(createColumns.map((field) => [field, '']))
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
      throw new Error('OLAP查询动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'OLAP查询操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('查询模板列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (executeForm.value.templateId === 0 && rows.value.length > 0) {
      executeForm.value.templateId = Number(rows.value[0].id)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'OLAP查询列表读取失败'
  }
}

function selectTemplate(row: Row) {
  executeForm.value.templateId = Number(row.id)
  document.querySelector<HTMLElement>('[data-testid="query-panel"]')?.scrollIntoView({ behavior: 'smooth' })
}

function buildExecuteFilters(): Record<string, string> {
  const params: Record<string, string> = {}
  if (executeForm.value.date.trim()) params['日期'] = executeForm.value.date.trim()
  if (executeForm.value.store.trim()) params['门店'] = executeForm.value.store.trim()
  return params
}

async function executeQuery() {
  if (!executeForm.value.templateId) {
    errorMessage.value = '请先选择查询模板'
    return
  }
  executing.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${executeForm.value.templateId}/execute`, {
      method: 'POST',
      body: JSON.stringify({ filters: buildExecuteFilters() }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '查询执行失败')
    lastResult.value = payload.entry as QueryResult
    await Promise.all([reloadHits(1), reloadStats()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '查询执行失败'
  } finally {
    executing.value = false
  }
}

function hitQueryString(page: number): string {
  const params = new URLSearchParams(hitFilters.value as Record<string, string>)
  params.set('page', String(page))
  params.set('size', String(HIT_PAGE_SIZE))
  return params.toString()
}

async function reloadHits(page: number) {
  hitPage.value = Math.max(1, page)
  try {
    const response = await request(`${ENDPOINT}/cache_hits?${hitQueryString(hitPage.value)}`)
    if (!response.ok) throw new Error('查询缓存命中记录读取失败')
    const payload = await response.json()
    hitRows.value = payload.items ?? []
    hitTotal.value = payload.total ?? 0
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '查询缓存命中记录读取失败'
  }
}

function resetHitFilters() {
  hitFilters.value = {}
  void reloadHits(1)
}

async function exportHits() {
  errorMessage.value = ''
  const query = new URLSearchParams(hitFilters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}/cache_hits/export?${query}`)
    if (!response.ok) throw new Error('命中记录导出失败')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'query_cache_hits.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '命中记录导出失败'
  }
}

async function verifyHit(hit: HitRow) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/cache_hits/${hit.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action: '核对命中记录' }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '核对失败')
    await reloadHits(hitPage.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '核对失败'
  }
}

async function reloadStats() {
  try {
    const response = await request(`${ENDPOINT}/cache_hits/stats`)
    if (!response.ok) return
    const stats = await response.json() as Record<string, number>
    cacheStats.value = [
      { label: '查询次数', value: stats['查询次数'] ?? 0 },
      { label: '缓存命中', value: stats['命中次数'] ?? 0 },
      { label: '重新执行', value: stats['重新执行次数'] ?? 0 },
      { label: '缓存键数量', value: stats['缓存键数量'] ?? 0 },
    ]
  } catch {
    cacheStats.value = []
  }
}

onMounted(() => {
  void reload()
  void reloadHits(1)
  void reloadStats()
})
</script>
