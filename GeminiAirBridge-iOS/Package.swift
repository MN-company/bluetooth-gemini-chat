// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "GeminiAirBridge",
    platforms: [
        .iOS(.v17),
    ],
    products: [
        .library(name: "GeminiAirBridge", targets: ["GeminiAirBridge"]),
    ],
    dependencies: [],
    targets: [
        .target(
            name: "GeminiAirBridge",
            dependencies: [],
            resources: []
        ),
    ]
)