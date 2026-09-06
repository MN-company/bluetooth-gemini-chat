import Foundation
import CoreBluetooth
import Combine
import OSLog

// MARK: - BLE Server Manager
/// Equivalent to Android's `BleServerManager`.
/// Manages a CBPeripheralManager that advertises and serves GATT connections.
public actor BLEServerManager: NSObject {
    // MARK: - Public types
    public typealias PromptHandler = @Sendable (_ json: String, _ address: String) async -> Void
    public typealias LogHandler = @Sendable (String) -> Void

    public enum State: Sendable, Equatable {
        case idle
        case starting
        case advertising
        case connected(count: Int)
        case error(String)
    }

    public enum BLEError: Swift.Error, LocalizedError {
        case serverNotReady
        case noConnectedCentral
        case invalidBundle
        case bundleSizeMismatch

        public var errorDescription: String? {
            switch self {
            case .serverNotReady: return "BLE bridge not ready"
            case .noConnectedCentral: return "No connected BLE central"
            case .invalidBundle: return "Invalid prompt bundle"
            case .bundleSizeMismatch: return "Prompt bundle size mismatch"
            }
        }
    }

    // MARK: - Properties
    private var peripheralManager: CBPeripheralManager?
    private var notifyCharacteristic: CBMutableCharacteristic?
    private var serviceAdded = false

    private var connectedCentrals: [String: CBCentral] = [:]
    private var mtuByCentral: [String: Int] = [:]
    private var mutexByCentral: [String: NSLock] = [:]

    private let frameAssembler = FrameAssembler()
    private let transportIds = TransportIdGenerator()

    private let onPrompt: PromptHandler
    private let onLog: LogHandler

    private var managerReady = false
    private var pendingStart = false

    // MARK: - State publisher for UI
    private let stateSubject = PassthroughSubject<State, Never>()
    public nonisolated var statePublisher: AnyPublisher<State, Never> {
        stateSubject.eraseToAnyPublisher()
    }

    // Compression magic
    private static let gzipMagic = Data("gz".utf8) + Data([0x01])
    private static let promptBundleMagic = Data("bgp2".utf8)
    private static let pbHeaderBytes = 13

    public init(onPrompt: @escaping PromptHandler, onLog: @escaping LogHandler) {
        self.onPrompt = onPrompt
        self.onLog = onLog
        super.init()
    }

    // MARK: - Public API
    public func start() {
        guard !managerReady else { return }
        pendingStart = true
        let options: [String: Any] = [
            CBPeripheralManagerOptionShowPowerAlertKey: true,
            CBPeripheralManagerOptionRestoreIdentifierKey: "com.mncompany.geminiairbridge.ble"
        ]
        peripheralManager = CBPeripheralManager(delegate: self, queue: nil, options: options)
    }

    public func stop() {
        pendingStart = false
        guard let pm = peripheralManager else { return }
        pm.stopAdvertising()
        pm.removeAllServices()
        connectedCentrals.removeAll()
        mtuByCentral.removeAll()
        mutexByCentral.removeAll()
        notifyCharacteristic = nil
        serviceAdded = false
        managerReady = false
        stateSubject.send(.idle)
        onLog("BLE bridge stopped")
    }

    public func isOperational() -> Bool {
        guard let pm = peripheralManager else { return false }
        return pm.state == .poweredOn && (pm.isAdvertising || !connectedCentrals.isEmpty)
    }

    /// Send a JSON response string to a connected central.
    public func sendJson(
        _ jsonMessage: String,
        targetAddress: String? = nil,
        highPriority: Bool = false
    ) async throws {
        guard let pm = peripheralManager, managerReady else {
            throw BLEError.serverNotReady
        }

        let central: CBCentral
        if let addr = targetAddress, let c = connectedCentrals[addr] {
            central = c
        } else {
            guard let first = connectedCentrals.values.first else {
                throw BLEError.noConnectedCentral
            }
            central = first
        }

        let address = central.identifier.uuidString
        let lock = mutexByCentral[address] ?? NSLock()
        mutexByCentral[address] = lock

        let mtu = mtuByCentral[address] ?? BLEConstants.defaultAttMtu
        let mtuPayloadMax = max(BLEConstants.defaultMaxPacketSize, mtu - 3)
        let maxPacketSize = min(BLEConstants.maxGattAttributeValueBytes, mtuPayloadMax)

        var payload = jsonMessage.data(using: .utf8) ?? Data()
        if payload.count >= BLEConstants.jsonGzipThresholdBytes {
            payload = Self.gzipMagic + (try (payload as NSData).compressed(using: .zlib) as Data)
        }

        let transportId = transportIds.next()
        var packets = try FrameCodec.encode(transportId: transportId, payload: payload, maxPacketSize: maxPacketSize)

        let multiClient = connectedCentrals.count > 1
        let throttleEvery: Int
        if highPriority || packets.count <= 12 {
            throttleEvery = 0
        } else if multiClient && packets.count > 140 {
            throttleEvery = BLEConstants.throttleMultiClientEvery
        } else if multiClient {
            throttleEvery = BLEConstants.throttleSingleClientEvery
        } else if packets.count > 180 {
            throttleEvery = 14
        } else if packets.count > 90 {
            throttleEvery = 10
        } else {
            throttleEvery = BLEConstants.throttleMultiClientEvery
        }

        lock.lock()
        defer { lock.unlock() }

        for (idx, packet) in packets.enumerated() {
            _ = pm.updateValue(packet, for: notifyCharacteristic!, onSubscribedCentrals: [central])
            if throttleEvery > 0 && (idx + 1) % throttleEvery == 0 {
                try await Task.sleep(nanoseconds: BLEConstants.throttleDelayMs * 1_000_000)
            }
        }
    }

    /// Send raw bytes (binary pong) to a central.
    public func sendRawBytes(_ data: Data, targetAddress: String? = nil) {
        guard let pm = peripheralManager, managerReady else { return }
        let central: CBCentral
        if let addr = targetAddress, let c = connectedCentrals[addr] {
            central = c
        } else {
            guard let first = connectedCentrals.values.first else { return }
            central = first
        }
        _ = pm.updateValue(data, for: notifyCharacteristic!, onSubscribedCentrals: [central])
    }

    // MARK: - Prompt bundle helpers
    private func isPromptBundle(_ data: Data) -> Bool {
        data.count >= Self.pbHeaderBytes && data[0..<4] == Self.promptBundleMagic
    }

    private func decodePromptBundleToJson(_ data: Data) throws -> String {
        var header = data
        let magic = header[0..<4]
        guard magic == Self.promptBundleMagic else { throw BLEError.invalidBundle }

        let flags = header[4]
        let metadataLen = Int(UInt32(bigEndian: header.withUnsafeBytes { $0.load(fromByteOffset: 5, as: UInt32.self) }))
        let imageLen = Int(UInt32(bigEndian: header.withUnsafeBytes { $0.load(fromByteOffset: 9, as: UInt32.self) }))

        let expectedSize = Self.pbHeaderBytes + metadataLen + imageLen
        guard data.count == expectedSize else { throw BLEError.bundleSizeMismatch }

        var metadata = data[Self.pbHeaderBytes..<(Self.pbHeaderBytes + metadataLen)]
        if (flags & BLEConstants.promptBundleFlagGzipMetadata) != 0 {
            metadata = try (metadata as NSData).decompressed(using: .zlib) as Data
        }

        guard var json = try JSONSerialization.jsonObject(with: metadata) as? [String: Any] else {
            throw BLEError.invalidBundle
        }

        if imageLen > 0 {
            let imageBytes = data[Self.pbHeaderBytes + metadataLen..<Self.pbHeaderBytes + metadataLen + imageLen]
            let b64 = imageBytes.base64EncodedString()
            json["imageBase64"] = b64
            json["imageMimeType"] = json["imageMimeType"] ?? "image/png"
        }

        let result = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])
        return String(data: result, encoding: .utf8) ?? ""
    }

    // MARK: - Handle incoming data
    private func handleWrite(_ request: CBATTRequest) async {
        guard request.characteristic.uuid == BLEConstants.writeCharUUID else { return }
        let address = request.central.identifier.uuidString
        let data = request.value ?? Data()

        // Binary ping/pong (8 bytes)
        if data.count == BLEConstants.binaryFrameSize
            && data[0] == BLEConstants.binaryFrameMagic[0]
            && data[1] == BLEConstants.binaryFrameMagic[1] {
            let frameType = data[2]
            if frameType == BLEConstants.binaryPingType {
                let tsMs = BLEConstants.parseBinaryTimestamp(data)
                let pong = BLEConstants.buildBinaryPong(tsMs: tsMs)
                sendRawBytes(pong, targetAddress: address)
            }
            return
        }

        do {
            let frame = try FrameCodec.decode(data)
            if let completePayload = try await frameAssembler.addFrame(frame) {
                await processCompletePayload(completePayload, from: address)
            }
        } catch {
            onLog("Frame decode error: \(error.localizedDescription)")
        }
    }

    private func processCompletePayload(_ payload: Data, from address: String) async {
        let json: String
        if payload.count >= 3 && payload[0] == Character("g").asciiValue!
            && payload[1] == Character("z").asciiValue! && payload[2] == 0x01 {
            let compressed = payload.dropFirst(3)
            do {
                let decompressed = try (compressed as NSData).decompressed(using: .zlib) as Data
                json = String(data: decompressed, encoding: .utf8) ?? ""
            } catch {
                onLog("Decompression failed: \(error.localizedDescription)")
                return
            }
        } else if isPromptBundle(payload) {
            do {
                json = try decodePromptBundleToJson(payload)
            } catch {
                onLog("Prompt bundle decode failed: \(error.localizedDescription)")
                return
            }
        } else {
            json = String(data: payload, encoding: .utf8) ?? ""
        }

        guard !json.isEmpty else { return }
        onLog("Received request (\(json.count) chars) from \(address)")
        await onPrompt(json, address)
    }
}

