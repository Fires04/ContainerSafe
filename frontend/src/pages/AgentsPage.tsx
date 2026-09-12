import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Card,
  CopyButton,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core'
import { IconAlertCircle, IconCheck, IconCopy, IconPlus, IconServer2, IconTrash } from '@tabler/icons-react'
import { api } from '../lib/api'
import type { Agent, AgentEnrollResult } from '../types'

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [enrolled, setEnrolled] = useState<AgentEnrollResult | null>(null)

  function load() {
    api.listAgents().then(setAgents).catch((err: Error) => setError(err.message))
  }

  useEffect(() => {
    load()
    const id = setInterval(load, 5000)
    return () => clearInterval(id)
  }, [])

  async function handleDelete(id: number) {
    if (!confirm('Remove this agent? Its containers stay in history, but it can no longer be reached.')) return
    try {
      await api.deleteAgent(id)
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <div>
      <Group justify="space-between" mb="lg">
        <div>
          <Title order={2}>Agents</Title>
          <Text c="dimmed" size="sm">
            Remote hosts running their own ContainerSafe agent
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={() => setShowForm(true)}>
          New agent
        </Button>
      </Group>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />} mb="md">
          {error}
        </Alert>
      )}

      <Modal opened={showForm} onClose={() => setShowForm(false)} title="Enroll a new agent" size="md">
        <EnrollForm
          onEnrolled={(result) => {
            setShowForm(false)
            setEnrolled(result)
            load()
          }}
          onError={setError}
        />
      </Modal>

      <Modal opened={enrolled !== null} onClose={() => setEnrolled(null)} title="Agent enrolled" size="lg">
        {enrolled && (
          <Stack gap="md">
            <Alert color="yellow">
              This link and token are shown <strong>only once</strong>. Copy what you need now — you'll have to
              re-enroll to get a new one.
            </Alert>

            <div>
              <Text size="sm" fw={600} mb={4}>
                Option A — run this on the target host
              </Text>
              <Text size="xs" c="dimmed" mb={6}>
                Downloads the agent, writes its <code>.env</code> (including the right{' '}
                <code>HOST_DATA_DIR</code> for that host automatically), and starts it. Works once, expires in 15
                minutes.
              </Text>
              <Card withBorder p="sm" bg="var(--mantine-color-default-hover)">
                <Text component="pre" size="xs" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-all', margin: 0 }}>
                  {enrolled.curl_command}
                </Text>
              </Card>
              <CopyButton value={enrolled.curl_command}>
                {({ copied, copy }) => (
                  <Button
                    mt={6}
                    size="xs"
                    leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
                    color={copied ? 'teal' : undefined}
                    onClick={copy}
                  >
                    {copied ? 'Copied' : 'Copy command'}
                  </Button>
                )}
              </CopyButton>
            </div>

            <div>
              <Text size="sm" fw={600} mb={4}>
                Option B — set it up by hand
              </Text>
              <Text size="xs" c="dimmed" mb={6}>
                Copy this into <code>agent/.env</code> on the target host (after getting a copy of this repo's{' '}
                <code>agent/</code> + <code>agentcore/</code> directories there), fill in{' '}
                <code>HOST_DATA_DIR</code> yourself, then <code>docker compose up -d --build</code>:
              </Text>
              <Card withBorder p="sm" bg="var(--mantine-color-default-hover)">
                <Text component="pre" size="xs" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>
                  {enrolled.env_snippet}
                </Text>
              </Card>
              <CopyButton value={enrolled.env_snippet}>
                {({ copied, copy }) => (
                  <Button
                    mt={6}
                    size="xs"
                    variant="light"
                    leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
                    color={copied ? 'teal' : undefined}
                    onClick={copy}
                  >
                    {copied ? 'Copied' : 'Copy snippet'}
                  </Button>
                )}
              </CopyButton>
            </div>
          </Stack>
        )}
      </Modal>

      {agents === null && !error && <Loader color="flame" />}
      {agents?.length === 0 && (
        <Text c="dimmed">No agents enrolled yet — this server only sees its own local containers.</Text>
      )}

      <Stack gap="sm">
        {agents?.map((a) => (
          <Card key={a.id} withBorder radius="md" padding="md">
            <Group justify="space-between">
              <Group gap="sm">
                <IconServer2 size={20} />
                <div>
                  <Group gap={6}>
                    <Text fw={600} size="sm">
                      {a.host_id}
                    </Text>
                    <Badge color={a.online ? 'teal' : 'gray'} variant="dot" size="sm">
                      {a.online ? 'online' : 'offline'}
                    </Badge>
                  </Group>
                  <Text size="xs" c="dimmed">
                    {a.agent_version ? `v${a.agent_version} · ` : ''}
                    {a.last_seen_at ? `last seen ${new Date(a.last_seen_at).toLocaleString()}` : 'never connected'}
                    {a.online && a.last_heartbeat_rtt_ms != null ? ` · ${a.last_heartbeat_rtt_ms}ms` : ''}
                  </Text>
                </div>
              </Group>
              <ActionIcon color="red" variant="subtle" onClick={() => handleDelete(a.id)}>
                <IconTrash size={16} />
              </ActionIcon>
            </Group>
          </Card>
        ))}
      </Stack>
    </div>
  )
}

function EnrollForm({
  onEnrolled,
  onError,
}: {
  onEnrolled: (result: AgentEnrollResult) => void
  onError: (msg: string) => void
}) {
  const [hostId, setHostId] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setSubmitting(true)
    onError('')
    try {
      onEnrolled(await api.enrollAgent(hostId))
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <TextInput
        label="Name"
        description="A short, unique name for this host — e.g. juliet, lima, nas-backup-01"
        value={hostId}
        onChange={(e) => setHostId(e.currentTarget.value)}
        required
        mb="md"
      />
      <Button type="submit" loading={submitting} fullWidth>
        Enroll
      </Button>
    </form>
  )
}
