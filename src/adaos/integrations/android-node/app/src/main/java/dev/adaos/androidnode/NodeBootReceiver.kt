package dev.adaos.androidnode

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

/** Restores an explicitly running node after normal boot or APK replacement. */
class NodeBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val reason = when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED -> NodeService.START_REASON_BOOT
            Intent.ACTION_MY_PACKAGE_REPLACED -> NodeService.START_REASON_PACKAGE_REPLACED
            else -> return
        }
        if (!NodeLifecycleStore.desiredRunning(context)) {
            Log.i(TAG, "Ignoring ${intent.action}; autostart is disabled")
            return
        }
        Log.i(TAG, "Restoring AdaOS node after $reason")
        try {
            context.startForegroundService(
                Intent(context, NodeService::class.java)
                    .setAction(NodeService.ACTION_START)
                    .putExtra(NodeService.EXTRA_START_REASON, reason)
            )
        } catch (error: RuntimeException) {
            // Android 15+ may reject BOOT_COMPLETED FGS promotion even for a
            // persisted user request. Preserve that request and resume when
            // the user next opens the Activity instead of crashing the app.
            val detail = "Autostart deferred by Android: ${error.javaClass.simpleName}"
            NodeLifecycleStore.recordStop(context, "autostart_deferred", detail)
            Log.w(TAG, detail, error)
        }
    }

    companion object {
        private const val TAG = "AdaOSNodeBoot"
    }
}
