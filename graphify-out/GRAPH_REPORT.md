# Graph Report - bluetooth-gemini-chat  (2026-09-06)

## Corpus Check
- Corpus is ~33,890 words - fits in a single context window. You may not need a graph.

## Summary
- 546 nodes · 1243 edges · 30 communities (12 shown, 12 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 36 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Prompt Bundle & Runtime State
- Desktop BLE Client
- Android KeepAlive Service
- Desktop App Core Logic
- Desktop UI Components
- Android Compose UI (MainActivity)
- Desktop PDF Context Store
- Android BLE Server Manager
- Android ViewModel & Updates
- Desktop UI Event Handlers
- Desktop App Bootstrap & Theme
- Desktop Chat Sessions Store
- Desktop PDF Context Engine
- Container Management UI
- BLE Frame Codec & Assembly
- Desktop Memory Store
- macOS Quick Action Installer
- BLE Constants
- macOS Quick Ask Script
- Android APK Build Script
- Desktop Bundle Build Script
- Android APK Install Script
- Desktop Run Script
- Desktop Setup Script

## God Nodes (most connected - your core abstractions)
1. `DesktopChatApp` - 177 edges
2. `BleKeepAliveService` - 39 edges
3. `BleChatClient` - 34 edges
4. `GeminiApiClient` - 22 edges
5. `BleServerManager` - 21 edges
6. `MainViewModel` - 20 edges
7. `ChatSessionsStore` - 20 edges
8. `ContextStore` - 16 edges
9. `PdfContextEngine` - 13 edges
10. `SettingsRepository` - 10 edges

## Surprising Connections (you probably didn't know these)
- `BleServerManager` --calls--> `TransportIdGenerator`  [INFERRED]
  android/GeminiBluetoothBridge/app/src/main/java/com/example/geminibridge/BleServerManager.kt → android/GeminiBluetoothBridge/app/src/main/java/com/example/geminibridge/BleFrameCodec.kt
- `MainViewModel` --calls--> `GeminiApiClient`  [INFERRED]
  android/GeminiBluetoothBridge/app/src/main/java/com/example/geminibridge/MainViewModel.kt → android/GeminiBluetoothBridge/app/src/main/java/com/example/geminibridge/GeminiApiClient.kt
- `DesktopChatApp` --uses--> `BleChatClient`  [INFERRED]
  desktop/app.py → desktop/ble_client.py
- `DesktopChatApp` --uses--> `ChatSessionsStore`  [INFERRED]
  desktop/app.py → desktop/chat_sessions.py
- `DesktopChatApp` --uses--> `ContextStore`  [INFERRED]
  desktop/app.py → desktop/context_store.py

## Import Cycles
- None detected.

## Communities (30 total, 12 thin omitted)

### Community 0 - "Prompt Bundle & Runtime State"
Cohesion: 0.05
Nodes (23): BinaryPromptBundle, ByteArray, BridgeRuntimeState, StateFlow, ContainerStore, StoredChunk, StoredContainer, CandidateParts (+15 more)

### Community 1 - "Desktop BLE Client"
Cohesion: 0.07
Nodes (19): BleakClient, BleChatClient, Any, Path, Transfer a full container to Android. Returns request_id for ACK matching.…, decode_binary_frame(), decode_prompt_bundle(), encode_binary_ping() (+11 more)

### Community 2 - "Android KeepAlive Service"
Cohesion: 0.10
Nodes (19): BleKeepAliveService, Intent, CancelRequest, ContainerAckResponse, ContainerSummary, ErrorResponse, IncomingEnvelope, ListContainersResponse (+11 more)

### Community 5 - "Android Compose UI (MainActivity)"
Cohesion: 0.14
Nodes (25): AppCard(), BannerButton(), BridgeScreen(), ContainersTab(), Intent, LogsTab(), MainActivity, outlinedColors() (+17 more)

### Community 6 - "Desktop PDF Context Store"
Cohesion: 0.12
Nodes (12): Chunk, Container, ContainerDoc, ContextStore, _extract_terms(), Any, Path, Context Store: named PDF containers with BM25-indexed chunks for knowledge base… (+4 more)

### Community 7 - "Android BLE Server Manager"
Cohesion: 0.14
Nodes (11): AdvertiseSettings, BleServerManager, AdvertiseCallback, ByteArray, BluetoothAdapter, BluetoothDevice, BluetoothGattCharacteristic, BluetoothGattServer (+3 more)

### Community 9 - "Android ViewModel & Updates"
Cohesion: 0.12
Nodes (7): StateFlow, LatestRelease, MainViewModel, SettingsRepository, AndroidViewModel, Context, SharedPreferences

### Community 11 - "Desktop App Bootstrap & Theme"
Cohesion: 0.12
Nodes (3): CTkinterDnD, Path, SSLContext

### Community 13 - "Desktop PDF Context Engine"
Cohesion: 0.29
Nodes (4): _Chunk, PdfContextEngine, Any, Path

### Community 14 - "Container Management UI"
Cohesion: 0.19
Nodes (3): Return the ID of the currently highlighted container (survives refresh)., User clicked a container row — update the highlighted index., Double-click on container row → activate/deactivate it.

### Community 15 - "BLE Frame Codec & Assembly"
Cohesion: 0.26
Nodes (6): BleFrame, BleFrameAssembler, BleFrameCodec, ByteArray, Pending, TransportIdGenerator

### Community 19 - "macOS Quick Action Installer"
Cohesion: 0.54
Nodes (7): _ensure_wrapper(), _info_plist_xml(), install(), main(), Path, _workflow_xml(), _write_wrapper()

## Knowledge Gaps
- **14 isolated node(s):** `StreamResponse`, `GenerationOutput`, `SETTINGS`, `CONTAINERS`, `LOGS` (+9 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 82 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **12 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DesktopChatApp` connect `Desktop App Core Logic` to `Prompt Bundle & Runtime State`, `Desktop BLE Client`, `Desktop UI Components`, `Desktop PDF Context Store`, `Desktop Screenshots & Media`, `Desktop UI Event Handlers`, `Desktop App Bootstrap & Theme`, `Desktop Chat Sessions Store`, `Desktop PDF Context Engine`, `Container Management UI`, `Desktop Overlay Window System`, `Desktop Settings & Request Mgmt`?**
  _High betweenness centrality (0.508) - this node is a cross-community bridge._
- **Why does `BleKeepAliveService` connect `Android KeepAlive Service` to `Prompt Bundle & Runtime State`, `Android ViewModel & Updates`, `Android BLE Server Manager`?**
  _High betweenness centrality (0.220) - this node is a cross-community bridge._
- **Why does `BleChatClient` connect `Desktop BLE Client` to `Prompt Bundle & Runtime State`, `Desktop App Bootstrap & Theme`, `Desktop App Core Logic`?**
  _High betweenness centrality (0.136) - this node is a cross-community bridge._
- **Are the 4 inferred relationships involving `DesktopChatApp` (e.g. with `BleChatClient` and `ChatSessionsStore`) actually correct?**
  _`DesktopChatApp` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `BleChatClient` (e.g. with `DesktopChatApp` and `FrameAssembler`) actually correct?**
  _`BleChatClient` has 4 INFERRED edges - model-reasoned connections that need verification._
- **What connects `StreamResponse`, `GenerationOutput`, `SETTINGS` to the rest of the system?**
  _14 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Prompt Bundle & Runtime State` be split into smaller, more focused modules?**
  _Cohesion score 0.05310734463276836 - nodes in this community are weakly interconnected._