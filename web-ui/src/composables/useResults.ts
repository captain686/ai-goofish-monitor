import { ref, reactive, watch, onMounted, computed } from 'vue'
import { useRoute } from 'vue-router'
import type { ResultInsights, ResultItem } from '@/types/result.d.ts'
import * as resultsApi from '@/api/results'
import type { ResultFileOption, GetResultContentParams } from '@/api/results'
import { useWebSocket } from '@/composables/useWebSocket'

type ResultFileEntry = ResultFileOption & { value?: string }

// ---------- helpers ----------
function isLikelyJsonObjectString(v: string): boolean {
  const t = v.trim()
  return (t.startsWith('{') && t.endsWith('}')) || t === '[object Object]'
}

function sanitizeFileName(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const t = value.trim()
  if (!t) return null
  if (isLikelyJsonObjectString(t)) return null
  return t
}

function resolveFileName(entry: ResultFileEntry): string {
  const raw = entry.file_name ?? entry.value ?? ''
  if (typeof raw !== 'string') return ''
  const t = raw.trim()
  if (!t || isLikelyJsonObjectString(t)) return ''
  return t
}

function isValidFileNameForApi(v: string): boolean {
  return /^[^\\/\\*?:"<>|\n]+\.jsonl$/.test(v)
}

// ---------- composable ----------
export function useResults() {
  const route = useRoute()

  // ---- state ----
  const fileEntries = ref<ResultFileOption[]>([])
  const selectedFile = ref<string | null>(null)
  const results = ref<ResultItem[]>([])
  const insights = ref<ResultInsights | null>(null)
  const totalItems = ref(0)
  const page = ref(1)
  const limit = ref(100)
  const blacklistKeywords = ref<string[]>([])
  const isFileOptionsReady = ref(false)
  const hasFetchedFiles = ref(false)
  const isSavingBlacklist = ref(false)
  let readyTimer: ReturnType<typeof setTimeout> | null = null

  const STORAGE_KEY_FILTERS = 'resultFilters'

  function loadPersistedFilters(): Required<Omit<GetResultContentParams, 'page' | 'limit'>> {
    const defaults: Required<Omit<GetResultContentParams, 'page' | 'limit'>> = {
      recommended_only: false,
      ai_recommended_only: false,
      keyword_recommended_only: false,
      include_hidden: false,
      sort_by: 'crawl_time',
      sort_order: 'desc',
    }
    try {
      const saved = localStorage.getItem(STORAGE_KEY_FILTERS)
      if (saved) return { ...defaults, ...JSON.parse(saved) }
    } catch { /* ignore */ }
    return defaults
  }

  const filters = reactive<Required<Omit<GetResultContentParams, 'page' | 'limit'>>>(loadPersistedFilters())

  const isLoading = ref(false)
  const error = ref<Error | null>(null)
  const { on } = useWebSocket()

  // ---- internal helpers ----
  function scheduleFileOptionsReady() {
    if (isFileOptionsReady.value || !hasFetchedFiles.value) return
    if (readyTimer) return
    isFileOptionsReady.value = true
  }

  function setSelectedFileSafely(file: string | null, source: string) {
    const safe = sanitizeFileName(file)
    if (safe !== file) {
      console.warn('[useResults] sanitize selectedFile', { source, raw: file, safe })
    }
    // only assign if different (avoid unnecessary watcher triggers)
    if (safe !== selectedFile.value) {
      console.log('[useResults] selectedFile set', { source, from: selectedFile.value, to: safe })
      selectedFile.value = safe
    }
  }

  // ---- data fetching ----
  async function fetchFiles() {
    try {
      const fileList = await resultsApi.getResultFiles()
      fileEntries.value = fileList

      const validFiles = fileList
        .map((item) => resolveFileName(item as ResultFileEntry))
        .filter(Boolean) as string[]

      // rule: ONLY this function may write selectedFile
      if (selectedFile.value && validFiles.includes(selectedFile.value)) {
        // keep current if still valid
        return
      }

      // route override (one-shot)
      const routeFile = sanitizeFileName(route.query.file)
      if (routeFile && validFiles.includes(routeFile)) {
        setSelectedFileSafely(routeFile, 'fetchFiles:route.query.file')
        return
      }

      // fallback: first valid file
      setSelectedFileSafely(validFiles[0] || null, 'fetchFiles:first-valid-file')
    } catch (e) {
      if (e instanceof Error) error.value = e
    } finally {
      hasFetchedFiles.value = true
      scheduleFileOptionsReady()
    }
  }

  async function fetchResults() {
    const file = sanitizeFileName(selectedFile.value)
    console.log('[useResults] fetchResults input', { raw: selectedFile.value, safe: file })
    if (!file || !isValidFileNameForApi(file)) {
      console.warn('[useResults] fetchResults skipped invalid filename', { raw: selectedFile.value, safe: file })
      results.value = []
      totalItems.value = 0
      return
    }

    isLoading.value = true
    error.value = null
    try {
      const data = await resultsApi.getResultContent(file, {
        ...filters,
        page: page.value,
        limit: limit.value,
      })
      results.value = data.items
      totalItems.value = data.total_items
    } catch (e) {
      if (e instanceof Error) error.value = e
      results.value = []
      totalItems.value = 0
    } finally {
      isLoading.value = false
    }
  }

  async function fetchInsights() {
    const file = sanitizeFileName(selectedFile.value)
    if (!file || !isValidFileNameForApi(file)) {
      insights.value = null
      return
    }
    try {
      insights.value = await resultsApi.getResultInsights(file)
    } catch (e) {
      if (e instanceof Error) error.value = e
      insights.value = null
    }
  }

  async function fetchBlacklistRules() {
    const file = sanitizeFileName(selectedFile.value)
    if (!file || !isValidFileNameForApi(file)) {
      blacklistKeywords.value = []
      return
    }
    try {
      const data = await resultsApi.getResultBlacklistRules(file)
      blacklistKeywords.value = data.keywords || []
    } catch (e) {
      if (e instanceof Error) error.value = e
      blacklistKeywords.value = []
    }
  }

  // ---- exposed actions ----
  async function refreshResults() {
    const current = selectedFile.value
    await fetchFiles()
    if (selectedFile.value && selectedFile.value === current) {
      await fetchResults()
      await fetchInsights()
      await fetchBlacklistRules()
    }
  }

  function exportSelectedResults() {
    const file = sanitizeFileName(selectedFile.value)
    if (!file || !isValidFileNameForApi(file)) return
    resultsApi.downloadResultExport(file, { ...filters })
  }

  async function deleteSelectedFile(filename?: string) {
    const target = sanitizeFileName(filename) || sanitizeFileName(selectedFile.value)
    if (!target || !isValidFileNameForApi(target)) return
    isLoading.value = true
    error.value = null
    try {
      await resultsApi.deleteResultFile(target)
      await fetchFiles() // this will also update selectedFile correctly
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function toggleItemBlock(item: ResultItem) {
    const file = sanitizeFileName(selectedFile.value)
    if (!file || !isValidFileNameForApi(file)) return
    const itemId = item.商品信息?.商品ID
    if (!itemId) return
    const newStatus = item._status === 'hidden' ? 'active' : 'hidden'
    try {
      await resultsApi.updateItemStatus(file, itemId, newStatus)
      await fetchResults()
    } catch (e) {
      if (e instanceof Error) error.value = e
    }
  }

  async function saveBlacklistRules(keywords: string[]) {
    const file = sanitizeFileName(selectedFile.value)
    if (!file || !isValidFileNameForApi(file)) return
    isSavingBlacklist.value = true
    error.value = null
    try {
      const data = await resultsApi.updateResultBlacklistRules(file, keywords)
      blacklistKeywords.value = data.keywords || []
      await fetchResults()
      await fetchInsights()
    } catch (e) {
      if (e instanceof Error) error.value = e
      throw e
    } finally {
      isSavingBlacklist.value = false
    }
  }

  // ---- watchers (controlled & minimal) ----
  watch(filters, (val) => {
    localStorage.setItem(STORAGE_KEY_FILTERS, JSON.stringify(val))
    page.value = 1
    fetchResults()
  }, { deep: true })

  // single controlled watcher for selectedFile changes
  watch(selectedFile, (file) => {
    const safe = sanitizeFileName(file)
    console.log('[useResults] watch(selectedFile)', { raw: file, safe })
    if (safe !== file) {
      // someone wrote unsafe value; correct it silently
      console.warn('[useResults] correcting unsafe selectedFile', { raw: file, safe })
      selectedFile.value = safe
      return
    }
    if (!safe) return
    fetchResults()
    fetchInsights()
    fetchBlacklistRules()
  })

  // route.file only affects initial choice (handled in fetchFiles)
  // but also listen for changes while staying safe
  watch(
    () => route.query.file,
    (q) => {
      const routeFile = sanitizeFileName(q)
      if (!routeFile) return
      const validFiles = fileEntries.value
        .map((item) => resolveFileName(item as ResultFileEntry))
        .filter(Boolean)
      if (validFiles.includes(routeFile)) {
        setSelectedFileSafely(routeFile, 'watch:route.query.file')
      }
    }
  )

  // real-time updates
  on('results_updated', async () => {
    const oldFile = selectedFile.value
    await fetchFiles()
    // if file unchanged, refresh content
    if (selectedFile.value && selectedFile.value === oldFile) {
      fetchResults()
      fetchInsights()
    }
  })

  // ---- computed ----
  const fileOptions = computed(() =>
    fileEntries.value
      .map((file) => {
        const resolved = resolveFileName(file as ResultFileEntry)
        if (!resolved) return null
        return {
          value: resolved,
          file_name: resolved,
          taskName: file.task_name || resolved,
          task_name: file.task_name || resolved,
          label: file.task_name || resolved,
        }
      })
      .filter((item): item is NonNullable<typeof item> => item !== null)
  )

  // ---- lifecycle ----
  onMounted(() => {
    fetchFiles()
  })

  return {
    fileEntries,
    selectedFile,
    results,
    insights,
    totalItems,
    filters,
    isLoading,
    error,
    fetchFiles,
    refreshResults,
    exportSelectedResults,
    deleteSelectedFile,
    toggleItemBlock,
    blacklistKeywords,
    isSavingBlacklist,
    saveBlacklistRules,
    fileOptions,
    isFileOptionsReady,
  }
}
