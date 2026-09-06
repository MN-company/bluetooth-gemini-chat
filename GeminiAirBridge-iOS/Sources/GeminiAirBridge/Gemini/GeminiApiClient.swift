import Foundation
import OSLog

// MARK: - Gemini API Client
/// Equivalent to Android's `GeminiApiClient`.
/// Uses URLSession instead of OkHttp.
public actor GeminiApiClient {
    private let session: URLSession
    private let decoder = JSONDecoder()
    private let urlRegex = try! NSRegularExpression(pattern: #"https?://[^\s<>"')\]]+"#)

    private let settings: SettingsStore

    public init(settings: SettingsStore) {
        self.settings = settings
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 20
        config.timeoutIntervalForResource = 300
        config.waitsForConnectivity = false
        self.session = URLSession(configuration: config)
    }

    // MARK: - Public generate
    public struct GenerationResult: Sendable {
        public let text: String
        public let thought: String
    }

    public func generate(
        prompt: String,
        modelOverride: String? = nil,
        enableWebSearch: Bool = false,
        thinkingEnabled: Bool = false,
        thinkingBudget: Int? = nil,
        includeThoughts: Bool = false,
        imageBase64: String? = nil,
        imageMimeType: String? = nil,
        contextBlocks: [ContextBlockRequest] = [],
        conversationMemory: [MemoryTurnRequest] = [],
        activeContainerId: String? = nil,
        activeContainerName: String? = nil,
        onPartialText: (@Sendable (String) -> Void)? = nil,
        onPartialThought: (@Sendable (String) -> Void)? = nil
    ) async throws -> GenerationResult {
        let apiKey = try settings.apiKey()
        let model = modelOverride?.nilIfEmpty ?? settings.model ?? "gemini-2.0-flash"
        guard !apiKey.isEmpty else { throw GeminiError.missingApiKey }

        if (imageBase64 != nil) != (imageMimeType != nil) {
            throw GeminiError.incompleteImagePayload
        }

        let cleanedImageB64 = imageBase64?.nilIfEmpty
        let cleanedImageMime = imageMimeType?.nilIfEmpty

        // BM25 retrieval from active container
        var effectiveContext = contextBlocks
        if let cId = activeContainerId?.nilIfEmpty,
           let container = ContainerStore.shared.get(id: cId),
           !container.chunks.isEmpty {
            let retrieved = BM25Retrieval.retrieveTopChunks(query: prompt, container: container, topK: 20)
            effectiveContext += retrieved.map { ContextBlockRequest(source: $0.source, page: $0.page, text: $0.text) }
        }

        let contextualPrompt = buildContextualPrompt(
            userPrompt: prompt,
            contextBlocks: effectiveContext,
            conversationMemory: conversationMemory,
            webSearchEnabled: enableWebSearch
        )

        let endpoint = "https://generativelanguage.googleapis.com/v1beta/models/\(model):generateContent"
        let streamEndpoint = "https://generativelanguage.googleapis.com/v1beta/models/\(model):streamGenerateContent?alt=sse"

        func buildPayload(useWebSearch: Bool, strictGrounding: Bool) throws -> Data {
            var promptText = contextualPrompt
            if useWebSearch && strictGrounding {
                promptText += "\n\n[MANDATORY] Use google_search grounding and include at least 2 source URLs in your response."
            }

            var body: [String: Any] = [
                "contents": [["parts": [["text": promptText]]]]
            ]

            if let b64 = cleanedImageB64, let mime = cleanedImageMime {
                var parts = (body["contents"] as! [[String: Any]])
                var partsArr = (parts[0]["parts"] as! [[String: Any]])
                partsArr.append(["inlineData": ["mimeType": mime, "data": b64]])
                parts[0]["parts"] = partsArr
                body["contents"] = parts
            }

            if useWebSearch {
                body["tools"] = [["google_search": [:]]]
            }

            if thinkingEnabled {
                let budget = sanitizeThinkingBudget(thinkingBudget)
                body["generationConfig"] = [
                    "thinkingConfig": [
                        "thinkingBudget": budget,
                        "includeThoughts": includeThoughts
                    ]
                ]
            }

            return try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        }

        // Main generate logic
        func tryStream(useWebSearch: Bool, strictGrounding: Bool) async throws -> GenerationResponse {
            let payload = try buildPayload(useWebSearch: useWebSearch, strictGrounding: strictGrounding)

            var request = URLRequest(url: URL(string: streamEndpoint)!)
            request.httpMethod = "POST"
            request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
            request.setValue(apiKey, forHTTPHeaderField: "x-goog-api-key")
            request.httpBody = payload

            let (bytes, response) = try await session.bytes(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw GeminiError.networkError("No HTTP response")
            }

            if http.statusCode == 429 {
                throw GeminiError.rateLimited
            }
            guard (200...299).contains(http.statusCode) else {
                let body = String(data: payload, encoding: .utf8) ?? ""
                throw GeminiError.httpError(http.statusCode, body)
            }

            var answerBuilder = ""
            var thoughtBuilder = ""
            var lastAnswerSnapshot = ""
            var lastThoughtSnapshot = ""
            var pendingData = ""

            for try await line in bytes.lines {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                guard trimmed.hasPrefix("data:") else { continue }
                let chunk = trimmed.dropFirst(5).trimmingCharacters(in: .whitespaces)
                guard !chunk.isEmpty, chunk != "[DONE]" else { continue }

                pendingData += chunk

                if let data = pendingData.data(using: .utf8),
                   (try? JSONSerialization.jsonObject(with: data)) != nil {
                    let parsed = parseSseCandidates(pendingData)
                    for candidate in parsed {
                        let parts = extractCandidateParts(candidate)

                        if !parts.answer.isEmpty {
                            let delta = computeDelta(lastAnswerSnapshot, parts.answer)
                            if !delta.isEmpty {
                                answerBuilder += delta
                                onPartialText?(answerBuilder)
                            }
                            lastAnswerSnapshot = parts.answer
                        }

                        if includeThoughts && !parts.thought.isEmpty {
                            let delta = computeDelta(lastThoughtSnapshot, parts.thought)
                            if !delta.isEmpty {
                                thoughtBuilder += delta
                                onPartialThought?(thoughtBuilder)
                            }
                            lastThoughtSnapshot = parts.thought
                        }
                    }
                    pendingData = ""
                }
            }

            return GenerationResponse(
                text: answerBuilder.trimmingCharacters(in: .whitespacesAndNewlines),
                thought: thoughtBuilder.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        }

        func tryNonStream(useWebSearch: Bool, strictGrounding: Bool) async throws -> GenerationResponse {
            let payload = try buildPayload(useWebSearch: useWebSearch, strictGrounding: strictGrounding)

            var request = URLRequest(url: URL(string: endpoint)!)
            request.httpMethod = "POST"
            request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
            request.setValue(apiKey, forHTTPHeaderField: "x-goog-api-key")
            request.httpBody = payload

            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw GeminiError.networkError("No HTTP response")
            }

            if http.statusCode == 429 {
                throw GeminiError.rateLimited
            }
            guard (200...299).contains(http.statusCode) else {
                let body = String(data: data, encoding: .utf8) ?? ""
                throw GeminiError.httpError(http.statusCode, body)
            }

            guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let candidates = root["candidates"] as? [[String: Any]],
                  let first = candidates.first else {
                throw GeminiError.emptyResponse
            }

            let parts = extractCandidateParts(first)
            return GenerationResponse(
                text: parts.answer.trimmingCharacters(in: .whitespacesAndNewlines),
                thought: parts.thought.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        }

        // Strategy: try streaming, fall back to non-stream on web search issues
        var usedWebSearch = enableWebSearch
        var result = try await tryStream(useWebSearch: enableWebSearch, strictGrounding: false)

        if enableWebSearch {
            let sources = extractWebSources(from: nil, text: result.text)
            if sources.isEmpty {
                result = try await tryStream(useWebSearch: true, strictGrounding: true)
            }

            if looksLikeToolError(result.text) {
                usedWebSearch = false
                result = try await tryNonStream(useWebSearch: false, strictGrounding: false)
            }
        }

        guard !result.text.isEmpty else { throw GeminiError.emptyResponse }

        var finalText = result.text
        if enableWebSearch && usedWebSearch {
            let sources = extractWebSources(from: nil, text: result.text)
            if !sources.isEmpty {
                finalText += "\n\nSources:\n"
                for (i, src) in sources.enumerated() {
                    finalText += "\(i+1). \(src.title) - \(src.uri)\n"
                }
            } else {
                finalText += "\n\n[Web search enabled but model returned no grounding sources]"
            }
        } else if enableWebSearch && !usedWebSearch {
            finalText += "\n\n[Web search unavailable for current model; answered without tool]"
        }

        return GenerationResult(
            text: finalText.trimmingCharacters(in: .whitespacesAndNewlines),
            thought: result.thought.trimmingCharacters(in: .whitespacesAndNewlines)
        )
    }

    // MARK: - Model list
    public func listAvailableModels(apiKeyOverride: String? = nil) async throws -> [String] {
        let apiKey = try apiKeyOverride?.nilIfEmpty ?? settings.apiKey()
        guard !apiKey.isEmpty else { throw GeminiError.missingApiKey }

        var request = URLRequest(url: URL(string: "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000")!)
        request.setValue(apiKey, forHTTPHeaderField: "x-goog-api-key")

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw GeminiError.httpError((response as? HTTPURLResponse)?.statusCode ?? 0, "")
        }

        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let models = root["models"] as? [[String: Any]] else { return [] }

        var out: [String] = []
        for model in models {
            guard let name = model["name"] as? String else { continue }
            let modelName = name.replacingOccurrences(of: "models/", with: "")
            guard modelName.lowercased().contains("gemini") else { continue }
            if let methods = model["supportedGenerationMethods"] as? [String] {
                guard methods.contains(where: { $0 == "generateContent" || $0 == "streamGenerateContent" }) else { continue }
            }
            out.append(modelName)
        }
        return out.sorted()
    }

    // MARK: - Private helpers
    private struct GenerationResponse: Sendable {
        let text: String
        let thought: String
    }

    private struct CandidateParts: Sendable {
        let answer: String
        let thought: String
    }

    private struct WebSource: Sendable {
        let title: String
        let uri: String
    }

    private func sanitizeThinkingBudget(_ budget: Int?) -> Int {
        guard let b = budget else { return -1 }
        if b < -1 { return -1 }
        if b > 24_576 { return 24_576 }
        return b
    }

    private func parseSseCandidates(_ raw: String) -> [[String: Any]] {
        guard let data = raw.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            return []
        }
        return root["candidates"] as? [[String: Any]] ?? []
    }

    private func extractCandidateParts(_ candidate: [String: Any]) -> CandidateParts {
        guard let content = candidate["content"] as? [String: Any],
              let parts = content["parts"] as? [[String: Any]] else {
            return CandidateParts(answer: "", thought: "")
        }

        var answer = ""
        var thought = ""
        for part in parts {
            guard let text = part["text"] as? String, !text.isEmpty else { continue }
            if part["thought"] as? Bool ?? false {
                thought = thought.isEmpty ? text : thought + "\n" + text
            } else {
                answer = answer.isEmpty ? text : answer + "\n" + text
            }
        }
        return CandidateParts(answer: answer, thought: thought)
    }

    private func computeDelta(_ prev: String, _ curr: String) -> String {
        guard !curr.isEmpty else { return "" }
        guard !prev.isEmpty else { return curr }
        if curr.hasPrefix(prev) { return String(curr.dropFirst(prev.count)) }
        if prev.hasSuffix(curr) { return "" }
        return curr
    }

    private func looksLikeToolError(_ text: String) -> Bool {
        let lower = text.lowercased()
        let mentionsTool = lower.contains("google_search") || lower.contains("tool")
        let unsupported = lower.contains("not supported") || lower.contains("unknown")
            || lower.contains("invalid") || lower.contains("unrecognized") || lower.contains("does not support")
        return mentionsTool && unsupported
    }

    private func extractWebSources(from candidate: [String: Any]?, text: String) -> [WebSource] {
        var sources: [WebSource] = []
        var seen = Set<String>()

        if let c = candidate,
           let grounding = c["groundingMetadata"] as? [String: Any],
           let chunks = grounding["groundingChunks"] as? [[String: Any]] {
            for chunk in chunks {
                guard let web = chunk["web"] as? [String: Any],
                      let uri = web["uri"] as? String, !uri.isEmpty,
                      seen.insert(uri).inserted else { continue }
                let title = web["title"] as? String ?? uri
                sources.append(WebSource(title: title, uri: uri))
                if sources.count >= 8 { break }
            }
        }

        if sources.isEmpty {
            let matches = urlRegex.matches(in: text, range: NSRange(text.startIndex..., in: text))
            for match in matches {
                guard let range = Range(match.range, in: text) else { continue }
                let uri = String(text[range]).trimmingCharacters(in: CharacterSet(charactersIn: ".,;)\"]"))
                guard !uri.isEmpty, seen.insert(uri).inserted else { continue }
                sources.append(WebSource(title: uri, uri: uri))
                if sources.count >= 8 { break }
            }
        }
        return sources
    }

    private func buildContextualPrompt(
        userPrompt: String,
        contextBlocks: [ContextBlockRequest],
        conversationMemory: [MemoryTurnRequest],
        webSearchEnabled: Bool
    ) -> String {
        var ctx: [String] = []
        ctx.append("You are an assistant in an ongoing conversation inside a custom Bluetooth chat app.")
        ctx.append("Never mention web UI actions like paperclip, upload buttons, or external chat interfaces.")
        ctx.append("Use conversation memory and PDF context only when relevant.")
        ctx.append("If document excerpts are missing or insufficient, clearly say you need more document context.")
        ctx.append("Current local date is \(formattedDate()). If asked about 'today', use this date unless explicitly quoting a source date.")

        if webSearchEnabled {
            ctx.append("Web search is enabled: use google_search grounding for current facts and include explicit source URLs.")
        }

        let limitedMem = conversationMemory.prefix(12)
        if !limitedMem.isEmpty {
            var memLines = ["CONVERSATION MEMORY:"]
            for turn in limitedMem {
                memLines.append("- \(turn.role.uppercased()): \(turn.text)")
            }
            ctx.append(memLines.joined(separator: "\n"))
        }

        ctx.append("QUESTION:")
        ctx.append(userPrompt.trimmingCharacters(in: .whitespacesAndNewlines))

        let limitedCtx = contextBlocks.prefix(10)
        if !limitedCtx.isEmpty {
            ctx.append("PDF CONTEXT:")
            for (index, block) in limitedCtx.enumerated() {
                ctx.append("[DOC \(index+1)] \(block.source) - page \(block.page)")
                ctx.append(block.text)
            }
        }

        return ctx.joined(separator: "\n")
    }

    private func formattedDate() -> String {
        let df = DateFormatter()
        df.dateFormat = "yyyy-MM-dd"
        return df.string(from: Date())
    }
}

// MARK: - Errors
public enum GeminiError: Swift.Error, LocalizedError {
    case missingApiKey
    case incompleteImagePayload
    case rateLimited
    case httpError(Int, String)
    case networkError(String)
    case emptyResponse
    case timeout

    public var errorDescription: String? {
        switch self {
        case .missingApiKey: return "Gemini API key is empty. Set it in the app first."
        case .incompleteImagePayload: return "Image payload is incomplete (missing mime type or data)."
        case .rateLimited: return "HTTP 429 — quota exceeded for this API key/plan. Check billing."
        case .httpError(let code, let body): return "Gemini HTTP \(code): \(body.prefix(400))"
        case .networkError(let msg): return "Gemini network error: \(msg)"
        case .emptyResponse: return "Gemini response did not include text."
        case .timeout: return "Gemini request timeout. Try shorter prompt/context or disable Web Search."
        }
    }
}

// MARK: - Helpers
extension String {
    var nilIfEmpty: String? {
        isEmpty ? nil : self
    }
}

public protocol SettingsStore: Sendable {
    func apiKey() throws -> String
    var model: String? { get }
}