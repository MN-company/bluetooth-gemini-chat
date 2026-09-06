import Foundation

// MARK: - Wire protocol model types
/// All JSON message types sent between desktop ↔ iOS.
/// Equivalent to the `@Serializable` data classes in `MainViewModel.kt`.

// MARK: - Envelope (desktop → iOS)
public struct IncomingEnvelope: Codable, Sendable {
    public let type: String
    public let messageId: String?

    public init(type: String, messageId: String? = nil) {
        self.type = type
        self.messageId = messageId
    }
}

// MARK: - Prompt request
public struct PromptRequest: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let prompt: String
    public let model: String?
    public let enableWebSearch: Bool?
    public let thinkingEnabled: Bool?
    public let thinkingBudget: Int?
    public let includeThoughts: Bool?
    public let imageBase64: String?
    public let imageMimeType: String?
    public let imageName: String?
    public let contextBlocks: [ContextBlockRequest]?
    public let conversationMemory: [MemoryTurnRequest]?
    public let activeContainerId: String?
    public let activeContainerName: String?

    public init(
        type: String = "prompt",
        messageId: String,
        prompt: String,
        model: String? = nil,
        enableWebSearch: Bool? = nil,
        thinkingEnabled: Bool? = nil,
        thinkingBudget: Int? = nil,
        includeThoughts: Bool? = nil,
        imageBase64: String? = nil,
        imageMimeType: String? = nil,
        imageName: String? = nil,
        contextBlocks: [ContextBlockRequest]? = nil,
        conversationMemory: [MemoryTurnRequest]? = nil,
        activeContainerId: String? = nil,
        activeContainerName: String? = nil
    ) {
        self.type = type
        self.messageId = messageId
        self.prompt = prompt
        self.model = model
        self.enableWebSearch = enableWebSearch
        self.thinkingEnabled = thinkingEnabled
        self.thinkingBudget = thinkingBudget
        self.includeThoughts = includeThoughts
        self.imageBase64 = imageBase64
        self.imageMimeType = imageMimeType
        self.imageName = imageName
        self.contextBlocks = contextBlocks
        self.conversationMemory = conversationMemory
        self.activeContainerId = activeContainerId
        self.activeContainerName = activeContainerName
    }
}

public struct ContextBlockRequest: Codable, Sendable {
    public let source: String
    public let page: Int
    public let text: String

    public init(source: String, page: Int, text: String) {
        self.source = source
        self.page = page
        self.text = text
    }
}

public struct MemoryTurnRequest: Codable, Sendable {
    public let role: String
    public let text: String

    public init(role: String, text: String) {
        self.role = role
        self.text = text
    }
}

// MARK: - Ping / Cancel
public struct PingRequest: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let clientTsMs: Int64?

    public init(type: String = "ping", messageId: String, clientTsMs: Int64? = nil) {
        self.type = type
        self.messageId = messageId
        self.clientTsMs = clientTsMs
    }
}

public struct CancelRequest: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let targetMessageId: String?

    public init(type: String = "cancel", messageId: String, targetMessageId: String? = nil) {
        self.type = type
        self.messageId = messageId
        self.targetMessageId = targetMessageId
    }
}

// MARK: - Container messages
public struct LoadContainerMessage: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let containerId: String
    public let containerName: String
    public let chunks: [ContainerChunk]

    public init(type: String = "load_container", messageId: String, containerId: String, containerName: String, chunks: [ContainerChunk]) {
        self.type = type
        self.messageId = messageId
        self.containerId = containerId
        self.containerName = containerName
        self.chunks = chunks
    }
}

public struct ContainerChunk: Codable, Sendable {
    public let source: String
    public let page: Int
    public let text: String
    public let terms: [String]?

    public init(source: String, page: Int, text: String, terms: [String]? = nil) {
        self.source = source
        self.page = page
        self.text = text
        self.terms = terms
    }
}

// MARK: - Responses (iOS → desktop)
public struct StatusResponse: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let state: String

    public init(type: String = "status", messageId: String, state: String) {
        self.type = type
        self.messageId = messageId
        self.state = state
    }
}

public struct PartialResponse: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let text: String
    public let channel: String?

    public init(type: String = "partial", messageId: String, text: String, channel: String? = nil) {
        self.type = type
        self.messageId = messageId
        self.text = text
        self.channel = channel
    }
}

public struct ResultResponse: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let text: String
    public let thought: String?

    public init(type: String = "result", messageId: String, text: String, thought: String? = nil) {
        self.type = type
        self.messageId = messageId
        self.text = text
        self.thought = thought
    }
}

public struct ErrorResponse: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let error: String

    public init(type: String = "error", messageId: String, error: String) {
        self.type = type
        self.messageId = messageId
        self.error = error
    }
}

public struct PongResponse: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let clientTsMs: Int64?
    public let serverTsMs: Int64?

    public init(type: String = "pong", messageId: String, clientTsMs: Int64? = nil, serverTsMs: Int64? = nil) {
        self.type = type
        self.messageId = messageId
        self.clientTsMs = clientTsMs
        self.serverTsMs = serverTsMs ?? Int64(Date().timeIntervalSince1970 * 1000)
    }
}

public struct ContainerAckResponse: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let containerId: String
    public let chunkCount: Int

    public init(type: String = "container_ack", messageId: String, containerId: String, chunkCount: Int) {
        self.type = type
        self.messageId = messageId
        self.containerId = containerId
        self.chunkCount = chunkCount
    }
}

public struct ContainerSummary: Codable, Sendable {
    public let id: String
    public let name: String
    public let chunkCount: Int

    public init(id: String, name: String, chunkCount: Int) {
        self.id = id
        self.name = name
        self.chunkCount = chunkCount
    }
}

public struct ListContainersResponse: Codable, Sendable {
    public let type: String
    public let messageId: String
    public let containers: [ContainerSummary]

    public init(type: String = "container_list", messageId: String, containers: [ContainerSummary]) {
        self.type = type
        self.messageId = messageId
        self.containers = containers
    }
}