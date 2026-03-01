package com.example.geminibridge

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import java.util.concurrent.ConcurrentHashMap

object BridgeRuntimeState {
    private const val maxLogs = 300

    private val _bridgeStatus = MutableStateFlow("Bridge service idle")
    val bridgeStatus: StateFlow<String> = _bridgeStatus.asStateFlow()

    private val _logs = MutableStateFlow<List<String>>(emptyList())
    val logs: StateFlow<List<String>> = _logs.asStateFlow()

    private val _serviceRunning = MutableStateFlow(false)
    val serviceRunning: StateFlow<Boolean> = _serviceRunning.asStateFlow()

    // ── Knowledge Base Containers ────────────────────────────────────────────
    /** In-memory cache of all loaded containers (id → container). */
    val containers: ConcurrentHashMap<String, StoredContainer> = ConcurrentHashMap()

    /** The ID of the container currently active for retrieval (null = disabled). */
    @Volatile
    var activeContainerId: String? = null

    fun addOrUpdateContainer(container: StoredContainer) {
        containers[container.id] = container
    }

    fun removeContainer(containerId: String) {
        containers.remove(containerId)
        if (activeContainerId == containerId) {
            activeContainerId = null
        }
    }

    fun getActiveContainer(): StoredContainer? {
        val id = activeContainerId ?: return null
        return containers[id]
    }
    // ─────────────────────────────────────────────────────────────────────────

    fun setBridgeStatus(value: String) {
        _bridgeStatus.value = value
    }

    fun setServiceRunning(value: Boolean) {
        _serviceRunning.value = value
    }

    fun appendLog(message: String) {
        val line = "${System.currentTimeMillis()} | $message"
        _logs.update { existing ->
            (existing + line).takeLast(maxLogs)
        }
    }
}
