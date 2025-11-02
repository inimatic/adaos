import { listBindings, getDefaultBinding, setDefault, getByAlias, renameAlias, unlinkAlias, upsertBinding, setSession, bindTopic, unbindTopic } from '../../db/tg.repo.js'
import type { InlineKeyboardButton } from './keyboards.js'
import { keyboardPicker } from './keyboards.js'

export type CmdCtx = { chat_id: number, text: string, topic_id?: number }

const HELP = [
  'Доступные команды:',
  '/use <alias|hub> — сделать текущей',
  '/current — показать текущую и дефолтную',
  '/list — список, выбрать текущую/дефолтную',
  '/default <alias> — сделать дефолтной',
  '/alias <hub|alias> <new> — переименовать',
  '/unlink <alias> — отвязать',
  '/bind_here <alias> — привязать тему к подсети',
  '/unbind_here — снять привязку темы',
  'Явная адресация: @alias текст',
].join('\n')

export async function handleCommand(ctx: CmdCtx): Promise<{ text: string, keyboard?: { inline_keyboard: InlineKeyboardButton[][] } } | null> {
  const parts = ctx.text.trim().split(/\s+/)
  const cmd = parts[0].toLowerCase()

  if (cmd === '/help') return { text: HELP }

  if (cmd === '/current') {
    const list = await listBindings(ctx.chat_id)
    const def = list.find(b => b.is_default)
    const current = def // for MVP, show default as current unless session logic added here
    const line = list.map(b => `${b.is_default ? '⭐' : ' '} ${current && current.hub_id===b.hub_id ? '✅' : ' '} ${b.alias} → ${b.hub_id}`).join('\n') || 'Пусто'
    return { text: `Текущая/дефолтная:\n${line}` }
  }

  if (cmd === '/list') {
    const list = await listBindings(ctx.chat_id)
    const kb = keyboardPicker(list.map(b => ({ alias: b.alias, is_default: b.is_default })))
    return { text: 'Подсети:', keyboard: kb }
  }

  if (cmd === '/use' && parts[1]) {
    const key = parts[1]
    const b = (await getByAlias(ctx.chat_id, key))
    if (!b) return { text: 'Не найден alias' }
    await setSession(ctx.chat_id, b.hub_id, 'manual')
    return { text: `Текущая подсеть: ${b.alias}` }
  }

  if (cmd === '/default' && parts[1]) {
    const alias = parts[1]
    const b = await getByAlias(ctx.chat_id, alias)
    if (!b) return { text: 'Не найден alias' }
    await setDefault(ctx.chat_id, alias)
    return { text: `Дефолтная: ${alias}` }
  }

  if (cmd === '/alias' && parts[1] && parts[2]) {
    const key = parts[1]
    const next = parts[2]
    const ok = await renameAlias(ctx.chat_id, key, next)
    return { text: ok ? `Переименовано: ${key} → ${next}` : 'Не найдено' }
  }

  if (cmd === '/unlink' && parts[1]) {
    const alias = parts[1]
    const ok = await unlinkAlias(ctx.chat_id, alias)
    return { text: ok ? `Отвязано: ${alias}` : 'Не найдено' }
  }

  if (cmd === '/bind_here' && parts[1]) {
    if (!ctx.topic_id) return { text: 'Команда доступна только в темах' }
    const alias = parts[1]
    const b = await getByAlias(ctx.chat_id, alias)
    if (!b) return { text: 'Не найден alias' }
    await bindTopic(ctx.chat_id, ctx.topic_id, b.hub_id)
    return { text: `Тема привязана к ${alias}` }
  }
  if (cmd === '/unbind_here') {
    if (!ctx.topic_id) return { text: 'Команда доступна только в темах' }
    await unbindTopic(ctx.chat_id, ctx.topic_id)
    return { text: 'Привязка темы снята' }
  }

  return null
}
