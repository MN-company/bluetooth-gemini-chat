import Foundation
import Combine
import OSLog

@MainActor
public final class BridgeService: ObservableObject {
    public static let shared = BridgeService()

    @Published public private(set) var status: String = "Idle"
    @Published public private(set) var bleState: BLEServerManager.State = .idle
    @Published public private(set) var logs: [String] = []
    @Published public private(set) var apiKeySet: Bool = false
    @Published public private(set) var modelName: String = "gemini-2.0-flash"

    private let bleManager: BLEServerManager
    private var geminiClient: GeminiApiClient?
    private let settings: UserDefaultsSettingsStore
    private var cancellables = Set<AnyCancellable>()

    private var requestRouteByMessageId: [String: String] = [:]
    private var activePromptTasks: [String: Task<Void, Never>] = [:]
    private let promptSemaphore = AsyncSemaphore(count: 2)

    private init() {
        self.settings = UserDefaultsSettingsStore()
        let ble = BLEServerManager(
            onPrompt: { json, address in await BridgeService.shared.handleIncomingJson(json, from: address) },
            onLog: { msg in BridgeService.shared._appendLogSync(msg) }
        )
        self.bleManager = ble

        ble.statePublisher.receive(on: DispatchQueue.main).sink { [weak self] state in
            self?.bleState = state
        }.store(in: &cancellables)
        refreshGeminiClient()
    }

    public func start() { Task { await bleManager.start() } }

    public func stop() {
        for (_, t) in activePromptTasks { t.cancel() }
        activePromptTasks.removeAll()
        Task { await bleManager.stop() }
    }

    public func saveApiKey(_ key: String) {
        settings.setApiKey(key.trimmingCharacters(in: .whitespaces))
        apiKeySet = !key.isEmpty; refreshGeminiClient()
    }

    public func saveModel(_ model: String) { settings.setModel(model.trimmingCharacters(in: .whitespaces)); modelName = model }
    public var currentApiKey: String { settings.getApiKey() }

    // MARK: - Incoming JSON
    private func handleIncomingJson(_ rawJson: String, from address: String) async {
        guard let d = rawJson.data(using: .utf8), let e = try? JSONDecoder().decode(IncomingEnvelope.self, from: d) else {
            _appendLogSync("Invalid request envelope"); return
        }
        let m = e.messageId ?? "unknown"
        if !m.isEmpty { requestRouteByMessageId[m] = address }
        switch e.type {
        case "prompt": await handlePrompt(rawJson, address: address)
        case "ping": await handlePing(rawJson, address: address)
        case "cancel": await handleCancel(rawJson, address: address)
        case "list_containers": await handleListContainers(messageId: m, address: address)
        case "load_container": await handleLoadContainer(rawJson, address: address)
        default: sendError(m, error: "Unsupported: \(e.type)", address: address)
        }
    }

