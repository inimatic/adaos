package dev.adaos.androidnode

enum class NodePhase {
    STOPPED,
    STARTING,
    READY,
    STOPPING,
    FAILED,
}

data class NodeStatus(
    val phase: NodePhase,
    val detail: String,
    val pythonVersion: String? = null,
    val dataRoot: String? = null,
    val port: Int? = null,
) {
    companion object {
        fun stopped(detail: String = "Node is stopped") = NodeStatus(NodePhase.STOPPED, detail)
    }
}
