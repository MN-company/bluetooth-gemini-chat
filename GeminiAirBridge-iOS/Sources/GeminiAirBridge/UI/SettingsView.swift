import SwiftUI

// MARK: - Settings view
struct SettingsView: View {
    @StateObject private var service = BridgeService.shared
    @State private var apiKey: String = ""
    @State private var modelName: String = ""
    @Environment(\.dismiss) private var dismiss

    private let presetModels = [
        "phone-default",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-pro-preview-03-25",
        "gemini-2.5-flash-preview-04-17",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-2.0-pro-exp",
    ]

    var body: some View {
        NavigationStack {
            Form {
                Section("Gemini API") {
                    SecureField("API Key", text: $apiKey)
                        .autocorrectionDisabled()
                        .onAppear { apiKey = service.currentApiKey }

                    Picker("Model", selection: $modelName) {
                        ForEach(presetModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                    .onAppear { modelName = service.modelName }

                    Button("Save") {
                        service.saveApiKey(apiKey)
                        service.saveModel(modelName)
                        dismiss()
                    }
                    .buttonStyle(.borderedProminent)
                    .frame(maxWidth: .infinity)
                }

                Section("About") {
                    HStack {
                        Text("Version")
                        Spacer()
                        Text("0.3.0")
                            .foregroundColor(.secondary)
                    }
                    HStack {
                        Text("Protocol")
                        Spacer()
                        Text("BLE GATT v1")
                            .foregroundColor(.secondary)
                    }
                }

                Section("Tips") {
                    Text("1. Enter your Gemini API key from ai.google.dev")
                    Text("2. Tap Start to begin BLE advertising")
                    Text("3. On desktop, connect to 'Gemini AirBridge'")
                    Text("4. API key stays on this device, never shared")
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}