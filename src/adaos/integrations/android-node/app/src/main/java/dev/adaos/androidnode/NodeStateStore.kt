package dev.adaos.androidnode

import android.os.Handler
import android.os.Looper
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.atomic.AtomicReference

object NodeStateStore {
    private val current = AtomicReference(NodeStatus.stopped())
    private val listeners = CopyOnWriteArraySet<(NodeStatus) -> Unit>()
    private val mainHandler = Handler(Looper.getMainLooper())

    fun snapshot(): NodeStatus = current.get()

    fun publish(status: NodeStatus) {
        current.set(status)
        mainHandler.post {
            listeners.forEach { listener -> listener(status) }
        }
    }

    fun subscribe(listener: (NodeStatus) -> Unit) {
        listeners.add(listener)
        listener(current.get())
    }

    fun unsubscribe(listener: (NodeStatus) -> Unit) {
        listeners.remove(listener)
    }
}
