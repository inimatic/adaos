export type WebIOChatMessage = {
  id: string
  from: string
  text: string
  ts?: number
}

export type WebIOVoiceChatState = {
  messages: WebIOChatMessage[]
}

export type WebIOTtsQueueItem = {
  id: string
  text: string
  ts?: number
  lang?: string
  voice?: string
  rate?: number
}

export type WebIOTtsState = {
  queue: WebIOTtsQueueItem[]
}

export function coerceChatState(raw: any): WebIOVoiceChatState {
  const messagesRaw = Array.isArray(raw?.messages) ? raw.messages : []
  const messages: WebIOChatMessage[] = messagesRaw
    .filter((m: any) => m && typeof m === 'object')
    .map((m: any, idx: number) => ({
      id: String(m.id || `m.${idx}`),
      from: String(m.from || 'hub'),
      text: String(m.text || ''),
      ts: typeof m.ts === 'number' ? m.ts : undefined,
    }))
    .filter((m: WebIOChatMessage) => m.text.trim().length > 0)
  return { messages }
}

export function coerceTtsState(raw: any): WebIOTtsState {
  const queueRaw = Array.isArray(raw?.queue) ? raw.queue : []
  const queue: WebIOTtsQueueItem[] = queueRaw
    .filter((m: any) => m && typeof m === 'object')
    .map((m: any, idx: number) => ({
      id: String(m.id || `t.${idx}`),
      text: String(m.text || ''),
      ts: typeof m.ts === 'number' ? m.ts : undefined,
      lang: typeof m.lang === 'string' ? m.lang : undefined,
      voice: typeof m.voice === 'string' ? m.voice : undefined,
      rate: typeof m.rate === 'number' ? m.rate : undefined,
    }))
    .filter((m: WebIOTtsQueueItem) => m.text.trim().length > 0)
  return { queue }
}

