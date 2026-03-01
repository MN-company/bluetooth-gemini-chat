package com.example.geminibridge

import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.ceil


data class BleFrame(
    val transportId: Int,
    val index: Int,
    val total: Int,
    val payload: ByteArray,
)

object BleFrameCodec {
    fun encodeMessage(transportId: Int, payload: ByteArray, maxPacketSize: Int): List<ByteArray> {
        require(transportId in 0..0xFFFF) { "transportId must be in [0, 65535]" }
        require(maxPacketSize > BleConstants.frameHeaderBytes) {
            "maxPacketSize must be > ${BleConstants.frameHeaderBytes}"
        }

        val chunkSize = maxPacketSize - BleConstants.frameHeaderBytes
        val totalChunks = maxOf(1, ceil(payload.size / chunkSize.toDouble()).toInt())
        require(totalChunks <= 0xFFFF) { "Too many chunks for this protocol" }

        val packets = ArrayList<ByteArray>(totalChunks)
        for (index in 0 until totalChunks) {
            val start = index * chunkSize
            val end = minOf(start + chunkSize, payload.size)
            val chunk = if (start >= payload.size) ByteArray(0) else payload.copyOfRange(start, end)

            val frame = ByteBuffer.allocate(BleConstants.frameHeaderBytes + chunk.size)
                .order(ByteOrder.BIG_ENDIAN)
                .put(BleConstants.protocolVersion.toByte())
                .putShort(transportId.toShort())
                .putShort(index.toShort())
                .putShort(totalChunks.toShort())
                .put(chunk)
                .array()

            packets.add(frame)
        }

        return packets
    }

    fun decodeFrame(raw: ByteArray): BleFrame? {
        if (raw.size < BleConstants.frameHeaderBytes) return null

        val buffer = ByteBuffer.wrap(raw).order(ByteOrder.BIG_ENDIAN)
        val version = buffer.get().toInt() and 0xFF
        if (version != BleConstants.protocolVersion) return null

        val transportId = buffer.short.toInt() and 0xFFFF
        val index = buffer.short.toInt() and 0xFFFF
        val total = buffer.short.toInt() and 0xFFFF
        if (total == 0 || index >= total) return null

        val payload = ByteArray(raw.size - BleConstants.frameHeaderBytes)
        buffer.get(payload)

        return BleFrame(
            transportId = transportId,
            index = index,
            total = total,
            payload = payload,
        )
    }
}

class BleFrameAssembler(
    private val timeoutMs: Long = BleConstants.assemblyTimeoutMs,
) {
    private data class Pending(
        val total: Int,
        val chunks: MutableMap<Int, ByteArray> = LinkedHashMap(),
        val createdAtMs: Long = System.currentTimeMillis(),
    )

    private val pendingMessages = mutableMapOf<Int, Pending>()

    @Synchronized
    fun addFrame(frame: BleFrame): ByteArray? {
        cleanupExpired()

        val pending = pendingMessages[frame.transportId]
        val active = when {
            pending == null -> {
                val created = Pending(total = frame.total)
                pendingMessages[frame.transportId] = created
                created
            }

            pending.total != frame.total -> {
                pendingMessages.remove(frame.transportId)
                return null
            }

            else -> pending
        }

        active.chunks[frame.index] = frame.payload
        if (active.chunks.size != active.total) return null

        val merged = ByteArray(active.chunks.values.sumOf { it.size })
        var offset = 0
        for (i in 0 until active.total) {
            val chunk = active.chunks[i] ?: return null
            chunk.copyInto(merged, destinationOffset = offset)
            offset += chunk.size
        }

        pendingMessages.remove(frame.transportId)
        return merged
    }

    @Synchronized
    private fun cleanupExpired() {
        val now = System.currentTimeMillis()
        val toRemove = pendingMessages
            .filterValues { now - it.createdAtMs > timeoutMs }
            .keys
            .toList()

        toRemove.forEach { pendingMessages.remove(it) }
    }
}

class TransportIdGenerator {
    private var nextId: Int = 1

    @Synchronized
    fun next(): Int {
        val current = nextId
        nextId += 1
        if (nextId > 0xFFFF) {
            nextId = 1
        }
        return current
    }
}
