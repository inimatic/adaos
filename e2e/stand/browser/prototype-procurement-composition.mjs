// This additional task-flow check is reported separately from preregistered gates.
process.env.ADAOS_E2E_PROCUREMENT_COMPOSITION = '1'
await import('./prototype-procurement.mjs')
