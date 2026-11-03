// Independent source-value matching; canonical enum fields lower to `choice`.
// A display caption may capitalize the exact English code. It may not change
// the code or apply case folding to ordinary source text.
export function sourceScalarTextMatches(field, actual, expected) {
  if (['choice', 'enum'].includes(field.value_type) && typeof expected === 'string')
    return actual.toLowerCase() === expected.toLowerCase()
  return actual === expected
}
