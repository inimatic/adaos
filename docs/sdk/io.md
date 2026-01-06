# SDK IO

SDK module: `adaos.sdk.io`

## Output (unified)

These helpers publish events onto the local bus. They do not write to Yjs directly.

- `io.out.chat.append(text, from_='hub', _meta={'webspace_id': '...'})`
  - RouterService projects into `data.voice_chat.messages` of the target webspace.
- `io.out.say(text, lang='ru-RU', _meta={'webspace_id': '...'})`
  - RouterService projects into `data.tts.queue` of the target webspace.

## Voice (local mock)

- `io.voice.stt.listen(timeout='20s')`
- `io.voice.tts.speak(text)`

