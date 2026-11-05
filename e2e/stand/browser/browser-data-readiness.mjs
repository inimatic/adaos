export function missingRequiredData(required, states, runtimeKinds = ['api', 'mcp', 'skill', 'resourceQuery']) {
  return required.filter(id => !states.some(state => state.id === id && state.state === 'ready'
    && state.has_value === 'true' && runtimeKinds.includes(state.kind)))
}
