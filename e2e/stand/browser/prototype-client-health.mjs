import { chromium, expect } from '@playwright/test'

if (process.env.ENV_TYPE !== 'dev') throw new Error('Requires DEV')
const url = new URL(process.env.ADAOS_E2E_CLIENT_URL || 'http://127.0.0.1:8100/')
const browser = await chromium.launch({ headless: true })
try {
  const context = await browser.newContext()
  const styles = await context.request.get(new URL('styles.css', url).href)
  const body = await styles.text()
  if (!styles.ok() || !String(styles.headers()['content-type']).includes('text/css')
    || !body.trim() || body.includes('Could not resolve')) {
    throw new Error('Client stylesheet build is not healthy; do not start paid generation')
  }
  const page = await context.newPage()
  await page.goto(url.href, { waitUntil: 'domcontentloaded' })
  await expect(page.locator('vite-error-overlay')).toHaveCount(0)
  console.log(JSON.stringify({ healthy: true, stylesheet_status: styles.status(), stylesheet_bytes: body.length,
    vite_error_overlays: 0 }))
} finally {
  await browser.close()
}
