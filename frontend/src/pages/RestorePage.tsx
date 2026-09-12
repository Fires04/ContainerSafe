import { useState } from 'react'
import type { ChangeEvent } from 'react'
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  FileInput,
  Group,
  Paper,
  Stepper,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { IconAlertCircle, IconFileUpload } from '@tabler/icons-react'
import { api } from '../lib/api'
import type { RestoreRun, UploadPreview } from '../types'

export default function RestorePage() {
  const [active, setActive] = useState(0)
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadId, setUploadId] = useState<string | null>(null)
  const [preview, setPreview] = useState<UploadPreview | null>(null)
  const [containerName, setContainerName] = useState('')
  const [allowTakeover, setAllowTakeover] = useState(false)
  const [restoring, setRestoring] = useState(false)
  const [result, setResult] = useState<RestoreRun | null>(null)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setActive(0)
    setFile(null)
    setUploadId(null)
    setPreview(null)
    setContainerName('')
    setAllowTakeover(false)
    setResult(null)
    setError(null)
  }

  function handleFileChange(f: File | null) {
    setFile(f)
  }

  async function handleUpload() {
    if (!file) return
    setUploading(true)
    setError(null)
    try {
      const staged = await api.uploadRestoreArchive(file)
      setUploadId(staged.upload_id)
      setPreview(staged.preview)
      setContainerName(staged.preview.container_name ?? '')
      setActive(1)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setUploading(false)
    }
  }

  async function handleCancelPreview() {
    if (uploadId) await api.cancelRestoreUpload(uploadId).catch(() => {})
    reset()
  }

  async function handleConfirm() {
    if (!uploadId) return
    setRestoring(true)
    setError(null)
    try {
      const body: { container_name?: string; allow_takeover?: boolean } = { allow_takeover: allowTakeover }
      if (containerName && containerName !== preview?.container_name) body.container_name = containerName
      setResult(await api.confirmRestoreUpload(uploadId, body))
      setActive(2)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setRestoring(false)
    }
  }

  return (
    <div>
      <Title order={2} mb={4}>
        Restore from file
      </Title>
      <Text c="dimmed" size="sm" mb="lg">
        Restore a container from a .tar.gz archive you provide
      </Text>

      <Stepper active={active} onStepClick={undefined} allowNextStepsSelect={false} mb="lg">
        <Stepper.Step label="Upload" description="Pick an archive" />
        <Stepper.Step label="Review" description="Check what will happen" />
        <Stepper.Step label="Result" description="Done" />
      </Stepper>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />} mb="md">
          {error}
        </Alert>
      )}

      {active === 0 && (
        <Paper withBorder p="lg" radius="md" maw={560}>
          <Text size="sm" mb="md">
            Pick a <code>.tar.gz</code> archive produced by this app (or another ContainerSafe instance) —
            its own recorded backups can also be restored directly from the Backup jobs tab without downloading
            anything first.
          </Text>
          <FileInput
            leftSection={<IconFileUpload size={16} />}
            placeholder="Choose archive"
            accept=".tar.gz,.tgz"
            value={file}
            onChange={handleFileChange}
            mb="md"
          />
          <Button onClick={handleUpload} disabled={!file} loading={uploading}>
            Upload &amp; review
          </Button>
        </Paper>
      )}

      {active === 1 && preview && (
        <Paper withBorder p="lg" radius="md" maw={640}>
          {preview.name_conflict && (
            <Alert color={preview.name_conflict_running ? 'red' : 'yellow'} mb="md">
              {preview.name_conflict_running
                ? `A container named "${preview.container_name}" is already RUNNING. It won't be touched — restoring will use a different name unless you change it below.`
                : `A stopped container named "${preview.container_name}" already exists. Check "take over its name" below to remove it and reuse the name, or leave unchecked to restore under a new name.`}
            </Alert>
          )}

          <Text fw={600} size="xs" tt="uppercase" c="dimmed" mb={6}>
            Container
          </Text>
          <Table fz="sm" mb="md">
            <Table.Tbody>
              <Table.Tr>
                <Table.Td c="dimmed">Original name</Table.Td>
                <Table.Td>{preview.container_name}</Table.Td>
              </Table.Tr>
              <Table.Tr>
                <Table.Td c="dimmed">Image</Table.Td>
                <Table.Td>{preview.image}</Table.Td>
              </Table.Tr>
              {preview.compose?.project && (
                <Table.Tr>
                  <Table.Td c="dimmed">Compose</Table.Td>
                  <Table.Td>
                    {preview.compose.project} / {preview.compose.service}
                  </Table.Td>
                </Table.Tr>
              )}
              <Table.Tr>
                <Table.Td c="dimmed">Backed up at</Table.Td>
                <Table.Td>{preview.created_at ? new Date(preview.created_at).toLocaleString() : '—'}</Table.Td>
              </Table.Tr>
            </Table.Tbody>
          </Table>

          <Text fw={600} size="xs" tt="uppercase" c="dimmed" mb={6}>
            Networks ({preview.networks.length})
          </Text>
          <Group gap={4} mb="md">
            {preview.networks.map((n, i) => (
              <Badge key={i} variant="light" color="shield" size="sm">
                {n.name} {n.driver && `(${n.driver})`}
              </Badge>
            ))}
            {preview.networks.length === 0 && (
              <Text c="dimmed" size="xs">
                none
              </Text>
            )}
          </Group>

          <Text fw={600} size="xs" tt="uppercase" c="dimmed" mb={6}>
            Volumes ({preview.volumes.length})
          </Text>
          <Group gap={4} mb="md">
            {preview.volumes.map((v, i) => (
              <Badge key={i} variant="light" color="shield" size="sm">
                {v.name} {v.driver && `(${v.driver})`}
              </Badge>
            ))}
            {preview.volumes.length === 0 && (
              <Text c="dimmed" size="xs">
                none
              </Text>
            )}
          </Group>

          <Text fw={600} size="xs" tt="uppercase" c="dimmed" mb={6}>
            Data to restore ({preview.data_manifest.length})
          </Text>
          <Table fz="xs" mb="md">
            <Table.Tbody>
              {preview.data_manifest.map((m, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{m.kind}</Table.Td>
                  <Table.Td>{m.name}</Table.Td>
                  <Table.Td>→ {m.destination}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>

          <TextInput
            label="Container name"
            value={containerName}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setContainerName(e.currentTarget.value)}
            mb="sm"
          />
          <Checkbox
            label="Take over the existing stopped container's name (removes it)"
            checked={allowTakeover}
            disabled={!preview.name_conflict || !!preview.name_conflict_running}
            onChange={(e) => setAllowTakeover(e.currentTarget.checked)}
            mb="md"
          />

          <Group>
            <Button onClick={handleConfirm} loading={restoring}>
              Restore now
            </Button>
            <Button variant="subtle" onClick={handleCancelPreview} disabled={restoring}>
              Cancel
            </Button>
          </Group>
        </Paper>
      )}

      {active === 2 && result && (
        <Paper withBorder p="lg" radius="md" maw={640}>
          <Text fw={600} c={result.status === 'success' ? 'teal' : 'red'} mb="sm">
            Restore {result.status}
            {result.target_identity_key && <> as container {result.target_identity_key}</>}
            {result.error_message && <> — {result.error_message}</>}
          </Text>
          {result.log_text && (
            <Paper bg="var(--mantine-color-default-hover)" p="sm" radius="sm" mb="md">
              <Text component="pre" fz={11} style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                {result.log_text}
              </Text>
            </Paper>
          )}
          <Button onClick={reset}>Restore another archive</Button>
        </Paper>
      )}
    </div>
  )
}
