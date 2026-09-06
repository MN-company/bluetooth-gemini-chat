import Foundation

// MARK: - Frame model
/// Equivalent to Android's `BleFrame` data class.
public struct Frame: Sendable {
    public let transportId: UInt16
    public let index: UInt16
    public let total: UInt16
    public let payload: Data

    public init(transportId: UInt16, index: UInt16, total: UInt16, payload: Data) {
        self.transportId = transportId
        self.index = index
        self.total = total
        self.payload = payload
    }
}

// MARK: - Frame codec
/// Equivalent to Android's `BleFrameCodec` object.
/// Header: [protoVer:1][transportId:2][index:2][total:2] + payload = 7 bytes header
public enum FrameCodecError: Error {
    case invalidMaxPacketSize
    case invalidTransportId
    case tooManyChunks
    case packetTooShort
    case unsupportedProtocolVersion
    case invalidTotalCount
    case indexOutOfRange
}

public enum FrameCodec {
    /// Encode a payload into a list of BLE packets.
    /// Matches `BleFrameCodec.encodeMessage()` in Android.
    public static func encode(
        transportId: UInt16,
        payload: Data,
        maxPacketSize: Int
    ) throws -> [Data] {
        guard maxPacketSize > BLEConstants.frameHeaderBytes else {
            throw FrameCodecError.invalidMaxPacketSize
        }

        let chunkSize = maxPacketSize - BLEConstants.frameHeaderBytes
        let totalChunks = UInt16(max(1, (payload.count + chunkSize - 1) / chunkSize))
        guard totalChunks <= BLEConstants.maxTotalChunks else {
            throw FrameCodecError.tooManyChunks
        }

        var packets: [Data] = []
        packets.reserveCapacity(Int(totalChunks))

        for index: UInt16 in 0..<totalChunks {
            let start = Int(index) * chunkSize
            let end = min(start + chunkSize, payload.count)
            let chunk = start < payload.count ? payload[start..<end] : Data()

            var frame = Data(capacity: BLEConstants.frameHeaderBytes + chunk.count)
            frame.append(BLEConstants.protocolVersion)
            withUnsafeBytes(of: transportId.bigEndian) { frame.append(contentsOf: $0) }
            withUnsafeBytes(of: index.bigEndian) { frame.append(contentsOf: $0) }
            withUnsafeBytes(of: totalChunks.bigEndian) { frame.append(contentsOf: $0) }
            frame.append(chunk)
            packets.append(frame)
        }

        return packets
    }

    /// Decode a single raw BLE packet into a Frame.
    /// Matches `BleFrameCodec.decodeFrame()` in Android.
    public static func decode(_ packet: Data) throws -> Frame {
        guard packet.count >= BLEConstants.frameHeaderBytes else {
            throw FrameCodecError.packetTooShort
        }

        let version = packet[0]
        guard version == BLEConstants.protocolVersion else {
            throw FrameCodecError.unsupportedProtocolVersion
        }

        let transportId = packet.withUnsafeBytes { $0.load(fromByteOffset: 1, as: UInt16.self) }.bigEndian
        let index = packet.withUnsafeBytes { $0.load(fromByteOffset: 3, as: UInt16.self) }.bigEndian
        let total = packet.withUnsafeBytes { $0.load(fromByteOffset: 5, as: UInt16.self) }.bigEndian

        guard total > 0 else {
            throw FrameCodecError.invalidTotalCount
        }
        guard index < total else {
            throw FrameCodecError.indexOutOfRange
        }

        let payload = packet.dropFirst(BLEConstants.frameHeaderBytes)
        return Frame(transportId: transportId, index: index, total: total, payload: payload)
    }
}

// MARK: - Frame assembler
/// Equivalent to Android's `BleFrameAssembler` class.
/// Reassembles chunks by transportId with a 300-second timeout.
public actor FrameAssembler {
    private let timeoutSeconds: TimeInterval

    private struct Pending {
        let total: UInt16
        var chunks: [UInt16: Data]
        let createdAtMs: UInt64
    }

    private var pending: [UInt16: Pending] = [:]

    public init(timeoutSeconds: TimeInterval = BLEConstants.assemblyTimeoutSeconds) {
        self.timeoutSeconds = timeoutSeconds
    }

    /// Add a decoded frame to the assembler.
    /// Returns the complete reassembled payload when all chunks arrive, else nil.
    public func addFrame(_ frame: Frame) throws -> Data? {
        cleanupExpired()

        let transportId = frame.transportId

        if var existing = pending[transportId] {
            guard existing.total == frame.total else {
                pending.removeValue(forKey: transportId)
                return nil
            }
            existing.chunks[frame.index] = frame.payload
            pending[transportId] = existing

            if existing.chunks.count == Int(existing.total) {
                defer { pending.removeValue(forKey: transportId) }
                // Reconstruct in order
                var result = Data(capacity: existing.chunks.values.reduce(0) { $0 + $1.count })
                for idx: UInt16 in 0..<existing.total {
                    guard let chunk = existing.chunks[idx] else { return nil }
                    result.append(chunk)
                }
                return result
            }
        } else {
            var chunks: [UInt16: Data] = [:]
            chunks[frame.index] = frame.payload
            pending[transportId] = Pending(
                total: frame.total,
                chunks: chunks,
                createdAtMs: nowMs()
            )
        }
        return nil
    }

    private func cleanupExpired() {
        let now = nowMs()
        let toRemove = pending.filter { _, p in
            now - p.createdAtMs > UInt64(timeoutSeconds * 1000)
        }
        for key in toRemove.keys {
            pending.removeValue(forKey: key)
        }
    }

    private func nowMs() -> UInt64 {
        UInt64(Date().timeIntervalSince1970 * 1000)
    }
}

// MARK: - Transport ID generator
/// Equivalent to Android's `TransportIdGenerator`.
public final class TransportIdGenerator: @unchecked Sendable {
    public private(set) var nextId: UInt16 = 1
    private let lock = NSLock()

    public init(start: UInt16 = 1) {
        self.nextId = start > 0 ? start : 1
    }

    public func next() -> UInt16 {
        lock.lock()
        defer { lock.unlock() }
        let current = nextId
        nextId = nextId &+ 1
        if nextId == 0 { nextId = 1 }
        return current
    }
}