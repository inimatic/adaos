import { expect } from '@playwright/test'

// Release notices belong to the surrounding Desktop, not to the isolated
// Prototype under review. They may arrive after first paint and must not make
// an otherwise deterministic probe depend on a user's notification history.
export async function installSystemOverlayDismissal(page, onDismiss = () => {}) {
  await page.addLocatorHandler(page.locator('.component-updates-backdrop'), async () => {
    const close = page.locator('.component-updates-panel__tools button[aria-label="Close"], .component-updates-panel__tools button').last()
    await close.click()
    onDismiss('component-updates')
  })
}

// Follow public tab controls, never mutate application state to reveal a fixture.
export async function revealPrototypeWidget(page, widgets, target) {
  const host = id => page.locator(`[data-webui-widget-id=${JSON.stringify(id)}]`).last()
  if (await host(target.id).isVisible()) return
  for (const navigation of widgets.filter(widget => widget.inputs?.variant === 'tabs')) {
    if (!await host(navigation.id).isVisible()) continue
    for (const button of navigation.inputs.buttons || []) {
      const actions = navigation.actions?.filter(action => action.on === `click:${button.id}`) || []
      if (!actions.length || actions.some(action => action.type !== 'updateState')) continue
      const control = host(navigation.id).locator(`[data-command-id=${JSON.stringify(button.id)}]`)
      if (!await control.isVisible() || !await control.isEnabled()) continue
      await control.click()
      try {
        await expect(host(target.id)).toBeVisible({ timeout: 1500 })
        return
      } catch (error) {
        if (page.isClosed()) throw error
      }
    }
  }
  throw new Error(`No reachable section for ${target.id}`)
}
