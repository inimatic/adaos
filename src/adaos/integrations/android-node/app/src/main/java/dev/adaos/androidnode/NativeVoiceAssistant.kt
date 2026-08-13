package dev.adaos.androidnode

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/** Browser-free, address-gated voice loop backed by the Android recognizer. */
class NativeVoiceAssistant(
    private val context: Context,
    dataRoot: File,
    private val onNativeCaptureChanged: (Boolean) -> Unit = {},
) : RecognitionListener {
    private val policyFile = dataRoot.resolve("voice-listening-policy.json")
    private val runtimeFile = dataRoot.resolve("voice-native-runtime.json")
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "adaos-native-voice-http").apply { isDaemon = true }
    }
    private val stopped = AtomicBoolean(true)
    private var recognizer: SpeechRecognizer? = null
    private var textToSpeech: TextToSpeech? = null
    private var ttsReady = false
    private var pendingSpeech: Pair<String, String>? = null
    private var listening = false
    private var processing = false
    private var speaking = false
    private var desired = false
    private var recognizerKind = "unavailable"
    private var forceSystemRecognizer = false
    private var listeningMode = "activation"
    private var bargeInEnabled = true
    private var currentSpeechText = ""
    private var recentSpeechText = ""
    private var ttsEchoGuardUntil = 0L
    private var lastStartAt = 0L
    private var recognitionCycles = 0L
    private var transcriptCount = 0L
    private var acceptedCount = 0L
    private var ignoredCount = 0L
    private var recognizerErrorCount = 0L
    private var lastTranscript = ""
    private var lastConfidence: Float? = null
    private var lastDecision = "none"
    private var lastErrorCode: Int? = null
    private var lastAlternatives: List<String> = emptyList()
    private var bargeInCandidateCount = 0L
    private var ttsEchoSuppressedCount = 0L
    @Volatile private var captureEligible = false

    private val policyPoll = object : Runnable {
        override fun run() {
            if (stopped.get()) return
            evaluatePolicy()
            main.postDelayed(this, POLICY_POLL_MS)
        }
    }
    private val restartRecognition = Runnable {
        if (!stopped.get() && desired && !listening && !processing) {
            startListening(allowWhileSpeaking = speaking && bargeInEnabled)
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
        main.post {
            main.removeCallbacks(policyPoll)
            main.removeCallbacks(restartRecognition)
            desired = false
            captureEligible = false
            stopRecognizer()
            textToSpeech?.stop()
            textToSpeech?.shutdown()
            textToSpeech = null
            ttsReady = false
            publishRuntime("stopped", JSONObject().put("reason", "node_service_stopped"))
        }
        worker.shutdownNow()
    }

    private fun evaluatePolicy() {
        val policy = readPolicy()
        listeningMode = policy.optString("listening_mode", "activation")
        val activation = policy.optJSONObject("activation") ?: JSONObject()
        val stt = policy.optJSONObject("stt") ?: JSONObject()
        val providerMode = stt.optString("provider_mode", "system")
        val activeProvider = if (providerMode == "auto") {
            stt.optString("active_provider", "system")
        } else {
            providerMode
        }
        bargeInEnabled = activation.optBoolean("barge_in_enabled", true)
        val backend = activation.optString("native_detector", "android_on_device_speech")
        val enabled = policy.optBoolean("native_detector_enabled", false)
        val configured = enabled && listeningMode in setOf("activation", "continuous") &&
            backend == "android_on_device_speech" && activeProvider == "system"
        if (configured && !captureEligible) {
            desired = false
            stopRecognizer()
            publishRuntime(
                "deferred_user_visible_start",
                JSONObject()
                    .put("reason", "android_while_in_use_microphone_restriction")
                    .put("listening_mode", listeningMode)
                    .put("native_detector", backend),
            )
            return
        }
        val nextDesired = configured
        if (nextDesired == desired) {
            if (desired && !listening && !processing && !speaking) scheduleRestart(0)
            return
        }
        desired = nextDesired
        if (!desired) {
            stopRecognizer()
            publishRuntime(
                if (listeningMode == "off") "disabled" else "parked",
                JSONObject()
                    .put("listening_mode", listeningMode)
                    .put("native_detector_enabled", enabled)
                    .put("native_detector", backend),
            )
            return
        }
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            desired = false
            publishRuntime("permission_required", JSONObject().put("permission", Manifest.permission.RECORD_AUDIO))
            return
        }
        ensureRecognizer(activation.optBoolean("prefer_on_device", true))
        scheduleRestart(0)
    }

    private fun ensureRecognizer(preferOnDevice: Boolean) {
        if (recognizer != null) return
        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            publishRuntime("recognizer_unavailable", JSONObject())
            return
        }
        val onDevice = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            SpeechRecognizer.isOnDeviceRecognitionAvailable(context)
        recognizer = if (preferOnDevice && onDevice && !forceSystemRecognizer) {
            recognizerKind = "android_on_device"
            SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        } else {
            recognizerKind = "android_system_default"
            SpeechRecognizer.createSpeechRecognizer(context)
        }.also { it.setRecognitionListener(this) }
    }

    private fun startListening(allowWhileSpeaking: Boolean = false) {
        if (!desired || stopped.get() || processing || (speaking && !allowWhileSpeaking) || listening) return
        val policy = readPolicy()
        val activation = policy.optJSONObject("activation") ?: JSONObject()
        ensureRecognizer(activation.optBoolean("prefer_on_device", true))
        val current = recognizer ?: run {
            scheduleRestart(RETRY_MS)
            return
        }
        try {
            val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, activation.optString("language", "ru-RU"))
                .putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
                .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, recognizerKind == "android_on_device")
            onNativeCaptureChanged(true)
            listening = true
            lastStartAt = System.currentTimeMillis()
            recognitionCycles += 1
            current.startListening(intent)
            publishRuntime(
                "listening",
                JSONObject()
                    .put("listening_mode", listeningMode)
                    .put("recognizer", recognizerKind)
                    .put("capture_owner", "android_speech_recognizer")
                    .put("address_required", listeningMode == "activation")
                    .put("barge_in", speaking && allowWhileSpeaking),
            )
        } catch (error: Throwable) {
            listening = false
            onNativeCaptureChanged(false)
            publishRuntime("failed", JSONObject().put("error", "${error.javaClass.simpleName}:${error.message}"))
            recreateRecognizer()
            scheduleRestart(RETRY_MS)
        }
    }

    private fun submitTranscript(text: String, confidence: Float?) {
        listening = false
        processing = true
        onNativeCaptureChanged(false)
        transcriptCount += 1
        lastTranscript = text.take(240)
        lastConfidence = confidence
        lastDecision = "processing"
        val captureId = "android:${UUID.randomUUID().toString().replace("-", "").take(16)}"
        publishRuntime(
            "processing",
            JSONObject()
                .put("transcript", text.take(240))
                .put("confidence", confidence)
                .put("capture_id", captureId)
                .put("recognizer", recognizerKind),
        )
        worker.execute {
            val result = postTranscript(text, confidence, captureId, lastAlternatives)
            main.post {
                processing = false
                val accepted = result.optBoolean("accepted", false)
                val response = result.optString("response", "").trim()
                val renderHere = result.optBoolean("voice_render_here", false)
                lastDecision = if (accepted) "accepted" else result.optString("state", "suppressed")
                if (accepted) acceptedCount += 1 else ignoredCount += 1
                if (accepted && speaking) {
                    textToSpeech?.stop()
                    speaking = false
                    currentSpeechText = ""
                }
                publishRuntime(
                    if (accepted) "accepted" else result.optString("state", "suppressed"),
                    JSONObject()
                        .put("transcript", text.take(240))
                        .put("confidence", confidence)
                        .put("capture_id", captureId)
                        .put("recognizer", recognizerKind)
                        .put("accepted", accepted)
                        .put("response_source", result.optString("response_source", ""))
                        .put("arbitration", result.optJSONObject("arbitration")),
                )
                if (accepted && renderHere && response.isNotEmpty()) {
                    speak(response, result.optString("active_agent_voice", ""))
                } else {
                    scheduleRestart(if (accepted) 250 else 450)
                }
            }
        }
    }

    private fun postTranscript(
        text: String,
        confidence: Float?,
        captureId: String,
        alternatives: List<String>,
    ): JSONObject {
        val connection = URL(TRANSCRIPT_URL).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 2_000
            connection.readTimeout = 50_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            val request = JSONObject()
                .put("text", text)
                .put("alternatives", JSONArray(alternatives))
                .put("confidence", confidence)
                .put("capture_id", captureId)
                .put("observed_at_ms", System.currentTimeMillis())
                .put("capture_backend", recognizerKind)
                .put("window_ms", 280)
            connection.outputStream.use { output -> output.write(request.toString().toByteArray(Charsets.UTF_8)) }
            val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
            val raw = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (raw.isBlank()) JSONObject().put("ok", false).put("accepted", false).put("state", "empty_response")
            else JSONObject(raw)
        } catch (error: Throwable) {
            Log.e(TAG, "Native transcript dispatch failed", error)
            JSONObject()
                .put("ok", false)
                .put("accepted", false)
                .put("state", "dispatch_failed")
                .put("error", "${error.javaClass.simpleName}:${error.message}")
        } finally {
            connection.disconnect()
        }
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
                pendingSpeech?.also { (text, profile) ->
                    pendingSpeech = null
                    speak(text, profile)
                }
            }
        }
    }

    private fun speak(text: String, voiceProfile: String) {
        if (!ttsReady) {
            pendingSpeech = text to voiceProfile
            initializeTts()
            scheduleRestart(1_500)
            return
        }
        speaking = true
        currentSpeechText = text
        recentSpeechText = text
        onNativeCaptureChanged(false)
        textToSpeech?.setPitch(if (voiceProfile.contains("male") && !voiceProfile.contains("female")) 0.9f else 1.08f)
        textToSpeech?.setSpeechRate(1.0f)
        val utteranceId = "adaos-${System.currentTimeMillis()}"
        publishRuntime(
            "speaking",
            JSONObject()
                .put("tts_utterance_id", utteranceId)
                .put("voice_profile", voiceProfile)
                .put("half_duplex", true),
        )
        val status = textToSpeech?.speak(text, TextToSpeech.QUEUE_FLUSH, Bundle(), utteranceId)
        if (status == TextToSpeech.ERROR) finishSpeaking()
        else if (bargeInEnabled) scheduleRestart(250)
    }

    private fun finishSpeaking() {
        speaking = false
        currentSpeechText = ""
        ttsEchoGuardUntil = System.currentTimeMillis() + TTS_ECHO_GUARD_MS
        publishRuntime("tts_complete", JSONObject().put("half_duplex", true))
        scheduleRestart(350)
    }

    private fun scheduleRestart(delayMs: Long) {
        main.removeCallbacks(restartRecognition)
        if (!stopped.get() && desired) main.postDelayed(restartRecognition, delayMs.coerceAtLeast(0))
    }

    private fun recreateRecognizer() {
        recognizer?.destroy()
        recognizer = null
        recognizerKind = "unavailable"
    }

    private fun stopRecognizer() {
        main.removeCallbacks(restartRecognition)
        try { recognizer?.cancel() } catch (_: Throwable) {}
        recreateRecognizer()
        listening = false
        processing = false
        speaking = false
        onNativeCaptureChanged(false)
    }

    private fun readPolicy(): JSONObject = try {
        JSONObject(policyFile.readText(Charsets.UTF_8))
    } catch (_: Throwable) {
        JSONObject()
            .put("listening_mode", "activation")
            .put("native_detector_enabled", false)
    }

    private fun publishRuntime(state: String, fields: JSONObject) {
        try {
            val payload = JSONObject()
                .put("schema_version", "android-native-voice-runtime.v1")
                .put("state", state)
                .put("updated_at_ms", System.currentTimeMillis())
                .put("recognizer", recognizerKind)
                .put("listening_mode", listeningMode)
                .put("continuous_until_stopped", desired)
                .put("recognition_cycles", recognitionCycles)
                .put("transcript_count", transcriptCount)
                .put("accepted_count", acceptedCount)
                .put("ignored_count", ignoredCount)
                .put("recognizer_error_count", recognizerErrorCount)
                .put("last_transcript", lastTranscript)
                .put("last_confidence", lastConfidence)
                .put("last_decision", lastDecision)
                .put("last_error_code", lastErrorCode)
                .put("last_alternatives", JSONArray(lastAlternatives))
                .put("barge_in_enabled", bargeInEnabled)
                .put("barge_in_candidate_count", bargeInCandidateCount)
                .put("tts_echo_suppressed_count", ttsEchoSuppressedCount)
            fields.keys().forEach { key -> payload.put(key, fields.opt(key)) }
            runtimeFile.parentFile?.mkdirs()
            val temporary = File(runtimeFile.parentFile, "${runtimeFile.name}.tmp")
            temporary.writeText(payload.toString(), Charsets.UTF_8)
            if (!temporary.renameTo(runtimeFile)) {
                runtimeFile.writeText(payload.toString(), Charsets.UTF_8)
                temporary.delete()
            }
        } catch (error: Throwable) {
            Log.w(TAG, "Failed to publish native voice runtime", error)
        }
    }

    override fun onReadyForSpeech(params: Bundle?) = Unit
    override fun onBeginningOfSpeech() = Unit
    override fun onRmsChanged(rmsdB: Float) = Unit
    override fun onBufferReceived(buffer: ByteArray?) = Unit
    override fun onEndOfSpeech() {
        publishRuntime("recognizing", JSONObject().put("capture_duration_ms", System.currentTimeMillis() - lastStartAt))
    }
    override fun onError(error: Int) {
        listening = false
        onNativeCaptureChanged(false)
        val idleMiss = error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT
        if (!idleMiss) recognizerErrorCount += 1
        lastErrorCode = if (idleMiss) 0 else error
        lastDecision = if (idleMiss) "idle_no_match" else "recognizer_error"
        val retry = error != SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS
        publishRuntime(
            if (idleMiss) "idle_no_match" else "recognizer_error",
            JSONObject().put("error_code", error).put("retry", retry).put("idle_miss", idleMiss),
        )
        if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) desired = false
        if (
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            error in setOf(SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED, SpeechRecognizer.ERROR_LANGUAGE_UNAVAILABLE)
        ) forceSystemRecognizer = true
        if (error == SpeechRecognizer.ERROR_CLIENT || error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY) recreateRecognizer()
        if (retry) {
            scheduleRestart(
                when {
                    error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> RETRY_MS
                    idleMiss -> 650
                    else -> 350
                },
            )
        }
    }
    override fun onResults(results: Bundle?) {
        val alternatives = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION).orEmpty()
        val confidences = results?.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES)
        lastAlternatives = alternatives.map { it.trim() }.filter { it.isNotEmpty() }.take(5)
        val text = alternatives.firstOrNull().orEmpty().trim()
        val selectedConfidence = confidences?.firstOrNull()?.takeIf { it >= 0f }
        if (text.isEmpty()) {
            listening = false
            onNativeCaptureChanged(false)
            scheduleRestart(350)
            return
        }
        if (speaking || System.currentTimeMillis() <= ttsEchoGuardUntil) {
            listening = false
            onNativeCaptureChanged(false)
            if (isLikelyTtsEcho(text)) {
                ttsEchoSuppressedCount += 1
                lastTranscript = text.take(240)
                lastConfidence = selectedConfidence
                lastDecision = "tts_echo_suppressed"
                publishRuntime(
                    "speaking",
                    JSONObject()
                        .put("barge_in", true)
                        .put("suppressed_transcript", text.take(160)),
                )
                scheduleRestart(250)
                return
            }
            if (speaking) {
                // Keep TTS playing until the Python address gate and room
                // lease accept this turn. Room chatter must not interrupt it.
                bargeInCandidateCount += 1
            }
        }
        submitTranscript(text, selectedConfidence)
    }
    override fun onPartialResults(partialResults: Bundle?) {
        val text = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            ?.firstOrNull().orEmpty().trim()
        if (text.isNotEmpty()) publishRuntime("hearing", JSONObject().put("partial", text.take(160)))
    }
    override fun onEvent(eventType: Int, params: Bundle?) = Unit

    private fun isLikelyTtsEcho(text: String): Boolean {
        val heard = normalizeSpeechText(text)
        val spoken = normalizeSpeechText(currentSpeechText.ifEmpty { recentSpeechText })
        if (heard.length < 4 || spoken.length < 4) return false
        if (heard in spoken || spoken in heard) return true
        val heardTokens = heard.split(' ').filter { it.length > 1 }.toSet()
        val spokenTokens = spoken.split(' ').filter { it.length > 1 }.toSet()
        if (heardTokens.isEmpty() || spokenTokens.isEmpty()) return false
        val overlap = heardTokens.intersect(spokenTokens).size.toDouble()
        return overlap / minOf(heardTokens.size, spokenTokens.size) >= 0.65
    }

    private fun normalizeSpeechText(text: String): String = text
        .lowercase(Locale.forLanguageTag("ru-RU"))
        .replace(Regex("[^\\p{L}\\p{N}]+"), " ")
        .trim()
        .replace(Regex("\\s+"), " ")

    companion object {
        private const val TAG = "AdaOSNativeVoice"
        private const val TRANSCRIPT_URL = "http://127.0.0.1:8777/api/node/voice/native/transcript"
        private const val POLICY_POLL_MS = 750L
        private const val RETRY_MS = 1_200L
        private const val TTS_ECHO_GUARD_MS = 1_800L
    }
}
