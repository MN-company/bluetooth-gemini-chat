import Foundation
import Combine
import OSLog

// MARK: - Bridge Service
/// Equivalent to Android's `BleKeepAliveService`.
/// Runs on MainActor for UI updates, dispatches BLE/Gemini work to actors.
@MainActor
public final class BridgeService: ObservableObject {
    public static let shared = BridgeService()

    // MARK: - Published UI state
    @Published public private(set) var status: String = "Idle"
    @Published public private(set) var bleState: BLEServerManager.State = .idle
    @Published public private(set) var logs: [String] = []
    @Published public private(set) var apiKeySet: Bool = false
    @Published public private(set) var modelName: String = "gemini-2.0-flash"

    // MARK: - Dependencies
    private let bleManager: BLEServerManager
    private var geminiClient: GeminiApiClient?
    private let settings: UserDefaultsSettingsStore
    private var cancellables = Set<AnyCancellable>()

    // Request routing
    private var requestRouteByMessageId: [String: String] = [:]
    private var activePromptTasks: [String: Task<Void, Never>] = [:]
    private let promptSemaphore = AsyncSemaphore(count: BLEConstants.maxConcurrentPrompts)

    private init() {
        self.settings = UserDefaultsSettingsStore()
        // BLE actor — uses nonisolated publisher, so subscribe outside the init closure chain
        let ble = BLEServerManager(
            onPrompt: { json, address in
                await BridgeService.shared.handleIncomingJson(json, from: address)
            },
            onLog: { msg in
                await BridgeService.shared.appendLog(msg)
            }
        )
        self.bleManager = ble

        // Subscribe to BLE state changes (nonisolated publisher, safe from MainActor)
        ble.statePublisher
            .receive(on: DispatchQueue.main)
            .sink { [weak self] state in
                self?.bleState = state
            }
            .store(in: &cancellables)

        refreshGeminiClient()
    }

    // MARK: - Public API
    public func start() {
        Task { await bleManager.start() }
    }

    public func stop() {
        for (_, task) in activePromptTasks { task.cancel() }
        activePromptTasks.removeAll()
        Task { await bleManager.stop() }
    }

    public func saveApiKey(_ key: String) {
        settings.setApiKey(key.trimmingCharacters(in: .whitespacesAndNewlines))
        apiKeySet = !key.isEmpty
        refreshGeminiClient()
    }

    public func saveModel(_ model: String) {
        settings.setModel(model.trimmingCharacters(in: .whitespacesAndNewlines))
        modelName = model
    }

    public var currentApiKey: String { settings.getApiKey() }

    // MARK: - Incoming JSON routing
    private func handleIncomingJson(_ rawJson: String, from address: String) async {
        guard let data = rawJson.data(using: .utf8),
              let envelope = try? JSONDecoder().decode(IncomingEnvelope.self, from: data) else {
            await appendLog("Invalid request envelope")
            return
        }

        let msgId = envelope.messageId ?? "unknown"
        if !msgId.isEmpty { requestRouteByMessageId[msgId] = address }

        switch envelope.type {
        case "prompt": await handlePrompt(rawJson, address: address)
        case "ping": await handlePing(rawJson, address: address)
        case "cancel": await handleCancel(rawJson, address: address)
        case "list_containers": await handleListContainers(messageId: msgId, address: address)
        case "load_container": await handleLoadContainer(rawJson, address: address)
        default: sendError(msgId, error: "Unsupported type: \(envelope.type)", address: address)
        }
    }

