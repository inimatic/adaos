import assert from 'node:assert/strict'
import test from 'node:test'
import { chromium } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import sass from '../../../src/adaos/integrations/adaos-client/node_modules/sass/sass.node.js'

// Exercise the real renderer stylesheet, not a parallel approximation of it.
const css = sass.compile(fileURLToPath(new URL('../../../src/adaos/integrations/adaos-client/src/app/renderer/desktop/desktop.component.scss', import.meta.url))).css
  .replaceAll(':host ::ng-deep ', '').replaceAll(':host', ':root')

test('populated workbench/master-detail detail and inspector never share a grid cell', async () => {
  const browser = await chromium.launch({ headless: true })
  try {
    for (const pattern of ['workbench', 'master-detail']) {
      for (const width of [940, 1100, 1440]) {
        for (const navigation of [false, true]) {
          for (const scroll of ['page', 'regions']) {
            const page = await browser.newPage({ viewport: { width, height: 900 } })
            await page.setContent(`<style>${css} body { margin: 0 } ada-layout-region { display: block; min-height: 100px }</style>
            <div class="desktop-grid layout-split pattern-${pattern} scroll-${scroll} ${navigation ? '' : 'no-sidebar'}">
              ${navigation ? '<ada-layout-region class="area area-role-navigation area-role-sidebar">Navigation</ada-layout-region>' : ''}
              <ada-layout-region class="area area-role-main">Main</ada-layout-region>
              <ada-layout-region class="area area-role-detail area-role-aux"><button id="edit">Editor</button></ada-layout-region>
              <ada-layout-region class="area area-role-inspector area-role-aux">Inspector</ada-layout-region>
            </div>`)
            const detail = await page.locator('.area-role-detail').boundingBox()
            const inspector = await page.locator('.area-role-inspector').boundingBox()
            assert.ok(detail && inspector && detail.width > 0 && inspector.width > 0)
            assert.ok(detail.y + detail.height <= inspector.y || inspector.y + inspector.height <= detail.y
              || detail.x + detail.width <= inspector.x || inspector.x + inspector.width <= detail.x,
            `Overlapping supporting regions: pattern=${pattern}, width=${width}, navigation=${navigation}, scroll=${scroll}`)
            await page.locator('#edit').click({ timeout: 2000 })
            await page.close()
          }
        }
      }
    }
  } finally { await browser.close() }
})
