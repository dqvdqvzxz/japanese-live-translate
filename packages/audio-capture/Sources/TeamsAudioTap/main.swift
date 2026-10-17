import Foundation

// teams-audio-tap
//
//   teams-audio-tap --list-sources
//   teams-audio-tap --capture <source-id> [--frame-ms 20]
//
// stdout: raw 24 kHz mono signed 16-bit little-endian PCM (capture mode), or a
//         single JSON object (list mode).
// stderr: newline-delimited JSON control messages.
//
// Protocol: docs/api-design.md §2.

struct Options {
    var listSources = false
    var sourceID: String?
    var frameMS = 20
    var includeMicrophone = false
}

func parseArguments(_ argv: [String]) -> Options? {
    var options = Options()
    var index = 1
    while index < argv.count {
        switch argv[index] {
        case "--list-sources":
            options.listSources = true
        case "--capture":
            index += 1
            guard index < argv.count else { return nil }
            options.sourceID = argv[index]
        case "--mic":
            options.includeMicrophone = true
        case "--frame-ms":
            index += 1
            guard index < argv.count, let value = Int(argv[index]), value > 0 else { return nil }
            options.frameMS = value
        case "--rate":
            // Accepted for forward compatibility and explicitness in the spawn
            // command, but 24 kHz is the only rate the STT model supports, so
            // anything else is a caller bug we refuse rather than silently honour.
            index += 1
            guard index < argv.count, argv[index] == "24000" else { return nil }
        case "--channels":
            index += 1
            guard index < argv.count, argv[index] == "1" else { return nil }
        case "-h", "--help":
            return nil
        default:
            return nil
        }
        index += 1
    }
    guard options.listSources != (options.sourceID != nil) else { return nil }
    return options
}

func usage() {
    FileHandle.standardError.write(Data("""
    usage:
      teams-audio-tap --list-sources
      teams-audio-tap --capture <source-id> [--mic] [--rate 24000] [--channels 1] [--frame-ms 20]

      --mic  also capture the microphone and mix it with the application audio
             (macOS 15 or later)

    """.utf8))
}

func runList() async -> Never {
    do {
        let sources = try await SourceEnumerator.list()
        let payload = try JSONEncoder().encode(["sources": sources])
        FileHandle.standardOutput.write(payload)
        FileHandle.standardOutput.write(Data([0x0A]))
        exit(ExitCode.ok.rawValue)
    } catch let error as TapError {
        Control.error(code: "enumeration_failed", message: error.description)
        exit(error.exitCode.rawValue)
    } catch {
        // SCShareableContent throws here when Screen Recording is not granted.
        Control.error(code: "permission_denied",
                      message: "Screen Recording permission is required to list audio sources. "
                             + "Grant it in System Settings > Privacy & Security > Screen Recording.")
        exit(ExitCode.permissionDenied.rawValue)
    }
}

func runCapture(sourceID: String, frameMS: Int, includeMicrophone: Bool) async -> Never {
    let tap = AudioTap(frameMS: frameMS, includeMicrophone: includeMicrophone)
    Control.ready(rate: Int(AudioTap.targetSampleRate),
                  channels: Int(AudioTap.targetChannels),
                  frameBytes: tap.frameBytes)

    // SIGINT is the documented graceful-stop signal. The default handler would
    // kill us before we could flush, so it is ignored and replaced.
    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, SIG_IGN)
    let sigint = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
    let sigterm = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
    let shutdown: @Sendable () -> Void = {
        Task {
            await tap.stop()
            exit(ExitCode.ok.rawValue)
        }
    }
    sigint.setEventHandler(handler: shutdown)
    sigterm.setEventHandler(handler: shutdown)
    sigint.resume()
    sigterm.resume()

    do {
        let name = try await tap.start(sourceID: sourceID)
        Control.started(sourceID: sourceID, name: name)
    } catch let error as TapError {
        Control.error(code: errorCode(for: error), message: error.description)
        exit(error.exitCode.rawValue)
    } catch {
        Control.error(code: "start_failed", message: error.localizedDescription)
        exit(ExitCode.failure.rawValue)
    }

    // Capture runs until a signal arrives.
    while true {
        try? await Task.sleep(nanoseconds: 1_000_000_000)
    }
}

func errorCode(for error: TapError) -> String {
    switch error {
    case .permissionDenied: return "permission_denied"
    case .sourceNotFound: return "source_not_found"
    case .audioFormat: return "audio_format"
    case .other: return "start_failed"
    }
}

guard let options = parseArguments(CommandLine.arguments) else {
    usage()
    exit(ExitCode.badUsage.rawValue)
}

let task = Task {
    if options.listSources {
        await runList()
    } else {
        await runCapture(sourceID: options.sourceID!,
                         frameMS: options.frameMS,
                         includeMicrophone: options.includeMicrophone)
    }
}
_ = task
dispatchMain()