    private func handlePrompt(_ rawJson: String, address: String) async {
        guard let data = rawJson.data(using: .utf8),
              let req = try? JSONDecoder().decode(PromptRequest.self, from: data) else {
            await appendLog("Invalid prompt payload")
            return
        }

        guard req.type == "prompt" else { sendError(req.messageId, error: "Unsupported type", address: address); return }
        guard !req.prompt.trimmingCharacters(in: .whitespaces).isEmpty else {
            sendError(req.messageId, error: "Empty prompt", address: address); return
        }

        let hasImage = req.imageBase64?.nilIfEmpty != nil
        let hasMime = req.imageMimeType?.nilIfEmpty != nil
        if hasImage != hasMime { sendError(req.messageId, error: "Invalid image", address: address); return }
        if (req.contextBlocks?.count ?? 0) > BLEConstants.maxContextBlocks {
            sendError(req.messageId, error: "Too many context blocks", address: address); return
        }
        if (req.conversationMemory?.count ?? 0) > BLEConstants.maxMemoryTurns {
            sendError(req.messageId, error: "Too many memory turns", address: address); return
        }

        let sanitizedBlocks = (req.contextBlocks ?? []).filter { !$0.text.isEmpty }.map {
            ContextBlockRequest(source: String($0.source.prefix(120)), page: $0.page,
                                text: String($0.text.prefix(1600)))
        }
        let sanitizedMemory = (req.conversationMemory ?? []).filter {
            $0.role == "user" || $0.role == "assistant"
        }.filter { !$0.text.trimmingCharacters(in: .whitespaces).isEmpty }.map {
            MemoryTurnRequest(role: $0.role, text: String($0.text.trimmingCharacters(in: .whitespaces).prefix(1200)))
        }
        let modelOverride = req.model?.nilIfEmpty
        let thinking = req.thinkingEnabled ?? false
        let includeThoughts = (req.includeThoughts ?? false) && thinking
        let budget: Int? = {
            guard let b = req.thinkingBudget else { return nil }
            return b < -1 ? -1 : b > 24_576 ? 24_576 : b
        }()

        let task = Task { [weak self] in
            guard let self else { return }
            defer { Task { @MainActor in self.activePromptTasks.removeValue(forKey: req.messageId) } }

            sendStatus(req.messageId, state: "processing (0s)", address: address)

            do {
                try await promptSemaphore.acquire()
                defer { promptSemaphore.release() }

                guard let client = self.geminiClient else {
                    sendError(req.messageId, error: "API key not set", address: address)
                    return
                }

                await appendLog("Processing prompt \(req.messageId)")

                let result = try await client.generate(
                    prompt: req.prompt,
                    modelOverride: modelOverride,
                    enableWebSearch: req.enableWebSearch ?? false,
                    thinkingEnabled: thinking,
                    thinkingBudget: budget,
                    includeThoughts: includeThoughts,
                    imageBase64: hasImage ? req.imageBase64 : nil,
                    imageMimeType: hasMime ? req.imageMimeType : nil,
                    contextBlocks: sanitizedBlocks,
                    conversationMemory: sanitizedMemory,
                    activeContainerId: req.activeContainerId?.nilIfEmpty,
                    activeContainerName: req.activeContainerName?.nilIfEmpty,
                    onPartialText: { [weak self] partial in
                        Task { await self?.sendPartial(req.messageId, text: partial, channel: "answer", address: address) }
                    },
                    onPartialThought: { [weak self] thought in
                        if includeThoughts {
                            Task { await self?.sendPartial(req.messageId, text: thought, channel: "thought", address: address) }
                        }
                    }
                )

                sendResult(req.messageId, text: result.text, thought: includeThoughts ? result.thought : nil, address: address)
                await appendLog("Response sent \(req.messageId)")

            } catch let error as GeminiError {
                sendError(req.messageId, error: error.errorDescription ?? "Error", address: address)
                await appendLog("Gemini error: \(error.localizedDescription)")
            } catch {
                if error is CancellationError {
                    sendStatus(req.messageId, state: "canceled", address: address)
                } else {
                    sendError(req.messageId, error: error.localizedDescription, address: address)
                    await appendLog("Error: \(error.localizedDescription)")
                }
            }
        }

        activePromptTasks[req.messageId] = task
    }

    private func handlePing(_ rawJson: String, address: String) async {
        guard let data = rawJson.data(using: .utf8),
              let ping = try? JSONDecoder().decode(PingRequest.self, from: data) else { return }
        sendPong(messageId: ping.messageId, clientTsMs: ping.clientTsMs, address: address)
    }

    private func handleCancel(_ rawJson: String, address: String) async {
        guard let data = rawJson.data(using: .utf8),
              let cancel = try? JSONDecoder().decode(CancelRequest.self, from: data) else { return }
        let targetId = cancel.targetMessageId?.nilIfEmpty ?? cancel.messageId
        if let task = activePromptTasks[targetId] {
            task.cancel()
            activePromptTasks.removeValue(forKey: targetId)
            sendStatus(targetId, state: "canceled", address: address)
        } else {
            sendStatus(targetId, state: "not-running", address: address)
        }
    }

    private func handleListContainers(messageId: String, address: String) async {
        let list = ContainerStore.shared.all().map {
            ContainerSummary(id: $0.id, name: $0.name, chunkCount: $0.chunks.count)
        }.sorted { $0.name.lowercased() < $1.name.lowercased() }
        let resp = ListContainersResponse(
            messageId: messageId.isEmpty ? "list-containers" : messageId,
            containers: list
        )
        sendEncodable(resp, to: address, priority: true)
    }

    private func handleLoadContainer(_ rawJson: String, address: String) async {
        guard let data = rawJson.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            sendError("unknown", error: "load_container parse error", address: address)
            return
        }

