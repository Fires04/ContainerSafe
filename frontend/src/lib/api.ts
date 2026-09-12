import type {
  Agent,
  AgentEnrollResult,
  BackupBatch,
  BackupJob,
  BackupRun,
  Container,
  CronPreview,
  RestoreRun,
  StorageTarget,
  UploadStageResult,
} from '../types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = await res.text()
    try {
      detail = JSON.parse(detail).detail ?? detail
    } catch {
      // plain text error body, use as-is
    }
    throw new Error(detail || `${init?.method ?? 'GET'} ${path} failed: ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

function jsonInit(method: string, body: unknown): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
}

export interface JobPayload {
  host_id: string
  identity_keys: string[]
  display_name?: string
  schedule_cron: string
  retention_count?: number | null
  retention_days?: number | null
  storage_target_id: number
  dest_subpath?: string
  include_bind_mounts?: boolean
  enabled?: boolean
}

export const api = {
  listContainers: () => request<Container[]>('/api/containers'),
  refreshContainers: () => request<Container[]>('/api/containers/refresh', { method: 'POST' }),

  listTargets: () => request<StorageTarget[]>('/api/targets'),
  createTarget: (body: { name: string; type: string; config: Record<string, unknown> }) =>
    request<StorageTarget>('/api/targets', jsonInit('POST', body)),
  deleteTarget: (id: number) => request<void>(`/api/targets/${id}`, { method: 'DELETE' }),

  listJobs: () => request<BackupJob[]>('/api/jobs'),
  createJob: (body: JobPayload) => request<BackupJob>('/api/jobs', jsonInit('POST', body)),
  updateJob: (id: number, body: JobPayload) => request<BackupJob>(`/api/jobs/${id}`, jsonInit('PUT', body)),
  deleteJob: (id: number) => request<void>(`/api/jobs/${id}`, { method: 'DELETE' }),
  runJobNow: (id: number) => request<BackupBatch>(`/api/jobs/${id}/run-now`, { method: 'POST' }),
  listRuns: (jobId: number) => request<BackupRun[]>(`/api/jobs/${jobId}/runs`),
  recentRuns: (limit = 20) => request<BackupRun[]>(`/api/runs/recent?limit=${limit}`),
  cronPreview: (expr: string, count = 3) =>
    request<CronPreview>(`/api/jobs/cron-preview?expr=${encodeURIComponent(expr)}&count=${count}`),

  restoreRun: (runId: number, body: { container_name?: string; allow_takeover?: boolean } = {}) =>
    request<RestoreRun>(`/api/runs/${runId}/restore`, jsonInit('POST', body)),
  downloadRunUrl: (runId: number) => `/api/runs/${runId}/download`,

  uploadRestoreArchive: async (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<UploadStageResult>('/api/restore/upload', { method: 'POST', body: form })
  },
  confirmRestoreUpload: (uploadId: string, body: { container_name?: string; allow_takeover?: boolean } = {}) =>
    request<RestoreRun>(`/api/restore/upload/${uploadId}/confirm`, jsonInit('POST', body)),
  cancelRestoreUpload: (uploadId: string) =>
    request<void>(`/api/restore/upload/${uploadId}`, { method: 'DELETE' }),

  listAgents: () => request<Agent[]>('/api/agents'),
  enrollAgent: (hostId: string) => request<AgentEnrollResult>('/api/agents', jsonInit('POST', { host_id: hostId })),
  deleteAgent: (id: number) => request<void>(`/api/agents/${id}`, { method: 'DELETE' }),
}
