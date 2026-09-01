package dev.adaos.androidnode

import android.content.Context
import android.media.AudioAttributes
import android.media.MediaPlayer
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

class LoopbackTtsAudioPlayer(private val context: Context) {
    private var player: MediaPlayer? = null

    data class AudioResult(
        val file: File?,
        val contentType: String,
        val bytes: Long,
        val error: String = "",
    ) {
        val ok: Boolean = file != null && error.isBlank()
    }

    fun stop() {
        val current = player
        player = null
        try { current?.stop() } catch (_: Throwable) {}
        try { current?.release() } catch (_: Throwable) {}
    }

    fun requestAudio(text: String, lang: String, voice: String): AudioResult {
        val connection = URL(TTS_URL).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 3_000
            connection.readTimeout = 55_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.setRequestProperty("Accept", "audio/mpeg, audio/*;q=0.9, application/json;q=0.3")
            val body = JSONObject()
                .put("text", text.take(4096))
                .put("lang", lang)
                .put("voice", voice)
                .toString()
                .toByteArray(Charsets.UTF_8)
            connection.outputStream.use { it.write(body) }
            val code = connection.responseCode
            val contentType = connection.contentType ?: ""
            val input = if (code in 200..299) connection.inputStream else connection.errorStream
            if (code !in 200..299 || !contentType.lowercase().startsWith("audio/")) {
                val detail = input?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
                return AudioResult(
                    file = null,
                    contentType = contentType,
                    bytes = 0,
                    error = "http_$code:${detail.take(240)}",
                )
            }
            val suffix = when {
                contentType.contains("wav", ignoreCase = true) -> ".wav"
                contentType.contains("aac", ignoreCase = true) -> ".aac"
                contentType.contains("ogg", ignoreCase = true) -> ".opus"
                else -> ".mp3"
            }
            val file = File.createTempFile("adaos-tts-", suffix, context.cacheDir)
            var total = 0L
            val audioInput = input ?: return AudioResult(null, contentType, 0, "empty_response")
            audioInput.use { source ->
                file.outputStream().use { target ->
                    val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                    while (true) {
                        val read = source.read(buffer)
                        if (read <= 0) break
                        total += read.toLong()
                        target.write(buffer, 0, read)
                    }
                }
            }
            if (total <= 0) {
                file.delete()
                AudioResult(null, contentType, 0, "empty_audio")
            } else {
                AudioResult(file, contentType, total)
            }
        } catch (error: Throwable) {
            AudioResult(
                file = null,
                contentType = "",
                bytes = 0,
                error = "${error.javaClass.simpleName}:${error.message}",
            )
        } finally {
            connection.disconnect()
        }
    }

    fun play(file: File, onDone: () -> Unit, onError: (String) -> Unit): Boolean {
        stop()
        val next = MediaPlayer()
        player = next
        next.setAudioAttributes(
            AudioAttributes.Builder()
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .build(),
        )
        next.setOnCompletionListener {
            cleanupPlayer(next, file)
            onDone()
        }
        next.setOnErrorListener { _, what, extra ->
            cleanupPlayer(next, file)
            onError("media_player_error:$what:$extra")
            true
        }
        return try {
            next.setDataSource(file.absolutePath)
            next.prepare()
            next.start()
            true
        } catch (error: Throwable) {
            cleanupPlayer(next, file)
            onError("${error.javaClass.simpleName}:${error.message}")
            false
        }
    }

    private fun cleanupPlayer(target: MediaPlayer, file: File) {
        if (player === target) player = null
        try { target.release() } catch (_: Throwable) {}
        try { file.delete() } catch (_: Throwable) {}
    }

    companion object {
        private const val TTS_URL = "http://127.0.0.1:8777/api/node/voice/tts/synthesize"
    }
}