        let msgId = root["messageId"] as? String ?? "unknown"
        guard let cId = root["containerId"] as? String, !cId.isEmpty,
              let cName = root["containerName"] as? String, !cName.isEmpty,
              let chunksArray = root["chunks"] as? [[String: Any]] else {
            sendError(msgId, error: "Missing containerId/name/chunks", address: address)
            return
        }

        var chunks: [StoredChunk] = []
        for item in chunksArray {
            guard let source = item["source"] as? String, let text = item["text"] as? String else { continue }
            let page = item["page"] as? Int ?? 0
            var terms = item["terms"] as? [String] ?? []
            if terms.isEmpty { terms = Array(BM25Retrieval.tokenize(text)) }
            chunks.append(StoredChunk(source: source, page: page, text: text, terms: terms))
        }

        ContainerStore.shared.save(StoredContainer(id: cId, name: cName, chunks: chunks))
        await appendLog("Loaded '\(cName)' (\(chunks.count) chunks)")

        sendEncodable(ContainerAckResponse(containerId: cId, chunkCount: chunks.count, messageId: msgId),
                      to: address, priority: true)
    }

    // MARK: - Send helpers
    private func sendStatus(_ msgId: String, state: String, address: String? = nil) {
        sendEncodable(StatusResponse(messageId: msgId, state: state), to: address)
    }
    private func sendPartial(_ msgId: String, text: String, channel: String, address: String? = nil) {
        sendEncodable(PartialResponse(messageId: msgId, text: text, channel: channel), to: address)
    }
    private func sendResult(_ msgId: String, text: String, thought: String?, address: String? = nil) {
        sendEncodable(ResultResponse(messageId: msgId, text: text, thought: thought), to: address, priority: true)
        requestRouteByMessageId.removeValue(forKey: msgId)
    }
    private func sendError(_ msgId: String, error: String, address: String? = nil) {
        sendEncodable(ErrorResponse(messageId: msgId, error: error), to: address, priority: true)
        requestRouteByMessageId.removeValue(forKey: msgId)
    }
    private func sendPong(messageId: String, clientTsMs: Int64?, address: String? = nil) {
        sendEncodable(PongResponse(messageId: messageId, clientTsMs: clientTsMs), to: address, priority: true)
        requestRouteByMessageId.removeValue(forKey: messageId)
    }

    private func sendEncodable<T: Encodable>(_ value: T, to address: String?, priority: Bool = false) {
        guard let payload = try? JSONEncoder().encode(value),
              let json = String(data: payload, encoding: .utf8) else { return }
        Task { try? await bleManager.sendJson(json, targetAddress: address, highPriority: priority) }
    }

    private nonisolated func appendLog(_ msg: String) {
        let line = "\(DateFormatter.logISO.string(from: Date())) | \(msg)"
        Task { @MainActor in
            logs.append(line)
            if logs.count > 300 { logs = Array(logs.suffix(300)) }
        }
    }

    private func refreshGeminiClient() {
        if !settings.getApiKey().isEmpty {
            geminiClient = GeminiApiClient(settings: settings)
            apiKeySet = true
            modelName = settings.getModel()
        } else {
            geminiClient = nil
            apiKeySet = false
        }
    }
}

// MARK: - Date formatter
extension DateFormatter {
    fileprivate static let logISO: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return f
    }()
}

// MARK: - AsyncSemaphore
public final class AsyncSemaphore: @unchecked Sendable {
    private let count: Int
    private var available: Int
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private let lock = NSLock()

    public init(count: Int) {
        self.count = count
        self.available = count
    }

    public func acquire() async {
        lock.lock()
        if available > 0 {
            available -= 1
            lock.unlock()
            return
        }
        await withCheckedContinuation { c in
            waiters.append(c)
            lock.unlock()
        }
    }

    public func release() {
        lock.lock()
        if let waiter = waiters.first {
            waiters.removeFirst()
            lock.unlock()
            waiter.resume()
        } else if available < count {
            available += 1
            lock.unlock()
        } else { lock.unlock() }
    }
}

// MARK: - SettingsStore
public final class UserDefaultsSettingsStore: SettingsStore, @unchecked Sendable {
    private let defaults = UserDefaults.standard
    private let keyApiKey = "gemini_api_key"
    private let keyModel = "gemini_model"

    public init() {}
    public func apiKey() throws -> String {
        let k = getApiKey(); if k.isEmpty { throw GeminiError.missingApiKey }; return k
    }
    public var model: String? { getModel() }
    public func getApiKey() -> String { defaults.string(forKey: keyApiKey) ?? "" }
    public func setApiKey(_ k: String) { defaults.set(k, forKey: keyApiKey) }
    public func getModel() -> String { defaults.string(forKey: keyModel) ?? "gemini-2.0-flash" }
    public func setModel(_ m: String) { defaults.set(m, forKey: keyModel) }
}