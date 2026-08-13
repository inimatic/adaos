package dev.adaos.androidnode

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Continuous PCM capture and streaming offline recognition for the Vosk provider. */
class VoskVoiceAssistant(
    private val context: Context,
    private val dataRoot: File,
    private val onNativeCaptureChanged: (Boolean) -> Unit = {},
) {
    private val policyFile = dataRoot.resolve("voice-listening-policy.json")
    private val runtimeFile = dataRoot.resolve("voice-vosk-runtime.json")
    private val main = Handler(Looper.getMainLooper())
    private val captureWorker = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "adaos-vosk-capture").apply { isDaemon = true }
    }
    private val dispatchWorker = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "adaos-vosk-dispatch").apply { isDaemon = true }
    }
    private val stopped = AtomicBoolean(true)
    private val captureRunning = AtomicBoolean(false)
    private val dispatchBusy = AtomicBoolean(false)
    @Volatile private var desired = false
    @Volatile private var captureEligible = false
    @Volatile private var speaking = false
    @Volatile private var aecEnabled = false
    private var textToSpeech: TextToSpeech? = null
    private var ttsReady = false
    private var currentModelId = ""
    private var currentModel: Model? = null
    private var currentRecognizer: Recognizer? = null
    private var audioRecord: AudioRecord? = null
    private var acousticEchoCanceler: AcousticEchoCanceler? = null
    private var noiseSuppressor: NoiseSuppressor? = null
    private var automaticGainControl: AutomaticGainControl? = null
    private var recentSpeechText = ""
    private var echoGuardUntil = 0L
    private var captureCycles = 0L
    private var finalizedUtterances = 0L
    private var dispatchedUtterances = 0L
    private var acceptedUtterances = 0L
    private var rejectedUtterances = 0L
    private var droppedUtterances = 0L
    private var echoSuppressed = 0L
    private var captureErrors = 0L
    private var partialText = ""
    private var lastTranscript = ""
    private var lastError = ""

    private val policyPoll = object : Runnable {
        override fun run() {
            if (stopped.get()) return
            evaluatePolicy()
            main.postDelayed(this, POLICY_POLL_MS)
        }
    }

    fun start(userVisibleCapture: Boolean) {
        if (userVisibleCapture) captureEligible = true
        if (!stopped.compareAndSet(true, false)) {
            if (userVisibleCapture) main.post { evaluatePolicy() }
            return
        }
        main.post {
            initializeTts()
            policyPoll.run()
        }
    }

    fun stop() {
        if (!stopped.compareAndSet(false, true)) return
        desired = false
        main.removeCallbacks(policyPoll)
        stopCapture()
        dispatchWorker.shutdownNow()
        captureWorker.shutdownNow()
        textToSpeech?.stop()
        textToSpeech?.shutdown()
        textToSpeech = null
        publishRuntime("stopped", JSONObject().put("reason", "node_service_stopped"))
    }

    private fun evaluatePolicy() {
        val policy = readPolicy()
        val mode = policy.optString("listening_mode", "activation")
        val stt = policy.optJSONObject("stt") ?: JSONObject()
        val providerMode = stt.optString("provider_mode", "system")
        val activeProvider = if (providerMode == "auto") stt.optString("active_provider", "system") else providerMode
        val nextDesired = policy.optBoolean("native_detector_enabled", false) &&
            mode in setOf("activation", "continuous") && activeProvider == "vosk"
        if (nextDesired && !captureEligible) {
            desired = false
            stopCapture()
            publishRuntime("deferred_user_visible_start", JSONObject().put("provider_mode", providerMode))
            return
        }
        desired = nextDesired
        if (!desired) {
            stopCapture()
            publishRuntime("parked", JSONObject().put("provider_mode", providerMode).put("active_provider", activeProvider))
            return
        }
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            desired = false
            publishRuntime("permission_required", JSONObject())
            return
        }
        val modelId = stt.optString("selected_model_id", "").ifBlank {
            selectedModelForLanguage(stt.optString("language", "ru-RU"))
        }
        val modelDir = dataRoot.resolve("models/vosk").resolve(modelId)
        if (modelId.isBlank() || !modelDir.resolve(".adaos-model.json").isFile) {
            stopCapture()
            publishRuntime(
                "model_required",
                JSONObject().put("model_id", modelId).put("language", stt.optString("language", "ru-RU")),
            )
            return
        }
        if (!captureRunning.get()) startCapture(modelId, modelDir)
    }

    private fun startCapture(modelId: String, modelDir: File) {
        if (!captureRunning.compareAndSet(false, true)) return
        captureWorker.execute {
            var record: AudioRecord? = null
            try {
                if (currentModelId != modelId || currentModel == null) {
                    currentRecognizer?.close()
                    currentModel?.close()
                    currentRecognizer = null
                    currentModel = Model(modelDir.absolutePath)
                    currentModelId = modelId
                }
                val loadedModel = currentModel ?: throw IllegalStateException("vosk_model_not_loaded")
                val recognizer = Recognizer(loadedModel, SAMPLE_RATE.toFloat()).also {
                    it.setWords(true)
                }
                currentRecognizer = recognizer
                val minimum = AudioRecord.getMinBufferSize(
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                )
                val bufferSize = maxOf(minimum, 8_192)
                record = AudioRecord(
                    MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufferSize * 2,
                )
                if (record.state != AudioRecord.STATE_INITIALIZED) throw IllegalStateException("audio_record_not_initialized")
                audioRecord = record
                configureAudioEffects(record.audioSessionId)
                record.startRecording()
                captureCycles += 1
                onNativeCaptureChanged(true)
                publishRuntime(
                    "listening",
                    JSONObject()
                        .put("model_id", modelId)
                        .put("capture_owner", "vosk_streaming")
                        .put("sample_rate", SAMPLE_RATE)
                        .put("aec_enabled", aecEnabled),
                )
                val pcm = ByteArray(bufferSize)
                while (!stopped.get() && desired && captureRunning.get()) {
                    if (speaking && !aecEnabled) {
                        Thread.sleep(40)
                        continue
                    }
                    val count = record.read(pcm, 0, pcm.size, AudioRecord.READ_BLOCKING)
                    if (count <= 0) {
                        if (count < 0) throw IllegalStateException("audio_record_read:$count")
                        continue
                    }
                    if (recognizer.acceptWaveForm(pcm, count)) {
                        val text = JSONObject(recognizer.result).optString("text", "").trim()
                        if (text.isNotEmpty()) handleFinalText(text, modelId)
                    } else {
                        val nextPartial = JSONObject(recognizer.partialResult).optString("partial", "").trim()
                        if (nextPartial != partialText) partialText = nextPartial.take(180)
                    }
                }
            } catch (error: Throwable) {
                captureErrors += 1
                lastError = "${error.javaClass.simpleName}:${error.message}"
                Log.e(TAG, "Vosk capture failed", error)
                publishRuntime("failed", JSONObject().put("error", lastError).put("model_id", modelId))
            } finally {
                onNativeCaptureChanged(false)
                releaseAudioEffects()
                try { record?.stop() } catch (_: Throwable) {}
                record?.release()
                if (audioRecord === record) audioRecord = null
                currentRecognizer?.close()
                currentRecognizer = null
                captureRunning.set(false)
                if (!stopped.get() && desired) main.postDelayed({ evaluatePolicy() }, RETRY_MS)
            }
        }
    }

    private fun handleFinalText(text: String, modelId: String) {
        finalizedUtterances += 1
        lastTranscript = text.take(240)
        partialText = ""
        if (isLikelyEcho(text)) {
            echoSuppressed += 1
            publishRuntime("echo_suppressed", JSONObject().put("transcript", lastTranscript).put("model_id", modelId))
            return
        }
        if (!dispatchBusy.compareAndSet(false, true)) {
            droppedUtterances += 1
            publishRuntime("dispatch_backpressure", JSONObject().put("transcript", lastTranscript).put("model_id", modelId))
            return
        }
        val captureId = "android-vosk:${UUID.randomUUID().toString().replace("-", "").take(16)}"
        dispatchWorker.execute {
            val result = postTranscript(text, captureId, modelId)
            dispatchedUtterances += 1
            dispatchBusy.set(false)
            val accepted = result.optBoolean("accepted", false)
            if (accepted) acceptedUtterances += 1 else rejectedUtterances += 1
            val response = result.optString("response", "").trim()
            val renderHere = result.optBoolean("voice_render_here", false)
            publishRuntime(
                if (accepted) "accepted" else result.optString("state", "suppressed"),
                JSONObject()
                    .put("transcript", lastTranscript)
                    .put("model_id", modelId)
                    .put("accepted", accepted)
                    .put("render_here", renderHere),
            )
            if (accepted && renderHere && response.isNotEmpty()) main.post {
                speak(response, result.optString("active_agent_voice", ""))
            }
        }
    }

    private fun postTranscript(text: String, captureId: String, modelId: String): JSONObject {
        val connection = URL(TRANSCRIPT_URL).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 2_000
            connection.readTimeout = 50_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            val request = JSONObject()
                .put("text", text)
                .put("alternatives", JSONArray().put(text))
                .put("confidence", JSONObject.NULL)
                .put("capture_id", captureId)
                .put("observed_at_ms", System.currentTimeMillis())
                .put("capture_backend", "vosk_streaming")
                .put("stt_model_id", modelId)
                .put("stt_language", readPolicy().optJSONObject("stt")?.optString("language", "ru-RU"))
                .put("window_ms", 280)
            connection.outputStream.use { it.write(request.toString().toByteArray(Charsets.UTF_8)) }
            val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
            val raw = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (raw.isBlank()) JSONObject().put("ok", false).put("accepted", false).put("state", "empty_response")
            else JSONObject(raw)
        } catch (error: Throwable) {
            lastError = "${error.javaClass.simpleName}:${error.message}"
            JSONObject().put("ok", false).put("accepted", false).put("state", "dispatch_failed").put("error", lastError)
        } finally {
            connection.disconnect()
        }
    }

    private fun configureAudioEffects(sessionId: Int) {
        acousticEchoCanceler = if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(sessionId) else null
        noiseSuppressor = if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(sessionId) else null
        automaticGainControl = if (AutomaticGainControl.isAvailable()) AutomaticGainControl.create(sessionId) else null
        acousticEchoCanceler?.enabled = true
        noiseSuppressor?.enabled = true
        automaticGainControl?.enabled = true
        aecEnabled = acousticEchoCanceler?.enabled == true
    }

    private fun releaseAudioEffects() {
        acousticEchoCanceler?.release()
        noiseSuppressor?.release()
        automaticGainControl?.release()
        acousticEchoCanceler = null
        noiseSuppressor = null
        automaticGainControl = null
        aecEnabled = false
    }

    private fun stopCapture() {
        captureRunning.set(false)
        try { audioRecord?.stop() } catch (_: Throwable) {}
    }

    private fun initializeTts() {
        if (textToSpeech != null) return
        textToSpeech = TextToSpeech(context) { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            if (ttsReady) {
                textToSpeech?.language = Locale.forLanguageTag("ru-RU")
                textToSpeech?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) = Unit
                    override fun onDone(utteranceId: String?) {
                        main.post { finishSpeaking() }
                    }
                    @Deprecated("Deprecated in Java")
                    override fun onError(utteranceId: String?) {
                        main.post { finishSpeaking() }
                    }
                })
            }
        }
    }

    private fun speak(text: String, voiceProfile: String) {
        if (!ttsReady) return
        speaking = true
        recentSpeechText = text
        echoGuardUntil = Long.MAX_VALUE
        val language = if (voiceProfile.startsWith("en", ignoreCase = true)) "en-US" else "ru-RU"
        textToSpeech?.language = Locale.forLanguageTag(language)
        textToSpeech?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "adaos-vosk-${System.currentTimeMillis()}")
    }

    private fun finishSpeaking() {
        speaking = false
        echoGuardUntil = System.currentTimeMillis() + ECHO_TAIL_MS
    }

    private fun isLikelyEcho(text: String): Boolean {
        if (System.currentTimeMillis() > echoGuardUntil) return false
        val candidate = normalizeSpeech(text)
        val spoken = normalizeSpeech(recentSpeechText)
        if (candidate.length < 6 || spoken.length < 6) return false
        if (candidate in spoken || spoken in candidate) return true
        val candidateWords = candidate.split(" ").filter { it.length > 1 }.toSet()
        val spokenWords = spoken.split(" ").filter { it.length > 1 }.toSet()
        if (candidateWords.size < 2 || spokenWords.size < 2) return false
        return candidateWords.count { it in spokenWords }.toDouble() / candidateWords.size >= 0.55
    }

    private fun normalizeSpeech(text: String): String = text
        .lowercase(Locale.ROOT)
        .replace(Regex("[^\\p{L}\\p{N}]+"), " ")
        .trim()
        .replace(Regex("\\s+"), " ")

    private fun selectedModelForLanguage(language: String): String {
        return try {
            val selection = JSONObject(dataRoot.resolve("models/vosk/selection.json").readText())
            val languages = selection.optJSONObject("languages") ?: JSONObject()
            languages.optString(language, "")
        } catch (_: Throwable) {
            ""
        }
    }

    private fun readPolicy(): JSONObject = try {
        JSONObject(policyFile.readText())
    } catch (_: Throwable) {
        JSONObject()
    }

    @Synchronized
    private fun publishRuntime(state: String, details: JSONObject) {
        val payload = JSONObject()
            .put("schema_version", "android-vosk-runtime.v1")
            .put("state", state)
            .put("provider", "vosk")
            .put("model_id", currentModelId)
            .put("capture_running", captureRunning.get())
            .put("dispatch_busy", dispatchBusy.get())
            .put("speaking", speaking)
            .put("aec_available", AcousticEchoCanceler.isAvailable())
            .put("aec_enabled", aecEnabled)
            .put("noise_suppression_available", NoiseSuppressor.isAvailable())
            .put("automatic_gain_control_available", AutomaticGainControl.isAvailable())
            .put("capture_cycles", captureCycles)
            .put("finalized_utterances", finalizedUtterances)
            .put("dispatched_utterances", dispatchedUtterances)
            .put("accepted_utterances", acceptedUtterances)
            .put("rejected_utterances", rejectedUtterances)
            .put("dropped_utterances", droppedUtterances)
            .put("echo_suppressed", echoSuppressed)
            .put("capture_errors", captureErrors)
            .put("partial", partialText)
            .put("last_transcript", lastTranscript)
            .put("last_error", lastError)
            .put("updated_at_epoch_ms", System.currentTimeMillis())
            .put("details", details)
        try {
            runtimeFile.parentFile?.mkdirs()
            val temporary = File(runtimeFile.parentFile, "${runtimeFile.name}.tmp")
            temporary.writeText(payload.toString(2))
            if (!temporary.renameTo(runtimeFile)) {
                runtimeFile.writeText(payload.toString(2))
                temporary.delete()
            }
        } catch (error: Throwable) {
            Log.w(TAG, "Failed to publish Vosk runtime", error)
        }
    }

    companion object {
        private const val SAMPLE_RATE = 16_000
        private const val POLICY_POLL_MS = 750L
        private const val RETRY_MS = 1_500L
        private const val ECHO_TAIL_MS = 2_500L
        private const val TRANSCRIPT_URL = "http://127.0.0.1:8777/api/node/voice/native/transcript"
        private const val TAG = "AdaOSVoskVoice"
    }
}
