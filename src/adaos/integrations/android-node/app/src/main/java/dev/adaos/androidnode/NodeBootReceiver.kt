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
        context.startForegroundService(
            Intent(context, NodeService::class.java)
                .setAction(NodeService.ACTION_START)
                .putExtra(NodeService.EXTRA_START_REASON, reason)
        )
    }

    companion object {
        private const val TAG = "AdaOSNodeBoot"
    }
}
