// Real delivery acceptance: JavaScript may be slow or unavailable; SEO HTML remains.
import { chromium, expect } from '@playwright/test'
import fs from 'node:fs/promises'

const url = process.env.ADAOS_E2E_PUBLIC_SITE_URL || 'https://beta.adaos.io/'
const output = process.env.ADAOS_E2E_OUTPUT
if (!output) throw new Error('ADAOS_E2E_OUTPUT is required')
await fs.mkdir(output, { recursive: true })
const browser = await chromium.launch({ headless: true })
const report = { url, checks: [] }
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const page = await context.newPage()
  let release
  const gate = new Promise(resolve => { release = resolve })
  await page.route('**/*.js', async route => { await gate; await route.continue() })
  await page.goto(url, { waitUntil: 'commit' })
  await expect(page.locator('#adaos-boot-status')).toBeVisible()
  await expect(page.locator('#adaos-static-content')).toBeHidden()
  // DOM assertions above are authoritative; a deliberately blocked module can
  // also delay document.fonts.ready, which screenshot() otherwise waits for.
  await page.screenshot({ path: output + '/slow-js.png', timeout: 2000 }).catch(() => {})
  report.checks.push('Slow JavaScript shows a loading state without an unformatted content flash')
  release()
  await expect(page.locator('ada-public-site-root')).toHaveAttribute('data-public-site-state', 'ready', { timeout: 90000 })
  await expect(page.locator('#adaos-boot-status')).toBeHidden()
  await expect(page.locator('#adaos-static-content')).toBeHidden()
  report.checks.push('Ready renderer replaces loading state and is the only visible site content')
  await context.close()

  const nojs = await browser.newContext({ javaScriptEnabled: false })
  const fallback = await nojs.newPage()
  await fallback.goto(url, { waitUntil: 'domcontentloaded' })
  await expect(fallback.locator('#adaos-static-content')).toBeVisible()
  await expect(fallback.locator('#adaos-boot-status')).toBeHidden()
  await expect(fallback.locator('ada-public-site-root')).toBeHidden()
  expect(await fallback.locator('#adaos-static-content').innerText()).toContain('AdaOS')
  report.checks.push('No-JavaScript visitors and crawlers retain the public semantic HTML')
  await nojs.close()

  const failed = await browser.newContext()
  const failure = await failed.newPage()
  await failure.route('**/*.js', route => route.abort())
  await failure.goto(url, { waitUntil: 'domcontentloaded' })
  await expect(failure.locator('#adaos-static-content')).toBeVisible({ timeout: 18000 })
  await expect(failure.locator('#adaos-boot-status')).toBeHidden()
  report.checks.push('Failed scripts restore the retained content instead of an endless loader')
  await failed.close()
} finally {
  await fs.writeFile(output + '/loading-report.json', JSON.stringify(report, null, 2))
  await browser.close()
}
console.log(JSON.stringify(report))