    private func handlePrompt(_ rawJson: String, address: String) async {
        guard let d = rawJson.data(using: .utf8), let req = try? JSONDecoder().decode(PromptRequest.self, from: d) else {
            _appendLogSync("Invalid prompt payload"); return
        }
        guard req.type == "prompt" else { sendError(req.messageId, error: "Bad type", address: address); return }
        guard !req.prompt.trimmingCharacters(in: .whitespaces).isEmpty else {
            sendError(req.messageId, error: "Empty prompt", address: address); return
        }
        let hasImg = req.imageBase64?.nilIfEmpty != nil
        let hasMime = req.imageMimeType?.nilIfEmpty != nil
        if hasImg != hasMime { sendError(req.messageId, error: "Invalid image", address: address); return }
        if (req.contextBlocks?.count ?? 0) > 12 { sendError(req.messageId, error: "Too many blocks", address: address); return }
        if (req.conversationMemory?.count ?? 0) > 24 { sendError(req.messageId, error: "Too many memory turns", address: address); return }

        let blocks = (req.contextBlocks ?? []).filter { !$0.text.isEmpty }.map {
            ContextBlockRequest(source: String($0.source.prefix(120)), page: $0.page, text: String($0.text.prefix(1600)))
        }
        let mem = (req.conversationMemory ?? []).filter { $0.role == "user" || $0.role == "assistant" }
            .filter { !$0.text.trimmingCharacters(in: .whitespaces).isEmpty }
            .map { MemoryTurnRequest(role: $0.role, text: String($0.text.trimmingCharacters(in: .whitespaces).prefix(1200))) }
        let modelOv = req.model?.nilIfEmpty
        let thinking = req.thinkingEnabled ?? false
        let incThoughts = (req.includeThoughts ?? false) && thinking
        let budget: Int? = { guard let b = req.thinkingBudget else { return nil }; return b < -1 ? -1 : b > 24576 ? 24576 : b }()

        let task = Task { [weak self] in
            guard let self else { return }
            defer { Task { @MainActor in self.activePromptTasks.removeValue(forKey: req.messageId) } }
            sendStatus(req.messageId, state: "processing (0s)", address: address)
            do {
                try await promptSemaphore.acquire()
                defer { promptSemaphore.release() }
                guard let client = self.geminiClient else { sendError(req.messageId, error: "No API key", address: address); return }
                _appendLogSync("Processing \(req.messageId)")
                let result = try await client.generate(
                    prompt: req.prompt, modelOverride: modelOv,
                    enableWebSearch: req.enableWebSearch ?? false,
                    thinkingEnabled: thinking, thinkingBudget: budget, includeThoughts: incThoughts,
                    imageBase64: hasImg ? req.imageBase64 : nil, imageMimeType: hasMime ? req.imageMimeType : nil,
                    contextBlocks: blocks, conversationMemory: mem,
                    activeContainerId: req.activeContainerId?.nilIfEmpty, activeContainerName: req.activeContainerName?.nilIfEmpty,
                    onPartialText: { Task { await self.sendPartial(req.messageId, text: $0, channel: "answer", address: address) } },
                    onPartialThought: { if incThoughts { Task { await self.sendPartial(req.messageId, text: $0, channel: "thought", address: address) } } }
                )
                sendResult(req.messageId, text: result.text, thought: incThoughts ? result.thought : nil, address: address)
                _appendLogSync("Sent \(req.messageId)")
            } catch let e as GeminiError {
                sendError(req.messageId, error: e.errorDescription ?? "Error", address: address)
                _appendLogSync("Gemini error: \(e.localizedDescription)")
            } catch { if error is CancellationError { sendStatus(req.messageId, state: "canceled", address: address) } else { sendError(req.messageId, error: error.localizedDescription, address: address); _appendLogSync("Error: \(error.localizedDescription)") } }
        }
        activePromptTasks[req.messageId] = task
    }

    private func handlePing(_ rawJson: String, address: String) async {
        guard let d = rawJson.data(using: .utf8), let p = try? JSONDecoder().decode(PingRequest.self, from: d) else { return }
        sendPong(messageId: p.messageId, clientTsMs: p.clientTsMs, address: address)
    }

    private func handleCancel(_ rawJson: String, address: String) async {
        guard let d = rawJson.data(using: .utf8), let c = try? JSONDecoder().decode(CancelRequest.self, from: d) else { return }
        let t = c.targetMessageId?.nilIfEmpty ?? c.messageId
        if let task = activePromptTasks[t] { task.cancel(); activePromptTasks.removeValue(forKey: t); sendStatus(t, state: "canceled", address: address) }
        else { sendStatus(t, state: "not-running", address: address) }
    }

    private func handleListContainers(messageId: String, address: String) async {
        let list = ContainerStore.shared.all().map { ContainerSummary(id: $0.id, name: $0.name, chunkCount: $0.chunks.count) }.sorted { $0.name.lowercased() < $1.name.lowercased() }
        sendEncodable(ListContainersResponse(messageId: messageId.isEmpty ? "list-containers" : messageId, containers: list), to: address, priority: true)
    }

