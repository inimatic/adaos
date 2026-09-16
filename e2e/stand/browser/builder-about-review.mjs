import { expect } from '@playwright/test'

export async function reviewAbout({ page, widget, command, closeModal, capture, report, profile }) {
  const firstRequest = report.requests.length
  const responseFor = name => page.waitForResponse(response =>
    response.request().postData()?.includes(`builder_sdk_control_skill:${name}`)
    && response.status() === 200, { timeout: 60000 })
  const aboutReply = responseFor('get_about')
  void aboutReply.catch(() => {})
  await command('inspect-section').click()
  await page.locator('[data-command-option="readme"]').filter({ visible: true }).click()
  const sourceName = await widget('design-readme').locator('ada-details-widget')
    .evaluate(el => window.ng.getComponent(el).widget.dataSource.name)
  expect(sourceName, 'Materialize the tested DEV Builder UI before browser review').toBe('builder_sdk_control_skill.get_about')
  const about = await (await aboutReply).json()
  expect(about.ok).toBe(true)
  expect(about.result.owner_status).toBe('registered')
  expect(about.result.owner_ref).toMatch(/^subnet:/)
  await expect(widget('design-readme')).toContainText(about.result.owner_ref)
  await capture('about-public-document')
  report.checks.push({ profile, check: 'about_registered_identity_and_document', passed: true,
    document_digest: about.result.digest, project_ref: about.result.project_ref })

  await widget('design-readme-actions').getByRole('button', { name: /редакт|edit/i }).click()
  await expect(widget('design-readme-form').locator('textarea')).toHaveValue(about.result.text)
  await expect(widget('readme-generate')).toBeVisible()
  await closeModal()
  report.checks.push({ profile, check: 'about_preserves_existing_readme_editor_and_generation', passed: true })

  let expectedDigest = null
  for (const attempt of ['open', 'reopen']) {
    const historyReply = responseFor('list_icon_drafts')
    await widget('design-readme-actions').getByRole('button', { name: /черновики иконки|icon drafts/i }).click()
    const history = await (await historyReply).json()
    expect(history.ok).toBe(true)
    const rows = history.result.items
    const index = rows.findIndex(row => row.status === 'completed')
    if (index < 0) throw new Error('No completed icon draft owned by this caller; no automatic generation is allowed')
    const draftReply = responseFor('get_icon_generation')
    await widget('about-icon-history').locator('ion-item.collection-focus-item').nth(index).click()
    const draft = await (await draftReply).json()
    expect(draft.ok).toBe(true)
    expect(draft.result.status).toBe('completed')
    expectedDigest ??= draft.result.media.sha256
    expect(draft.result.media.sha256).toBe(expectedDigest)
    const image = widget('about-icon-preview').locator('img.details-image')
    await image.scrollIntoViewIfNeeded()
    await expect.poll(() => image.evaluate(el => el.complete && el.naturalWidth > 0)).toBe(true)
    const pixels = await image.evaluate(el => {
      const canvas = document.createElement('canvas')
      canvas.width = 32; canvas.height = 32
      const context = canvas.getContext('2d')
      context.drawImage(el, 0, 0, 32, 32)
      const bytes = context.getImageData(0, 0, 32, 32).data
      return { width: el.naturalWidth, height: el.naturalHeight,
        colors: new Set(Array.from({ length: 1024 }, (_, n) => bytes.slice(n * 4, n * 4 + 3).join(','))).size }
    })
    expect(pixels.colors).toBeGreaterThan(10)
    await capture(`about-icon-${attempt}`)
    await closeModal()
    report.checks.push({ profile, check: `owned_icon_draft_${attempt}_rendered`, passed: true,
      image_sha256: expectedDigest, pixels })
  }
  const requests = report.requests.slice(firstRequest).map(row => row.body?.tool || '')
  expect(requests.some(name => /:(generate_icon|generate_readme|save_readme)$/.test(name))).toBe(false)
  report.checks.push({ profile, check: 'about_readonly_review_no_generation_or_source_save', passed: true })
}
