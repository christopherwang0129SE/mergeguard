import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

const here = path.dirname(fileURLToPath(import.meta.url))

// Absolute path to the demo repo (demo/checkout-demo), resolved from this
// config's location so the dashboard's "Try demo repo" button works from any
// checkout. Overridable via VITE_DEMO_REPO_PATH in the environment.
const demoRepoPath =
  process.env.VITE_DEMO_REPO_PATH || path.resolve(here, '../demo/checkout-demo')

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  define: {
    'import.meta.env.VITE_DEMO_REPO_PATH': JSON.stringify(demoRepoPath),
  },
  server: {
    port: 3000,
    strictPort: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
