import { useState } from 'react'
import { AppShell, Group, NavLink, Text } from '@mantine/core'
import {
  IconLayoutDashboard,
  IconBox,
  IconClockPlay,
  IconDatabase,
  IconRestore,
  IconServer2,
} from '@tabler/icons-react'
import DashboardPage from './pages/DashboardPage'
import ContainersPage from './pages/ContainersPage'
import JobsPage from './pages/JobsPage'
import RestorePage from './pages/RestorePage'
import TargetsPage from './pages/TargetsPage'
import AgentsPage from './pages/AgentsPage'

const NAV = [
  { key: 'dashboard', label: 'Dashboard', icon: IconLayoutDashboard, render: () => <DashboardPage /> },
  { key: 'containers', label: 'Containers', icon: IconBox, render: () => <ContainersPage /> },
  { key: 'agents', label: 'Agents', icon: IconServer2, render: () => <AgentsPage /> },
  { key: 'jobs', label: 'Backup jobs', icon: IconClockPlay, render: () => <JobsPage /> },
  { key: 'targets', label: 'Storage targets', icon: IconDatabase, render: () => <TargetsPage /> },
  { key: 'restore', label: 'Restore from file', icon: IconRestore, render: () => <RestorePage /> },
] as const

export default function App() {
  const [tab, setTab] = useState<(typeof NAV)[number]['key']>('dashboard')
  const active = NAV.find((t) => t.key === tab)!

  return (
    <AppShell navbar={{ width: 230, breakpoint: 'sm' }} padding="lg">
      <AppShell.Navbar p="md">
        <Group gap="xs" mb="lg" px={4}>
          <img src="/logo.png" alt="" width={28} height={28} />
          <Text fw={700} size="sm">
            ContainerSafe
          </Text>
        </Group>

        {NAV.map((item) => (
          <NavLink
            key={item.key}
            label={item.label}
            leftSection={<item.icon size={18} stroke={1.6} />}
            active={item.key === tab}
            onClick={() => setTab(item.key)}
            variant="filled"
            mb={2}
          />
        ))}
      </AppShell.Navbar>

      <AppShell.Main>{active.render()}</AppShell.Main>
    </AppShell>
  )
}
