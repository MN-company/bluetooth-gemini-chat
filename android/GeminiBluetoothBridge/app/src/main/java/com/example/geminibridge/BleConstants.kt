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
}
