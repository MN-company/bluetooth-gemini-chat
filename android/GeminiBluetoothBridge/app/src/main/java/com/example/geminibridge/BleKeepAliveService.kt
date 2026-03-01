package com.example.geminibridge

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import java.net.SocketTimeoutException
import java.util.concurrent.TimeoutException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

class BleKeepAliveService : Service() {
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    private lateinit var settingsRepository: SettingsRepository
    private lateinit var geminiApiClient: GeminiApiClient

    private var bleServerManager: BleServerManager? = null
    private var watchdogJob: Job? = null
    private var restartJob: Job? = null
    private var wakeLock: PowerManager.WakeLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        settingsRepository = SettingsRepository(applicationContext)
        geminiApiClient = GeminiApiClient(settingsRepository)

        createChannelIfNeeded()
        startForeground(NOTIFICATION_ID, buildNotification("Starting BLE bridge..."))
        acquireWakeLockIfPossible()

        BridgeRuntimeState.setServiceRunning(true)
        appendLog("Foreground BLE service created")
        updateBridgeStatus("Starting BLE bridge...")

        startBridgeIfNeeded()
        startWatchdog()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                appendLog("Stop requested")
                stopSelf()
                return START_NOT_STICKY
            }

            ACTION_RESTART -> {
                appendLog("Manual bridge restart requested")
                serviceScope.launch { restartBridge("manual restart") }
            }

            else -> {
                startBridgeIfNeeded()
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        watchdogJob?.cancel()
        watchdogJob = null
        restartJob?.cancel()
        restartJob = null

        stopBridge()
        releaseWakeLock()

        BridgeRuntimeState.setServiceRunning(false)
        BridgeRuntimeState.setBridgeStatus("Bridge service stopped")
        appendLog("Foreground BLE service destroyed")

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }

        serviceScope.cancel()
        super.onDestroy()
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        appendLog("App task removed, service stays active")
        super.onTaskRemoved(rootIntent)
    }

    private fun startBridgeIfNeeded(forceRestart: Boolean = false) {
        if (forceRestart) {
            stopBridge()
        } else if (bleServerManager != null) {
            return
        }

        val manager = BleServerManager(
            context = applicationContext,
            scope = serviceScope,
            onPromptJson = { handleIncomingJson(it) },
            onLog = { appendLog(it) },
            onBridgeStatus = { status -> updateBridgeStatus(status) },
        )

        val result = manager.start()
        if (result.isSuccess) {
            bleServerManager = manager
            appendLog("BLE bridge setup completed")
        } else {
            val reason = result.exceptionOrNull()?.message ?: "Unknown BLE start error"
            updateBridgeStatus("BLE start failed: $reason")
            appendLog("BLE start failed: $reason")
            scheduleRestart(delayMs = 4_000L, reason = "start failure")
        }
    }

    private fun stopBridge() {
        bleServerManager?.stop()
        bleServerManager = null
    }

    private fun startWatchdog() {
        if (watchdogJob != null) return

        watchdogJob = serviceScope.launch {
            while (isActive) {
                delay(20_000L)
                val manager = bleServerManager
                if (manager == null) {
                    appendLog("Watchdog: BLE manager missing, restarting")
                    startBridgeIfNeeded(forceRestart = true)
                    continue
                }

                if (!manager.isOperational()) {
                    appendLog("Watchdog: bridge not operational, checking advertising")
                    val ensured = manager.ensureAdvertising()
                    if (ensured.isFailure) {
                        appendLog(
                            "Watchdog: ensure advertising failed: " +
                                (ensured.exceptionOrNull()?.message ?: "unknown")
                        )
                        restartBridge("watchdog recovery")
                    }
                }
            }
        }
    }

    private suspend fun restartBridge(reason: String) {
        appendLog("Restarting BLE bridge ($reason)")
        stopBridge()
        delay(750L)
        startBridgeIfNeeded(forceRestart = false)
    }

    private fun scheduleRestart(delayMs: Long, reason: String) {
        if (restartJob != null) return
        restartJob = serviceScope.launch {
            delay(delayMs)
            restartJob = null
            restartBridge(reason)
        }
    }

    private suspend fun handleIncomingJson(rawJson: String) {
        val envelope = try {
            json.decodeFromString<IncomingEnvelope>(rawJson)
        } catch (t: Throwable) {
            appendLog("Invalid request payload: ${t.message}")
            return
        }

        when (envelope.type) {
            "prompt" -> handlePromptRequest(rawJson)
            "ping" -> handlePing(rawJson)
            else -> {
                val messageId = envelope.messageId.ifBlank { "unknown" }
                sendError(messageId, "Unsupported request type: ${envelope.type}")
            }
        }
    }

    private suspend fun handlePing(rawJson: String) {
        val ping = try {
            json.decodeFromString<PingRequest>(rawJson)
        } catch (_: Throwable) {
            return
        }
        sendPong(messageId = ping.messageId, clientTsMs = ping.clientTsMs)
    }

    private suspend fun handlePromptRequest(rawJson: String) {
        val request = try {
            json.decodeFromString<PromptRequest>(rawJson)
        } catch (t: Throwable) {
            appendLog("Invalid prompt payload: ${t.message}")
            return
        }

        if (request.type != "prompt") {
            sendError(request.messageId, "Unsupported request type: ${request.type}")
            return
        }

        if (request.prompt.isBlank()) {
            sendError(request.messageId, "Prompt is empty")
            return
        }

        val hasImageData = !request.imageBase64.isNullOrBlank()
        val hasImageMime = !request.imageMimeType.isNullOrBlank()
        if (hasImageData != hasImageMime) {
            sendError(request.messageId, "Invalid image payload: missing mime type or image data")
            return
        }

        if (hasImageData && request.imageBase64!!.length > 1_200_000) {
            sendError(request.messageId, "Image payload too large for BLE bridge")
            return
        }

        if (request.contextBlocks.size > 12) {
            sendError(request.messageId, "Too many context blocks")
            return
        }

        val sanitizedContextBlocks = request.contextBlocks.mapNotNull { block ->
            if (block.text.isBlank()) return@mapNotNull null
            ContextBlockRequest(
                source = block.source.take(120),
                page = block.page,
                text = block.text.take(1_600),
            )
        }

        if (request.conversationMemory.size > 24) {
            sendError(request.messageId, "Too many memory turns")
            return
        }

        val sanitizedMemoryTurns = request.conversationMemory.mapNotNull { turn ->
            val role = turn.role.trim().lowercase()
            if (role != "user" && role != "assistant") return@mapNotNull null
            val text = turn.text.trim()
            if (text.isBlank()) return@mapNotNull null
            MemoryTurnRequest(role = role, text = text.take(1_200))
        }

        val imageInfo = if (hasImageData) {
            " with image (${request.imageName ?: "image"})"
        } else {
            ""
        }
        val contextInfo = if (sanitizedContextBlocks.isNotEmpty()) {
            " + ${sanitizedContextBlocks.size} context block(s)"
        } else {
            ""
        }
        val memoryInfo = if (sanitizedMemoryTurns.isNotEmpty()) {
            " + ${sanitizedMemoryTurns.size} memory turn(s)"
        } else {
            ""
        }
        val webInfo = if (request.enableWebSearch) {
            " + web search"
        } else {
            ""
        }
        val modelOverride = request.model?.trim()?.takeIf { it.isNotEmpty() }
        val modelInfo = if (modelOverride != null) {
            " + model=$modelOverride"
        } else {
            ""
        }
        val thinkingEnabled = request.thinkingEnabled
        val includeThoughts = request.includeThoughts && thinkingEnabled
        val sanitizedThinkingBudget = request.thinkingBudget?.let { budget ->
            when {
                budget < -1 -> -1
                budget > 24_576 -> 24_576
                else -> budget
            }
        }
        val thinkingInfo = if (thinkingEnabled) {
            if (sanitizedThinkingBudget == null || sanitizedThinkingBudget < 0) {
                " + thinking(auto${if (includeThoughts) ", trace" else ""})"
            } else {
                " + thinking($sanitizedThinkingBudget${if (includeThoughts) ", trace" else ""})"
            }
        } else {
            ""
        }

        appendLog("Prompt received (${request.messageId})$imageInfo$contextInfo$memoryInfo$webInfo$modelInfo$thinkingInfo")
        val startedMs = System.currentTimeMillis()
        sendStatus(request.messageId, "processing (0s)")
        val progressJob = serviceScope.launch {
            var elapsedSec = 5
            while (isActive) {
                delay(5_000L)
                runCatching {
                    sendStatus(request.messageId, "processing (${elapsedSec}s)")
                }
                elapsedSec += 5
            }
        }

        try {
            var lastAnswerPartialSentMs = 0L
            var lastThoughtPartialSentMs = 0L
            var lastPartialLength = 0
            var lastThoughtLength = 0
            val response = geminiApiClient.generate(
                prompt = request.prompt,
                modelOverride = modelOverride,
                enableWebSearch = request.enableWebSearch,
                thinkingEnabled = thinkingEnabled,
                thinkingBudget = sanitizedThinkingBudget,
                includeThoughts = includeThoughts,
                imageBase64 = request.imageBase64?.takeIf { it.isNotBlank() },
                imageMimeType = request.imageMimeType?.takeIf { it.isNotBlank() },
                contextBlocks = sanitizedContextBlocks,
                conversationMemory = sanitizedMemoryTurns,
                onPartialText = { partial ->
                    val now = System.currentTimeMillis()
                    val longEnough = partial.length >= (lastPartialLength + 12)
                    val timed = (now - lastAnswerPartialSentMs) >= 350L
                    if (longEnough && timed) {
                        lastPartialLength = partial.length
                        lastAnswerPartialSentMs = now
                        serviceScope.launch {
                            runCatching {
                                sendPartial(request.messageId, partial, channel = "answer")
                            }
                        }
                    }
                },
                onPartialThought = { partialThought ->
                    if (includeThoughts) {
                        val now = System.currentTimeMillis()
                        val longEnough = partialThought.length >= (lastThoughtLength + 12)
                        val timed = (now - lastThoughtPartialSentMs) >= 450L
                        if (longEnough && timed) {
                            lastThoughtLength = partialThought.length
                            lastThoughtPartialSentMs = now
                            serviceScope.launch {
                                runCatching {
                                    sendPartial(request.messageId, partialThought, channel = "thought")
                                }
                            }
                        }
                    }
                },
            )
            progressJob.cancel()
            sendResult(
                ResultResponse(
                    messageId = request.messageId,
                    text = response.text,
                    thought = response.thought.takeIf { includeThoughts && it.isNotBlank() },
                )
            )
            val elapsed = ((System.currentTimeMillis() - startedMs) / 1000L).coerceAtLeast(0)
            appendLog("Response sent (${request.messageId}) in ${elapsed}s")
        } catch (t: Throwable) {
            progressJob.cancel()
            val elapsed = ((System.currentTimeMillis() - startedMs) / 1000L).coerceAtLeast(0)
            val timeoutLike = t is SocketTimeoutException ||
                t is TimeoutException ||
                (t.message?.contains("timeout", ignoreCase = true) == true)
            val errorText = if (timeoutLike) {
                "Timeout after ${elapsed}s. Prova prompt piu corto, meno PDF context, oppure disattiva Web Search."
            } else {
                t.message ?: "Unknown Gemini error"
            }
            runCatching {
                sendError(request.messageId, errorText)
            }.onFailure { sendFailure ->
                appendLog("Failed to send error to PC (${request.messageId}): ${sendFailure.message}")
            }
            appendLog("Gemini error (${request.messageId}) after ${elapsed}s: ${t.message}")
        }
    }

    private suspend fun sendStatus(messageId: String, state: String) {
        val payload = json.encodeToString(StatusResponse(messageId = messageId, state = state))
        sendToPc(payload, "status", messageId)
    }

    private suspend fun sendResult(response: ResultResponse) {
        val payload = json.encodeToString(response)
        sendToPc(payload, "result", response.messageId)
    }

    private suspend fun sendPartial(messageId: String, text: String, channel: String) {
        val safeChannel = if (channel == "thought") "thought" else "answer"
        val payload = json.encodeToString(
            PartialResponse(
                messageId = messageId,
                text = text,
                channel = safeChannel,
            )
        )
        sendToPc(payload, "partial", messageId)
    }

    private suspend fun sendError(messageId: String, error: String) {
        val payload = json.encodeToString(ErrorResponse(messageId = messageId, error = error))
        sendToPc(payload, "error", messageId)
    }

    private suspend fun sendPong(messageId: String, clientTsMs: Long?) {
        val payload = json.encodeToString(PongResponse(messageId = messageId, clientTsMs = clientTsMs))
        sendToPc(payload, "pong", messageId)
    }

    private suspend fun sendToPc(payload: String, type: String, messageId: String) {
        val manager = bleServerManager
        if (manager == null) {
            appendLog("BLE send skipped ($type $messageId): bridge not ready")
            return
        }

        try {
            manager.sendJson(payload)
        } catch (t: Throwable) {
            appendLog("BLE send failed ($type $messageId): ${t.message}")
            throw t
        }
    }

    private fun updateBridgeStatus(status: String) {
        BridgeRuntimeState.setBridgeStatus(status)
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.notify(NOTIFICATION_ID, buildNotification(status))
    }

    private fun appendLog(message: String) {
        BridgeRuntimeState.appendLog(message)
    }

    private fun acquireWakeLockIfPossible() {
        val manager = getSystemService(Context.POWER_SERVICE) as PowerManager
        val lock = manager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "$packageName:GeminiBleBridgeWakeLock",
        )
        lock.setReferenceCounted(false)
        runCatching {
            lock.acquire()
            wakeLock = lock
            appendLog("Partial wake lock acquired")
        }.onFailure {
            appendLog("Wake lock not acquired: ${it.message}")
        }
    }

    private fun releaseWakeLock() {
        val lock = wakeLock ?: return
        runCatching {
            if (lock.isHeld) {
                lock.release()
            }
        }
        wakeLock = null
    }

    private fun createChannelIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return

        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Gemini BLE Bridge",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Keeps BLE bridge alive while the screen is off"
            setShowBadge(false)
        }
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(content: String): Notification {
        val openAppIntent = Intent(this, MainActivity::class.java)
        val pendingIntentFlags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            openAppIntent,
            pendingIntentFlags,
        )

        val sanitized = content.replace('\n', ' ').take(100)
        val builder = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setContentTitle("Gemini BLE Bridge")
            .setContentText(sanitized)
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setContentIntent(pendingIntent)

        return builder.build()
    }

    companion object {
        private const val CHANNEL_ID = "gemini_ble_bridge_keep_alive"
        const val NOTIFICATION_ID = 10042

        const val ACTION_START = "com.example.geminibridge.action.START"
        const val ACTION_STOP = "com.example.geminibridge.action.STOP"
        const val ACTION_RESTART = "com.example.geminibridge.action.RESTART"
    }
}
