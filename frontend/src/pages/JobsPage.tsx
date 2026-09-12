import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import cronstrue from 'cronstrue'
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Collapse,
  Group,
  Loader,
  Modal,
  MultiSelect,
  NumberInput,
  Paper,
  Progress,
  Select,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import {
  IconAlertCircle,
  IconChevronDown,
  IconDownload,
  IconEdit,
  IconPlayerPlay,
  IconPlus,
  IconTrash,
} from '@tabler/icons-react'
import { api } from '../lib/api'
import type { BackupJob, BackupRun, Container, CronPreview, RestoreRun, StorageTarget } from '../types'

const STAGE_ORDER = ['inspecting', 'archiving', 'packaging', 'uploading', 'done']
const STAGE_LABELS: Record<string, string> = {
  inspecting: 'Inspecting',
  archiving: 'Archiving',
  packaging: 'Packaging',
  uploading: 'Uploading',
  done: 'Done',
}
const STATUS_COLOR: Record<string, string> = { success: 'teal', failed: 'red', running: 'flame', pending: 'gray' }
const CRON_PRESETS = [
  { label: 'Daily 03:00', value: '0 3 * * *' },
  { label: 'Every 6h', value: '0 */6 * * *' },
  { label: 'Hourly', value: '0 * * * *' },
  { label: 'Weekly (Sun 03:00)', value: '0 3 * * 0' },
]

export default function JobsPage() {
  const [jobs, setJobs] = useState<BackupJob[] | null>(null)
  const [containers, setContainers] = useState<Container[]>([])
  const [targets, setTargets] = useState<StorageTarget[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<BackupJob | 'new' | null>(null)

  function load() {
    api.listJobs().then(setJobs).catch((err: Error) => setError(err.message))
    api.listContainers().then(setContainers).catch(() => {})
    api.listTargets().then(setTargets).catch(() => {})
  }

  useEffect(load, [])

  async function handleDelete(id: number) {
    if (!confirm('Delete this backup job? Run history stays until pruned separately.')) return
    try {
      await api.deleteJob(id)
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  if (targets.length === 0 && editing === null) {
    return (
      <Group justify="center" mt="xl">
        <Text c="dimmed">Create a storage target first (Storage targets), then come back to configure a job.</Text>
      </Group>
    )
  }

  return (
    <div>
      <Group justify="space-between" mb="lg">
        <div>
          <Title order={2}>Backup jobs</Title>
          <Text c="dimmed" size="sm">
            Per-container schedules
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setEditing('new')}>
          New job
        </Button>
      </Group>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />} mb="md">
          {error}
        </Alert>
      )}

      <Modal
        opened={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === 'new' ? 'New backup job' : 'Edit backup job'}
        size="lg"
      >
        {editing !== null && (
          <JobForm
            job={editing === 'new' ? null : editing}
            containers={containers}
            targets={targets}
            onSaved={() => {
              setEditing(null)
              load()
            }}
            onCancel={() => setEditing(null)}
            onError={setError}
          />
        )}
      </Modal>

      {jobs === null && !error && <Loader color="flame" />}
      {jobs?.length === 0 && <Text c="dimmed">No backup jobs yet.</Text>}

      <Stack gap="md">
        {jobs?.map((j) => (
          <JobRow key={j.id} job={j} onEdit={() => setEditing(j)} onDelete={() => handleDelete(j.id)} onError={setError} />
        ))}
      </Stack>
    </div>
  )
}

