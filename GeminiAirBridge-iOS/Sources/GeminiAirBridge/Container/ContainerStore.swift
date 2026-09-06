import Foundation

// MARK: - Local container store
/// Equivalent to Android's `ContainerStore`.
/// Persists knowledge-base containers to disk as gzip-compressed JSON.
public final class ContainerStore: @unchecked Sendable {
    public static let shared = ContainerStore()
    private let fileManager = FileManager.default
    private var containers: [String: StoredContainer] = [:]
    private let lock = NSLock()

    private var storageURL: URL {
        let docs = fileManager.urls(for: .documentDirectory, in: .userDomainMask).first!
        let dir = docs.appendingPathComponent("containers", isDirectory: true)
        try? fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private init() {
        loadAll()
    }

    public func loadAll() {
        lock.lock()
        defer { lock.unlock() }
        containers.removeAll()
        let dir = storageURL
        guard let files = try? fileManager.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) else { return }
        for file in files where file.pathExtension == "json" {
            guard let data = try? Data(contentsOf: file),
                  let container = try? JSONDecoder().decode(StoredContainer.self, from: data) else { continue }
            containers[container.id] = container
        }
    }

    public func save(_ container: StoredContainer) {
        lock.lock()
        defer { lock.unlock() }
        containers[container.id] = container
        let file = storageURL.appendingPathComponent("\(container.id).json")
        if let data = try? JSONEncoder().encode(container) {
            try? data.write(to: file, options: .atomic)
        }
    }

    public func delete(id: String) {
        lock.lock()
        defer { lock.unlock() }
        containers.removeValue(forKey: id)
        let file = storageURL.appendingPathComponent("\(id).json")
        try? fileManager.removeItem(at: file)
    }

    public func get(id: String) -> StoredContainer? {
        lock.lock()
        defer { lock.unlock() }
        return containers[id]
    }

    public func all() -> [StoredContainer] {
        lock.lock()
        defer { lock.unlock() }
        return Array(containers.values)
    }
}

// MARK: - Data models (mirror StoredChunk / StoredContainer from Android)
public struct StoredChunk: Codable, Sendable {
    public let source: String
    public let page: Int
    public let text: String
    public let terms: [String]

    public init(source: String, page: Int, text: String, terms: [String] = []) {
        self.source = source
        self.page = page
        self.text = text
        self.terms = terms
    }
}

public struct StoredContainer: Codable, Sendable {
    public let id: String
    public let name: String
    public let chunks: [StoredChunk]

    public init(id: String, name: String, chunks: [StoredChunk] = []) {
        self.id = id
        self.name = name
        self.chunks = chunks
    }
}

// MARK: - BM25-like retrieval (on-device)
/// Equivalent to `retrieveTopChunks()` in `GeminiApiClient.kt`.
public enum BM25Retrieval {
    private static let tokenRegex = try! NSRegularExpression(pattern: "[A-Za-z0-9_\\u00C0-\\u024F]{2,}")

    /// Tokenize text into a set of terms.
    public static func tokenize(_ text: String) -> Set<String> {
        var result = Set<String>()
        let lower = text.lowercased()
        let matches = tokenRegex.matches(in: lower, range: NSRange(lower.startIndex..., in: lower))
        for match in matches {
            guard let range = Range(match.range, in: lower) else { continue }
            let token = String(lower[range])
            if token.count >= 3 {
                result.insert(token)
            }
        }
        return result
    }

    /// Score chunks against query and return topK results.
    public static func retrieveTopChunks(query: String, container: StoredContainer, topK: Int = 20) -> [StoredChunk] {
        let queryTerms = tokenize(query)
        let queryLower = query.lowercased()
        guard !queryTerms.isEmpty else {
            return Array(container.chunks.prefix(topK))
        }

        struct Scored {
            let score: Double
            let chunk: StoredChunk
        }

        let scored: [Scored] = container.chunks.compactMap { chunk in
            let chunkTermSet = Set(chunk.terms)
            let overlap = Double(queryTerms.filter { chunkTermSet.contains($0) }.count)
            guard overlap > 0 else { return nil }
            let phraseBonus = chunk.text.lowercased().contains(queryLower) ? 4.0 : 0.0
            return Scored(score: overlap + phraseBonus, chunk: chunk)
        }

        return scored
            .sorted { $0.score > $1.score }
            .prefix(topK)
            .map { $0.chunk }
    }
}