export const presentRuntimeDataStates = ['ready', 'refreshing']

export function missingRequiredData(required, states, runtimeKinds = ['api', 'mcp', 'skill', 'resourceQuery']) {
  return required.filter(id => !states.some(state => state.id === id && presentRuntimeDataStates.includes(state.state)
    && state.has_value === 'true' && runtimeKinds.includes(state.kind)))
}
