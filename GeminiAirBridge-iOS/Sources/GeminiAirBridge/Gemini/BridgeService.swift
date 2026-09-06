import Foundation
import Combine

@MainActor
public final class BridgeService: ObservableObject {
    public static let shared = BridgeService()

    @Published public private(set) var status: String = "Idle"
    @Published public private(set) var bleState: BLEServerManager.State = .idle
    @Published public private(set) var logs: [String] = []
    @Published public private(set) var apiKeySet: Bool = false
    @Published public private(set) var modelName: String = "gemini-2.0-flash"

    private var bleManager: BLEServerManager!
        private var geminiClient: GeminiApiClient?
        private let settings = UserDefaultsSettingsStore()
        private var cancellables = Set<AnyCancellable>()

        private var msgRoute: [String: String] = [:]
        private var promptTasks: [String: Task<Void, Never>] = [:]
        private let sema = AsyncSemaphore(count: 2)

        private init() {
            // 1. Create bleManager FIRST with closures that don't capture self (use shared)
            let ble = BLEServerManager(
                onPrompt: { json, addr in await BridgeService.shared.handleIncoming(json, addr: addr) },
                onLog: { msg in Task { await BridgeService.shared.appendLog(msg) } }
            )
            // 2. Assign before using self in subscriber (avoids 'used before init')
            self.bleManager = ble
            // 3. Now safe to use self
            ble.statePublisher.receive(on: DispatchQueue.main).sink { [weak self] s in self?.bleState = s }.store(in: &cancellables)
            refreshClient()
        }

    public func start() { Task { await bleManager.start() } }
    public func stop() { promptTasks.values.forEach { $0.cancel() }; promptTasks.removeAll(); Task { await bleManager.stop() } }
    public func saveApiKey(_ k: String) { settings.setApiKey(k.trimmingCharacters(in: .whitespaces)); apiKeySet = !k.isEmpty; refreshClient() }
    public func saveModel(_ m: String) { settings.setModel(m.trimmingCharacters(in: .whitespaces)); modelName = m }
    public var currentApiKey: String { settings.getApiKey() }

    // MARK: - Incoming
    private func handleIncoming(_ raw: String, addr: String) async {
        guard let d = raw.data(using: .utf8), let e = try? JSONDecoder().decode(IncomingEnvelope.self, from: d) else { await appendLog("Bad envelope"); return }
        let mid = e.messageId ?? "unknown"
        if !mid.isEmpty { msgRoute[mid] = addr }
        switch e.type {
        case "prompt": await handlePrompt(raw, addr: addr)
        case "ping": await handlePing(raw, addr: addr)
        case "cancel": await handleCancel(raw, addr: addr)
        case "list_containers": await listContainers(mid: mid, addr: addr)
        case "load_container": await loadContainer(raw, addr: addr)
        default: sendErr(mid, err: "Unsupported: \(e.type)", addr: addr)
        }
    }

    private func handlePrompt(_ raw: String, addr: String) async {
        guard let d = raw.data(using: .utf8), let req = try? JSONDecoder().decode(PromptRequest.self, from: d) else { await appendLog("Bad prompt"); return }
        guard req.type == "prompt", !req.prompt.trimmingCharacters(in: .whitespaces).isEmpty else { sendErr(req.messageId, err: "Bad/empty prompt", addr: addr); return }
        let hasImg = req.imageBase64?.isEmpty == false
        let hasMime = req.imageMimeType?.isEmpty == false
        if hasImg != hasMime { sendErr(req.messageId, err: "Invalid image", addr: addr); return }
        if (req.contextBlocks?.count ?? 0) > 12 || (req.conversationMemory?.count ?? 0) > 24 {
            sendErr(req.messageId, err: "Too many blocks/memory", addr: addr); return
        }
        let blocks = (req.contextBlocks ?? []).filter { !$0.text.isEmpty }.map { ContextBlockRequest(source: String($0.source.prefix(120)), page: $0.page, text: String($0.text.prefix(1600))) }
        let mem = (req.conversationMemory ?? []).filter { ($0.role == "user" || $0.role == "assistant") && !$0.text.trimmingCharacters(in: .whitespaces).isEmpty }
            .map { MemoryTurnRequest(role: $0.role, text: String($0.text.trimmingCharacters(in: .whitespaces).prefix(1200))) }
        let mo = req.model?.nilIfEmpty; let think = req.thinkingEnabled ?? false; let incThought = (req.includeThoughts ?? false) && think
        let budget: Int? = { guard let b = req.thinkingBudget else { return nil }; return b < -1 ? -1 : b > 24576 ? 24576 : b }()

        let task = Task { [weak self] in
            guard let self else { return }
            defer { Task { @MainActor in self.promptTasks.removeValue(forKey: req.messageId) } }
            self.sendStatus(req.messageId, state: "processing", addr: addr)
            do {
                try await sema.acquire(); defer { sema.release() }
                guard let client = self.geminiClient else { self.sendErr(req.messageId, err: "No API key", addr: addr); return }
                await appendLog("Processing \(req.messageId)")
                let res = try await client.generate(prompt: req.prompt, modelOverride: mo, enableWebSearch: req.enableWebSearch ?? false, thinkingEnabled: think, thinkingBudget: budget, includeThoughts: incThought, imageBase64: hasImg ? req.imageBase64 : nil, imageMimeType: hasMime ? req.imageMimeType : nil, contextBlocks: blocks, conversationMemory: mem, activeContainerId: req.activeContainerId?.nilIfEmpty, activeContainerName: req.activeContainerName?.nilIfEmpty, onPartialText: { [weak self] t in Task { await self?.sendPartial(req.messageId, text: t, channel: "answer", addr: addr) } }, onPartialThought: { [weak self] t in if incThought { Task { await self?.sendPartial(req.messageId, text: t, channel: "thought", addr: addr) } } })
                sendResult(req.messageId, text: res.text, thought: incThought ? res.thought : nil, addr: addr)
                await appendLog("Sent \(req.messageId)")
            } catch let e as GeminiError { self.sendErr(req.messageId, err: e.errorDescription ?? "Error", addr: addr); await appendLog("Gemini err: \(e.localizedDescription)")
            } catch { if error is CancellationError { self.sendStatus(req.messageId, state: "canceled", addr: addr) } else { self.sendErr(req.messageId, err: error.localizedDescription, addr: addr); await appendLog("Err: \(error.localizedDescription)") } }
        }
        promptTasks[req.messageId] = task
    }

