import Foundation
import CoreBluetooth

// MARK: - GATT Service UUIDs
/// Matches Android BleConstants exactly: 8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c0{1,2,3}
public enum BLEConstants {
    public static let serviceUUID      = CBUUID(string: "8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c01")
    public static let writeCharUUID    = CBUUID(string: "8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c02")
    public static let notifyCharUUID   = CBUUID(string: "8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c03")
    public static let cccdUUID         = CBUUID(string: "00002902-0000-1000-8000-00805f9b34fb")

    // MARK: - Frame protocol constants
    public static let protocolVersion: UInt8      = 1
    public static let frameHeaderBytes: Int        = 7
    public static let defaultAttMtu: Int           = 23
    public static let defaultMaxPacketSize: Int    = 20
    public static let maxGattAttributeValueBytes   = 512
    public static let assemblyTimeoutSeconds: TimeInterval = 300.0

    // MARK: - Binary ping/pong v2 (8 bytes, 85% smaller than JSON)
    /// Frame: [0xFE][0xFD][type:1][ts_ms:4 big-endian][status:1]
    public static let binaryFrameMagic: Data = Data([0xFE, 0xFD])
    public static let binaryPingType: UInt8  = 0x01
    public static let binaryPongType: UInt8  = 0x02
    public static let binaryFrameSize: Int   = 8

    // MARK: - Prompt bundle magic
    public static let promptBundleMagic: Data = Data("bgp2".utf8)
    public static let promptBundleHeaderBytes = 13
    public static let promptBundleFlagGzipMetadata: UInt8 = 0x01

    // MARK: - Gzip compression
    public static let gzipMagic: Data = Data("gz".utf8) + Data([0x01])
    public static let jsonGzipThresholdBytes = 900

    // MARK: - Heartbeat
    public static let pingIntervalSeconds: TimeInterval = 30.0
    public static let pingTimeoutSeconds: TimeInterval  = 90.0

    // MARK: - Transport protocol
    public static let maxTransportId: UInt16 = 0xFFFF
    public static let maxTotalChunks: UInt16 = 0xFFFF

    // MARK: - Request limits
    public static let maxPromptBytes           = 220 * 1024
    public static let maxImageBytes            = 1_200_000
    public static let maxContextBlocks          = 12
    public static let maxMemoryTurns            = 24
    public static let maxConcurrentPrompts      = 2
    public static let maxPendingPrompts         = 10

    // MARK: - Send throttle
    public static let throttleMultiClientEvery = 6
    public static let throttleSingleClientEvery = 4
    public static let throttleDelayMs: UInt64   = 1

    // MARK: - Convenience
    public static let packetServiceUUIDString = "8e7f1f10-6c7a-4a89-b2e8-4e20f4f31c01"
}

// MARK: - Binary frame helpers
public extension BLEConstants {
    static func isBinaryFrame(_ data: Data) -> Bool {
        data.count >= binaryFrameSize
            && data[0] == binaryFrameMagic[0]
            && data[1] == binaryFrameMagic[1]
    }

    static func parseBinaryFrameType(_ data: Data) -> UInt8 {
        data[2]
    }

    static func buildBinaryPong(tsMs: Int64) -> Data {
        var buf = Data(capacity: binaryFrameSize)
        buf.append(binaryFrameMagic)
        buf.append(binaryPongType)
        withUnsafeBytes(of: UInt32(truncatingIfNeeded: tsMs).bigEndian) { buf.append(contentsOf: $0) }
        buf.append(0)
        return buf
    }

    static func parseBinaryTimestamp(_ data: Data) -> Int64 {
        let raw = UInt32(bigEndian: data.withUnsafeBytes { $0.load(fromByteOffset: 3, as: UInt32.self) })
        return Int64(raw)
    }
}