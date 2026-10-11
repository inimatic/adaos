// Namespaces are not field meaning; i18n keys are not visible labels.
export function fieldSemanticText(field) {
  const label = typeof field.label === 'string' ? field.label : Object.entries(field.label || {})
    .filter(([key, value]) => key !== 'key' && typeof value === 'string').map(([, value]) => value).join(' ')
  return `${field.id.split('.').at(-1)} ${label}`
}
