package com.example.geminibridge

import java.io.ByteArrayOutputStream
import java.util.zip.GZIPOutputStream
import kotlinx.serialization.json.Json
import org.junit.Assert.assertTrue
import org.junit.Test

class BinaryPromptBundleTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun decodeToJson_expandsEmbeddedImage() {
        val metadata = """{"type":"prompt","messageId":"req-1","prompt":"Pick one","imageMimeType":"image/png"}""".encodeToByteArray()
        val imageBytes = byteArrayOf(1, 2, 3, 4, 5)
        val payload = buildBundle(metadata, imageBytes, gzipMetadata = true)

        val decoded = BinaryPromptBundle.decodeToJson(payload, json)

        assertTrue(decoded.contains("imageBase64"))
        assertTrue(decoded.contains("\"imageMimeType\":\"image/png\""))
        assertTrue(decoded.contains("\"messageId\":\"req-1\""))
    }

    private fun buildBundle(metadata: ByteArray, imageBytes: ByteArray, gzipMetadata: Boolean): ByteArray {
        val finalMetadata = if (gzipMetadata) {
            val output = ByteArrayOutputStream()
            GZIPOutputStream(output).use { it.write(metadata) }
            output.toByteArray()
        } else {
            metadata
        }
        val header = ByteArrayOutputStream()
        header.write(byteArrayOf('b'.code.toByte(), 'g'.code.toByte(), 'p'.code.toByte(), '2'.code.toByte()))
        header.write(if (gzipMetadata) 1 else 0)
        header.write(intBytes(finalMetadata.size))
        header.write(intBytes(imageBytes.size))
        header.write(finalMetadata)
        header.write(imageBytes)
        return header.toByteArray()
    }

    private fun intBytes(value: Int): ByteArray =
        byteArrayOf(
            ((value ushr 24) and 0xFF).toByte(),
            ((value ushr 16) and 0xFF).toByte(),
            ((value ushr 8) and 0xFF).toByte(),
            (value and 0xFF).toByte(),
        )
}
