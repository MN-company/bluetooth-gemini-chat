package com.example.geminibridge

import android.app.Application
import android.content.Intent
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable

@Serializable
data class IncomingEnvelope(
    val type: String,
    val messageId: String = "",
)

@Serializable
data class PromptRequest(
    val type: String,
    val messageId: String,
    val prompt: String,
    val model: String? = null,
    val enableWebSearch: Boolean = false,
    val thinkingEnabled: Boolean = false,
    val thinkingBudget: Int? = null,
    val includeThoughts: Boolean = false,
    val imageBase64: String? = null,
    val imageMimeType: String? = null,
    val imageName: String? = null,
    val contextBlocks: List<ContextBlockRequest> = emptyList(),
    val conversationMemory: List<MemoryTurnRequest> = emptyList(),
)

@Serializable
data class ContextBlockRequest(
    val source: String,
    val page: Int,
    val text: String,
)

@Serializable
data class MemoryTurnRequest(
    val role: String,
    val text: String,
)

@Serializable
data class PingRequest(
    val type: String,
    val messageId: String,
    val clientTsMs: Long? = null,
)

@Serializable
data class StatusResponse(
    val type: String = "status",
    val messageId: String,
    val state: String,
)

@Serializable
data class ResultResponse(
    val type: String = "result",
    val messageId: String,
    val text: String,
    val thought: String? = null,
)

@Serializable
data class PartialResponse(
    val type: String = "partial",
    val messageId: String,
    val text: String,
    val channel: String = "answer",
)

@Serializable
data class ErrorResponse(
    val type: String = "error",
    val messageId: String,
    val error: String,
)

@Serializable
data class PongResponse(
    val type: String = "pong",
    val messageId: String,
    val clientTsMs: Long? = null,
    val serverTsMs: Long = System.currentTimeMillis(),
)

data class UiState(
    val permissionsGranted: Boolean = false,
    val bridgeStatus: String = "Waiting for permissions",
    val apiKey: String = "",
    val model: String = SettingsRepository.DEFAULT_MODEL,
    val availableModels: List<String> = emptyList(),
    val modelsLoading: Boolean = false,
    val modelsError: String = "",
    val logs: List<String> = emptyList(),
    val serviceRunning: Boolean = false,
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settingsRepository = SettingsRepository(application)
    private val geminiApiClient = GeminiApiClient(settingsRepository)

    private val _uiState = MutableStateFlow(
        UiState(
            apiKey = settingsRepository.getApiKey(),
            model = settingsRepository.getModel(),
            bridgeStatus = BridgeRuntimeState.bridgeStatus.value,
            logs = BridgeRuntimeState.logs.value,
            serviceRunning = BridgeRuntimeState.serviceRunning.value,
        )
    )
    val uiState: StateFlow<UiState> = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            BridgeRuntimeState.bridgeStatus.collect { status ->
                _uiState.update { it.copy(bridgeStatus = status) }
            }
        }
        viewModelScope.launch {
            BridgeRuntimeState.logs.collect { logs ->
                _uiState.update { it.copy(logs = logs) }
            }
        }
        viewModelScope.launch {
            BridgeRuntimeState.serviceRunning.collect { running ->
                _uiState.update { it.copy(serviceRunning = running) }
            }
        }
    }

    fun updatePermissions(granted: Boolean) {
        _uiState.update { it.copy(permissionsGranted = granted) }

        if (granted) {
            startBridgeService()
        } else {
            stopBridgeService()
            BridgeRuntimeState.setBridgeStatus("Bluetooth permissions missing")
            BridgeRuntimeState.appendLog("Bridge stopped: missing Bluetooth permissions")
        }
    }

    fun saveSettings(apiKey: String, model: String) {
        settingsRepository.setApiKey(apiKey)
        settingsRepository.setModel(model)
        _uiState.update {
            it.copy(
                apiKey = settingsRepository.getApiKey(),
                model = settingsRepository.getModel(),
            )
        }
        BridgeRuntimeState.appendLog("Settings saved")
    }

    fun loadAvailableModels(apiKeyOverride: String) {
        val key = apiKeyOverride.trim().ifBlank { settingsRepository.getApiKey() }
        if (key.isBlank()) {
            _uiState.update { it.copy(modelsError = "Set API key first", modelsLoading = false) }
            return
        }

        _uiState.update { it.copy(modelsLoading = true, modelsError = "") }
        viewModelScope.launch {
            runCatching {
                geminiApiClient.listAvailableModels(apiKeyOverride = key)
            }.onSuccess { models ->
                _uiState.update {
                    it.copy(
                        availableModels = models,
                        modelsLoading = false,
                        modelsError = if (models.isEmpty()) "No compatible models found" else "",
                    )
                }
            }.onFailure { err ->
                _uiState.update {
                    it.copy(
                        modelsLoading = false,
                        modelsError = err.message ?: "Failed to load models",
                    )
                }
            }
        }
    }

    fun restartBridgeService() {
        val app = getApplication<Application>()
        val intent = Intent(app, BleKeepAliveService::class.java).apply {
            action = BleKeepAliveService.ACTION_RESTART
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            app.startForegroundService(intent)
        } else {
            app.startService(intent)
        }
        BridgeRuntimeState.appendLog("Foreground service restart requested")
    }

    private fun startBridgeService() {
        val app = getApplication<Application>()
        val intent = Intent(app, BleKeepAliveService::class.java).apply {
            action = BleKeepAliveService.ACTION_START
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            app.startForegroundService(intent)
        } else {
            app.startService(intent)
        }
        BridgeRuntimeState.appendLog("Foreground service start requested")
    }

    private fun stopBridgeService() {
        val app = getApplication<Application>()
        val intent = Intent(app, BleKeepAliveService::class.java).apply {
            action = BleKeepAliveService.ACTION_STOP
        }
        app.startService(intent)
    }
}
