import Foundation
import ScreenCaptureKit

/// Bundle identifiers we surface as recommended capture targets.
/// Teams Desktop ships under two identifiers depending on the generation, and
/// Teams-in-the-browser means the audio belongs to the browser process.
enum KnownSources {
    static let teams: Set<String> = [
        "com.microsoft.teams",   // classic Teams
        "com.microsoft.teams2"   // new Teams
    ]
    static let browsers: Set<String> = [
        "com.google.Chrome",
        "com.microsoft.edgemac",
        "com.apple.Safari",
        "org.mozilla.firefox",
        "com.brave.Browser",
        "company.thebrowser.Browser"
    ]
    static var recommended: Set<String> { teams.union(browsers) }
}

struct SourceDescriptor: Encodable {
    let id: String
    let kind: String
    let name: String
    let bundle_id: String?
    let pid: Int?
    let is_recommended: Bool

    enum CodingKeys: String, CodingKey {
        case id, kind, name, bundle_id, pid, is_recommended
    }

    /// Encodes nil optionals as explicit `null` rather than omitting the key.
    ///
    /// Swift's synthesised encoder uses `encodeIfPresent` for optionals, which
    /// drops the key entirely. That left display sources with no `bundle_id`
    /// field at all, diverging from both the documented protocol and the test
    /// fake — and a fake whose shape differs from the real helper is a fake
    /// that can hide a bug.
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(kind, forKey: .kind)
        try container.encode(name, forKey: .name)
        try container.encode(bundle_id, forKey: .bundle_id)
        try container.encode(pid, forKey: .pid)
        try container.encode(is_recommended, forKey: .is_recommended)
    }
}

enum SourceEnumerator {
    /// `SCShareableContent` is the only supported way to discover capturable
    /// applications. It also doubles as our permission probe: it throws when
    /// Screen Recording has not been granted, which macOS requires even for
    /// audio-only capture.
    static func shareableContent() async throws -> SCShareableContent {
        try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    }

    static func list() async throws -> [SourceDescriptor] {
        let content = try await shareableContent()
        var seen = Set<String>()
        var out: [SourceDescriptor] = []

        for app in content.applications {
            let bundleID = app.bundleIdentifier
            // One entry per bundle: Electron apps expose several helper
            // processes, and the user thinks in terms of "Teams", not helpers.
            guard !bundleID.isEmpty, seen.insert(bundleID).inserted else { continue }
            out.append(SourceDescriptor(
                id: "app:\(bundleID)",
                kind: "application",
                name: app.applicationName.isEmpty ? bundleID : app.applicationName,
                bundle_id: bundleID,
                pid: Int(app.processID),
                is_recommended: KnownSources.recommended.contains(bundleID)
            ))
        }

        for display in content.displays {
            out.append(SourceDescriptor(
                id: "display:\(display.displayID)",
                kind: "display",
                name: "Entire system audio (display \(display.displayID))",
                bundle_id: nil,
                pid: nil,
                is_recommended: false
            ))
        }

        // Recommended first, then alphabetical, so Teams is at the top.
        out.sort {
            if $0.is_recommended != $1.is_recommended { return $0.is_recommended }
            return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
        }
        return out
    }

    /// Builds the capture filter for a source id of the form
    /// `app:<bundle-id>` or `display:<id>`.
    ///
    /// Note: ScreenCaptureKit has no audio-only filter. Even for an application
    /// audio tap the filter must name a display, so we anchor every filter to
    /// the main display and narrow it with the application list.
    static func filter(for sourceID: String,
                       content: SCShareableContent) throws -> (SCContentFilter, String) {
        guard let display = content.displays.first else {
            throw TapError.sourceNotFound("No display available to anchor the capture filter")
        }

        if sourceID.hasPrefix("app:") {
            let bundleID = String(sourceID.dropFirst("app:".count))

            // Include helper processes, not just the exact bundle id.
            //
            // Teams and every other Electron-style app render their content in
            // separate helper processes — `com.microsoft.teams2.helper` is the
            // WebView that a meeting actually runs in. Filtering on an exact
            // match captured the main process, so macOS showed the app as
            // being captured, while the meeting's audio came from a helper and
            // never arrived. Matching the prefix captures the whole app.
            let apps = content.applications.filter {
                $0.bundleIdentifier == bundleID
                    || $0.bundleIdentifier.hasPrefix(bundleID + ".")
            }
            guard !apps.isEmpty else {
                throw TapError.sourceNotFound("No running application with bundle id \(bundleID)")
            }
            let filter = SCContentFilter(display: display,
                                         including: apps,
                                         exceptingWindows: [])
            let name = apps.first(where: { $0.bundleIdentifier == bundleID })?.applicationName
                ?? apps[0].applicationName
            return (filter, name)
        }

        if sourceID.hasPrefix("display:") {
            let raw = String(sourceID.dropFirst("display:".count))
            guard let id = UInt32(raw),
                  let target = content.displays.first(where: { $0.displayID == id }) else {
                throw TapError.sourceNotFound("No display with id \(raw)")
            }
            return (SCContentFilter(display: target, excludingWindows: []),
                    "Display \(raw)")
        }

        throw TapError.sourceNotFound("Unrecognised source id \(sourceID)")
    }
}

enum TapError: Error, CustomStringConvertible {
    case sourceNotFound(String)
    case permissionDenied(String)
    case audioFormat(String)
    case other(String)

    var description: String {
        switch self {
        case .sourceNotFound(let m): return m
        case .permissionDenied(let m): return m
        case .audioFormat(let m): return m
        case .other(let m): return m
        }
    }

    var exitCode: ExitCode {
        switch self {
        case .sourceNotFound: return .sourceNotFound
        case .permissionDenied: return .permissionDenied
        default: return .failure
        }
    }
}
