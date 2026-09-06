import Foundation
import CoreBluetooth
import Combine

// MARK: - BLE Delegate Coordinator (non-actor, @objc-compatible)
/// Bridges CBPeripheralManagerDelegate callbacks to the actor-isolated BLEServerManager.
final class BLEPeripheralDelegate: NSObject, CBPeripheralManagerDelegate {
    weak var owner: BLEServerManager?

    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        owner?.handleStateChange(peripheral.state)
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        if let err = error { owner?.log("Failed to add service: \(err.localizedDescription)"); owner?.subject.send(.error(err.localizedDescription)); return }
        owner?.log("GATT service added")
        owner?.startAdvertising()
    }

    func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        if let err = error { owner?.log("Advertising failed: \(err.localizedDescription)"); owner?.subject.send(.error(err.localizedDescription)) }
        else { owner?.subject.send(.advertising) }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        owner?.handleWrites(requests)
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        owner?.addCentral(central)
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        owner?.removeCentral(central)
    }
}

// MARK: - BLEServerManager (actor, state)
public actor BLEServerManager {
    public typealias PromptHandler = @Sendable (_ json: String, _ address: String) async -> Void
    public typealias LogHandler = @Sendable (String) -> Void

    public enum State: Sendable, Equatable {
        case idle, starting, advertising, connected(count: Int), error(String)
    }

    public enum BLEError: Swift.Error, LocalizedError {
        case serverNotReady, noConnectedCentral, invalidBundle, bundleSizeMismatch
        public var errorDescription: String? {
            switch self {
            case .serverNotReady: return "BLE bridge not ready"
            case .noConnectedCentral: return "No connected BLE central"
            case .invalidBundle: return "Invalid prompt bundle"
            case .bundleSizeMismatch: return "Prompt bundle size mismatch"
            }
        }
    }

    private var pm: CBPeripheralManager?
    private var delegateCoordinator: BLEPeripheralDelegate?
    private var notifyChar: CBMutableCharacteristic?

    private var centrals: [String: CBCentral] = [:]
    private var mtu: [String: Int] = [:]
    private let sendQ = DispatchQueue(label: "com.geminiairbridge.ble.send")

    private let assembler = FrameAssembler()
    private let tids = TransportIdGenerator()

    private let onPrompt: PromptHandler
    private let onLog: LogHandler

    private var ready = false
    private var pendingStart = false

    let subject = PassthroughSubject<State, Never>()
    public nonisolated var statePublisher: AnyPublisher<State, Never> { subject.eraseToAnyPublisher() }

    private static let gzMagic = Data("gz".utf8) + Data([0x01])
    private static let pbMagic = Data("bgp2".utf8)
    private static let pbHead = 13

    public init(onPrompt: @escaping PromptHandler, onLog: @escaping LogHandler) {
        self.onPrompt = onPrompt
        self.onLog = onLog
    }

    // MARK: - Public
    public func start() {
        guard !ready else { return }
        pendingStart = true
        let d = BLEPeripheralDelegate()
        d.owner = self
        delegateCoordinator = d
        pm = CBPeripheralManager(delegate: d, queue: nil, options: [
            CBPeripheralManagerOptionShowPowerAlertKey: true,
            CBPeripheralManagerOptionRestoreIdentifierKey: "com.mncompany.geminiairbridge.ble"
        ])
    }

    public func stop() {
        pendingStart = false
        pm?.stopAdvertising()
        pm?.removeAllServices()
        pm = nil; delegateCoordinator = nil; notifyChar = nil
        centrals.removeAll(); mtu.removeAll(); ready = false
        subject.send(.idle); log("BLE bridge stopped")
    }

    public func isOperational() -> Bool {
        pm?.state == .poweredOn && (pm?.isAdvertising ?? false || !centrals.isEmpty)
    }

    public func sendJson(_ msg: String, targetAddress: String? = nil, highPriority: Bool = false) async throws {
        guard let pm, ready else { throw BLEError.serverNotReady }
        let central: CBCentral
        if let a = targetAddress, let c = centrals[a] { central = c }
        else { guard let f = centrals.values.first else { throw BLEError.noConnectedCentral }; central = f }

        let maxSize = min(BLEConstants.maxGattAttributeValueBytes, max(BLEConstants.defaultMaxPacketSize, (mtu[central.identifier.uuidString] ?? BLEConstants.defaultAttMtu) - 3))
        var payload = msg.data(using: .utf8) ?? Data()
        if payload.count >= BLEConstants.jsonGzipThresholdBytes {
            payload = Self.gzMagic + (try (payload as NSData).compressed(using: .zlib) as Data)
        }
        let pkts = try FrameCodec.encode(transportId: tids.next(), payload: payload, maxPacketSize: maxSize)
        let mc = centrals.count > 1
        let throttle: Int
        if highPriority || pkts.count <= 12 { throttle = 0 }
        else if mc && pkts.count > 140 { throttle = BLEConstants.throttleMultiClientEvery }
        else if mc { throttle = BLEConstants.throttleSingleClientEvery }
        else if pkts.count > 180 { throttle = 14 }
        else if pkts.count > 90 { throttle = 10 }
        else { throttle = BLEConstants.throttleMultiClientEvery }

        for (i, pkt) in pkts.enumerated() {
            sendQ.sync { _ = pm.updateValue(pkt, for: notifyChar!, onSubscribedCentrals: [central]) }
            if throttle > 0 && (i + 1) % throttle == 0 { try await Task.sleep(nanoseconds: BLEConstants.throttleDelayMs * 1_000_000) }
        }
    }

    public func sendRaw(_ data: Data, targetAddress: String? = nil) {
        guard let pm, ready else { return }
        let central: CBCentral
        if let a = targetAddress, let c = centrals[a] { central = c }
        else { guard let f = centrals.values.first else { return }; central = f }
        sendQ.sync { _ = pm.updateValue(data, for: notifyChar!, onSubscribedCentrals: [central]) }
    }

    // MARK: - Internal (called from delegate)
    func handleStateChange(_ state: CBManagerState) {
        switch state {
        case .poweredOn: log("Bluetooth on"); ready = true; if pendingStart { setup() }
        case .poweredOff: log("Bluetooth off"); ready = false; subject.send(.error("Bluetooth off"))
        case .unauthorized: log("Bluetooth unauthorized"); subject.send(.error("Unauthorized"))
        case .unsupported: log("Bluetooth unsupported"); subject.send(.error("Unsupported"))
        default: break
        }
    }

    func setup() {
        guard let pm else { return }
        subject.send(.starting)
        let svc = CBMutableService(type: BLEConstants.serviceUUID, primary: true)
        let wc = CBMutableCharacteristic(type: BLEConstants.writeCharUUID, properties: [.write, .writeWithoutResponse], value: nil, permissions: .writeable)
        let nc = CBMutableCharacteristic(type: BLEConstants.notifyCharUUID, properties: [.notify], value: nil, permissions: .readable)
        nc.descriptors = [CBMutableDescriptor(type: BLEConstants.cccdUUID, value: nil)]
        svc.characteristics = [wc, nc]
        pm.add(svc)
        notifyChar = nc
    }

    func startAdvertising() {
        pm?.startAdvertising([CBAdvertisementDataServiceUUIDsKey: [BLEConstants.serviceUUID], CBAdvertisementDataLocalNameKey: "Gemini AirBridge"])
        subject.send(.advertising); log("BLE advertising")
    }

    func handleWrites(_ requests: [CBATTRequest]) {
        for req in requests {
            let addr = req.central.identifier.uuidString
            guard let data = req.value else { continue }

            // Binary ping/pong
            if data.count == BLEConstants.binaryFrameSize && data[0] == 0xFE && data[1] == 0xFD {
                if data[2] == BLEConstants.binaryPingType {
                    sendRaw(BLEConstants.buildBinaryPong(tsMs: BLEConstants.parseBinaryTimestamp(data)), targetAddress: addr)
                }
                continue
            }

            Task {
                do {
                    let frame = try FrameCodec.decode(data)
                    if let payload = try await assembler.addFrame(frame) {
                        await processPayload(payload, addr: addr)
                    }
                } catch { self.log("Frame decode: \(error.localizedDescription)") }
            }
        }
    }

    func addCentral(_ c: CBCentral) {
        let addr = c.identifier.uuidString
        centrals[addr] = c; mtu[addr] = c.maximumUpdateValueLength
        log("Connected: \(addr)"); subject.send(.connected(count: centrals.count))
    }

    func removeCentral(_ c: CBCentral) {
        let addr = c.identifier.uuidString
        centrals.removeValue(forKey: addr); mtu.removeValue(forKey: addr)
        log("Disconnected: \(addr)")
        subject.send(centrals.isEmpty ? .advertising : .connected(count: centrals.count))
    }

    private func processPayload(_ payload: Data, addr: String) async {
        let json: String
        if payload.count >= 3 && payload[0] == 103 && payload[1] == 122 && payload[2] == 1 {
            do { let d = try (payload.dropFirst(3) as NSData).decompressed(using: .zlib) as Data; json = String(data: d, encoding: .utf8) ?? "" }
            catch { log("Decompress fail"); return }
        } else if payload.count >= Self.pbHead && payload[0..<4] == Self.pbMagic {
            json = decodeBundle(payload)
        } else { json = String(data: payload, encoding: .utf8) ?? "" }
        guard !json.isEmpty else { return }
        log("Request \(json.count)B from \(addr)")
        await onPrompt(json, addr)
    }

    private func decodeBundle(_ data: Data) -> String {
        // Simplified: just extract metadata JSON with base64 image appended
        do {
            let flags = data[4]
            let metaLen = Int(UInt32(bigEndian: data.withUnsafeBytes { $0.load(fromByteOffset: 5, as: UInt32.self) }))
            let imgLen = Int(UInt32(bigEndian: data.withUnsafeBytes { $0.load(fromByteOffset: 9, as: UInt32.self) }))
            var meta = data[Self.pbHead..<(Self.pbHead + metaLen)]
            if (flags & BLEConstants.promptBundleFlagGzipMetadata) != 0 {
                meta = try (meta as NSData).decompressed(using: .zlib) as Data
            }
            guard var obj = try JSONSerialization.jsonObject(with: meta) as? [String: Any] else { return "" }
            if imgLen > 0 {
                let img = data[Self.pbHead + metaLen..<Self.pbHead + metaLen + imgLen]
                obj["imageBase64"] = img.base64EncodedString()
                obj["imageMimeType"] = obj["imageMimeType"] ?? "image/png"
            }
            return String(data: try JSONSerialization.data(withJSONObject: obj, options: .sortedKeys), encoding: .utf8) ?? ""
        } catch { log("Bundle decode error"); return "" }
    }

    func log(_ msg: String) { onLog(msg) }
}
