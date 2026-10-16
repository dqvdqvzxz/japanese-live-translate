// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TeamsAudioTap",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "teams-audio-tap", targets: ["TeamsAudioTap"])
    ],
    targets: [
        .executableTarget(
            name: "TeamsAudioTap",
            path: "Sources/TeamsAudioTap"
        )
    ]
)
