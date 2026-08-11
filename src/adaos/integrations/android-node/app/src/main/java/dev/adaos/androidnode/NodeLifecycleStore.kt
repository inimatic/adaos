package dev.adaos.androidnode

import android.content.Context

/** Durable user intent and compact lifecycle evidence for the native node host. */
object NodeLifecycleStore {
    private const val PREFERENCES = "adaos_node_lifecycle"
    private const val KEY_DESIRED_RUNNING = "desired_running"
    private const val KEY_LAST_START_REASON = "last_start_reason"
    private const val KEY_LAST_START_AT = "last_start_at_ms"
    private const val KEY_LAST_STOP_REASON = "last_stop_reason"
    private const val KEY_LAST_STOP_DETAIL = "last_stop_detail"
    private const val KEY_LAST_STOP_AT = "last_stop_at_ms"

    fun desiredRunning(context: Context): Boolean =
        preferences(context).getBoolean(KEY_DESIRED_RUNNING, false)

    fun setDesiredRunning(context: Context, desired: Boolean) {
        // User intent must reach disk before a process death or package replacement.
        preferences(context).edit().putBoolean(KEY_DESIRED_RUNNING, desired).commit()
    }

    fun recordStart(context: Context, reason: String) {
        preferences(context).edit()
            .putString(KEY_LAST_START_REASON, reason.take(64))
            .putLong(KEY_LAST_START_AT, System.currentTimeMillis())
            .apply()
    }

    fun recordStop(context: Context, reason: String, detail: String) {
        preferences(context).edit()
            .putString(KEY_LAST_STOP_REASON, reason.take(64))
            .putString(KEY_LAST_STOP_DETAIL, detail.take(256))
            .putLong(KEY_LAST_STOP_AT, System.currentTimeMillis())
            .apply()
    }

    fun lastStopDetail(context: Context): String? =
        preferences(context).getString(KEY_LAST_STOP_DETAIL, null)?.takeIf { it.isNotBlank() }

    fun summary(context: Context): String {
        val preferences = preferences(context)
        val desired = if (desiredRunning(context)) "enabled" else "disabled"
        val startReason = preferences.getString(KEY_LAST_START_REASON, null)
        val stopReason = preferences.getString(KEY_LAST_STOP_REASON, null)
        val stopDetail = lastStopDetail(context)
        return buildList {
            add("Autostart $desired")
            startReason?.takeIf { it.isNotBlank() }?.let { add("Last start $it") }
            stopReason?.takeIf { it.isNotBlank() }?.let {
                add("Last stop $it${stopDetail?.let { detail -> ": $detail" } ?: ""}")
            }
        }.joinToString("\n")
    }

    private fun preferences(context: Context) =
        context.applicationContext.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
}
