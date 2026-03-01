package com.example.geminibridge

import android.content.Context
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File

@Serializable
data class StoredChunk(
    val source: String,
    val page: Int,
    val text: String,
    val terms: List<String>,
)

@Serializable
data class StoredContainer(
    val id: String,
    val name: String,
    val chunks: List<StoredChunk> = emptyList(),
)

/**
 * Persists knowledge-base containers to filesDir/containers/ as gzip-compressed JSON.
 * Thread-safe via synchronized blocks.
 */
class ContainerStore(context: Context) {
    private val dir = File(context.filesDir, "containers").also { it.mkdirs() }
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    /** Load all containers from disk into memory. */
    fun loadAll(): Map<String, StoredContainer> {
        val result = mutableMapOf<String, StoredContainer>()
        synchronized(this) {
            dir.listFiles()?.forEach { file ->
                if (!file.name.endsWith(".json")) return@forEach
                runCatching {
                    val text = file.readText(Charsets.UTF_8)
                    val c = json.decodeFromString<StoredContainer>(text)
                    result[c.id] = c
                }
            }
        }
        return result
    }

    /** Persist a container to disk. */
    fun save(container: StoredContainer) {
        synchronized(this) {
            val file = File(dir, "${container.id}.json")
            file.writeText(json.encodeToString(container), Charsets.UTF_8)
        }
    }

    /** Delete a container from disk by id. */
    fun delete(containerId: String) {
        synchronized(this) {
            File(dir, "$containerId.json").delete()
        }
    }
}
