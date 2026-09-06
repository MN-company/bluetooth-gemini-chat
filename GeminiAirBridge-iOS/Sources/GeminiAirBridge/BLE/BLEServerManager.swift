import Foundation
import CoreBluetooth
import Combine
import OSLog

public actor BLEServerManager: NSObject {
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

    private var peripheralManager: CBPeripheralManager?
    private var notifyCharacteristic: CBMutableCharacteristic?

    private var connectedCentrals: [String: CBCentral] = [:]
    private var mtuByCentral: [String: Int] = [:]
    private let sendQueue = DispatchQueue(label: "com.geminiairbridge.ble.send")

    private let frameAssembler = FrameAssembler()
    private let transportIds = TransportIdGenerator()

    private let onPrompt: PromptHandler
    private let onLog: LogHandler

    private var managerReady = false
    private var pendingStart = false

    private let stateSubject = PassthroughSubject<State, Never>()
    public nonisolated var statePublisher: AnyPublisher<State, Never> {
        stateSubject.eraseToAnyPublisher()
    }

    private func emitState(_ state: State) {
        stateSubject.send(state)
    }

    private static let gzipMagic = Data("gz".utf8) + Data([0x01])
    private static let promptBundleMagic = Data("bgp2".utf8)
    private static let pbHeaderBytes = 13

    public init(onPrompt: @escaping PromptHandler, onLog: @escaping LogHandler) {
        self.onPrompt = onPrompt
        self.onLog = onLog
        super.init()
    }

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
        notifyCharacteristic = nil
        managerReady = false
        emitState(.idle)
        onLog("BLE bridge stopped")
    }

    public func isOperational() -> Bool {
        guard let pm = peripheralManager else { return false }
        return pm.state == .poweredOn && (pm.isAdvertising || !connectedCentrals.isEmpty)
    }

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

        let mtu = mtuByCentral[central.identifier.uuidString] ?? BLEConstants.defaultAttMtu
        let mtuPayloadMax = max(BLEConstants.defaultMaxPacketSize, mtu - 3)
        let maxPacketSize = min(BLEConstants.maxGattAttributeValueBytes, mtuPayloadMax)

        var payload = jsonMessage.data(using: .utf8) ?? Data()
        if payload.count >= BLEConstants.jsonGzipThresholdBytes {
            payload = Self.gzipMagic + (try (payload as NSData).compressed(using: .zlib) as Data)
        }

        let transportId = transportIds.next()
        let packets = try FrameCodec.encode(transportId: transportId, payload: payload, maxPacketSize: maxPacketSize)

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

        for (idx, packet) in packets.enumerated() {
            sendQueue.sync {
                _ = pm.updateValue(packet, for: notifyCharacteristic!, onSubscribedCentrals: [central])
            }
            if throttleEvery > 0 && (idx + 1) % throttleEvery == 0 {
                try await Task.sleep(nanoseconds: BLEConstants.throttleDelayMs * 1_000_000)
            }
        }
    }

    public func sendRawBytes(_ data: Data, targetAddress: String? = nil) {
        guard let pm = peripheralManager, managerReady else { return }
        let central: CBCentral
        if let addr = targetAddress, let c = connectedCentrals[addr] {
            central = c
        } else {
            guard let first = connectedCentrals.values.first else { return }
            central = first
        }
        sendQueue.sync {
            _ = pm.updateValue(data, for: notifyCharacteristic!, onSubscribedCentrals: [central])
        }
    }

    private func isPromptBundle(_ data: Data) -> Bool {
        data.count >= Self.pbHeaderBytes && data[0..<4] == Self.promptBundleMagic
    }

    private func decodePromptBundleToJson(_ data: Data) throws -> String {
        let magic = data[0..<4]
        guard magic == Self.promptBundleMagic else { throw BLEError.invalidBundle }
        let flags = data[4]
        let metadataLen = Int(UInt32(bigEndian: data.withUnsafeBytes { $0.load(fromByteOffset: 5, as: UInt32.self) }))
        let imageLen = Int(UInt32(bigEndian: data.withUnsafeBytes { $0.load(fromByteOffset: 9, as: UInt32.self) }))
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
            json["imageBase64"] = imageBytes.base64EncodedString()
            json["imageMimeType"] = json["imageMimeType"] ?? "image/png"
        }
        let result = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])
        return String(data: result, encoding: .utf8) ?? ""
    }

    private func handleWrite(_ request: CBATTRequest) async {
        let address = request.central.identifier.uuidString
        let data = request.value ?? Data()
        if data.count == BLEConstants.binaryFrameSize
            && data[0] == BLEConstants.binaryFrameMagic[0]
            && data[1] == BLEConstants.binaryFrameMagic[1] {
            if data[2] == BLEConstants.binaryPingType {
                let tsMs = BLEConstants.parseBinaryTimestamp(data)
                sendRawBytes(BLEConstants.buildBinaryPong(tsMs: tsMs), targetAddress: address)
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
        if payload.count >= 3 && payload[0] == 103 && payload[1] == 122 && payload[2] == 0x01 {
            let compressed = payload.dropFirst(3)
            do {
                let d = try (compressed as NSData).decompressed(using: .zlib) as Data
                json = String(data: d, encoding: .utf8) ?? ""
            } catch { onLog("Decompression failed: \(error.localizedDescription)"); return }
        } else if isPromptBundle(payload) {
            do { json = try decodePromptBundleToJson(payload) }
            catch { onLog("Bundle decode failed: \(error.localizedDescription)"); return }
        } else {
            json = String(data: payload, encoding: .utf8) ?? ""
        }
        guard !json.isEmpty else { return }
        onLog("Received request (\(json.count) chars) from \(address)")
        await onPrompt(json, address)
    }

    // MARK: - Nonisolated delegation
    nonisolated public func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        Task { await self._handleStateChange(peripheral.state) }
    }

    private func _handleStateChange(_ state: CBManagerState) {
        switch state {
        case .poweredOn:
            onLog("Bluetooth powered on"); managerReady = true
            if pendingStart { _setupService() }
        case .poweredOff:
            onLog("Bluetooth powered off"); managerReady = false; emitState(.error("Bluetooth off"))
        case .unauthorized:
            onLog("Bluetooth unauthorized"); managerReady = false; emitState(.error("Bluetooth unauthorized"))
        case .unsupported:
            onLog("Bluetooth unsupported"); managerReady = false; emitState(.error("Bluetooth unsupported"))
        default: break
        }
    }

    private func _setupService() {
        guard let pm = peripheralManager else { return }
        emitState(.starting)
        let service = CBMutableService(type: BLEConstants.serviceUUID, primary: true)
        let writeChar = CBMutableCharacteristic(type: BLEConstants.writeCharUUID, properties: [.write, .writeWithoutResponse], value: nil, permissions: .writeable)
        let notifyChar = CBMutableCharacteristic(type: BLEConstants.notifyCharUUID, properties: [.notify], value: nil, permissions: .readable)
        notifyChar.descriptors = [CBMutableDescriptor(type: BLEConstants.cccdUUID, value: nil)]
        service.characteristics = [writeChar, notifyChar]
        pm.add(service)
        self.notifyCharacteristic = notifyChar
    }

    public nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Swift.Error?) {
        Task {
            if let err = error {
                await self.onLog("Failed to add service: \(err.localizedDescription)")
                await self.emitState(.error(err.localizedDescription))
                return
            }
            await self.onLog("GATT service added")
            await self._startAdvertising()
        }
    }

    private func _startAdvertising() {
        guard let pm = peripheralManager else { return }
        pm.startAdvertising([
            CBAdvertisementDataServiceUUIDsKey: [BLEConstants.serviceUUID],
            CBAdvertisementDataLocalNameKey: "Gemini AirBridge"
        ])
        emitState(.advertising)
        onLog("BLE advertising started")
    }

    public nonisolated func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Swift.Error?) {
        Task {
            if let err = error {
                await self.onLog("Advertising failed: \(err.localizedDescription)")
                await self.emitState(.error(err.localizedDescription))
            } else {
                await self.emitState(.advertising)
            }
        }
    }

    public nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        Task { for r in requests { await self.handleWrite(r) } }
    }

    public nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        Task { await self._addCentral(address: central.identifier.uuidString, central: central) }
    }

    private func _addCentral(address: String, central: CBCentral) {
        connectedCentrals[address] = central
        mtuByCentral[address] = central.maximumUpdateValueLength
        onLog("Central subscribed: \(address)")
        emitState(.connected(count: connectedCentrals.count))
    }

    public nonisolated func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        Task { await self._removeCentral(address: central.identifier.uuidString) }
    }

    private func _removeCentral(address: String) {
        connectedCentrals.removeValue(forKey: address)
        mtuByCentral.removeValue(forKey: address)
        onLog("Central unsubscribed: \(address)")
        emitState(connectedCentrals.isEmpty ? .advertising : .connected(count: connectedCentrals.count))
    }
}
