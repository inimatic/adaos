package dev.adaos.androidnode

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.os.Build
import android.os.IBinder
import android.util.Log

class NodeService : Service() {
    private lateinit var pythonHost: PythonHost
    private lateinit var voiceActivationDetector: VoiceActivationDetector
    private lateinit var nativeVoiceAssistant: NativeVoiceAssistant
    private var stopping = false
    private var stopReason = STOP_REASON_SYSTEM_DESTROY
    private val microphoneOwners = mutableSetOf<String>()
    @Volatile private var microphoneForeground = false

    override fun onCreate() {
        super.onCreate()
        Log.i(TAG, "onCreate")
        pythonHost = PythonHost(this)
        voiceActivationDetector = VoiceActivationDetector(
            this,
            filesDir.resolve("adaos"),
        ) { active -> setNativeCaptureActive("audio_record_vad", active) }
        nativeVoiceAssistant = NativeVoiceAssistant(
            this,
            filesDir.resolve("adaos"),
        ) { active -> setNativeCaptureActive("speech_recognizer", active) }
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.i(TAG, "onStartCommand action=${intent?.action} startId=$startId")
        if (intent?.action == ACTION_STOP) {
            NodeLifecycleStore.setDesiredRunning(this, false)
            stopRuntime(STOP_REASON_USER, "Node stopped by user")
            return START_NOT_STICKY
        }

        startRuntimeForeground("Starting embedded Python")
        val startReason = intent?.getStringExtra(EXTRA_START_REASON)
            ?: if (intent == null) START_REASON_STICKY_RESTART else START_REASON_USER
        val userVisibleCapture = intent?.action == ACTION_ARM_VOICE || startReason == START_REASON_USER
        // Android's while-in-use microphone permission does not allow a boot,
        // package-replaced, or sticky receiver to promote this service to the
        // microphone type. Preserve the specialUse node and defer capture until
        // a visible Activity sends ACTION_ARM_VOICE.
        if (userVisibleCapture) {
            voiceActivationDetector.start()
        } else {
            voiceActivationDetector.deferUntilUserVisible(startReason)
        }
        nativeVoiceAssistant.start(userVisibleCapture)
        val current = NodeStateStore.snapshot()
        if (current.phase == NodePhase.STARTING || current.phase == NodePhase.READY) {
            return START_STICKY
        }

        stopping = false
        NodeLifecycleStore.recordStart(this, startReason)
        publish(NodeStatus(NodePhase.STARTING, "Starting embedded Python"))
        pythonHost.start(filesDir.resolve("adaos")) { result ->
            result.fold(
                onSuccess = { status ->
                    if (!stopping) {
                        publish(status)
                    }
                },
                onFailure = { error ->
                    Log.e(TAG, "AdaOS Python bootstrap failed", error)
                    NodeLifecycleStore.recordStop(
                        this,
                        STOP_REASON_BOOTSTRAP_FAILED,
                        error.message ?: error.javaClass.simpleName,
                    )
                    publish(
                        NodeStatus(
                            NodePhase.FAILED,
                            error.message ?: error.javaClass.simpleName,
                        )
                    )
                    voiceActivationDetector.stop()
                    nativeVoiceAssistant.stop()
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf()
                },
            )
        }
        return START_STICKY
    }

    override fun onDestroy() {
        Log.i(TAG, "onDestroy stopping=$stopping phase=${NodeStateStore.snapshot().phase}")
        val phase = NodeStateStore.snapshot().phase
        if (!stopping && phase != NodePhase.STOPPED && phase != NodePhase.FAILED) {
            pythonHost.stop { }
            val detail = "Android stopped the service; sticky restart remains requested"
            NodeLifecycleStore.recordStop(this, STOP_REASON_SYSTEM_DESTROY, detail)
            publish(NodeStatus.stopped(detail))
        }
        voiceActivationDetector.stop()
        nativeVoiceAssistant.stop()
        super.onDestroy()
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        stopRuntime(
            STOP_REASON_PLATFORM_TIMEOUT,
            "Android foreground-service timeout (type=$fgsType)",
        )
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun stopRuntime(reason: String, requestedDetail: String) {
        if (stopping) return
        Log.i(TAG, "stopRuntime requested reason=$reason")
        stopping = true
        stopReason = reason
        voiceActivationDetector.stop()
        nativeVoiceAssistant.stop()
        publish(NodeStatus(NodePhase.STOPPING, "Flushing and stopping Python"))
        pythonHost.stop { result ->
            val detail = result.exceptionOrNull()?.message?.let { "Stopped with warning: $it" }
                ?: requestedDetail
            NodeLifecycleStore.recordStop(this, stopReason, detail)
            publish(NodeStatus.stopped(detail))
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun publish(status: NodeStatus) {
        NodeStateStore.publish(status)
        if (status.phase != NodePhase.STOPPED) {
            val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            manager.notify(NOTIFICATION_ID, notification(status.detail))
        }
    }

    @Synchronized
    private fun setNativeCaptureActive(owner: String, active: Boolean) {
        if (active) microphoneOwners.add(owner) else microphoneOwners.remove(owner)
        val next = microphoneOwners.isNotEmpty()
        if (microphoneForeground == next) return
        microphoneForeground = next
        startRuntimeForeground(
            if (next) "Native voice activation is listening" else NodeStateStore.snapshot().detail,
        )
    }

    private fun startRuntimeForeground(text: String) {
        val currentNotification = notification(text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            var serviceType = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            if (microphoneForeground) {
                serviceType = serviceType or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            }
            startForeground(NOTIFICATION_ID, currentNotification, serviceType)
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && microphoneForeground) {
            startForeground(
                NOTIFICATION_ID,
                currentNotification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
        } else {
            startForeground(NOTIFICATION_ID, currentNotification)
        }
    }

    private fun notification(text: String): Notification {
        val activityIntent = Intent(this, MainActivity::class.java)
        val activityPendingIntent = PendingIntent.getActivity(
            this,
            0,
            activityIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val stopIntent = Intent(this, NodeService::class.java).setAction(ACTION_STOP)
        val stopPendingIntent = PendingIntent.getService(
            this,
            1,
            stopIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val stopAction = Notification.Action.Builder(
            Icon.createWithResource(this, android.R.drawable.ic_menu_close_clear_cancel),
            "Stop",
            stopPendingIntent,
        ).build()
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("AdaOS Node")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentIntent(activityPendingIntent)
            .setOngoing(true)
            .addAction(stopAction)
            .build()
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        )
        manager.createNotificationChannel(channel)
    }

    companion object {
        const val ACTION_START = "dev.adaos.androidnode.action.START"
        const val ACTION_ARM_VOICE = "dev.adaos.androidnode.action.ARM_VOICE"
        const val ACTION_STOP = "dev.adaos.androidnode.action.STOP"
        const val EXTRA_START_REASON = "start_reason"
        const val START_REASON_USER = "user"
        const val START_REASON_BOOT = "boot_completed"
        const val START_REASON_PACKAGE_REPLACED = "package_replaced"
        const val START_REASON_STICKY_RESTART = "sticky_restart"
        private const val STOP_REASON_USER = "user"
        private const val STOP_REASON_PLATFORM_TIMEOUT = "platform_timeout"
        private const val STOP_REASON_SYSTEM_DESTROY = "system_destroy"
        private const val STOP_REASON_BOOTSTRAP_FAILED = "bootstrap_failed"
        private const val CHANNEL_ID = "adaos_node_runtime"
        private const val NOTIFICATION_ID = 1701
        private const val TAG = "AdaOSNodeService"
    }
}