    private func handlePing(_ raw: String, addr: String) async {
        guard let d = raw.data(using: .utf8), let p = try? JSONDecoder().decode(PingRequest.self, from: d) else { return }
        sendPong(msgId: p.messageId, clientTs: p.clientTsMs, addr: addr)
    }

    private func handleCancel(_ raw: String, addr: String) async {
        guard let d = raw.data(using: .utf8), let c = try? JSONDecoder().decode(CancelRequest.self, from: d) else { return }
        let t = c.targetMessageId?.nilIfEmpty ?? c.messageId
        if let task = promptTasks[t] { task.cancel(); promptTasks.removeValue(forKey: t); sendStatus(t, state: "canceled", addr: addr) }
        else { sendStatus(t, state: "not-running", addr: addr) }
    }

    private func listContainers(mid: String, addr: String) async {
        let list = ContainerStore.shared.all().map { ContainerSummary(id: $0.id, name: $0.name, chunkCount: $0.chunks.count) }.sorted { $0.name.lowercased() < $1.name.lowercased() }
        sendEnc(ListContainersResponse(messageId: mid.isEmpty ? "list-containers" : mid, containers: list), addr: addr, priority: true)
    }

    private func loadContainer(_ raw: String, addr: String) async {
        guard let d = raw.data(using: .utf8), let r = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let mid = r["messageId"] as? String,
              let cid = r["containerId"] as? String, !cid.isEmpty, let cnm = r["containerName"] as? String, !cnm.isEmpty,
              let arr = r["chunks"] as? [[String: Any]] else { sendErr("?", err: "Bad load_container", addr: addr); return }
        var chks: [StoredChunk] = []
        for item in arr {
            guard let src = item["source"] as? String, let txt = item["text"] as? String else { continue }
            let page = item["page"] as? Int ?? 0
            var terms = item["terms"] as? [String] ?? []; if terms.isEmpty { terms = Array(BM25Retrieval.tokenize(txt)) }
            chks.append(StoredChunk(source: src, page: page, text: txt, terms: terms))
        }
        ContainerStore.shared.save(StoredContainer(id: cid, name: cnm, chunks: chks))
        await appendLog("Loaded '\(cnm)' (\(chks.count) chunks)")
        sendEnc(ContainerAckResponse(messageId: mid, containerId: cid, chunkCount: chks.count), addr: addr, priority: true)
    }

    // MARK: - Sends
    private func sendStatus(_ id: String, state: String, addr: String?) { sendEnc(StatusResponse(messageId: id, state: state), addr: addr) }
    private func sendPartial(_ id: String, text: String, channel: String, addr: String?) { sendEnc(PartialResponse(messageId: id, text: text, channel: channel), addr: addr) }
    private func sendResult(_ id: String, text: String, thought: String?, addr: String?) {
        sendEnc(ResultResponse(messageId: id, text: text, thought: thought), addr: addr, priority: true); msgRoute.removeValue(forKey: id)
    }
    private func sendErr(_ id: String, err: String, addr: String?) { sendEnc(ErrorResponse(messageId: id, error: err), addr: addr, priority: true); msgRoute.removeValue(forKey: id) }
    private func sendPong(msgId: String, clientTs: Int64?, addr: String?) { sendEnc(PongResponse(messageId: msgId, clientTsMs: clientTs), addr: addr, priority: true); msgRoute.removeValue(forKey: msgId) }
    private func sendEnc<T: Encodable>(_ v: T, addr: String?, priority: Bool = false) {
        guard let d = try? JSONEncoder().encode(v), let s = String(data: d, encoding: .utf8) else { return }
        Task { try? await bleManager.sendJson(s, targetAddress: addr, highPriority: priority) }
    }

    private nonisolated func appendLog(_ msg: String) {
        let l = "\(Self.lf.string(from: Date())) | \(msg)"
        Task { @MainActor in self.logs.append(l); if self.logs.count > 300 { self.logs = Array(self.logs.suffix(300)) } }
    }

    private func refreshClient() {
        if !settings.getApiKey().isEmpty { geminiClient = GeminiApiClient(settings: settings); apiKeySet = true; modelName = settings.getModel() }
        else { geminiClient = nil; apiKeySet = false }
    }

    private static let lf: DateFormatter = { let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss"; return f }()
}

// MARK: - AsyncSemaphore
public final class AsyncSemaphore: @unchecked Sendable {
    private let count: Int; private var available: Int; private var waiters: [CheckedContinuation<Void, Never>] = []; private let q = DispatchQueue(label: "com.geminiairbridge.sem")
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
