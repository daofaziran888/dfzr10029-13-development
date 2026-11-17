<template>
  <section class="page" data-module="query_builder">
    <header class="page-head">
      <div>
        <h2>OLAP查询管理</h2>
        <p class="page-desc">维护查询模板，围绕模板编号、模板名称、查询语句、参数定义做登记、筛选与状态流转。</p>
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
              class="link"
              type="button"
              :data-testid="`batch-${row.id}`"
              @click="openBatch(row)"
            >
              批量查询
            </button>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无OLAP查询数据，可先登记查询模板</td>
        </tr>
      </tbody>
    </table>

    <section class="batch-section">
      <div class="page-head">
        <div>
          <h3>批量查询结果包</h3>
          <p class="page-desc">一次提交多组参数生成的批量结果，每组参数都能追溯到查询结果或失败原因。</p>
        </div>
        <div class="page-actions">
          <input v-model="packageKeyword" data-testid="batch-keyword" placeholder="按批次编号检索" @keyup.enter="reloadPackages" />
          <button class="btn" data-testid="batch-query" type="button" @click="reloadPackages">查询结果包</button>
        </div>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in packageColumns" :key="column">{{ column }}</th>
            <th>批次状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="pkg in packages" :key="String(pkg.id)" :data-testid="`batch-row-${pkg.id}`">
            <td v-for="column in packageColumns" :key="column">{{ pkg[column] ?? '—' }}</td>
            <td><span class="tag" :class="statusTag(String(pkg.status))">{{ pkg.status }}</span></td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`batch-detail-${pkg.id}`" @click="openDetail(Number(pkg.id))">查看明细</button>
              <button class="link" type="button" :data-testid="`batch-export-${pkg.id}`" @click="exportBatch(pkg)">导出</button>
            </td>
          </tr>
          <tr v-if="!packages.length">
            <td :colspan="packageColumns.length + 2" class="empty-state">暂无批量查询结果包，可在模板上发起批量查询</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ packageTotal }} 个批量查询结果包</span>
      </footer>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记查询模板</h3>
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

    <div v-if="showBatch && batchTemplate" class="modal-backdrop" data-testid="batch-modal">
      <form class="modal" @submit.prevent="submitBatch">
        <h3>批量查询：{{ batchTemplate.模板编号 }} {{ batchTemplate.模板名称 }}</h3>
        <dl class="batch-meta">
          <div><dt>模板版本</dt><dd>{{ templateVersion(batchTemplate) }}</dd></div>
          <div><dt>参数定义</dt><dd>{{ batchTemplate.参数定义 || '—' }}</dd></div>
          <div><dt>数据集范围</dt><dd>{{ batchTemplate.数据集范围 || '—' }}</dd></div>
          <div><dt>展开顺序</dt><dd>按参数定义顺序展开，绑定当前模板版本</dd></div>
        </dl>
        <label class="create-field">
          <span>参数表（每行一组参数，JSON 对象；取值可填数组以展开多组组合）</span>
          <textarea
            v-model="batchParamText"
            class="param-input"
            rows="6"
            data-testid="batch-params"
            placeholder='{"门店": "S001", "日期范围": "2026-01"}&#10;{"门店": ["S002", "S003"], "日期范围": ["2026-01", "2026-02"]}'
          ></textarea>
        </label>
        <label class="create-field">
          <span>失败策略（部分组合超出数据集范围时的处理边界）</span>
          <select v-model="batchPolicy" class="param-input" data-testid="batch-policy">
            <option v-for="policy in failurePolicies" :key="policy" :value="policy">{{ policy }}</option>
          </select>
        </label>
        <label class="create-field">
          <span>请求编号（可选，重复提交时返回已有结果包，不生成新批次）</span>
          <input v-model="batchRequestKey" data-testid="batch-request-key" placeholder="如 REQ-20261117-001" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showBatch = false">取消</button>
          <button class="btn primary" data-testid="batch-submit" type="submit" :disabled="batchSubmitting">
            {{ batchSubmitting ? '执行中…' : '提交批量查询' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="showDetail && batchDetail" class="modal-backdrop" data-testid="batch-detail-modal">
      <div class="modal wide">
        <h3>批量查询结果包：{{ batchDetail.批次编号 }}</h3>
        <dl class="batch-meta">
          <div><dt>模板版本</dt><dd>{{ batchDetail.模板版本 }}</dd></div>
          <div><dt>主版本</dt><dd>{{ batchDetail.主版本 }}</dd></div>
          <div><dt>失败策略</dt><dd>{{ batchDetail.失败策略 }}</dd></div>
          <div><dt>批次状态</dt><dd><span class="tag" :class="statusTag(String(batchDetail.status))">{{ batchDetail.status }}</span></dd></div>
          <div><dt>组合总数 / 成功 / 失败</dt><dd>{{ batchDetail.组合总数 }} / {{ batchDetail.成功数 }} / {{ batchDetail.失败数 }}</dd></div>
          <div><dt>提交时间</dt><dd>{{ batchDetail.提交时间 }}</dd></div>
        </dl>

        <template v-if="batchDetail.来源记录">
          <h4>关联来源记录</h4>
          <dl class="batch-meta">
            <div><dt>模板编号</dt><dd>{{ batchDetail.来源记录.模板编号 }}</dd></div>
            <div><dt>模板名称</dt><dd>{{ batchDetail.来源记录.模板名称 }}</dd></div>
            <div><dt>查询语句</dt><dd>{{ batchDetail.来源记录.查询语句 }}</dd></div>
            <div><dt>参数定义</dt><dd>{{ batchDetail.来源记录.参数定义 }}</dd></div>
            <div><dt>数据集范围</dt><dd>{{ batchDetail.来源记录.数据集范围 }}</dd></div>
            <div><dt>模板状态</dt><dd>{{ batchDetail.来源记录.模板状态 }}</dd></div>
          </dl>
        </template>

        <h4>结果明细（每组参数对应查询结果或失败原因）</h4>
        <table class="data-table">
          <thead>
            <tr>
              <th v-for="column in detailColumns" :key="column">{{ column }}</th>
              <th>版本核对</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in batchDetail.结果明细" :key="item.序号" :data-testid="`batch-result-${item.序号}`">
              <td>{{ item.序号 }}</td>
              <td>{{ item.来源组号 }}</td>
              <td>{{ fmtCombo(item.参数组合) }}</td>
              <td><span class="tag" :class="statusTag(String(item.结果状态))">{{ item.结果状态 }}</span></td>
              <td>{{ item.结果行数 ?? '—' }}</td>
              <td>{{ item.结果摘要 || '—' }}</td>
              <td>{{ item.失败原因 || '—' }}</td>
              <td><span class="tag" :class="item.版本一致 ? 'ok' : 'fail'">{{ item.版本一致 ? '一致' : '不一致' }}</span></td>
            </tr>
          </tbody>
        </table>

        <template v-if="batchDetail.失败组合清单 && batchDetail.失败组合清单.length">
          <h4>失败组合清单</h4>
          <table class="data-table">
            <thead>
              <tr><th>序号</th><th>来源组号</th><th>参数组合</th><th>失败原因</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in batchDetail.失败组合清单" :key="item.序号">
                <td>{{ item.序号 }}</td>
                <td>{{ item.来源组号 }}</td>
                <td>{{ fmtCombo(item.参数组合) }}</td>
                <td>{{ item.失败原因 }}</td>
              </tr>
            </tbody>
          </table>
        </template>

        <div class="page-actions">
          <button class="btn" type="button" @click="backToSource">回到来源模板</button>
          <button class="btn" type="button" data-testid="batch-detail-export" @click="exportBatch(batchDetail)">导出结果包</button>
          <button class="btn primary" type="button" @click="showDetail = false">关闭</button>
        </div>
      </div>
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
type PackageRow = Record<string, string | number | null>
type BatchDetail = {
  id: number
  批次编号: string
  模板版本: string
  主版本: string
  失败策略: string
  status: string
  组合总数: number
  成功数: number
  失败数: number
  提交时间: string
  来源记录: Record<string, string | number> | null
  结果明细: Array<Record<string, any>>
  失败组合清单: Array<Record<string, any>>
}

const ENDPOINT = '/api/query_builder'
const columns = ["模板编号", "模板名称", "查询语句", "参数定义", "数据集范围", "超时秒数", "缓存策略", "模板状态"]
const actions = ["保存模板", "发布模板", "停用模板"]
const statuses = ["草稿", "已保存", "已发布", "已停用"]
const metricNames = ["已发布模板", "草稿模板", "停用模板"]
const packageColumns = ["批次编号", "模板编号", "模板版本", "失败策略", "组合总数", "成功数", "失败数", "提交时间"]
const detailColumns = ["序号", "来源组号", "参数组合", "结果状态", "结果行数", "结果摘要", "失败原因"]
const failurePolicies = ["继续执行", "整组回滚"]

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

const packages = ref<PackageRow[]>([])
const packageTotal = ref(0)
const packageKeyword = ref('')
const showBatch = ref(false)
const batchTemplate = ref<Row | null>(null)
const batchParamText = ref('')
const batchPolicy = ref(failurePolicies[0])
const batchRequestKey = ref('')
const batchSubmitting = ref(false)
const showDetail = ref(false)
const batchDetail = ref<BatchDetail | null>(null)

function templateVersion(row: Row): string {
  return `${row.模板编号}@v${row.版本号 ?? 1}`
}

function statusTag(status: string): string {
  if (status === '成功' || status === '已完成') return 'ok'
  if (status === '部分失败' || status === '已回滚') return 'warn'
  return 'fail'
}

function fmtCombo(combo: unknown): string {
  if (!combo || typeof combo !== 'object') return '—'
  return Object.entries(combo as Record<string, unknown>).map(([key, value]) => `${key}=${value}`).join('，')
}

function parseParamSets(text: string): Array<Record<string, unknown>> {
  const sets: Array<Record<string, unknown>> = []
  text.split('\n').forEach((line, index) => {
    const trimmed = line.trim()
    if (!trimmed) return
    try {
      const parsed = JSON.parse(trimmed)
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('not-object')
      sets.push(parsed as Record<string, unknown>)
    } catch {
      throw new Error(`第 ${index + 1} 行参数不是有效的 JSON 对象`)
    }
  })
  return sets
}

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
      throw new Error('OLAP查询动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'OLAP查询操作失败'
  }
}

function openBatch(row: Row) {
  batchTemplate.value = row
  batchParamText.value = ''
  batchPolicy.value = failurePolicies[0]
  batchRequestKey.value = ''
  errorMessage.value = ''
  showBatch.value = true
}

async function submitBatch() {
  if (!batchTemplate.value) return
  batchSubmitting.value = true
  errorMessage.value = ''
  try {
    const paramSets = parseParamSets(batchParamText.value)
    if (!paramSets.length) throw new Error('参数表为空，至少填写一行参数')
    const response = await request(`${ENDPOINT}/${batchTemplate.value.id}/batch`, {
      method: 'POST',
      body: JSON.stringify({
        参数表: paramSets,
        失败策略: batchPolicy.value,
        请求编号: batchRequestKey.value.trim() || null,
      }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '批量查询提交失败')
    showBatch.value = false
    await reloadPackages()
    if (payload.entry?.id) await openDetail(Number(payload.entry.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量查询提交失败'
  } finally {
    batchSubmitting.value = false
  }
}

async function reloadPackages() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (packageKeyword.value.trim()) query.set('keyword', packageKeyword.value.trim())
  try {
    const response = await request(`${ENDPOINT}/batches?${query.toString()}`)
    if (!response.ok) throw new Error('批量查询结果包读取失败')
    const payload = await response.json()
    packages.value = payload.items ?? []
    packageTotal.value = payload.total ?? packages.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量查询结果包读取失败'
  }
}

async function openDetail(batchId: number) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/batches/${batchId}`)
    if (!response.ok) throw new Error('批量查询结果包明细读取失败')
    batchDetail.value = (await response.json()) as BatchDetail
    showDetail.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量查询结果包明细读取失败'
  }
}

async function exportBatch(pkg: PackageRow | BatchDetail) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/batches/${pkg.id}/export`)
    if (!response.ok) throw new Error('批量查询结果包导出失败')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `${pkg.批次编号}.csv`
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量查询结果包导出失败'
  }
}

function backToSource() {
  const code = batchDetail.value?.来源记录?.模板编号
  showDetail.value = false
  if (code) {
    filters.value = { 模板编号: String(code) }
    void reload()
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
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'OLAP查询列表读取失败'
  }
}

onMounted(() => {
  void reload()
  void reloadPackages()
})
</script>
