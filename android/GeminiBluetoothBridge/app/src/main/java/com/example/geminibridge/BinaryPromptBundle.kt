package com.example.geminibridge

import android.util.Base64
import java.io.ByteArrayInputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.GZIPInputStream
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private val promptBundleMagic = byteArrayOf('b'.code.toByte(), 'g'.code.toByte(), 'p'.code.toByte(), '2'.code.toByte())
private const val promptBundleHeaderBytes = 13
private const val promptBundleFlagGzipMetadata = 0x01

object BinaryPromptBundle {
    fun isPromptBundle(payload: ByteArray): Boolean {
        if (payload.size < promptBundleHeaderBytes) return false
        return payload[0] == promptBundleMagic[0] &&
            payload[1] == promptBundleMagic[1] &&
            payload[2] == promptBundleMagic[2] &&
            payload[3] == promptBundleMagic[3]
    }

    fun decodeToJson(payload: ByteArray, json: Json): String {
        require(isPromptBundle(payload)) { "Not a prompt bundle" }

        val header = ByteBuffer.wrap(payload, 0, promptBundleHeaderBytes).order(ByteOrder.BIG_ENDIAN)
        val magic = ByteArray(4)
        header.get(magic)
        val flags = header.get().toInt() and 0xFF
        val metadataLength = header.int
        val imageLength = header.int

        val expectedSize = promptBundleHeaderBytes + metadataLength + imageLength
        require(payload.size == expectedSize) {
            "Prompt bundle size mismatch: expected $expectedSize bytes, got ${payload.size}"
        }

        val metadataStart = promptBundleHeaderBytes
        val metadataEnd = metadataStart + metadataLength
        var metadataBytes = payload.copyOfRange(metadataStart, metadataEnd)
        if ((flags and promptBundleFlagGzipMetadata) != 0) {
            metadataBytes = GZIPInputStream(ByteArrayInputStream(metadataBytes)).use { it.readBytes() }
        }

        val root = json.parseToJsonElement(metadataBytes.decodeToString()).let {
            require(it is JsonObject) { "Prompt bundle metadata must be a JSON object" }
            it
        }

        if (imageLength <= 0) {
            return root.toString()
        }

        val imageBytes = payload.copyOfRange(metadataEnd, metadataEnd + imageLength)
        val imageBase64 = Base64.encodeToString(imageBytes, Base64.NO_WRAP)

        return buildJsonObject {
            root.forEach { (key, value) -> put(key, value) }
            put("imageBase64", JsonPrimitive(imageBase64))
        }.toString()
    }
}
