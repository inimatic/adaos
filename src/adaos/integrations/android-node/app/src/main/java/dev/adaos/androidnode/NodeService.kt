package dev.adaos.androidnode

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.drawable.Icon
import android.os.IBinder

class NodeService : Service() {
    private lateinit var pythonHost: PythonHost
    private var stopping = false

    override fun onCreate() {
        super.onCreate()
        pythonHost = PythonHost(this)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopRuntime()
            return START_NOT_STICKY
        }

        startForeground(NOTIFICATION_ID, notification("Starting embedded Python"))
        val current = NodeStateStore.snapshot()
        if (current.phase == NodePhase.STARTING || current.phase == NodePhase.READY) {
            return START_NOT_STICKY
        }

        stopping = false
        publish(NodeStatus(NodePhase.STARTING, "Starting embedded Python"))
        pythonHost.start(filesDir.resolve("adaos")) { result ->
            result.fold(
                onSuccess = { status ->
                    if (!stopping) {
                        publish(status)
                    }
                },
                onFailure = { error ->
                    publish(
                        NodeStatus(
                            NodePhase.FAILED,
                            error.message ?: error.javaClass.simpleName,
                        )
                    )
                    stopForeground(STOP_FOREGROUND_REMOVE)
                    stopSelf()
                },
            )
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        if (!stopping && NodeStateStore.snapshot().phase != NodePhase.STOPPED) {
            pythonHost.stop { }
            publish(NodeStatus.stopped("Android stopped the service"))
        }
        super.onDestroy()
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        stopRuntime()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun stopRuntime() {
        if (stopping) return
        stopping = true
        publish(NodeStatus(NodePhase.STOPPING, "Flushing and stopping Python"))
        pythonHost.stop { result ->
            val detail = result.exceptionOrNull()?.message?.let { "Stopped with warning: $it" }
                ?: "Node is stopped"
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
        const val ACTION_STOP = "dev.adaos.androidnode.action.STOP"
        private const val CHANNEL_ID = "adaos_node_runtime"
        private const val NOTIFICATION_ID = 1701
    }
}
