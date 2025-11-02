import { getByAlias, getDefaultBinding, getSession, getTopicHub, mapMsgToHub } from '../../db/tg.repo.js'

export type ResolveCtx = {
  chat_id: number
  text?: string
  reply_to_msg_id?: number
  topic_id?: number
}

export type Target = { hub_id: string, alias: string, via: 'explicit'|'reply'|'topic'|'session'|'default' }

const ALIAS_RE = /^[#@]([A-Za-z0-9._-]{1,32})\b\s*/

export function parseExplicitAlias(text?: string): string | null {
  if (!text) return null
  const m = text.match(ALIAS_RE)
  return m ? m[1] : null
}

export function stripExplicitAlias(text?: string): string {
  if (!text) return ''
  return text.replace(ALIAS_RE, '')
}

export async function resolveTarget(ctx: ResolveCtx): Promise<Target> {
  // 1) explicit alias
  const exp = parseExplicitAlias(ctx.text)
  if (exp) {
    const b = await getByAlias(ctx.chat_id, exp)
    if (b) return { hub_id: b.hub_id, alias: b.alias, via: 'explicit' }
  }

  // 2) reply context
  if (ctx.reply_to_msg_id) {
    const m = await mapMsgToHub(ctx.reply_to_msg_id)
    if (m) return { hub_id: m.hub_id, alias: m.alias, via: 'reply' }
  }

  // 3) topic binding
  if (ctx.topic_id) {
    const t = await getTopicHub(ctx.chat_id, ctx.topic_id)
    if (t) return { hub_id: t.hub_id, alias: t.alias, via: 'topic' }
  }

  // 4) session current
  const sess = await getSession(ctx.chat_id)
  if (sess?.current_hub_id) {
    // Need alias lookup
    const def = await getDefaultBinding(ctx.chat_id)
    if (def && def.hub_id === sess.current_hub_id) return { hub_id: def.hub_id, alias: def.alias, via: 'session' }
    // fallback: any alias for this hub
    // (simple approach: default when missing)
  }

  // 5) default
  const def = await getDefaultBinding(ctx.chat_id)
  if (def) return { hub_id: def.hub_id, alias: def.alias, via: 'default' }

  throw new Error('need_choice')
}

