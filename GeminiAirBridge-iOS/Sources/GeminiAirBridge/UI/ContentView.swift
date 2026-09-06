import SwiftUI

// MARK: - Main content view
struct ContentView: View {
    @StateObject private var service = BridgeService.shared
    @State private var showSettings = false
    @State private var showLogs = false
    @State private var showContainers = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                // Status card
                VStack(spacing: 8) {
                    HStack {
                        Circle()
                            .fill(statusColor(service.bleState))
                            .frame(width: 12, height: 12)
                        Text(statusText(service.bleState))
                            .font(.headline)
                    }
                    Text(service.status)
                        .font(.subheadline)
                        .foregroundColor(.secondary)

                    if !service.apiKeySet {
                        Label("API key not set", systemImage: "key.fill")
                            .foregroundColor(.orange)
                            .font(.caption)
                    }
                }
                .padding()
                .frame(maxWidth: .infinity)
                .background(Color(.secondarySystemBackground))
                .cornerRadius(12)

                // Controls
                VStack(spacing: 12) {
                    if !service.apiKeySet {
                        Button(action: { showSettings = true }) {
                            Label("Set API Key", systemImage: "key")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.orange)
                    }

                    HStack(spacing: 16) {
                        Button(action: { service.start() }) {
                            Label("Start", systemImage: "play.fill")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(service.bleState != .idle || !service.apiKeySet)

                        Button(action: { service.stop() }) {
                            Label("Stop", systemImage: "stop.fill")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)
                        .tint(.red)
                        .disabled(service.bleState == .idle)
                    }
                }

                // Info rows
                GroupBox("Connection") {
                    InfoRow(label: "State", value: statusText(service.bleState))
                    if case .connected(let count) = service.bleState {
                        InfoRow(label: "Clients", value: "\(count)")
                    }
                    InfoRow(label: "Model", value: service.modelName)
                }

                // Quick actions
                HStack(spacing: 12) {
                    Button(action: { showSettings = true }) {
                        Label("Settings", systemImage: "gear")
                    }
                    .buttonStyle(.bordered)

                    Button(action: { showLogs = true }) {
                        Label("Logs", systemImage: "list.bullet")
                    }
                    .buttonStyle(.bordered)

                    Button(action: { showContainers = true }) {
                        Label("Containers", systemImage: "tray.full")
                    }
                    .buttonStyle(.bordered)
                }
                .font(.caption)
            }
            .padding()
            .navigationTitle("Gemini AirBridge")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: { showSettings = true }) {
                        Image(systemName: "gear")
                    }
                }
            }
            .sheet(isPresented: $showSettings) { SettingsView() }
            .sheet(isPresented: $showLogs) { LogView() }
            .sheet(isPresented: $showContainers) { ContainerListView() }
            .onAppear {
                service.start()
            }
        }
    }

    private func statusColor(_ state: BLEServerManager.State) -> Color {
        switch state {
        case .idle: return .gray
        case .starting: return .yellow
        case .advertising: return .blue
        case .connected: return .green
        case .error: return .red
        }
    }

    private func statusText(_ state: BLEServerManager.State) -> String {
        switch state {
        case .idle: return "Idle"
        case .starting: return "Starting..."
        case .advertising: return "Advertising"
        case .connected(let c): return "Connected (\(c))"
        case .error(let e): return "Error: \(e)"
        }
    }
}

// MARK: - Info row
struct InfoRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
                .foregroundColor(.secondary)
            Spacer()
            Text(value)
                .fontWeight(.medium)
        }
    }
}

// MARK: - Log view
struct LogView: View {
    @StateObject private var service = BridgeService.shared

    var body: some View {
        NavigationStack {
            List(service.logs.reversed(), id: \.self) { line in
                Text(line)
                    .font(.system(.caption, design: .monospaced))
                    .lineLimit(3)
            }
            .navigationTitle("Logs")
        }
    }
}

// MARK: - Container list view
struct ContainerListView: View {
    @State private var containers: [StoredContainer] = []
    @State private var isLoading = true

    var body: some View {
        NavigationStack {
            List(containers, id: \.id) { container in
                VStack(alignment: .leading) {
                    Text(container.name)
                        .font(.headline)
                    Text("\(container.chunks.count) chunks")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            .navigationTitle("Containers")
            .overlay {
                if containers.isEmpty && !isLoading {
                    ContentUnavailableView(
                        "No Containers",
                        systemImage: "tray",
                        description: Text("Loaded containers from desktop appear here.")
                    )
                }
            }
            .task {
                containers = ContainerStore.shared.all()
                isLoading = false
            }
        }
    }
}