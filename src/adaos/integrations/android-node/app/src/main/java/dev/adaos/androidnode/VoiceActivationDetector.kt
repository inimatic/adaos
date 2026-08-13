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
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.max
import kotlin.math.sqrt

class VoiceActivationDetector(
    private val context: Context,
    dataRoot: File,
    private val onNativeCaptureChanged: (Boolean) -> Unit = {},
) {
    private val policyFile = dataRoot.resolve("voice-listening-policy.json")
    private val runtimeFile = dataRoot.resolve("voice-audio-runtime.json")
    private val stopping = AtomicBoolean(false)
    private var thread: Thread? = null

    fun start() {
        if (thread?.isAlive == true) return
        stopping.set(false)
        thread = Thread(::runLoop, "adaos-android-voice-detector").also {
            it.isDaemon = true
            it.start()
        }
    }

    fun deferUntilUserVisible(reason: String) {
        publishRuntime(
            "deferred_user_visible_start",
            JSONObject().put("reason", reason).put("capture_owner", "none"),
        )
    }

    fun stop() {
        stopping.set(true)
        thread?.interrupt()
        thread?.join(2500)
        thread = null
        publishRuntime("stopped", JSONObject().put("reason", "node_service_stopped"))
    }

    private fun runLoop() {
        while (!stopping.get()) {
            val policy = readPolicy()
            val mode = policy.optString("listening_mode", "activation")
            val nativeEnabled = policy.optBoolean("native_detector_enabled", false)
            val detector = policy.optJSONObject("activation")
                ?.optString("native_detector", "android_on_device_speech")
                ?: "android_on_device_speech"
            if (!nativeEnabled || mode !in setOf("activation", "continuous") || detector != "audio_record_vad") {
                val stt = policy.optJSONObject("stt") ?: JSONObject()
                val providerMode = stt.optString("provider_mode", "system")
                val activeProvider = if (providerMode == "auto") {
                    stt.optString("active_provider", "system")
                } else {
                    providerMode
                }
                val delegatedOwner = when {
                    !nativeEnabled -> "browser"
                    activeProvider == "vosk" -> "vosk_streaming"
                    else -> "android_speech_recognizer"
                }
                publishRuntime(
                    when {
                        mode == "off" -> "disabled"
                        !nativeEnabled -> "parked_browser_owns_mic"
                        activeProvider == "vosk" -> "delegated_vosk"
                        else -> "delegated_native_speech"
                    },
                    JSONObject()
                        .put("listening_mode", mode)
                        .put("native_detector_enabled", nativeEnabled)
                        .put("native_detector", detector)
                        .put("capture_owner", delegatedOwner)
                        .put("stt_provider", activeProvider),
                )
                sleepBounded(1000)
                continue
            }
            if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                publishRuntime(
                    "permission_required",
                    JSONObject().put("listening_mode", mode).put("permission", Manifest.permission.RECORD_AUDIO),
                )
                sleepBounded(1500)
                continue
            }
            captureUntilPolicyChanges(mode)
        }
    }

    private fun captureUntilPolicyChanges(mode: String) {
        val sampleRate = 16000
        val minimum = AudioRecord.getMinBufferSize(
            sampleRate,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minimum <= 0) {
            publishRuntime("audio_record_unsupported", JSONObject().put("minimum_buffer", minimum))
            sleepBounded(1500)
            return
        }
        val bufferSamples = max(minimum / 2, 1600)
        var recorder: AudioRecord? = null
        var aec: AcousticEchoCanceler? = null
        var noiseSuppressor: NoiseSuppressor? = null
        var gainControl: AutomaticGainControl? = null
        try {
            // Promote the already-running specialUse foreground service only
            // for the interval in which this process owns AudioRecord.
            onNativeCaptureChanged(true)
            recorder = AudioRecord.Builder()
                .setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .build(),
                )
                .setBufferSizeInBytes(bufferSamples * 2)
                .build()
            aec = if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(recorder.audioSessionId) else null
            noiseSuppressor = if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(recorder.audioSessionId) else null
            gainControl = if (AutomaticGainControl.isAvailable()) AutomaticGainControl.create(recorder.audioSessionId) else null
            aec?.enabled = true
            noiseSuppressor?.enabled = true
            gainControl?.enabled = true
            recorder.startRecording()
            val samples = ShortArray(bufferSamples)
            var noiseFloor = 200.0
            var speechActive = false
            var voiceEvents = 0L
            var frames = 0L
            var clippedSamples = 0L
            var lastPublishAt = 0L
            while (!stopping.get()) {
                val current = readPolicy()
                if (
                    !current.optBoolean("native_detector_enabled", false) ||
                    current.optString("listening_mode", "activation") !in setOf("activation", "continuous") ||
                    current.optJSONObject("activation")?.optString("native_detector", "") != "audio_record_vad"
                ) break
                val count = recorder.read(samples, 0, samples.size, AudioRecord.READ_BLOCKING)
                if (count <= 0) continue
                frames += 1
                var energy = 0.0
                for (index in 0 until count) {
                    val value = samples[index].toDouble()
                    energy += value * value
                    if (kotlin.math.abs(samples[index].toInt()) >= 32760) clippedSamples += 1
                }
                val rms = sqrt(energy / max(1, count))
                if (!speechActive) noiseFloor = noiseFloor * 0.96 + rms * 0.04
                val nextSpeech = rms >= max(650.0, noiseFloor * 2.8)
                if (nextSpeech && !speechActive) voiceEvents += 1
                speechActive = nextSpeech
                val now = System.currentTimeMillis()
                if (now - lastPublishAt >= 1000) {
                    lastPublishAt = now
                    publishRuntime(
                        "listening",
                        JSONObject()
                            .put("listening_mode", mode)
                            .put("capture_owner", "native_audio_record")
                            .put("sample_rate", sampleRate)
                            .put("audio_source", "voice_communication")
                            .put("rms", rms)
                            .put("noise_floor", noiseFloor)
                            .put("speech_active", speechActive)
                            .put("voice_activity_events", voiceEvents)
                            .put("frames", frames)
                            .put("clipped_samples", clippedSamples)
                            .put(
                                "aec",
                                JSONObject()
                                    .put("available", AcousticEchoCanceler.isAvailable())
                                    .put("enabled", aec?.enabled == true)
                                    .put("reference", "android_platform_render_mix"),
                            )
                            .put(
                                "noise_suppression",
                                JSONObject()
                                    .put("available", NoiseSuppressor.isAvailable())
                                    .put("enabled", noiseSuppressor?.enabled == true),
                            )
                            .put(
                                "automatic_gain_control",
                                JSONObject()
                                    .put("available", AutomaticGainControl.isAvailable())
                                    .put("enabled", gainControl?.enabled == true),
                            ),
                    )
                }
            }
        } catch (error: Throwable) {
            Log.e(TAG, "AudioRecord activation detector failed", error)
            publishRuntime(
                "failed",
                JSONObject().put("error", "${error.javaClass.simpleName}:${error.message ?: "unknown"}"),
            )
            sleepBounded(1500)
        } finally {
            try { recorder?.stop() } catch (_: Throwable) {}
            recorder?.release()
            aec?.release()
            noiseSuppressor?.release()
            gainControl?.release()
            try { onNativeCaptureChanged(false) } catch (_: Throwable) {}
        }
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
                .put("schema_version", "android-voice-audio-runtime.v1")
                .put("state", state)
                .put("updated_at_ms", System.currentTimeMillis())
            fields.keys().forEach { key -> payload.put(key, fields.get(key)) }
            runtimeFile.parentFile?.mkdirs()
            val temporary = File(runtimeFile.parentFile, "${runtimeFile.name}.tmp")
            temporary.writeText(payload.toString(), Charsets.UTF_8)
            if (!temporary.renameTo(runtimeFile)) {
                runtimeFile.writeText(payload.toString(), Charsets.UTF_8)
                temporary.delete()
            }
        } catch (error: Throwable) {
            Log.w(TAG, "Failed to publish voice runtime", error)
        }
    }

    private fun sleepBounded(durationMs: Long) {
        try {
            Thread.sleep(durationMs)
        } catch (_: InterruptedException) {}
    }

    companion object {
        private const val TAG = "AdaOSVoiceDetector"
    }
}
