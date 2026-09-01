package dev.adaos.androidnode

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private lateinit var phaseView: TextView
    private lateinit var detailView: TextView
    private lateinit var factsView: TextView
    private lateinit var openButton: Button
    private lateinit var voiceRouteView: TextView
    private lateinit var listeningButton: Button
    private lateinit var providerButton: Button
    private lateinit var modelButton: Button
    private lateinit var ttsTestButton: Button
    private lateinit var ttsSettingsButton: Button
    private val main = Handler(Looper.getMainLooper())
    private val controlWorker = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "adaos-node-controls").apply { isDaemon = true }
    }
    private var voiceListeningMode = "activation"
    private var sttProviderMode = "system"
    private var ttsProbe: TextToSpeech? = null
    private var ttsProbeStatus = ""
    private val voiceStatusPoll = object : Runnable {
        override fun run() {
            refreshVoiceControls()
            main.postDelayed(this, VOICE_STATUS_POLL_MS)
        }
    }
    private val statusListener: (NodeStatus) -> Unit = { status -> render(status) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Log.i(TAG, "onCreate instance=${System.identityHashCode(this)}")
        setContentView(buildContent())
        requestNotificationPermission()
        requestMicrophonePermission()
        handleLaunchIntent(intent)
    }

    override fun onStart() {
        super.onStart()
        Log.i(TAG, "onStart instance=${System.identityHashCode(this)}")
        NodeStateStore.subscribe(statusListener)
        if (NodeLifecycleStore.desiredRunning(this)) {
            startService(Intent(this, NodeService::class.java).setAction(NodeService.ACTION_ARM_VOICE))
        }
        main.removeCallbacks(voiceStatusPoll)
        voiceStatusPoll.run()
    }

    override fun onStop() {
        Log.i(TAG, "onStop instance=${System.identityHashCode(this)}")
        NodeStateStore.unsubscribe(statusListener)
        main.removeCallbacks(voiceStatusPoll)
        super.onStop()
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy instance=${System.identityHashCode(this)}")
        main.removeCallbacks(voiceStatusPoll)
        controlWorker.shutdownNow()
        ttsProbe?.stop()
        ttsProbe?.shutdown()
        ttsProbe = null
        super.onDestroy()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        Log.i(
            TAG,
            "onNewIntent action=${intent.action} " +
                "debugStop=${intent.getBooleanExtra(EXTRA_STOP_NODE, false)}",
        )
        setIntent(intent)
        handleLaunchIntent(intent)
    }

    private fun handleLaunchIntent(intent: Intent?) {
        if (BuildConfig.DEBUG && intent?.action == ACTION_DEBUG_START_NODE) {
            Log.i(TAG, "debug lifecycle verifier requested node start")
            startNode()
            return
        }
        if (
            BuildConfig.DEBUG &&
            (
                intent?.action == ACTION_DEBUG_STOP_NODE ||
                    intent?.getBooleanExtra(EXTRA_STOP_NODE, false) == true
            )
        ) {
            Log.i(TAG, "debug lifecycle verifier requested user stop")
            intent.removeExtra(EXTRA_STOP_NODE)
            stopNode()
            return
        }
        if (intent?.getBooleanExtra(EXTRA_START_NODE, false) == true) {
            intent.removeExtra(EXTRA_START_NODE)
            startNode()
        }
    }

    private fun buildContent(): View {
        val scroll = ScrollView(this).apply {
            setBackgroundColor(Color.rgb(17, 24, 39))
            isFillViewport = true
        }
        val content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(dp(24), dp(48), dp(24), dp(32))
        }

        content.addView(label("AdaOS Node", 32f, Color.WHITE, true))
        content.addView(
            label(
                "Experimental arm64 AdaOS host",
                16f,
                Color.rgb(156, 163, 175),
                false,
            )
        )
        phaseView = label("STOPPED", 22f, Color.rgb(129, 140, 248), true)
        detailView = label("Node is stopped", 18f, Color.WHITE, false)
        factsView = label("", 14f, Color.rgb(209, 213, 219), false)
        content.addView(phaseView)
        content.addView(detailView)
        content.addView(factsView)
        voiceRouteView = label(
            "🎙 Node ready · System STT → General · Assistant → 🔊 Node",
            14f,
            Color.rgb(167, 243, 208),
            false,
        )
        content.addView(voiceRouteView)
        listeningButton = button("Always-on assistant: On") { toggleListeningMode() }
        content.addView(listeningButton)
        providerButton = button("STT: System") { cycleSttProvider() }
        content.addView(providerButton)
        modelButton = button("Install Vosk Russian · compact") { installDefaultVoskModel() }
        content.addView(modelButton)
        content.addView(button("Start node") { startNode() })
        content.addView(button("Stop node") { stopNode() })
        openButton = button("Open AdaOS") { openAdaos() }.apply { isEnabled = false }
        content.addView(openButton)
        ttsTestButton = button("Test phone TTS") { testPhoneTts() }
        content.addView(ttsTestButton)
        ttsSettingsButton = button("Open TTS settings") { openTtsSettings() }
        content.addView(ttsSettingsButton)
        scroll.addView(
            content,
            ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
        return scroll
    }

    private fun startNode() {
        NodeLifecycleStore.setDesiredRunning(this, true)
        val intent = Intent(this, NodeService::class.java).setAction(NodeService.ACTION_START)
            .putExtra(NodeService.EXTRA_START_REASON, NodeService.START_REASON_USER)
        startForegroundService(intent)
    }

    private fun stopNode() {
        NodeLifecycleStore.setDesiredRunning(this, false)
        startService(Intent(this, NodeService::class.java).setAction(NodeService.ACTION_STOP))
    }

    private fun openAdaos() {
        startActivity(
            Intent(
                Intent.ACTION_VIEW,
                Uri.parse("https://inimatic.com/?zone=lo&try_local_hub=1"),
            )
        )
    }

    private fun render(status: NodeStatus) {
        phaseView.text = status.phase.name
        detailView.text = status.detail
        factsView.text = listOfNotNull(
            status.pythonVersion?.let { "Python $it" },
            status.port?.let { "LO http://127.0.0.1:$it" },
            status.dataRoot?.let { "Data $it" },
            "APK ${BuildConfig.VERSION_NAME}",
            NodeLifecycleStore.summary(this),
        ).joinToString("\n")
        openButton.isEnabled = status.phase == NodePhase.READY
    }

    private fun refreshVoiceControls() {
        controlWorker.execute {
            val voice = getJson("$LOOPBACK/api/node/voice/listening")
            val models = getJson("$LOOPBACK/api/node/voice/stt/models")
            main.post {
                val service = voice?.optJSONObject("service")
                val runtime = voice?.optJSONObject("runtime")
                voiceListeningMode = service?.optString("listening_mode", voiceListeningMode) ?: voiceListeningMode
                val stt = service?.optJSONObject("stt")
                sttProviderMode = stt?.optString("provider_mode", sttProviderMode) ?: sttProviderMode
                val activeProvider = stt?.optString("active_provider", "system") ?: "system"
                val runtimeState = runtime?.optString("state", "not started") ?: "not started"
                val native = runtime?.optJSONObject("native_assistant")
                val vosk = runtime?.optJSONObject("vosk_assistant")
                val input = if (voiceListeningMode == "off") "Mic off" else "Phone"
                val provider = if (activeProvider == "vosk") "Vosk" else "System STT"
                val install = models?.optJSONObject("install")
                val installState = install?.optString("state", "idle") ?: "idle"
                val installError = install?.optString("error", "")?.takeIf { it.isNotBlank() }
                val voiceDetails = listOfNotNull(
                    "State: $runtimeState",
                    native?.optString("state", "")?.takeIf { it.isNotBlank() }?.let {
                        "System: $it cycles=${native.optLong("recognition_cycles", 0)} " +
                            "heard=${native.optLong("transcript_count", 0)} " +
                            "accepted=${native.optLong("accepted_count", 0)} " +
                            "last=${native.optString("last_decision", "none")}"
                    },
                    native?.optString("last_transcript", "")?.takeIf { it.isNotBlank() }?.let {
                        "Last STT: ${it.take(80)}"
                    },
                    native?.optInt("last_error_code", 0)?.takeIf { it != 0 }?.let {
                        "System error code: $it"
                    },
                    vosk?.optString("state", "")?.takeIf { it.isNotBlank() }?.let {
                        "Vosk: $it model=${vosk.optString("model_id", "")}"
                    },
                    vosk?.optString("last_error", "")?.takeIf { it.isNotBlank() }?.let {
                        "Vosk error: ${it.take(120)}"
                    },
                    native?.let { ttsRuntimeSummary(it, "System TTS") },
                    vosk?.let { ttsRuntimeSummary(it, "Vosk TTS") },
                    installError?.let { "Install error: ${it.take(120)}" },
                    ttsProbeStatus.takeIf { it.isNotBlank() },
                ).joinToString("\n")
                voiceRouteView.text = "🎙 $input · $provider → General · Assistant → 🔊 Phone\n$voiceDetails"
                listeningButton.text = if (voiceListeningMode == "off") {
                    "Always-on assistant: Off"
                } else {
                    "Always-on assistant: On · $voiceListeningMode"
                }
                providerButton.text = "STT: ${sttProviderMode.replaceFirstChar { it.uppercase() }}"
                val installed = models?.optJSONArray("installed")
                val defaultInstalled = (0 until (installed?.length() ?: 0)).any { index ->
                    installed?.optJSONObject(index)?.optString("id") == DEFAULT_VOSK_MODEL
                }
                modelButton.text = when {
                    installState in setOf("downloading", "installing") -> "Vosk model: $installState…"
                    installState == "failed" -> "Vosk install failed · retry"
                    defaultInstalled -> "Vosk Russian compact: installed"
                    else -> "Install Vosk Russian · compact"
                }
                modelButton.isEnabled = installState !in setOf("downloading", "installing")
            }
        }
    }

    private fun ttsRuntimeSummary(runtime: JSONObject, label: String): String? {
        if (!runtime.has("tts_ready") && !runtime.has("last_tts_error")) return null
        val skipped = runtime.optString("last_tts_skipped_reason", "")
        val error = runtime.optString("last_tts_error", "")
        return listOfNotNull(
            "$label: ready=${runtime.optBoolean("tts_ready", false)} " +
                "init=${jsonValue(runtime, "tts_init_status")} " +
                "lang=${jsonValue(runtime, "tts_language_status")} " +
                "speak=${jsonValue(runtime, "last_tts_speak_status")} " +
                "queued=${runtime.optLong("tts_queued_count", 0)} " +
                "done=${runtime.optLong("tts_done_count", 0)}",
            skipped.takeIf { it.isNotBlank() }?.let { "$label skipped: $it" },
            error.takeIf { it.isNotBlank() }?.let { "$label error: ${it.take(80)}" },
        ).joinToString("\n")
    }

    private fun jsonValue(payload: JSONObject, key: String): String =
        if (!payload.has(key) || payload.isNull(key)) "null" else payload.opt(key)?.toString() ?: "null"

    private fun toggleListeningMode() {
        val next = if (voiceListeningMode == "off") "activation" else "off"
        postVoicePolicy(JSONObject().put("listening_mode", next).put("source", "android_node_ui"))
    }

    private fun cycleSttProvider() {
        val next = when (sttProviderMode) {
            "system" -> "auto"
            "auto" -> "vosk"
            else -> "system"
        }
        postVoicePolicy(
            JSONObject()
                .put("listening_mode", voiceListeningMode)
                .put("source", "android_node_ui")
                .put("stt", JSONObject().put("provider_mode", next)),
        )
    }

    private fun installDefaultVoskModel() {
        modelButton.isEnabled = false
        modelButton.text = "Starting Vosk install…"
        controlWorker.execute {
            postJson(
                "$LOOPBACK/api/node/voice/stt/models/install",
                defaultVoskInstallPayload(),
            )
            main.post { refreshVoiceControls() }
        }
    }

    private fun defaultVoskInstallPayload(): JSONObject {
        val payload = JSONObject().put("model_id", DEFAULT_VOSK_MODEL).put("select", true)
        val mirrorBase = BuildConfig.VOSK_MODEL_MIRROR_BASE.trim().trimEnd('/')
        if (mirrorBase.isBlank()) return payload
        val descriptor = JSONObject()
            .put("id", DEFAULT_VOSK_MODEL)
            .put("provider", "vosk")
            .put("language", "ru-RU")
            .put("quality_tier", "compact")
            .put("resource_class", "mobile")
            .put("archive_url", "$mirrorBase/$DEFAULT_VOSK_MODEL.zip")
            .put("archive_sha256", DEFAULT_VOSK_MODEL_SHA256)
            .put("archive_bytes", DEFAULT_VOSK_MODEL_ARCHIVE_BYTES)
            .put("unpacked_folder", DEFAULT_VOSK_MODEL)
            .put("expected_runtime_memory_mb", 300)
            .put("recommended_min_memory_mb", 3072)
            .put("license", "Apache-2.0")
            .put(
                "platforms",
                JSONArray()
                    .put("android-arm64")
                    .put("android-armv7")
                    .put("linux")
                    .put("windows")
                    .put("macos"),
            )
        return payload.put("descriptor", descriptor)
    }

    private fun testPhoneTts() {
        ttsTestButton.isEnabled = false
        ttsProbeStatus = "TTS test: initializing"
        refreshVoiceControls()
        val existing = ttsProbe
        if (existing != null) {
            speakTtsProbe(existing)
            return
        }
        ttsProbe = TextToSpeech(this) { status ->
            main.post {
                if (status != TextToSpeech.SUCCESS) {
                    ttsProbeStatus = "TTS test: init failed status=$status"
                    ttsTestButton.isEnabled = true
                    refreshVoiceControls()
                    return@post
                }
                speakTtsProbe(ttsProbe ?: return@post)
            }
        }
    }

    private fun speakTtsProbe(tts: TextToSpeech) {
        val requested = Locale.forLanguageTag("ru-RU")
        var languageStatus = tts.setLanguage(requested)
        var languageTag = requested.toLanguageTag()
        if (languageStatus in setOf(TextToSpeech.LANG_MISSING_DATA, TextToSpeech.LANG_NOT_SUPPORTED)) {
            val fallback = Locale.getDefault()
            languageStatus = tts.setLanguage(fallback)
            languageTag = fallback.toLanguageTag()
        }
        val engine = tts.defaultEngine ?: "unknown"
        val engines = tts.engines.joinToString(limit = 4) { it.name }
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(utteranceId: String?) = Unit
            override fun onDone(utteranceId: String?) {
                main.post {
                    ttsProbeStatus = "$ttsProbeStatus · done"
                    ttsTestButton.isEnabled = true
                    refreshVoiceControls()
                }
            }
            @Deprecated("Deprecated in Java")
            override fun onError(utteranceId: String?) {
                main.post {
                    ttsProbeStatus = "$ttsProbeStatus · error"
                    ttsTestButton.isEnabled = true
                    refreshVoiceControls()
                }
            }
        })
        val speakStatus = tts.speak(
            "Проверка голосового ответа AdaOS.",
            TextToSpeech.QUEUE_FLUSH,
            Bundle(),
            "adaos-tts-test-${System.currentTimeMillis()}",
        )
        ttsProbeStatus = "TTS test: engine=$engine lang=$languageTag langStatus=$languageStatus " +
            "speak=$speakStatus engines=$engines"
        ttsTestButton.isEnabled = speakStatus != TextToSpeech.SUCCESS
        refreshVoiceControls()
    }

    private fun openTtsSettings() {
        try {
            startActivity(Intent("com.android.settings.TTS_SETTINGS"))
        } catch (error: Throwable) {
            try {
                startActivity(Intent(TextToSpeech.Engine.ACTION_INSTALL_TTS_DATA))
            } catch (installError: Throwable) {
                ttsProbeStatus = "TTS settings failed: ${installError.javaClass.simpleName}"
                refreshVoiceControls()
                startActivity(Intent(Settings.ACTION_SETTINGS))
            }
        }
    }

    private fun postVoicePolicy(payload: JSONObject) {
        controlWorker.execute {
            postJson("$LOOPBACK/api/node/voice/listening", payload)
            main.post { refreshVoiceControls() }
        }
    }

    private fun getJson(url: String): JSONObject? {
        val connection = URL(url).openConnection() as HttpURLConnection
        return try {
            connection.connectTimeout = 1_000
            connection.readTimeout = 2_000
            if (connection.responseCode !in 200..299) return null
            JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
        } catch (_: Throwable) {
            null
        } finally {
            connection.disconnect()
        }
    }

    private fun postJson(url: String, payload: JSONObject): JSONObject? {
        val connection = URL(url).openConnection() as HttpURLConnection
        return try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 1_500
            connection.readTimeout = 4_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            val stream = if (connection.responseCode in 200..299) connection.inputStream else connection.errorStream
            val raw = stream?.bufferedReader().use { it?.readText().orEmpty() }
            if (raw.isBlank()) null else JSONObject(raw)
        } catch (_: Throwable) {
            null
        } finally {
            connection.disconnect()
        }
    }

    private fun label(text: String, size: Float, color: Int, bold: Boolean): TextView =
        TextView(this).apply {
            this.text = text
            textSize = size
            setTextColor(color)
            gravity = Gravity.CENTER_HORIZONTAL
            setPadding(0, dp(10), 0, dp(10))
            if (bold) setTypeface(typeface, android.graphics.Typeface.BOLD)
        }

    private fun button(text: String, listener: (View) -> Unit): Button = Button(this).apply {
        this.text = text
        isAllCaps = false
        textSize = 17f
        setOnClickListener(listener)
        val params = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            dp(56),
        )
        params.topMargin = dp(10)
        layoutParams = params
    }

    private fun requestNotificationPermission() {
        if (
            Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1701)
        }
    }

    private fun requestMicrophonePermission() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQUEST_RECORD_AUDIO)
        }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    companion object {
        private const val REQUEST_RECORD_AUDIO = 1702
        private const val TAG = "AdaOSNodeActivity"
        private const val ACTION_DEBUG_STOP_NODE =
            "dev.adaos.androidnode.action.DEBUG_STOP_NODE"
        private const val ACTION_DEBUG_START_NODE =
            "dev.adaos.androidnode.action.DEBUG_START_NODE"
        private const val EXTRA_START_NODE = "start_node"
        private const val EXTRA_STOP_NODE = "stop_node"
        private const val LOOPBACK = "http://127.0.0.1:8777"
        private const val DEFAULT_VOSK_MODEL = "vosk-model-small-ru-0.22"
        private const val DEFAULT_VOSK_MODEL_SHA256 =
            "961d5ff98a17f4aa6de69864d0aa71fa5bac682301d2b5d17a3f24c5c99a46d4"
        private const val DEFAULT_VOSK_MODEL_ARCHIVE_BYTES = 46_236_750
        private const val VOICE_STATUS_POLL_MS = 2_500L
    }
}
