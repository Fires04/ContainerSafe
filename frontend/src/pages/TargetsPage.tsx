import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Modal,
  Select,
  SimpleGrid,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { IconAlertCircle, IconDatabase, IconPlus, IconTrash } from '@tabler/icons-react'
import { api } from '../lib/api'
import type { StorageTarget } from '../types'

const RCLONE_TYPES = ['sftp', 'smb', 'ftp', 's3', 'webdav'] as const

export default function TargetsPage() {
  const [targets, setTargets] = useState<StorageTarget[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)

  function load() {
    api.listTargets().then(setTargets).catch((err: Error) => setError(err.message))
  }

  useEffect(load, [])

  async function handleDelete(id: number) {
    if (!confirm('Delete this storage target?')) return
    try {
      await api.deleteTarget(id)
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div>
      <Group justify="space-between" mb="lg">
        <div>
          <Title order={2}>Storage targets</Title>
          <Text c="dimmed" size="sm">
            Where backup archives get written
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setShowForm(true)}>
          New target
        </Button>
      </Group>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />} mb="md">
          {error}
        </Alert>
      )}

      <Modal opened={showForm} onClose={() => setShowForm(false)} title="New storage target" size="md">
        <TargetForm
          onCreated={() => {
            setShowForm(false)
            load()
          }}
          onError={setError}
        />
      </Modal>

      {targets === null && !error && <Loader color="flame" />}
      {targets?.length === 0 && <Text c="dimmed">No storage targets yet.</Text>}

      <SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
        {targets?.map((t) => (
          <Card key={t.id} withBorder radius="md" padding="md">
            <Group justify="space-between" mb={4}>
              <Group gap={6}>
                <IconDatabase size={16} />
                <Text fw={600} size="sm">
                  {t.name}
                </Text>
              </Group>
              <ActionIcon color="red" variant="subtle" onClick={() => handleDelete(t.id)}>
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
            <Badge variant="light" color={t.type === 'local' ? 'shield' : 'flame'} size="sm" mb={6}>
              {t.type}
            </Badge>
            <Text size="xs" c="dimmed">
              {t.type === 'local'
                ? `/app/backup/${(t.config_json.path as string) || ''}`
                : `${t.config_json.remote_type}: ${t.config_json.host ?? ''} ${t.config_json.path ?? ''}`}
            </Text>
          </Card>
        ))}
      </SimpleGrid>
    </div>
  )
}

function TargetForm({ onCreated, onError }: { onCreated: () => void; onError: (msg: string) => void }) {
  const [name, setName] = useState('')
  const [type, setType] = useState<'local' | 'rclone'>('local')
  const [path, setPath] = useState('')
  const [remoteType, setRemoteType] = useState<(typeof RCLONE_TYPES)[number]>('sftp')
  const [host, setHost] = useState('')
  const [port, setPort] = useState('')
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [explicitTls, setExplicitTls] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    onError('')
    try {
      const config: Record<string, unknown> =
        type === 'local'
          ? { path }
          : {
              remote_type: remoteType,
              host,
              port: port || undefined,
              user,
              password: password || undefined,
              path,
              ...(remoteType === 'ftp' ? { explicit_tls: explicitTls } : {}),
            }
      await api.createTarget({ name, type, config })
      onCreated()
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <TextInput label="Name" value={name} onChange={(e) => setName(e.currentTarget.value)} required mb="sm" />
      <Select
        label="Type"
        value={type}
        onChange={(v) => setType((v as 'local' | 'rclone') ?? 'local')}
        data={[
          { value: 'local', label: 'Local (./backup, also covers NFS mounted at host level)' },
          { value: 'rclone', label: 'Remote via rclone (SFTP / SMB / FTP / S3 / WebDAV)' },
        ]}
        mb="sm"
      />

      {type === 'local' ? (
        <TextInput
          label="Subpath under /app/backup (optional)"
          value={path}
          onChange={(e) => setPath(e.currentTarget.value)}
          placeholder="e.g. nas"
          mb="sm"
        />
      ) : (
        <>
          <Select
            label="Remote type"
            value={remoteType}
            onChange={(v) => setRemoteType((v as (typeof RCLONE_TYPES)[number]) ?? 'sftp')}
            data={RCLONE_TYPES.map((t) => ({ value: t, label: t }))}
            mb="sm"
          />
          <TextInput label="Host" value={host} onChange={(e) => setHost(e.currentTarget.value)} required mb="sm" />
          <TextInput label="Port (optional)" value={port} onChange={(e) => setPort(e.currentTarget.value)} mb="sm" />
          <TextInput label="User" value={user} onChange={(e) => setUser(e.currentTarget.value)} mb="sm" />
          <TextInput
            label="Password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.currentTarget.value)}
            mb="sm"
          />
          {remoteType === 'ftp' && (
            <Checkbox
              label="Use FTPS (explicit TLS / AUTH TLS)"
              checked={explicitTls}
              onChange={(e) => setExplicitTls(e.currentTarget.checked)}
              mb="sm"
            />
          )}
          <TextInput
            label="Remote path"
            value={path}
            onChange={(e) => setPath(e.currentTarget.value)}
            required
            placeholder="/backups/containersafe"
            mb="sm"
          />
        </>
      )}

      <Button type="submit" loading={submitting} fullWidth mt="sm">
        Create target
      </Button>
    </form>
  )
}
