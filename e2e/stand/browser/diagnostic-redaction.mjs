const sensitiveKey = /^(?:authorization|cookie|setcookie|password|passwd|secret|clientsecret|token|accesstoken|refreshtoken|idtoken|authtoken|sessiontoken|apikey|signature)$/i

export function redactDiagnosticText(value) {
  return String(value)
    .replace(/([?&](?:token|access_token|refresh_token|id_token|auth_token|api_key|key|signature)=)[^&#\s"'<>]*/gi, '$1[redacted]')
    .replace(/(\bBearer\s+)[^\s"'<>]+/gi, '$1[redacted]')
    .replace(/(https?:\/\/)[^\s/@]+:[^\s/@]+@/gi, '$1[redacted]@')
}

export function redactDiagnosticValue(value) {
  if (typeof value === 'string') return redactDiagnosticText(value)
  if (Array.isArray(value)) return value.map(redactDiagnosticValue)
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [
      key, sensitiveKey.test(key.replace(/[_-]/g, '')) ? '[redacted]' : redactDiagnosticValue(item),
    ]))
  }
  return value
}
