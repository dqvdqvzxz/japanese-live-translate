import Foundation

/// Control-channel messages. These go to **stderr** as newline-delimited JSON.
/// stdout carries nothing but raw PCM bytes so the audio stream stays byte-exact.
///
/// The protocol is specified in `docs/api-design.md` §2. Keep both in sync.
enum Control {
    private static let lock = NSLock()

    private static func emit(_ object: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else {
            return
        }
        lock.lock()
        defer { lock.unlock() }
        FileHandle.standardError.write(data)
        FileHandle.standardError.write(Data([0x0A]))
    }

    static func ready(rate: Int, channels: Int, frameBytes: Int) {
        emit(["type": "ready", "rate": rate, "channels": channels, "frame_bytes": frameBytes])
    }

    static func started(sourceID: String, name: String) {
        emit(["type": "started", "source_id": sourceID, "name": name])
    }

    static func level(rmsDBFS: Double) {
        // Rounded: the UI meter needs one decimal, and a stable value keeps the
        // control channel compressible and low-noise.
        emit(["type": "level", "rms_dbfs": (rmsDBFS * 10).rounded() / 10])
    }

    static func warning(code: String, message: String) {
        emit(["type": "warning", "code": code, "message": message])
    }

    static func error(code: String, message: String) {
        emit(["type": "error", "code": code, "message": message])
    }

    static func stopped(frames: Int, dropped: Int) {
        emit(["type": "stopped", "frames": frames, "dropped": dropped])
    }
}

/// Exit codes are part of the contract with the Python supervisor.
enum ExitCode: Int32 {
    case ok = 0
    case failure = 1
    case permissionDenied = 2
    case sourceNotFound = 3
    case badUsage = 64
}
