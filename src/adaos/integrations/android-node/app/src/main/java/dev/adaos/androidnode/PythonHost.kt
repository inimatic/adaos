package dev.adaos.androidnode

import android.content.Context
import android.os.Build
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONObject
import java.io.File
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

class PythonHost(context: Context) {
    private val appContext = context.applicationContext
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "adaos-python-host")
    }
    private val started = AtomicBoolean(false)

    fun start(dataRoot: File, callback: (Result<NodeStatus>) -> Unit) {
        if (!started.compareAndSet(false, true)) {
            callback(Result.success(NodeStateStore.snapshot()))
            return
        }

        executor.execute {
            try {
                if (!Python.isStarted()) {
                    Python.start(AndroidPlatform(appContext))
                }
                val payload = Python.getInstance()
                    .getModule("adaos.android.bootstrap")
                    .callAttr(
                        "start",
                        dataRoot.absolutePath,
                        BuildConfig.VERSION_NAME,
                        8777,
                        deviceLabel(),
                        BuildConfig.DEFAULT_ROOT_URL,
                    )
                    .toString()
                val parsed = JSONObject(payload)
                callback(
                    Result.success(
                        NodeStatus(
                            phase = NodePhase.READY,
                            detail = "Native Yjs and fixed in-process skills are ready",
                            pythonVersion = parsed.optString("python_version"),
                            dataRoot = parsed.optString("data_root"),
                            port = parsed.optInt("port", 8777),
                        )
                    )
                )
            } catch (error: Throwable) {
                started.set(false)
                callback(Result.failure(error))
            }
        }
    }

    fun stop(callback: (Result<Unit>) -> Unit) {
        if (!started.compareAndSet(true, false)) {
            callback(Result.success(Unit))
            return
        }
        executor.execute {
            try {
                if (Python.isStarted()) {
                    Python.getInstance().getModule("adaos.android.bootstrap").callAttr("stop")
                }
                callback(Result.success(Unit))
            } catch (error: Throwable) {
                callback(Result.failure(error))
            }
        }
    }

    private fun deviceLabel(): String {
        val manufacturer = Build.MANUFACTURER.trim().replaceFirstChar { it.uppercase() }
        val model = Build.MODEL.trim()
        return listOf(manufacturer, model)
            .filter { it.isNotBlank() }
            .distinctBy { it.lowercase() }
            .joinToString(" ")
            .ifBlank { "Android phone" }
    }
}
