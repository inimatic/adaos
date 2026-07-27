import fs from 'node:fs'
import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

const bundleRoot = path.resolve(process.env.ADAOS_E2E_OUTPUT || 'artifacts/e2e-runs/browser-local')
const storageState = String(process.env.ADAOS_E2E_STORAGE_STATE || '').trim()

export default defineConfig({
  testDir: '.',
  testMatch: 'stand.smoke.spec.mjs',
  timeout: 120_000,
  expect: { timeout: 90_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  outputDir: path.join(bundleRoot, 'client', 'test-results'),
  reporter: [
    ['line'],
    ['html', { outputFolder: path.join(bundleRoot, 'client', 'playwright-report'), open: 'never' }],
  ],
  use: {
    baseURL: process.env.ADAOS_E2E_CLIENT_URL,
    headless: true,
    trace: 'off',
    screenshot: 'only-on-failure',
    video: 'off',
    ...(storageState && fs.existsSync(storageState) ? { storageState } : {}),
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
