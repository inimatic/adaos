// Deterministic usability floors, separate from functionality and visual taste.
// These checks do not claim WCAG conformance or human task-completion success.
export function assessUsability(observation, { compact = false } = {}) {
  const actions = observation.actions || []
  const unnamed = actions.filter(action => !String(action.name || '').trim())
  const undersized = compact ? actions.filter(action => action.width < 24 || action.height < 24) : []
  const gates = {
    no_page_horizontal_overflow: observation.documentWidth <= observation.viewportWidth + 2,
    visible_actions_have_names: unnamed.length === 0,
    compact_action_minimum_footprint: undersized.length === 0,
  }
  return { schema: 'adaos.e2e.prototype_usability.v1', ok: Object.values(gates).every(Boolean), gates,
    metrics: { visible_actions: actions.length, unnamed_actions: unnamed.length, undersized_actions: undersized.length },
    findings: { unnamed, undersized },
    scope: 'Current visible surface only; 24px is a minimum footprint proxy, not a complete accessibility audit. Functional workflow and blind UX review remain separate.' }
}

export async function observeUsability(page, options) {
  const observation = await page.evaluate(() => {
    const visible = element => {
      const rect = element.getBoundingClientRect(), style = getComputedStyle(element)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
        && rect.right > 0 && rect.left < innerWidth && rect.bottom > 0 && rect.top < innerHeight
    }
    const actions = [...document.querySelectorAll('ada-page-widget-host ion-button, ada-page-widget-host button, ada-widget ion-button')]
      .filter(element => visible(element) && !element.disabled && element.getAttribute('aria-disabled') !== 'true')
      .map(element => {
        const rect = element.getBoundingClientRect()
        const references = String(element.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
          .map(id => document.getElementById(id)?.textContent || '').join(' ')
        const shadowButton = element.shadowRoot?.querySelector('button')
        const name = [element.getAttribute('aria-label'), references, shadowButton?.getAttribute('aria-label'),
          element.textContent, element.getAttribute('title')].map(value => String(value || '').replace(/\s+/g, ' ').trim()).find(Boolean) || ''
        return { name: String(name).replace(/\s+/g, ' ').trim(),
          width: Math.round(rect.width * 10) / 10, height: Math.round(rect.height * 10) / 10 }
      })
    return { viewportWidth: innerWidth, documentWidth: document.documentElement.scrollWidth, actions }
  })
  return assessUsability(observation, options)
}
