package com.example.geminibridge

import java.util.UUID

object BleConstants {
    val serviceUuid: UUID = UUID.fromString("8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c01")
    val writeCharUuid: UUID = UUID.fromString("8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c02")
    val notifyCharUuid: UUID = UUID.fromString("8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c03")
    val cccdUuid: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    const val protocolVersion: Int = 1
    const val frameHeaderBytes: Int = 7
    const val defaultAttMtu: Int = 23
    const val defaultMaxPacketSize: Int = 20
    const val maxGattAttributeValueBytes: Int = 512
    const val assemblyTimeoutMs: Long = 300_000L

    // Protocol v2 binary ping/pong (8 bytes vs ~55 bytes JSON — 85% smaller)
    // Frame: [0xFE][0xFD][type:1][ts_ms:4 big-endian][status:1]
    val binaryFrameMagic: ByteArray = byteArrayOf(0xFE.toByte(), 0xFD.toByte())
    const val binaryPingType: Byte = 0x01
    const val binaryPongType: Byte = 0x02
    const val binaryFrameSize: Int = 8 // magic(2) + type(1) + ts_ms(4) + status(1)

    fun isBinaryFrame(data: ByteArray): Boolean =
        data.size >= binaryFrameSize && data[0] == binaryFrameMagic[0] && data[1] == binaryFrameMagic[1]

    fun buildBinaryPong(tsMs: Long): ByteArray {
        val buf = java.nio.ByteBuffer.allocate(binaryFrameSize).order(java.nio.ByteOrder.BIG_ENDIAN)
        buf.put(binaryFrameMagic[0])
        buf.put(binaryFrameMagic[1])
        buf.put(binaryPongType)
        buf.putInt((tsMs and 0xFFFFFFFFL).toInt())
        buf.put(0)
        return buf.array()
    }

    fun parseBinaryFrameType(data: ByteArray): Byte = data[2]
}
