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
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

const val APP_VERSION_NAME = "0.1.12"

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
    val activeContainerId: String? = null,
    val activeContainerName: String? = null,
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
data class CancelRequest(
    val type: String,
    val messageId: String,
    val targetMessageId: String = "",
)

@Serializable
data class ListContainersResponse(
    val type: String = "container_list",
    val messageId: String,
    val containers: List<ContainerSummary> = emptyList(),
)

@Serializable
data class ContainerSummary(
    val id: String,
    val name: String,
    val chunkCount: Int,
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

@Serializable
data class ContainerAckResponse(
    val type: String = "container_ack",
    val messageId: String,
    val containerId: String,
    val chunkCount: Int,
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
    val updateChecking: Boolean = false,
    val updateAvailable: Boolean = false,
    val latestVersion: String = "",
    val updateUrl: String = "",
    val updateAssetUrl: String = "",
    val updateError: String = "",
)

class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val settingsRepository = SettingsRepository(application)
    private val geminiApiClient = GeminiApiClient(settingsRepository)
    private val updateHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .writeTimeout(10, TimeUnit.SECONDS)
        .build()
    private val updateJson = Json { ignoreUnknownKeys = true }

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
        checkForAppUpdates(silent = true)
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

    fun checkForAppUpdates(silent: Boolean = false) {
        _uiState.update {
            it.copy(
                updateChecking = true,
                updateError = if (silent) it.updateError else "",
            )
        }
        viewModelScope.launch {
            runCatching {
                fetchLatestRelease()
            }.onSuccess { release ->
                val hasUpdate = isVersionNewer(release.tag, APP_VERSION_NAME)
                _uiState.update {
                    it.copy(
                        updateChecking = false,
                        updateAvailable = hasUpdate,
                        latestVersion = release.tag,
                        updateUrl = release.url,
                        updateAssetUrl = release.apkUrl,
                        updateError = "",
                    )
                }
                if (hasUpdate) {
                    BridgeRuntimeState.appendLog("Update disponibile: ${release.tag}")
                } else if (!silent) {
                    BridgeRuntimeState.appendLog("App aggiornata ($APP_VERSION_NAME)")
                }
            }.onFailure { err ->
                _uiState.update {
                    it.copy(
                        updateChecking = false,
                        updateError = err.message ?: "Update check failed",
                    )
                }
                if (!silent) {
                    BridgeRuntimeState.appendLog("Update check failed: ${err.message}")
                }
            }
        }
    }

    private data class LatestRelease(
        val tag: String,
        val url: String,
        val apkUrl: String,
    )

    private fun fetchLatestRelease(): LatestRelease {
        val request = Request.Builder()
            .url("https://api.github.com/repos/MN-company/bluetooth-gemini-chat/releases/latest")
            .get()
            .build()

        updateHttpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw IllegalStateException("GitHub release API error: ${response.code}")
            }
            val raw = response.body?.string().orEmpty()
            if (raw.isBlank()) {
                throw IllegalStateException("Empty release response")
            }
            val root = updateJson.parseToJsonElement(raw).jsonObject
            val tag = root["tag_name"]?.jsonPrimitive?.content.orEmpty()
            val url = root["html_url"]?.jsonPrimitive?.content.orEmpty()
            var apkUrl = ""
            root["assets"]?.jsonArray?.forEach { asset ->
                val obj = asset.jsonObject
                val name = obj["name"]?.jsonPrimitive?.content.orEmpty()
                val download = obj["browser_download_url"]?.jsonPrimitive?.content.orEmpty()
                if (name == "app-debug.apk" || (apkUrl.isBlank() && name.endsWith(".apk"))) {
                    apkUrl = download
                }
            }
            return LatestRelease(
                tag = tag.ifBlank { "unknown" },
                url = url,
                apkUrl = apkUrl,
            )
        }
    }

    private fun isVersionNewer(candidate: String, current: String): Boolean {
        val cand = versionTuple(candidate)
        val curr = versionTuple(current)
        val maxLen = maxOf(cand.size, curr.size)
        for (idx in 0 until maxLen) {
            val a = cand.getOrElse(idx) { 0 }
            val b = curr.getOrElse(idx) { 0 }
            if (a != b) return a > b
        }
        return false
    }

    private fun versionTuple(value: String): List<Int> {
        val normalized = value.trim().lowercase().removePrefix("v")
        if (normalized.isBlank()) return listOf(0)
        return Regex("\\d+")
            .findAll(normalized)
            .map { it.value.toIntOrNull() ?: 0 }
            .toList()
            .ifEmpty { listOf(0) }
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
