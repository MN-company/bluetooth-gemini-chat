package com.example.geminibridge

import java.io.IOException
import java.net.SocketTimeoutException
import java.time.LocalDate
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody

data class GeminiGenerationResult(
    val text: String,
    val thought: String = "",
)

class GeminiApiClient(
    private val settingsRepository: SettingsRepository,
) {
    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(240, TimeUnit.SECONDS)
        .writeTimeout(140, TimeUnit.SECONDS)
        .callTimeout(300, TimeUnit.SECONDS)
        .build()

    private val jsonParser = Json { ignoreUnknownKeys = true }
    private val urlRegex = Regex("""https?://[^\s<>"')\]]+""")

    suspend fun generate(
        prompt: String,
        modelOverride: String? = null,
        enableWebSearch: Boolean = false,
        thinkingEnabled: Boolean = false,
        thinkingBudget: Int? = null,
        includeThoughts: Boolean = false,
        imageBase64: String? = null,
        imageMimeType: String? = null,
        contextBlocks: List<ContextBlockRequest> = emptyList(),
        conversationMemory: List<MemoryTurnRequest> = emptyList(),
        activeContainerId: String? = null,
        activeContainerName: String? = null,
        onPartialText: ((String) -> Unit)? = null,
        onPartialThought: ((String) -> Unit)? = null,
    ): GeminiGenerationResult = withContext(Dispatchers.IO) {
        val apiKey = settingsRepository.getApiKey()
        if (apiKey.isBlank()) {
            throw IllegalStateException("Gemini API key is empty. Set it in the app first.")
        }

        if ((imageBase64.isNullOrBlank() && !imageMimeType.isNullOrBlank()) ||
            (!imageBase64.isNullOrBlank() && imageMimeType.isNullOrBlank())
        ) {
            throw IllegalStateException("Image payload is incomplete")
        }

        val selectedModel = modelOverride
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: settingsRepository.getModel().ifBlank { SettingsRepository.DEFAULT_MODEL }

        val cleanedImageBase64 = imageBase64?.trim()?.takeIf { it.isNotEmpty() }
        val cleanedImageMimeType = imageMimeType?.trim()?.takeIf { it.isNotEmpty() }

        // On-device BM25 retrieval from active container
        val resolvedContainer = resolveActiveContainer(
            activeContainerId = activeContainerId,
            activeContainerName = activeContainerName,
        )
        val effectiveContextBlocks: List<ContextBlockRequest> = if (resolvedContainer != null && resolvedContainer.chunks.isNotEmpty()) {
            val retrieved = retrieveTopChunks(query = prompt, container = resolvedContainer, topK = 20)
            contextBlocks + retrieved
        } else {
            contextBlocks
        }

        val contextualPrompt = buildContextualPrompt(
            userPrompt = prompt,
            contextBlocks = effectiveContextBlocks,
            conversationMemory = conversationMemory,
            webSearchEnabled = enableWebSearch,
        )

        fun endpointFor(model: String): String {
            return "https://generativelanguage.googleapis.com/v1beta/models/$model:generateContent"
        }

        fun streamEndpointFor(model: String): String {
            return "https://generativelanguage.googleapis.com/v1beta/models/$model:streamGenerateContent?alt=sse"
        }

        fun buildPayload(useWebSearch: Boolean, strictGrounding: Boolean): String {
            val promptText = if (useWebSearch && strictGrounding) {
                "$contextualPrompt\n\n[MANDATORY] Use google_search grounding and include at least 2 source URLs in your response."
            } else {
                contextualPrompt
            }

            return buildJsonObject {
                put("contents", buildJsonArray {
                    add(buildJsonObject {
                        put("parts", buildJsonArray {
                            add(buildJsonObject {
                                put("text", JsonPrimitive(promptText))
                            })

                            if (cleanedImageBase64 != null && cleanedImageMimeType != null) {
                                add(buildJsonObject {
                                    put("inlineData", buildJsonObject {
                                        put("mimeType", JsonPrimitive(cleanedImageMimeType))
                                        put("data", JsonPrimitive(cleanedImageBase64))
                                    })
                                })
                            }
                        })
                    })
                })

                if (useWebSearch) {
                    put("tools", buildJsonArray {
                        add(buildJsonObject {
                            put("google_search", buildJsonObject {})
                        })
                    })
                }

                if (thinkingEnabled) {
                    val budget = sanitizeThinkingBudget(thinkingBudget)
                    put("generationConfig", buildJsonObject {
                        put("thinkingConfig", buildJsonObject {
                            put("thinkingBudget", JsonPrimitive(budget))
                            put("includeThoughts", JsonPrimitive(includeThoughts))
                        })
                    })
                }
            }.toString()
        }

        fun execute(model: String, payload: String): Pair<Int, String> {
            val request = Request.Builder()
                .url(endpointFor(model))
                .addHeader("x-goog-api-key", apiKey)
                .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
                .build()

            try {
                httpClient.newCall(request).execute().use { response ->
                    return Pair(response.code, response.body?.string().orEmpty())
                }
            } catch (t: SocketTimeoutException) {
                throw IllegalStateException(
                    "Gemini request timeout. Try shorter prompt/context or disable Web Search.",
                )
            } catch (t: IOException) {
                throw IllegalStateException(
                    "Gemini network error: ${t.message ?: "unknown I/O error"}",
                )
            }
        }

        fun executeStream(model: String, payload: String): StreamResponse {
            val request = Request.Builder()
                .url(streamEndpointFor(model))
                .addHeader("x-goog-api-key", apiKey)
                .post(payload.toRequestBody("application/json; charset=utf-8".toMediaType()))
                .build()

            try {
                httpClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        return StreamResponse(
                            statusCode = response.code,
                            text = "",
                            thought = "",
                            bestCandidate = null,
                            rawBody = response.body?.string().orEmpty(),
                        )
                    }

                    val reader = response.body?.charStream()?.buffered()
                        ?: return StreamResponse(
                            statusCode = response.code,
                            text = "",
                            thought = "",
                            bestCandidate = null,
                            rawBody = "",
                        )

                    val answerBuilder = StringBuilder()
                    val thoughtBuilder = StringBuilder()
                    var lastAnswerSnapshot = ""
                    var lastThoughtSnapshot = ""
                    var bestCandidate: JsonObject? = null
                    var pendingData = ""

                    reader.useLines { lines ->
                        lines.forEach { rawLine ->
                            val line = rawLine.trim()
                            if (line.isEmpty()) {
                                if (pendingData.isNotBlank()) {
                                    val parsedCandidates = parseSseCandidates(pendingData)
                                    parsedCandidates.forEach { candidate ->
                                        val parts = extractCandidateParts(candidate)

                                        if (candidate["groundingMetadata"] != null) {
                                            bestCandidate = candidate
                                        } else if (bestCandidate == null) {
                                            bestCandidate = candidate
                                        }

                                        if (parts.answer.isNotBlank()) {
                                            val delta = computeDelta(lastAnswerSnapshot, parts.answer)
                                            if (delta.isNotBlank()) {
                                                answerBuilder.append(delta)
                                                onPartialText?.invoke(answerBuilder.toString())
                                            }
                                            if (parts.answer.length >= lastAnswerSnapshot.length) {
                                                lastAnswerSnapshot = parts.answer
                                            }
                                        }

                                        if (includeThoughts && parts.thought.isNotBlank()) {
                                            val delta = computeDelta(lastThoughtSnapshot, parts.thought)
                                            if (delta.isNotBlank()) {
                                                thoughtBuilder.append(delta)
                                                onPartialThought?.invoke(thoughtBuilder.toString())
                                            }
                                            if (parts.thought.length >= lastThoughtSnapshot.length) {
                                                lastThoughtSnapshot = parts.thought
                                            }
                                        }
                                    }
                                    pendingData = ""
                                }
                                return@forEach
                            }

                            if (!line.startsWith("data:")) {
                                return@forEach
                            }

                            val payloadChunk = line.removePrefix("data:").trim()
                            if (payloadChunk == "[DONE]") {
                                return@forEach
                            }

                            pendingData = if (pendingData.isEmpty()) {
                                payloadChunk
                            } else {
                                pendingData + payloadChunk
                            }
                        }
                    }

                    if (pendingData.isNotBlank()) {
                        val parsedCandidates = parseSseCandidates(pendingData)
                        parsedCandidates.forEach { candidate ->
                            val parts = extractCandidateParts(candidate)
                            if (candidate["groundingMetadata"] != null) {
                                bestCandidate = candidate
                            } else if (bestCandidate == null) {
                                bestCandidate = candidate
                            }

                            if (parts.answer.isNotBlank()) {
                                val delta = computeDelta(lastAnswerSnapshot, parts.answer)
                                if (delta.isNotBlank()) {
                                    answerBuilder.append(delta)
                                    onPartialText?.invoke(answerBuilder.toString())
                                }
                            }
                            if (includeThoughts && parts.thought.isNotBlank()) {
                                val delta = computeDelta(lastThoughtSnapshot, parts.thought)
                                if (delta.isNotBlank()) {
                                    thoughtBuilder.append(delta)
                                    onPartialThought?.invoke(thoughtBuilder.toString())
                                }
                            }
                        }
                    }

                    return StreamResponse(
                        statusCode = response.code,
                        text = answerBuilder.toString().trim(),
                        thought = thoughtBuilder.toString().trim(),
                        bestCandidate = bestCandidate,
                        rawBody = "",
                    )
                }
            } catch (t: SocketTimeoutException) {
                throw IllegalStateException(
                    "Gemini request timeout. Try shorter prompt/context or disable Web Search.",
                )
            } catch (t: IOException) {
                throw IllegalStateException(
                    "Gemini network error: ${t.message ?: "unknown I/O error"}",
                )
            }
        }

        fun tryGenerate(
            model: String,
            useWebSearch: Boolean,
            strictGrounding: Boolean = false,
            allowStream: Boolean = true,
        ): GenerationOutput {
            val payload = buildPayload(useWebSearch = useWebSearch, strictGrounding = strictGrounding)

            if (allowStream) {
                val stream = executeStream(model = model, payload = payload)
                if (stream.statusCode in 200..299 && stream.text.isNotBlank()) {
                    return GenerationOutput(
                        statusCode = stream.statusCode,
                        text = stream.text,
                        thought = stream.thought,
                        candidate = stream.bestCandidate,
                        rawErrorBody = "",
                        model = model,
                    )
                }

                if (stream.statusCode !in 200..299) {
                    return GenerationOutput(
                        statusCode = stream.statusCode,
                        text = "",
                        thought = "",
                        candidate = null,
                        rawErrorBody = stream.rawBody,
                        model = model,
                    )
                }
            }

            val nonStream = execute(model = model, payload = payload)
            if (nonStream.first !in 200..299) {
                return GenerationOutput(
                    statusCode = nonStream.first,
                    text = "",
                    thought = "",
                    candidate = null,
                    rawErrorBody = nonStream.second,
                    model = model,
                )
            }

            val root = jsonParser.parseToJsonElement(nonStream.second).jsonObject
            val candidate = root["candidates"]?.jsonArray?.firstOrNull()?.jsonObject
            val parts = candidate?.let { extractCandidateParts(it) } ?: CandidateParts()

            return GenerationOutput(
                statusCode = nonStream.first,
                text = parts.answer.trim(),
                thought = parts.thought.trim(),
                candidate = candidate,
                rawErrorBody = "",
                model = model,
            )
        }

        var usedWebSearch = enableWebSearch
        var generated = tryGenerate(
            model = selectedModel,
            useWebSearch = enableWebSearch,
            strictGrounding = false,
            allowStream = true,
        )

        if (enableWebSearch) {
            if (generated.statusCode in 200..299) {
                val initialSources = extractWebSources(generated.candidate, generated.text)
                if (initialSources.isEmpty()) {
                    generated = tryGenerate(
                        model = generated.model,
                        useWebSearch = true,
                        strictGrounding = true,
                        allowStream = false,
                    )

                }
            }

            if (generated.statusCode !in 200..299 && looksLikeToolError(generated.rawErrorBody)) {
                usedWebSearch = false
                generated = tryGenerate(
                    model = selectedModel,
                    useWebSearch = false,
                    strictGrounding = false,
                    allowStream = true,
                )
            }
        }

        if (generated.statusCode !in 200..299) {
            if (generated.statusCode == 429) {
                throw IllegalStateException(
                    "Gemini HTTP 429 (model=${generated.model}): quota exceeded for this API key/plan. " +
                        "Check ai.dev/rate-limit and billing.",
                )
            }
            throw IllegalStateException(
                "Gemini HTTP ${generated.statusCode} (model=${generated.model}): ${generated.rawErrorBody.take(400)}",
            )
        }

        if (generated.text.isBlank()) {
            throw IllegalStateException("Gemini response did not include text.")
        }

        val text = generated.text.trim()
        val thought = generated.thought.trim()
        val sources = if (usedWebSearch) extractWebSources(generated.candidate, text) else emptyList()

        val finalText = if (sources.isEmpty()) {
            if (enableWebSearch && !usedWebSearch) {
                "$text\n\n[Web search unavailable for current model; answered without tool]"
            } else if (enableWebSearch && usedWebSearch) {
                "$text\n\n[Web search enabled but model returned no grounding sources]"
            } else {
                text
            }
        } else {
            buildString {
                append(text)
                append("\n\nSources:\n")
                sources.forEachIndexed { index, source ->
                    append(index + 1)
                    append(". ")
                    append(source.title)
                    append(" - ")
                    append(source.uri)
                    append('\n')
                }
            }.trim()
        }

        GeminiGenerationResult(
            text = finalText,
            thought = thought,
        )
    }

    suspend fun listAvailableModels(apiKeyOverride: String? = null): List<String> = withContext(Dispatchers.IO) {
        val apiKey = apiKeyOverride?.trim()?.ifBlank { null } ?: settingsRepository.getApiKey()
        if (apiKey.isBlank()) {
            throw IllegalStateException("Gemini API key is empty. Set it in the app first.")
        }

        val request = Request.Builder()
            .url("https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000")
            .addHeader("x-goog-api-key", apiKey)
            .get()
            .build()

        val rawBody = try {
            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    val err = response.body?.string().orEmpty()
                    throw IllegalStateException("Models API HTTP ${response.code}: ${err.take(280)}")
                }
                response.body?.string().orEmpty()
            }
        } catch (t: SocketTimeoutException) {
            throw IllegalStateException("Models API timeout")
        } catch (t: IOException) {
            throw IllegalStateException("Models API network error: ${t.message ?: "unknown I/O error"}")
        }

        val root = jsonParser.parseToJsonElement(rawBody).jsonObject
        val models = root["models"]?.jsonArray ?: JsonArray(emptyList())
        if (models.isEmpty()) {
            return@withContext emptyList()
        }

        val out = linkedSetOf<String>()
        models.forEach { element ->
            val obj = element as? JsonObject ?: return@forEach
            val rawName = obj["name"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
            if (rawName.isBlank()) return@forEach
            val modelName = rawName.removePrefix("models/")
            if (!modelName.contains("gemini", ignoreCase = true)) return@forEach

            val methods = obj["supportedGenerationMethods"]?.jsonArray ?: JsonArray(emptyList())
            val supportsText = methods.any { method ->
                val value = method.jsonPrimitive.contentOrNull.orEmpty()
                value == "generateContent" || value == "streamGenerateContent"
            }
            if (!supportsText) return@forEach

            out.add(modelName)
        }

        return@withContext out.toList().sorted()
    }

    private fun parseSseCandidates(rawData: String): List<JsonObject> {
        val root = try {
            jsonParser.parseToJsonElement(rawData.trim())
        } catch (_: Throwable) {
            return emptyList()
        }

        val candidates = mutableListOf<JsonObject>()

        fun collectFromObject(obj: JsonObject) {
            obj["candidates"]?.jsonArray?.forEach { item ->
                val candidate = item as? JsonObject ?: return@forEach
                candidates.add(candidate)
            }
        }

        when (root) {
            is JsonObject -> collectFromObject(root)
            is JsonArray -> {
                root.forEach { element ->
                    val obj = element as? JsonObject ?: return@forEach
                    collectFromObject(obj)
                }
            }

            else -> {}
        }

        return candidates
    }

    private fun extractCandidateParts(candidate: JsonObject): CandidateParts {
        val parts = candidate["content"]
            ?.jsonObject
            ?.get("parts")
            ?.jsonArray
            ?: return CandidateParts()

        val answerBuilder = StringBuilder()
        val thoughtBuilder = StringBuilder()

        for (part in parts) {
            val obj = part.jsonObject
            val text = obj["text"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
            if (text.isEmpty()) continue

            val isThought = obj["thought"]?.jsonPrimitive?.booleanOrNull ?: false
            if (isThought) {
                if (thoughtBuilder.isNotEmpty()) thoughtBuilder.append('\n')
                thoughtBuilder.append(text)
            } else {
                if (answerBuilder.isNotEmpty()) answerBuilder.append('\n')
                answerBuilder.append(text)
            }
        }

        return CandidateParts(
            answer = answerBuilder.toString(),
            thought = thoughtBuilder.toString(),
        )
    }

    private fun computeDelta(previousSnapshot: String, currentChunkOrSnapshot: String): String {
        if (currentChunkOrSnapshot.isBlank()) return ""
        if (previousSnapshot.isBlank()) return currentChunkOrSnapshot
        if (currentChunkOrSnapshot.startsWith(previousSnapshot)) {
            return currentChunkOrSnapshot.substring(previousSnapshot.length)
        }
        if (previousSnapshot.endsWith(currentChunkOrSnapshot)) {
            return ""
        }
        return currentChunkOrSnapshot
    }

    private fun sanitizeThinkingBudget(thinkingBudget: Int?): Int {
        return when {
            thinkingBudget == null -> -1
            thinkingBudget < -1 -> -1
            thinkingBudget > 24_576 -> 24_576
            else -> thinkingBudget
        }
    }

    private fun looksLikeToolError(rawErrorBody: String): Boolean {
        if (rawErrorBody.isBlank()) return false
        val lowered = rawErrorBody.lowercase()
        val mentionsTool = lowered.contains("google_search") || lowered.contains("tool")
        val unsupported = lowered.contains("not supported") ||
            lowered.contains("unknown") ||
            lowered.contains("invalid") ||
            lowered.contains("unrecognized") ||
            lowered.contains("does not support")
        return mentionsTool && unsupported
    }

    private fun extractWebSources(candidate: JsonObject?, finalText: String): List<WebSource> {
        if (candidate == null) {
            return extractWebSourcesFromText(finalText)
        }

        val grounding = candidate["groundingMetadata"]?.jsonObject
            ?: return extractWebSourcesFromText(finalText)

        val out = mutableListOf<WebSource>()
        val seen = linkedSetOf<String>()

        grounding["groundingChunks"]?.jsonArray?.forEach { chunk ->
            val web = chunk.jsonObject["web"]?.jsonObject ?: return@forEach
            val uri = web["uri"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
            if (uri.isEmpty() || !seen.add(uri)) return@forEach
            val title = web["title"]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()
            out.add(
                WebSource(
                    title = if (title.isNotEmpty()) title else uri,
                    uri = uri,
                )
            )
        }

        if (out.isNotEmpty()) {
            return out.take(8)
        }
        return extractWebSourcesFromText(finalText)
    }

    private fun extractWebSourcesFromText(text: String): List<WebSource> {
        if (text.isBlank()) return emptyList()
        val out = mutableListOf<WebSource>()
        val seen = linkedSetOf<String>()
        for (match in urlRegex.findAll(text)) {
            val uri = match.value.trim().trimEnd('.', ',', ';', ')', ']', '}')
            if (!seen.add(uri)) continue
            out.add(WebSource(title = uri, uri = uri))
            if (out.size >= 8) break
        }
        return out
    }

    private fun buildContextualPrompt(
        userPrompt: String,
        contextBlocks: List<ContextBlockRequest>,
        conversationMemory: List<MemoryTurnRequest>,
        webSearchEnabled: Boolean,
    ): String {
        val limitedContext = contextBlocks.take(10)
        val contextText = buildString {
            limitedContext.forEachIndexed { index, block ->
                append("[DOC ")
                append(index + 1)
                append("] ")
                append(block.source)
                append(" - page ")
                append(block.page)
                append('\n')
                append(block.text)
                append("\n\n")
            }
        }.trim()

        val memoryText = buildString {
            conversationMemory.take(12).forEach { turn ->
                append("- ")
                append(turn.role.uppercase())
                append(": ")
                append(turn.text)
                append('\n')
            }
        }.trim()

        val today = LocalDate.now().toString()
        return buildString {
            append("You are an assistant in an ongoing conversation inside a custom Bluetooth chat app.\n")
            append("Never mention web UI actions like paperclip, upload buttons, or external chat interfaces.\n")
            append("Use conversation memory and PDF context only when relevant.\n")
            append("If document excerpts are missing or insufficient, clearly say you need more document context.\n")
            append("Current local date is $today. If asked about 'today', use this date unless explicitly quoting a source date.\n")
            if (webSearchEnabled) {
                append("Web search is enabled: use google_search grounding for current facts and include explicit source URLs.\n")
            }
            append('\n')
            if (memoryText.isNotEmpty()) {
                append("CONVERSATION MEMORY:\n")
                append(memoryText)
                append("\n\n")
            }
            append("QUESTION:\n")
            append(userPrompt.trim())
            if (contextText.isNotEmpty()) {
                append("\n\nPDF CONTEXT:\n")
                append(contextText)
            }
        }
    }

    /**
     * On-device BM25-style retrieval: scores all chunks in the container against
     * the user's query and returns the top [topK] most relevant as ContextBlockRequest.
     */
    private fun retrieveTopChunks(
        query: String,
        container: StoredContainer,
        topK: Int = 20,
    ): List<ContextBlockRequest> {
        val queryTerms = tokenize(query)
        val queryLower = query.lowercase()
        if (queryTerms.isEmpty()) {
            // No scorable terms → return first topK chunks as fallback
            return container.chunks.take(topK).map {
                ContextBlockRequest(source = it.source, page = it.page, text = it.text)
            }
        }
        data class Scored(val score: Double, val chunk: StoredChunk)
        val scored = container.chunks.mapNotNull { chunk ->
            val chunkTermSet = chunk.terms.toHashSet()
            val overlap = queryTerms.count { it in chunkTermSet }.toDouble()
            if (overlap == 0.0) return@mapNotNull null
            val phraseBonus = if (chunk.text.lowercase().contains(queryLower)) 4.0 else 0.0
            Scored(score = overlap + phraseBonus, chunk = chunk)
        }
        return scored
            .sortedByDescending { it.score }
            .take(topK)
            .map { ContextBlockRequest(source = it.chunk.source, page = it.chunk.page, text = it.chunk.text) }
    }

    private fun tokenize(text: String): Set<String> {
        val result = mutableSetOf<String>()
        val tokenRegex = Regex("[A-Za-z0-9_\\u00C0-\\u024F]{2,}")
        tokenRegex.findAll(text.lowercase()).forEach { match ->
            val token = match.value
            if (token.length >= 3) result.add(token)
        }
        return result
    }

    private fun resolveActiveContainer(
        activeContainerId: String?,
        activeContainerName: String?,
    ): StoredContainer? {
        if (!activeContainerId.isNullOrBlank()) {
            BridgeRuntimeState.containers[activeContainerId]?.let { return it }
        }
        if (!activeContainerName.isNullOrBlank()) {
            val wanted = activeContainerName.trim().lowercase()
            return BridgeRuntimeState.containers.values.firstOrNull {
                it.name.trim().lowercase() == wanted
            }
        }
        return null
    }

    private data class CandidateParts(

        val answer: String = "",
        val thought: String = "",
    )

    private data class WebSource(
        val title: String,
        val uri: String,
    )

    private data class StreamResponse(
        val statusCode: Int,
        val text: String,
        val thought: String,
        val bestCandidate: JsonObject?,
        val rawBody: String,
    )

    private data class GenerationOutput(
        val statusCode: Int,
        val text: String,
        val thought: String,
        val candidate: JsonObject?,
        val rawErrorBody: String,
        val model: String,
    )

}
