import { useEffect, useState } from 'react'
import {
  Accordion,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Table,
  Text,
  Title,
} from '@mantine/core'
import { IconAlertCircle, IconRefresh } from '@tabler/icons-react'
import { api } from '../lib/api'
import type { Container } from '../types'

export default function ContainersPage() {
  const [containers, setContainers] = useState<Container[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  useEffect(() => {
    api
      .listContainers()
      .then(setContainers)
      .catch((err: Error) => setError(err.message))
  }, [])

  async function handleRefresh() {
    setRefreshing(true)
    setError(null)
    try {
      setContainers(await api.refreshContainers())
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div>
      <Group justify="space-between" mb="lg">
        <div>
          <Title order={2}>Containers</Title>
          <Text c="dimmed" size="sm">
            Discovered on this host
          </Text>
        </div>
        <Button leftSection={<IconRefresh size={16} />} onClick={handleRefresh} loading={refreshing}>
          Refresh
        </Button>
      </Group>

      {error && (
        <Alert color="red" icon={<IconAlertCircle size={16} />} mb="md">
          {error}
        </Alert>
      )}

      {containers === null && !error && <Loader color="flame" />}
      {containers?.length === 0 && (
        <Text c="dimmed">No containers discovered yet. Click Refresh.</Text>
      )}

      {containers && containers.length > 0 && (
        <Accordion variant="separated" radius="md">
          {containers.map((c) => (
            <Accordion.Item key={c.id} value={String(c.id)}>
              <Accordion.Control>
                <Group>
                  <Badge color={c.status === 'running' ? 'teal' : 'gray'} variant="dot">
                    {c.status}
                  </Badge>
                  <div>
                    <Text fw={600} size="sm">
                      {c.name}
                    </Text>
                    <Text c="dimmed" size="xs">
                      {c.image}
                    </Text>
                  </div>
                  {c.compose_project && c.compose_service && (
                    <Badge variant="light" color="shield">
                      {c.compose_project} / {c.compose_service}
                    </Badge>
                  )}
                  <Badge variant="outline" color={c.host_id === 'local' ? 'gray' : 'flame'} ml="auto">
                    {c.host_id}
                  </Badge>
                </Group>
              </Accordion.Control>
              <Accordion.Panel>
                <Text fw={600} size="xs" tt="uppercase" c="dimmed" mb={4}>
                  Mounts ({c.mounts_json.length})
                </Text>
                <Table fz="xs" mb="md">
                  <Table.Tbody>
                    {c.mounts_json.map((m, i) => (
                      <Table.Tr key={i}>
                        <Table.Td>{m.type}</Table.Td>
                        <Table.Td>{m.name ?? m.source}</Table.Td>
                        <Table.Td>→ {m.destination}</Table.Td>
                        <Table.Td>{m.rw ? 'rw' : 'ro'}</Table.Td>
                      </Table.Tr>
                    ))}
                    {c.mounts_json.length === 0 && (
                      <Table.Tr>
                        <Table.Td c="dimmed">none</Table.Td>
                      </Table.Tr>
                    )}
                  </Table.Tbody>
                </Table>

                <Text fw={600} size="xs" tt="uppercase" c="dimmed" mb={4}>
                  Networks ({c.networks_json.length})
                </Text>
                <Table fz="xs" mb="md">
                  <Table.Tbody>
                    {c.networks_json.map((n, i) => (
                      <Table.Tr key={i}>
                        <Table.Td>{n.name}</Table.Td>
                        <Table.Td c="dimmed">{n.driver ?? ''}</Table.Td>
                        <Table.Td c="dimmed">{n.ip_address ?? ''}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>

                <Text fw={600} size="xs" tt="uppercase" c="dimmed" mb={4}>
                  Environment ({c.env_json.length}) — values hidden
                </Text>
                <Group gap={4}>
                  {c.env_json.map((name, i) => (
                    <Badge key={i} variant="outline" color="gray" size="sm">
                      {name}
                    </Badge>
                  ))}
                </Group>
              </Accordion.Panel>
            </Accordion.Item>
          ))}
        </Accordion>
      )}
    </div>
  )
}
