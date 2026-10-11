// Invalid drafts may be rejected on submit OR prevented from submitting.
// Exercise only public controls; forcing a disabled action would bypass the UI.
export async function assertInvalidSubmissionGuard({ button, error, settle, mutationCount }) {
  const before = mutationCount()
  await settle()
  const disabled = await button.isDisabled()
  if (!disabled) {
    await button.click()
    await error.waitFor({ state: 'visible' })
  }
  await settle()
  if (mutationCount() !== before) throw new Error('Invalid edit submitted a resource mutation')
  return disabled ? 'disabled-submit' : 'validation-error'
}
