import { useEffect, useState } from 'react'
import type { ComponentType } from 'react'
import {
  Badge,
  Card,
  Group,
  Loader,
  Paper,
  SimpleGrid,
  Table,
  Text,
  Title,
} from '@mantine/core'
import { IconBox, IconCircleCheck, IconCircleX, IconClockPlay, IconDatabase } from '@tabler/icons-react'
import { api } from '../lib/api'
import type { BackupJob, BackupRun, Container, StorageTarget } from '../types'

const STATUS_COLOR: Record<string, string> = {
  success: 'teal',
  failed: 'red',
  running: 'flame',
  pending: 'gray',
}

export default function DashboardPage() {
  const [containers, setContainers] = useState<Container[] | null>(null)
  const [jobs, setJobs] = useState<BackupJob[] | null>(null)
  const [targets, setTargets] = useState<StorageTarget[] | null>(null)
  const [recent, setRecent] = useState<BackupRun[] | null>(null)

  useEffect(() => {
    api.listContainers().then(setContainers).catch(() => setContainers([]))
    api.listJobs().then(setJobs).catch(() => setJobs([]))
    api.listTargets().then(setTargets).catch(() => setTargets([]))
    api.recentRuns(20).then(setRecent).catch(() => setRecent([]))
  }, [])

  if (!containers || !jobs || !targets || !recent) {
    return (
      <Group justify="center" mt="xl">
        <Loader color="flame" />
      </Group>
    )
  }

  const dayAgo = Date.now() - 24 * 3600 * 1000
  const last24h = recent.filter((r) => new Date(r.started_at).getTime() >= dayAgo)
  const succeeded24h = last24h.filter((r) => r.status === 'success').length
  const failed24h = last24h.filter((r) => r.status === 'failed').length

  const upcoming = jobs
    .filter((j) => j.next_run_at)
    .sort((a, b) => new Date(a.next_run_at!).getTime() - new Date(b.next_run_at!).getTime())
    .slice(0, 5)

  return (
    <div>
      <Title order={2} mb="lg">
        Dashboard
      </Title>

      <SimpleGrid cols={{ base: 2, md: 4 }} mb="xl">
        <StatCard icon={IconBox} label="Containers discovered" value={containers.length} />
        <StatCard icon={IconClockPlay} label="Backup jobs" value={jobs.length} />
        <StatCard icon={IconDatabase} label="Storage targets" value={targets.length} />
        <StatCard
          icon={failed24h > 0 ? IconCircleX : IconCircleCheck}
          label="Last 24h"
          value={`${succeeded24h} ok${failed24h ? ` / ${failed24h} failed` : ''}`}
          color={failed24h > 0 ? 'red' : 'teal'}
        />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Paper withBorder p="md" radius="md">
          <Text fw={600} size="sm" mb="sm">
            Next scheduled runs
          </Text>
          {upcoming.length === 0 && (
            <Text c="dimmed" size="sm">
              No enabled jobs with a schedule yet.
            </Text>
          )}
          {upcoming.length > 0 && (
            <Table verticalSpacing="xs">
              <Table.Tbody>
                {upcoming.map((j) => (
                  <Table.Tr key={j.id}>
                    <Table.Td>{j.display_name}</Table.Td>
                    <Table.Td c="dimmed">{new Date(j.next_run_at!).toLocaleString()}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Paper>

        <Paper withBorder p="md" radius="md">
          <Text fw={600} size="sm" mb="sm">
            Recent activity
          </Text>
          {recent.length === 0 && (
            <Text c="dimmed" size="sm">
              No backup runs yet.
            </Text>
          )}
          {recent.length > 0 && (
            <Table verticalSpacing="xs">
              <Table.Tbody>
                {recent.slice(0, 8).map((r) => (
                  <Table.Tr key={r.id}>
                    <Table.Td>
                      <Badge color={STATUS_COLOR[r.status] ?? 'gray'} variant="light" size="sm">
                        {r.status}
                      </Badge>
                    </Table.Td>
                    <Table.Td>{r.job_display_name ?? r.identity_key}</Table.Td>
                    <Table.Td c="dimmed">{new Date(r.started_at).toLocaleString()}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Paper>
      </SimpleGrid>
    </div>
  )
}

function StatCard({
  icon: Icon,
  label,
  value,
  color = 'flame',
}: {
  icon: ComponentType<{ size?: number; stroke?: number }>
  label: string
  value: string | number
  color?: string
}) {
  return (
    <Card withBorder radius="md" padding="md">
      <Group gap="sm">
        <Icon size={22} stroke={1.6} />
        <div>
          <Text size="xs" c="dimmed">
            {label}
          </Text>
          <Text fw={700} size="lg" c={color === 'flame' ? undefined : color}>
            {value}
          </Text>
        </div>
      </Group>
    </Card>
  )
}
