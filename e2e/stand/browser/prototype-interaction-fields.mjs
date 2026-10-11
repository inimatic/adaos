// Prefer edits which do not move the record out of its current parent/filter.
// An explicitly requested choice field is still exercised as requested.
export function pickEditableInteractionField(fields, update, { fieldId, fieldType } = {}) {
  const editable = field => update?.params?.payload?.[field.id] === `$event.values.${field.id}`
    && !field.visibleIf && !field.readOnly
  if (fieldId) return fields.find(field => field.id === fieldId && editable(field))
  if (fieldType) return fields.find(field => field.type === fieldType && editable(field))
  for (const types of [['shortText', 'longText'], ['number', 'integer', 'date', 'time'], ['dropdown', 'singleChoice']]) {
    const field = fields.find(field => types.includes(field.type) && editable(field))
    if (field) return field
  }
  return undefined
}
