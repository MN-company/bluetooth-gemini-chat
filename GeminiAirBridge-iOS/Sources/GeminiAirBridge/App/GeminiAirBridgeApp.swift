import SwiftUI
#if canImport(UIKit)
import UIKit

// MARK: - App Entry Point
@main
struct GeminiAirBridgeApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var service = BridgeService.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(service)
        }
    }
}

// MARK: - App Delegate
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        _ = BridgeService.shared
        return true
    }

    func applicationDidEnterBackground(_ application: UIApplication) {}

    func applicationWillTerminate(_ application: UIApplication) {
        BridgeService.shared.stop()
    }
}
#else
// macOS stub (not used, keeping for SPM build compatibility)
@main
struct GeminiAirBridgeApp: App {
    @StateObject private var service = BridgeService.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(service)
        }
    }
}
#endif