function JobRow({
  job,
  onEdit,
  onDelete,
  onError,
}: {
  job: BackupJob
  onEdit: () => void
  onDelete: () => void
  onError: (msg: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [runs, setRuns] = useState<BackupRun[] | null>(null)
  const [running, setRunning] = useState(false)

  function loadRuns() {
    api
      .listRuns(job.id)
      .then(setRuns)
      .catch((err: Error) => onError(err.message))
  }

  useEffect(() => {
    if (!expanded) return
    loadRuns()
    const id = setInterval(loadRuns, 1500)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded])

  async function handleRunNow() {
    setRunning(true)
    onError('')
    try {
      await api.runJobNow(job.id)
      setExpanded(true)
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setRunning(false)
    }
  }

  const batches = groupByBatch(runs ?? [])

  return (
    <Card withBorder radius="md" padding="md">
      <Group justify="space-between" wrap="nowrap">
        <div style={{ minWidth: 0 }}>
          <Group gap={6}>
            <Text fw={600} size="sm">
              {job.display_name}
            </Text>
            {job.host_id !== 'local' && (
              <Badge size="xs" color="flame" variant="outline">
                {job.host_id}
              </Badge>
            )}
            {!job.enabled && (
              <Badge size="xs" color="gray" variant="light">
                disabled
              </Badge>
            )}
          </Group>
          <Text size="xs" c="dimmed" truncate>
            {job.container_names.join(', ')} · cron <code>{job.schedule_cron}</code> · {job.storage_target_name ?? '?'}
            {job.retention_count ? ` · keep last ${job.retention_count}` : ''}
            {job.next_run_at ? ` · next ${new Date(job.next_run_at).toLocaleString()}` : ''}
          </Text>
        </div>
        <Group gap={4} wrap="nowrap">
          <Button
            size="xs"
            variant="subtle"
            rightSection={
              <IconChevronDown size={14} style={{ transform: expanded ? 'rotate(180deg)' : undefined }} />
            }
            onClick={() => setExpanded((v) => !v)}
          >
            Runs
          </Button>
          <ActionIcon variant="light" color="flame" onClick={handleRunNow} loading={running} title="Run now">
            <IconPlayerPlay size={16} />
          </ActionIcon>
          <ActionIcon variant="subtle" onClick={onEdit} title="Edit">
            <IconEdit size={16} />
          </ActionIcon>
          <ActionIcon variant="subtle" color="red" onClick={onDelete} title="Delete">
            <IconTrash size={16} />
          </ActionIcon>
        </Group>
      </Group>

      <Collapse expanded={expanded}>
        <Stack gap="sm" mt="md" pt="md" style={{ borderTop: '1px solid var(--mantine-color-default-border)' }}>
          {runs === null && <Loader size="sm" color="flame" />}
          {runs?.length === 0 && (
            <Text c="dimmed" size="sm">
              No runs yet.
            </Text>
          )}
          {batches.groupEntries.map(([batchId, batchRuns]) => (
            <BatchGroup key={batchId} runs={batchRuns} expectedCount={job.identity_keys.length} />
          ))}
          {batches.ungrouped.map((r) => (
            <RunCard key={r.id} run={r} />
          ))}
        </Stack>
      </Collapse>
    </Card>
  )
}

function groupByBatch(runs: BackupRun[]) {
  const groups = new Map<string, BackupRun[]>()
  const ungrouped: BackupRun[] = []
  for (const r of runs) {
    if (r.batch_id) {
      const arr = groups.get(r.batch_id) ?? []
      arr.push(r)
      groups.set(r.batch_id, arr)
    } else {
      ungrouped.push(r)
    }
  }
  const groupEntries = [...groups.entries()].sort(
    (a, b) => new Date(b[1][0].started_at).getTime() - new Date(a[1][0].started_at).getTime(),
  )
  return { groupEntries, ungrouped }
}

function BatchGroup({ runs, expectedCount }: { runs: BackupRun[]; expectedCount: number }) {
  const total = Math.max(expectedCount, runs.length)
  const finished = runs.filter((r) => r.status === 'success' || r.status === 'failed').length
  const succeeded = runs.filter((r) => r.status === 'success').length
  const isActive = finished < total
  const [open, setOpen] = useState(isActive)

  useEffect(() => {
    if (isActive) setOpen(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive])

  if (runs.length === 1 && !isActive && total === 1) return <RunCard run={runs[0]} />

  return (
    <div>
      <Group gap={8} onClick={() => setOpen((v) => !v)} style={{ cursor: 'pointer' }} mb={6}>
        <IconChevronDown
          size={14}
          style={{ transform: open ? undefined : 'rotate(-90deg)', transition: 'transform 0.15s' }}
        />
        <Badge color={isActive ? 'flame' : succeeded === total ? 'teal' : 'red'} variant="light">
          {isActive ? `Running ${finished}/${total}` : `${succeeded}/${total} succeeded`}
        </Badge>
        <Text size="sm" c="dimmed">
          {new Date(runs[0].started_at).toLocaleString()}
        </Text>
      </Group>
      {isActive && <Progress value={(finished / total) * 100} size="sm" mb={8} color="flame" animated />}
      <Collapse expanded={open}>
        <Stack gap={6} pl="lg">
          {runs.map((r) => (
            <RunCard key={r.id} run={r} showContainer />
          ))}
        </Stack>
      </Collapse>
    </div>
  )
}

function stagePercent(run: BackupRun): number {
  if (run.status === 'success') return 100
  const idx = STAGE_ORDER.indexOf(run.current_stage ?? 'inspecting')
  const base = idx >= 0 ? idx : 0
  const span = 100 / (STAGE_ORDER.length - 1)
  let pct = base * span
  if (run.current_stage === 'archiving' && run.progress_total) {
    pct += (Math.min(run.progress_current ?? 0, run.progress_total) / run.progress_total) * span
  }
  return Math.min(100, pct)
}

function RunCard({ run, showContainer }: { run: BackupRun; showContainer?: boolean }) {
  const [showLog, setShowLog] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [restoreResult, setRestoreResult] = useState<RestoreRun | null>(null)
  const sizeKb = run.size_bytes ? `${(run.size_bytes / 1024).toFixed(1)} KB` : ''
  const isActive = run.status === 'running' || run.status === 'pending'

  async function handleRestore() {
    if (!confirm('Restore this backup as a new running container? A name clash with an existing container gets an auto-suffixed name.'))
      return
    setRestoring(true)
    setRestoreResult(null)
    try {
      setRestoreResult(await api.restoreRun(run.id))
    } catch (err) {
      setRestoreResult({
        id: -1,
        source_archive_locator: run.archive_locator ?? '',
        target_identity_key: null,
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
        status: 'failed',
        log_text: null,
        error_message: (err as Error).message,
      })
    } finally {
      setRestoring(false)
    }
  }

  return (
    <Paper withBorder p="sm" radius="sm">
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Group gap="sm">
          <Badge color={STATUS_COLOR[run.status] ?? 'gray'} size="md" variant="light">
            {run.status}
          </Badge>
          {showContainer && (
            <Text size="sm" fw={500}>
              {run.identity_key}
            </Text>
          )}
          <Text size="sm" c="dimmed">
            {new Date(run.started_at).toLocaleString()}
          </Text>
        </Group>
        <Group gap={6}>
          {isActive && run.current_stage && (
            <Text size="xs" c="dimmed">
              {STAGE_LABELS[run.current_stage] ?? run.current_stage}
              {run.current_stage === 'archiving' && run.progress_total
                ? ` (${run.progress_current}/${run.progress_total})`
                : ''}
            </Text>
          )}
          {run.status === 'success' && (
            <>
              <Button
                size="xs"
                variant="light"
                leftSection={<IconDownload size={14} />}
                component="a"
                href={api.downloadRunUrl(run.id)}
              >
                Download
              </Button>
              <Button size="xs" variant="outline" onClick={handleRestore} loading={restoring}>
                Restore
              </Button>
            </>
          )}
          <ActionIcon size="sm" variant="subtle" onClick={() => setShowLog((v) => !v)}>
            <IconChevronDown size={14} />
          </ActionIcon>
        </Group>
      </Group>

      {isActive && <Progress value={stagePercent(run)} size="sm" mt={8} color="flame" animated />}

      {run.archive_locator && (
        <Text size="xs" c="dimmed" ff="monospace" mt={6}>
          {run.archive_locator} {sizeKb && `· ${sizeKb}`}
        </Text>
      )}
      {run.error_message && (
        <Text size="xs" c="red" mt={6}>
          {run.error_message}
        </Text>
      )}

      <Collapse expanded={showLog}>
        {run.log_text && (
          <Paper bg="var(--mantine-color-default-hover)" p={8} mt={8} radius="sm">
            <Text component="pre" size="xs" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
              {run.log_text}
            </Text>
          </Paper>
        )}
      </Collapse>

      {restoreResult && (
        <Text size="sm" c={restoreResult.status === 'success' ? 'teal' : 'red'} mt={8}>
          Restore {restoreResult.status}
          {restoreResult.target_identity_key && <> as container {restoreResult.target_identity_key}</>}
          {restoreResult.error_message && <> — {restoreResult.error_message}</>}
        </Text>
      )}
    </Paper>
  )
}

function JobForm({
  job,
  containers,
  targets,
  onSaved,
  onCancel,
  onError,
}: {
  job: BackupJob | null
  containers: Container[]
  targets: StorageTarget[]
  onSaved: () => void
  onCancel: () => void
  onError: (msg: string) => void
}) {
  const [hostId, setHostId] = useState(job?.host_id ?? 'local')
  const [identityKeys, setIdentityKeys] = useState<string[]>(job?.identity_keys ?? [])
  const [displayName, setDisplayName] = useState(job?.display_name ?? '')
  const [cron, setCron] = useState(job?.schedule_cron ?? '0 3 * * *')
  const [cronDescription, setCronDescription] = useState<string | null>(null)
  const [cronPreview, setCronPreview] = useState<CronPreview | null>(null)
  const [targetId, setTargetId] = useState<string>(String(job?.storage_target_id ?? targets[0]?.id ?? ''))
  const [retentionCount, setRetentionCount] = useState<number | ''>(job?.retention_count ?? 7)
  const [enabled, setEnabled] = useState(job?.enabled ?? true)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let cancelled = false
    try {
      setCronDescription(cronstrue.toString(cron))
    } catch {
      setCronDescription(null)
    }
    const id = setTimeout(() => {
      api
        .cronPreview(cron, 3)
        .then((p) => !cancelled && setCronPreview(p))
        .catch(() => !cancelled && setCronPreview(null))
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(id)
    }
  }, [cron])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (identityKeys.length === 0) {
      onError('Pick at least one container')
      return
    }
    setSubmitting(true)
    onError('')
    try {
      const body = {
        host_id: hostId,
        identity_keys: identityKeys,
        display_name: displayName || undefined,
        schedule_cron: cron,
        retention_count: retentionCount === '' ? null : retentionCount,
        retention_days: null,
        storage_target_id: Number(targetId),
        dest_subpath: '',
        include_bind_mounts: true,
        enabled,
      }
      if (job) {
        await api.updateJob(job.id, body)
      } else {
        await api.createJob(body)
      }
      onSaved()
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const hostIds = [...new Set(containers.map((c) => c.host_id))].sort((a, b) => (a === 'local' ? -1 : b === 'local' ? 1 : a.localeCompare(b)))
  const containersOnHost = containers.filter((c) => c.host_id === hostId)

  return (
    <form onSubmit={handleSubmit}>
      <Select
        label="Host"
        description="A job's containers must all live on the same host"
        data={hostIds.map((h) => ({ value: h, label: h === 'local' ? 'local (this server)' : h }))}
        value={hostId}
        onChange={(v) => {
          setHostId(v ?? 'local')
          setIdentityKeys([])
        }}
        disabled={job !== null}
        required
        mb="sm"
      />
      <MultiSelect
        label="Containers"
        description="Pick every container this job should back up together — picked ones drop off this list"
        data={containersOnHost.map((c) => ({ value: c.identity_key, label: c.name }))}
        value={identityKeys}
        onChange={setIdentityKeys}
        searchable
        hidePickedOptions
        required
        mb="sm"
      />
      <TextInput
        label="Display name (optional)"
        value={displayName}
        onChange={(e) => setDisplayName(e.currentTarget.value)}
        mb="sm"
      />

      <Group gap={6} mb={6}>
        {CRON_PRESETS.map((p) => (
          <Button
            key={p.value}
            size="compact-xs"
            variant={cron === p.value ? 'filled' : 'light'}
            onClick={() => setCron(p.value)}
            type="button"
          >
            {p.label}
          </Button>
        ))}
      </Group>
      <TextInput
        label="Schedule (5-field cron, UTC)"
        value={cron}
        onChange={(e) => setCron(e.currentTarget.value)}
        required
        placeholder="0 3 * * *"
        mb={4}
      />
      {cronDescription && (
        <Text size="xs" c="dimmed" mb={2}>
          {cronDescription}
        </Text>
      )}
      {cronPreview?.valid && cronPreview.next_runs.length > 0 && (
        <Text size="xs" c="dimmed" mb="sm">
          Next runs: {cronPreview.next_runs.map((t) => new Date(t).toLocaleString()).join('  ·  ')}
        </Text>
      )}
      {cronPreview && !cronPreview.valid && (
        <Text size="xs" c="red" mb="sm">
          {cronPreview.error}
        </Text>
      )}

      <Select
        label="Storage target"
        data={targets.map((t) => ({ value: String(t.id), label: t.name }))}
        value={targetId}
        onChange={(v) => setTargetId(v ?? '')}
        required
        mb="sm"
      />
      <NumberInput
        label="Keep last N runs per container (optional)"
        value={retentionCount}
        onChange={(v) => setRetentionCount(v === '' ? '' : Number(v))}
        min={1}
        mb="sm"
      />
      <Checkbox label="Enabled" checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} mb="md" />

      <Group justify="flex-end">
        <Button variant="subtle" onClick={onCancel} type="button">
          Cancel
        </Button>
        <Button type="submit" loading={submitting}>
          {job ? 'Save changes' : 'Create job'}
        </Button>
      </Group>
    </form>
  )
}