    private func handleLoadContainer(_ rawJson: String, address: String) async {
        guard let d = rawJson.data(using: .utf8), let r = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              let msgId = r["messageId"] as? String,
              let cId = r["containerId"] as? String, !cId.isEmpty,
              let cName = r["containerName"] as? String, !cName.isEmpty,
              let arr = r["chunks"] as? [[String: Any]] else { sendError("unk", error: "Bad load_container", address: address); return }
        var chunks: [StoredChunk] = []
        for item in arr {
            guard let src = item["source"] as? String, let txt = item["text"] as? String else { continue }
            let page = item["page"] as? Int ?? 0
            var terms = item["terms"] as? [String] ?? []
            if terms.isEmpty { terms = Array(BM25Retrieval.tokenize(txt)) }
            chunks.append(StoredChunk(source: src, page: page, text: txt, terms: terms))
        }
        ContainerStore.shared.save(StoredContainer(id: cId, name: cName, chunks: chunks))
        _appendLogSync("Loaded '\(cName)' (\(chunks.count) chunks)")
        sendEncodable(ContainerAckResponse(messageId: msgId, containerId: cId, chunkCount: chunks.count), to: address, priority: true)
    }

    // MARK: - Sends
    private func sendStatus(_ id: String, state: String, address: String? = nil) { sendEncodable(StatusResponse(messageId: id, state: state), to: address) }
    private func sendPartial(_ id: String, text: String, channel: String, address: String? = nil) { sendEncodable(PartialResponse(messageId: id, text: text, channel: channel), to: address) }
    private func sendResult(_ id: String, text: String, thought: String?, address: String? = nil) { sendEncodable(ResultResponse(messageId: id, text: text, thought: thought), to: address, priority: true); requestRouteByMessageId.removeValue(forKey: id) }
    private func sendError(_ id: String, error: String, address: String? = nil) { sendEncodable(ErrorResponse(messageId: id, error: error), to: address, priority: true); requestRouteByMessageId.removeValue(forKey: id) }
    private func sendPong(messageId: String, clientTsMs: Int64?, address: String? = nil) { sendEncodable(PongResponse(messageId: messageId, clientTsMs: clientTsMs), to: address, priority: true); requestRouteByMessageId.removeValue(forKey: messageId) }

    private func sendEncodable<T: Encodable>(_ v: T, to address: String?, priority: Bool = false) {
        guard let d = try? JSONEncoder().encode(v), let s = String(data: d, encoding: .utf8) else { return }
        Task { try? await bleManager.sendJson(s, targetAddress: address, highPriority: priority) }
    }

    /// Synchronous log — safe to call from any context
    private func _appendLogSync(_ msg: String) {
        let line = "\(DateFormatter.logISO.string(from: Date())) | \(msg)"
        Task { @MainActor in logs.append(line); if logs.count > 300 { logs = Array(logs.suffix(300)) } }
    }

    private func refreshGeminiClient() {
        if !settings.getApiKey().isEmpty { geminiClient = GeminiApiClient(settings: settings); apiKeySet = true; modelName = settings.getModel() }
        else { geminiClient = nil; apiKeySet = false }
    }
}

extension DateFormatter {
    fileprivate static let logISO: DateFormatter = { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss"; return f }()
}

// MARK: - AsyncSemaphore (lock-free, Swift 6 safe)
public final class AsyncSemaphore: @unchecked Sendable {
    private let count: Int; private var available: Int; private var waiters: [CheckedContinuation<Void, Never>] = []
    private let q = DispatchQueue(label: "com.geminiairbridge.sem")
    public init(count: Int) { self.count = count; self.available = count }
    public func acquire() async { await withCheckedContinuation { c in q.sync { if available > 0 { available -= 1; c.resume() } else { waiters.append(c) } } } }
    public func release() { q.sync { if let w = waiters.first { waiters.removeFirst(); w.resume() } else if available < count { available += 1 } } }
}

public final class UserDefaultsSettingsStore: SettingsStore, @unchecked Sendable {
    private let d = UserDefaults.standard
    public init() {}
    public func apiKey() throws -> String { let k = getApiKey(); if k.isEmpty { throw GeminiError.missingApiKey }; return k }
    public var model: String? { getModel() }
    public func getApiKey() -> String { d.string(forKey: "gemini_api_key") ?? "" }
    public func setApiKey(_ k: String) { d.set(k, forKey: "gemini_api_key") }
    public func getModel() -> String { d.string(forKey: "gemini_model") ?? "gemini-2.0-flash" }
    public func setModel(_ m: String) { d.set(m, forKey: "gemini_model") }
}
