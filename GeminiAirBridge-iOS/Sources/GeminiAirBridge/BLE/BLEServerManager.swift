import Foundation
import CoreBluetooth
import Combine

// MARK: - BLE Delegate (plain class, @objc-compatible)
final class BLEPeripheralDelegate: NSObject, CBPeripheralManagerDelegate {
    weak var owner: BLEServerManager?

    func peripheralManagerDidUpdateState(_ peripheral: CBPeripheralManager) {
        let s = peripheral.state
        Task { await owner?.handleStateChange(s) }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didAdd service: CBService, error: Error?) {
        if let err = error {
            Task { await owner?.log("Add service failed: \(err.localizedDescription)"); await owner?.emit(.error(err.localizedDescription)) }
            return
        }
        Task { await owner?.log("GATT service added"); await owner?.adv() }
    }

    func peripheralManagerDidStartAdvertising(_ peripheral: CBPeripheralManager, error: Error?) {
        Task {
            if let err = error { await owner?.log("Adv failed: \(err.localizedDescription)"); await owner?.emit(.error(err.localizedDescription)) }
            else { await owner?.emit(.advertising) }
        }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, didReceiveWrite requests: [CBATTRequest]) {
        Task { await owner?.handleWrites(requests) }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didSubscribeTo characteristic: CBCharacteristic) {
        Task { await owner?.addC(central) }
    }

    func peripheralManager(_ peripheral: CBPeripheralManager, central: CBCentral, didUnsubscribeFrom characteristic: CBCharacteristic) {
        Task { await owner?.removeC(central) }
    }
}