// MARK: - CBPeripheralManagerDelegate
extension BLEServerManager: CBPeripheralManagerDelegate {
    public nonisolated func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        Task { await handleStateChange(peripheral.state) }
    }

    private func handleStateChange(_ state: CBManagerState) {
        switch state {
        case .poweredOn:
            onLog("Bluetooth powered on")
            managerReady = true
            if pendingStart { setupServiceAndAdvertise() }
        case .poweredOff:
            onLog("Bluetooth powered off")
            managerReady = false
            stateSubject.send(.error("Bluetooth off"))
        case .unauthorized:
            onLog("Bluetooth unauthorized")
            managerReady = false
            stateSubject.send(.error("Bluetooth unauthorized"))
        case .unsupported:
            onLog("Bluetooth unsupported")
            managerReady = false
            stateSubject.send(.error("Bluetooth unsupported"))
        default:
            break
        }
    }

    private func setupServiceAndAdvertise() {
        guard let pm = peripheralManager else { return }
        stateSubject.send(.starting)

        let service = CBMutableService(type: BLEConstants.serviceUUID, primary: true)

        let writeChar = CBMutableCharacteristic(
            type: BLEConstants.writeCharUUID,
            properties: [.write, .writeWithoutResponse],
            value: nil,
            permissions: .writeable
        )

        let notifyChar = CBMutableCharacteristic(
            type: BLEConstants.notifyCharUUID,
            properties: [.notify],
            value: nil,
            permissions: .readable
        )

        let cccd = CBMutableDescriptor(type: BLEConstants.cccdUUID, value: nil)
        notifyChar.descriptors = [cccd]
        service.characteristics = [writeChar, notifyChar]
        pm.add(service)
        self.notifyCharacteristic = notifyChar
    }

    public nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Swift.Error?) {
        Task {
            if let err = error {
                onLog("Failed to add service: \(err.localizedDescription)")
                stateSubject.send(.error(err.localizedDescription))
                return
            }
            onLog("GATT service added")
            startAdvertising()
        }
    }

    private func startAdvertising() {
        guard let pm = peripheralManager else { return }
        let data: [String: Any] = [
            CBAdvertisementDataServiceUUIDsKey: [BLEConstants.serviceUUID],
            CBAdvertisementDataLocalNameKey: "Gemini AirBridge"
        ]
        pm.startAdvertising(data)
        stateSubject.send(.advertising)
        onLog("BLE advertising started")
    }

    public nonisolated func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Swift.Error?) {
        Task {
            if let err = error {
                onLog("Advertising failed: \(err.localizedDescription)")
                stateSubject.send(.error(err.localizedDescription))
            } else {
                stateSubject.send(.advertising)
            }
        }
    }

    // MARK: - Write requests
    public nonisolated func peripheralManager(
        _ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]
    ) {
        Task {
            for request in requests {
                await handleWrite(request)
            }
        }
    }

    // MARK: - Connection state
    public nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral,
                                              didSubscribeTo characteristic: CBCharacteristic) {
        Task {
            let address = central.identifier.uuidString
            connectedCentrals[address] = central
            mtuByCentral[address] = central.maximumUpdateValueLength
            mutexByCentral[address] = NSLock()
            onLog("Central subscribed: \(address)")
            stateSubject.send(.connected(count: connectedCentrals.count))
        }
    }

    public nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral,
                                              didUnsubscribeFrom characteristic: CBCharacteristic) {
        Task {
            let address = central.identifier.uuidString
            connectedCentrals.removeValue(forKey: address)
            mtuByCentral.removeValue(forKey: address)
            mutexByCentral.removeValue(forKey: address)
            onLog("Central unsubscribed: \(address)")
            if connectedCentrals.isEmpty {
                stateSubject.send(.advertising)
            } else {
                stateSubject.send(.connected(count: connectedCentrals.count))
            }
        }
    }
}