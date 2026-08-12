package dev.adaos.androidnode

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

class MainActivity : Activity() {
    private lateinit var phaseView: TextView
    private lateinit var detailView: TextView
    private lateinit var factsView: TextView
    private lateinit var openButton: Button
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
    }

    override fun onStop() {
        Log.i(TAG, "onStop instance=${System.identityHashCode(this)}")
        NodeStateStore.unsubscribe(statusListener)
        super.onStop()
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy instance=${System.identityHashCode(this)}")
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
        content.addView(button("Start node") { startNode() })
        content.addView(button("Stop node") { stopNode() })
        openButton = button("Open AdaOS") { openAdaos() }.apply { isEnabled = false }
        content.addView(openButton)
        content.addView(
            label(
                "Native Yjs and local Notebook state are persistent. Weather, AdaOS " +
                    "Connect, Notebook, and Taiga UI run from the fixed in-process bundle.",
                14f,
                Color.rgb(156, 163, 175),
                false,
            )
        )
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
    }
}