// MARK: - BLEServerManager (actor)
public actor BLEServerManager {
    public typealias PromptHandler = @Sendable (_ json: String, _ address: String) async -> Void
    public typealias LogHandler = @Sendable (String) -> Void

    public enum State: Sendable, Equatable { case idle, starting, advertising, connected(count: Int), error(String) }
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

    private var pm: CBPeripheralManager?; private var nc: CBMutableCharacteristic?
    private var cents: [String: CBCentral] = [:]; private var mtuVals: [String: Int] = [:]
    private let sq = DispatchQueue(label: "com.geminiairbridge.ble.send")
    private let asm = FrameAssembler(); private let tids = TransportIdGenerator()
    private let onP: PromptHandler; private let onL: LogHandler
    private var ready = false; private var pending = false

    fileprivate let subj = PassthroughSubject<State, Never>()
    public nonisolated var statePublisher: AnyPublisher<State, Never> { subj.eraseToAnyPublisher() }
    fileprivate func emit(_ s: State) { subj.send(s) }

    public init(onPrompt: @escaping PromptHandler, onLog: @escaping LogHandler) { onP = onPrompt; onL = onLog }

    public func start() {
        guard !ready else { return }; pending = true
        let d = BLEPeripheralDelegate(); d.owner = self
        pm = CBPeripheralManager(delegate: d, queue: nil, options: [
            CBPeripheralManagerOptionShowPowerAlertKey: true,
            CBPeripheralManagerOptionRestoreIdentifierKey: "com.mncompany.geminiairbridge.ble"
        ])
    }

    public func stop() {
        pending = false; pm?.stopAdvertising(); pm?.removeAllServices()
        pm = nil; nc = nil; cents.removeAll(); mtuVals.removeAll(); ready = false
        emit(.idle); onL("BLE bridge stopped")
    }

    public func isOperational() -> Bool { pm?.state == .poweredOn && (pm?.isAdvertising ?? false || !cents.isEmpty) }

    public func sendJson(_ msg: String, targetAddress: String? = nil, highPriority: Bool = false) async throws {
        guard let pm, ready else { throw BLEError.serverNotReady }
        let c: CBCentral
        if let a = targetAddress, let c2 = cents[a] { c = c2 }
        else { guard let f = cents.values.first else { throw BLEError.noConnectedCentral }; c = f }

        let maxPkt = min(BLEConstants.maxGattAttributeValueBytes, max(BLEConstants.defaultMaxPacketSize, (mtuVals[c.identifier.uuidString] ?? BLEConstants.defaultAttMtu) - 3))
        var pl = msg.data(using: .utf8) ?? Data()
        if pl.count >= 900 { pl = Data("gz\u{01}".utf8) + (try (pl as NSData).compressed(using: .zlib) as Data) }
        let pkts = try FrameCodec.encode(transportId: tids.next(), payload: pl, maxPacketSize: maxPkt)
        let mc = cents.count > 1
        let thr: Int
        if highPriority || pkts.count <= 12 { thr = 0 } else if mc && pkts.count > 140 { thr = 6 } else if mc { thr = 4 }
        else if pkts.count > 180 { thr = 14 } else if pkts.count > 90 { thr = 10 } else { thr = 6 }

        for (i, p) in pkts.enumerated() {
            sq.sync { _ = pm.updateValue(p, for: nc!, onSubscribedCentrals: [c]) }
            if thr > 0 && (i + 1) % thr == 0 { try await Task.sleep(nanoseconds: 1_000_000) }
        }
    }

    public func sendRaw(_ data: Data, targetAddress: String? = nil) {
        guard let pm, ready else { return }
        let c: CBCentral
        if let a = targetAddress, let c2 = cents[a] { c = c2 }
        else { guard let f = cents.values.first else { return }; c = f }
        sq.sync { _ = pm.updateValue(data, for: nc!, onSubscribedCentrals: [c]) }
    }

    // MARK: - Called via delegate → Task bridging
    fileprivate func handleStateChange(_ state: CBManagerState) {
        switch state {
        case .poweredOn: onL("Bluetooth on"); ready = true; if pending { setup() }
        case .poweredOff: onL("Bluetooth off"); ready = false; emit(.error("Bluetooth off"))
        case .unauthorized: onL("Bluetooth unauthorized"); emit(.error("Unauthorized"))
        case .unsupported: onL("Bluetooth unsupported"); emit(.error("Unsupported"))
        default: break
        }
    }

    fileprivate func setup() {
        guard let pm else { return }; emit(.starting)
        let s = CBMutableService(type: BLEConstants.serviceUUID, primary: true)
        let w = CBMutableCharacteristic(type: BLEConstants.writeCharUUID, properties: [.write, .writeWithoutResponse], value: nil, permissions: .writeable)
        let n = CBMutableCharacteristic(type: BLEConstants.notifyCharUUID, properties: [.notify], value: nil, permissions: .readable)
        n.descriptors = [CBMutableDescriptor(type: BLEConstants.cccdUUID, value: nil)]
        s.characteristics = [w, n]; pm.add(s); nc = n
    }

    fileprivate func adv() {
        pm?.startAdvertising([CBAdvertisementDataServiceUUIDsKey: [BLEConstants.serviceUUID], CBAdvertisementDataLocalNameKey: "Gemini AirBridge"])
        emit(.advertising); onL("BLE advertising")
    }

    fileprivate func handleWrites(_ requests: [CBATTRequest]) {
        for req in requests {
            let addr = req.central.identifier.uuidString
            guard let d = req.value else { continue }
            if d.count == 8 && d[0] == 0xFE && d[1] == 0xFD {
                if d[2] == 1 { sendRaw(BLEConstants.buildBinaryPong(tsMs: BLEConstants.parseBinaryTimestamp(d)), targetAddress: addr) }
                continue
            }
            Task {
                do { let f = try FrameCodec.decode(d); if let p = try await asm.addFrame(f) { await process(p, addr: addr) } }
                catch { onL("Frame err: \(error.localizedDescription)") }
            }
        }
    }

    fileprivate func addC(_ c: CBCentral) {
        let a = c.identifier.uuidString; cents[a] = c; mtuVals[a] = c.maximumUpdateValueLength
        onL("Connected: \(a)"); emit(.connected(count: cents.count))
    }

    fileprivate func removeC(_ c: CBCentral) {
        let a = c.identifier.uuidString; cents.removeValue(forKey: a); mtuVals.removeValue(forKey: a)
        onL("Disconnected: \(a)"); emit(cents.isEmpty ? .advertising : .connected(count: cents.count))
    }

    private func process(_ payload: Data, addr: String) async {
        let json: String
        if payload.count >= 3 && payload[0] == 103 && payload[1] == 122 && payload[2] == 1 {
            do { let d = try (payload.dropFirst(3) as NSData).decompressed(using: .zlib) as Data; json = String(data: d, encoding: .utf8) ?? "" }
            catch { onL("Decompress fail"); return }
        } else if payload.count >= 13 && payload[0..<4] == Data("bgp2".utf8) {
            json = decodeBundle(payload)
        } else { json = String(data: payload, encoding: .utf8) ?? "" }
        guard !json.isEmpty else { return }
        onL("Request \(json.count)B from \(addr)"); await onP(json, addr)
    }

    private func decodeBundle(_ d: Data) -> String {
        do {
            let f = d[4]; let mL = Int(UInt32(bigEndian: d.withUnsafeBytes { $0.load(fromByteOffset: 5, as: UInt32.self) }))
            let iL = Int(UInt32(bigEndian: d.withUnsafeBytes { $0.load(fromByteOffset: 9, as: UInt32.self) }))
            var m = d[13..<(13 + mL)]
            if (f & 1) != 0 { m = try (m as NSData).decompressed(using: .zlib) as Data }
            guard var o = try JSONSerialization.jsonObject(with: m) as? [String: Any] else { return "" }
            if iL > 0 { let img = d[(13 + mL)..<(13 + mL + iL)]; o["imageBase64"] = img.base64EncodedString(); o["imageMimeType"] = o["imageMimeType"] ?? "image/png" }
            return String(data: try JSONSerialization.data(withJSONObject: o, options: .sortedKeys), encoding: .utf8) ?? ""
        } catch { onL("Bundle decode err"); return "" }
    }

    fileprivate func log(_ m: String) { onL(m) }
}
