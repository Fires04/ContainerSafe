import { createTheme, type MantineColorsTuple } from '@mantine/core'

// ContainerSafe's brand palette, taken from the shield+flame mark: "shield"
// (blue) for structure, "flame" (orange/red) as the one accent color
// (status/actions) — matching the enterprise-backup-tool look this app was
// asked for, now tied to the actual logo colors instead of an arbitrary pick.
const shield: MantineColorsTuple = [
  '#eaf3fa',
  '#cfe4f2',
  '#a3cbe6',
  '#74b0d8',
  '#4a97c9',
  '#2c6c98',
  '#235777',
  '#1c455f',
  '#163649',
  '#0f2836',
]

const flame: MantineColorsTuple = [
  '#fff4e6',
  '#ffe3bf',
  '#ffc78a',
  '#ffa94d',
  '#ff922b',
  '#f76707',
  '#e8590c',
  '#d9480f',
  '#b93815',
  '#8a2a10',
]

export const theme = createTheme({
  primaryColor: 'flame',
  colors: { shield, flame },
  defaultRadius: 'md',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  fontFamilyMonospace: '"IBM Plex Mono", ui-monospace, monospace',
  headings: { fontWeight: '600' },
})
