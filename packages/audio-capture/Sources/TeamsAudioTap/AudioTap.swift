import AVFoundation
import Foundation
import ScreenCaptureKit

/// Captures application (or display) audio via ScreenCaptureKit, converts it to
/// the one format the OpenAI realtime transcription model accepts — 24 kHz mono
/// signed 16-bit little-endian PCM — and streams it to stdout.
///
/// Why we convert ourselves instead of asking SCStreamConfiguration for
/// 24 kHz mono: the configuration's sample rate and channel count are requests,
/// and the delivered `CMSampleBuffer` format is authoritative. Reading the
/// actual input format from each buffer and converting with `AVAudioConverter`
/// is correct regardless of what the system decides to hand us, and it survives
/// a mid-stream format change (which happens when the user switches output
/// devices, e.g. plugging in headphones).
final class AudioTap: NSObject, SCStreamOutput, SCStreamDelegate {

    static let targetSampleRate: Double = 24_000
    static let targetChannels: AVAudioChannelCount = 1
    static let bytesPerSample = 2

    private let frameMS: Int
    private let includeMicrophone: Bool
    private let ring: PCMRing
    /// A separate ring for the microphone. The two streams arrive on their own
    /// schedules and from different clocks, so they are buffered apart and
    /// summed at drain time rather than interleaved as they land.
    private let micRing: PCMRing
    private let outputFormat: AVAudioFormat

    private var stream: SCStream?
    private var converter: AVAudioConverter?
    private var converterInputFormat: AVAudioFormat?
    private var micConverter: AVAudioConverter?
    private var micConverterInputFormat: AVAudioFormat?

    /// What actually arrived from each input; reported from the drain thread.
    private let appStats = SourceStats()
    private let micStats = SourceStats()
    /// True only once the `.microphone` output is registered, which is not the
    /// same as the microphone having been requested: a denied permission or an
    /// unsupported OS leaves it false.
    private var micActive = false
    private var streamStartedAt = Date()
    private var lastStatsEmit = Date()
    private var diagnosed = Set<String>()

    private let sampleQueue = DispatchQueue(label: "jlt.audiotap.samples",
                                            qos: .userInitiated)
    private let micQueue = DispatchQueue(label: "jlt.audiotap.mic", qos: .userInitiated)
    private var drainThread: Thread?
    private let stopping = AtomicFlag()

    private var framesWritten = 0
    private var lastLevelEmit = Date.distantPast
    private var lastAudioAt = Date()
    private var warnedNoAudio = false

    /// Bytes per 20 ms frame at the target format: 480 samples * 2 bytes.
    var frameBytes: Int {
        Int(Self.targetSampleRate) * Self.bytesPerSample * frameMS / 1000
    }

    init(frameMS: Int, includeMicrophone: Bool = false, ringSeconds: Double = 8.0) {
        self.frameMS = frameMS
        self.includeMicrophone = includeMicrophone
        let capacity = Int(Self.targetSampleRate * Double(Self.bytesPerSample) * ringSeconds)
        self.ring = PCMRing(capacity: capacity)
        self.micRing = PCMRing(capacity: capacity)
        guard let fmt = AVAudioFormat(commonFormat: .pcmFormatInt16,
                                      sampleRate: Self.targetSampleRate,
                                      channels: Self.targetChannels,
                                      interleaved: true) else {
            fatalError("24 kHz mono int16 is always constructible")
        }
        self.outputFormat = fmt
        super.init()
    }

    // MARK: - Lifecycle

    func start(sourceID: String) async throws -> String {
        let content: SCShareableContent
        do {
            content = try await SourceEnumerator.shareableContent()
        } catch {
            // SCShareableContent is also our permission probe: it fails when
            // Screen Recording has not been granted.
            throw TapError.permissionDenied(
                "Screen Recording permission is required to capture application audio. "
                + "Grant it in System Settings > Privacy & Security > Screen Recording, then restart the app."
            )
        }

        let (filter, name) = try SourceEnumerator.filter(for: sourceID, content: content)

        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        // ScreenCaptureKit always runs a video pipeline even in an audio-only
        // tap, so we shrink it to the minimum the API accepts and ask for the
        // slowest frame rate. We never register a `.screen` output, so no video
        // frame is ever delivered or copied.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.queueDepth = 5
        // Requested, not guaranteed; the converter handles whatever arrives.
        config.sampleRate = Int(Self.targetSampleRate)
        config.channelCount = Int(Self.targetChannels)

        if includeMicrophone {
            if #available(macOS 15.0, *) {
                // Ask for the permission explicitly rather than letting
                // ScreenCaptureKit discover it is missing. Without this, a
                // denied or never-requested microphone produces no buffers,
                // no error and no prompt — the capture simply runs silent,
                // which is indistinguishable from a quiet room.
                guard requestMicrophoneAccess() else {
                    Control.warning(
                        code: "microphone_denied",
                        message: "Microphone access was not granted. Enable it in System Settings "
                               + "> Privacy & Security > Microphone, then restart the app. "
                               + "Continuing with application audio only."
                    )
                    config.captureMicrophone = false
                    return try await startStream(filter: filter, config: config, name: name)
                }
                config.captureMicrophone = true
            } else {
                Control.warning(
                    code: "microphone_unsupported",
                    message: "Microphone capture needs macOS 15 or later; continuing with application audio only."
                )
            }
        }

        return try await startStream(filter: filter, config: config, name: name)
    }

    /// Block until the user answers the microphone prompt.
    ///
    /// `AVCaptureDevice` is what actually triggers the system dialog;
    /// ScreenCaptureKit assumes the permission already exists.
    private func requestMicrophoneAccess() -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return true
        case .notDetermined:
            let gate = DispatchSemaphore(value: 0)
            var granted = false
            AVCaptureDevice.requestAccess(for: .audio) { allowed in
                granted = allowed
                gate.signal()
            }
            gate.wait()
            return granted
        default:
            return false
        }
    }

    private func startStream(filter: SCContentFilter,
                             config: SCStreamConfiguration,
                             name: String) async throws -> String {
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: sampleQueue)
        if includeMicrophone, #available(macOS 15.0, *), config.captureMicrophone {
            do {
                try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: micQueue)
                micActive = true
            } catch {
                // Losing the microphone should not lose the meeting: carry on
                // with application audio and say what happened.
                Control.warning(
                    code: "microphone_unavailable",
                    message: "Could not start microphone capture: \(error.localizedDescription)"
                )
            }
        }
        try await stream.startCapture()
        self.stream = stream

        startDrain()
        return name
    }

    func stop() async {
        guard !stopping.exchange(true) else { return }
        if let stream {
            try? await stream.stopCapture()
            self.stream = nil
        }
        // Let the drain thread flush what is still buffered before we report.
        drainThread?.cancel()
        flushRemaining()
        Control.stopped(frames: framesWritten,
                        dropped: (ring.droppedBytes + micRing.droppedBytes)
                            / max(1, frameBytes))
    }

    // MARK: - stdout drain
    //
    // Runs on a dedicated thread so a slow or blocked stdout can never reach
    // the ScreenCaptureKit callback. Writes are frame-aligned so a reader that
    // stops mid-stream still sees whole frames.

    private func startDrain() {
        let thread = Thread { [weak self] in
            guard let self else { return }
            let handle = FileHandle.standardOutput
            let frameInterval = Double(self.frameMS) / 1000.0
            var nextDeadline = Date().timeIntervalSinceReferenceDate
            self.streamStartedAt = Date()
            self.lastStatsEmit = Date()

            while !Thread.current.isCancelled && !self.stopping.value {
                // Paced by the wall clock, one frame per frame interval.
                //
                // Reading "whatever is available" from each ring and writing
                // it meant that whenever only one ring happened to be full the
                // streams were emitted one after the other rather than summed
                // — producing roughly 2.7x realtime audio, which no
                // transcriber can make sense of. A fixed cadence makes the
                // output exactly realtime regardless of which source is
                // currently delivering.
                nextDeadline += frameInterval
                let chunk = self.mixedFrame()
                handle.write(Data(chunk))
                self.framesWritten += 1
                self.reportIfDue()

                let sleepFor = nextDeadline - Date().timeIntervalSinceReferenceDate
                if sleepFor > 0 {
                    usleep(useconds_t(sleepFor * 1_000_000))
                } else {
                    // Fell behind — give up the lost time rather than
                    // sprinting to catch up and emitting faster than realtime.
                    nextDeadline = Date().timeIntervalSinceReferenceDate
                }
            }
        }
        thread.name = "jlt.audiotap.drain"
        thread.qualityOfService = QualityOfService.userInitiated
        thread.start()
        drainThread = thread
    }

    /// Exactly one frame of output, summing whatever each ring has.
    ///
    /// A ring that is empty or short contributes silence, so a source that is
    /// quiet — or absent entirely — simply does not add anything, and the
    /// stream keeps running at realtime either way. That also keeps frames
    /// flowing when the captured application is silent, which previously
    /// stalled the whole pipeline with no indication.
    ///
    /// Sums are clamped rather than allowed to wrap: a wrapped overflow turns
    /// a loud moment into white noise, far worse for transcription than a
    /// clipped peak.
    private func mixedFrame() -> [UInt8] {
        let size = frameBytes
        var app = ring.read(max: size)
        if app.count < size { app += [UInt8](repeating: 0, count: size - app.count) }

        guard includeMicrophone else {
            meter(app)
            return app
        }

        var mic = micRing.read(max: size)
        if mic.count < size { mic += [UInt8](repeating: 0, count: size - mic.count) }

        var mixed = [UInt8](repeating: 0, count: size)
        app.withUnsafeBytes { a in
            mic.withUnsafeBytes { m in
                mixed.withUnsafeMutableBytes { out in
                    let aS = a.bindMemory(to: Int16.self)
                    let mS = m.bindMemory(to: Int16.self)
                    let oS = out.bindMemory(to: Int16.self)
                    for i in 0..<oS.count {
                        let sum = Int32(aS[i]) + Int32(mS[i])
                        oS[i] = Int16(max(-32768, min(32767, sum)))
                    }
                }
            }
        }
        meter(mixed)
        return mixed
    }

    /// Report the level of what is actually being sent.
    private func meter(_ chunk: [UInt8]) {
        guard !chunk.isEmpty else { return }
        chunk.withUnsafeBytes { raw in
            let samples = raw.bindMemory(to: Int16.self)
            emitLevelIfDue(samples, frames: samples.count)
        }
    }

    /// Emit whatever is still buffered when stopping, without the pacing.
    private func flushRemaining() {
        let handle = FileHandle.standardOutput
        while ring.count > 0 || micRing.count > 0 {
            handle.write(Data(mixedFrame()))
            framesWritten += 1
        }
    }

    // MARK: - SCStreamOutput

    func stream(_ stream: SCStream,
                didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard CMSampleBufferDataIsReady(sampleBuffer) else { return }

        if #available(macOS 15.0, *), type == .microphone {
            handleMicrophone(sampleBuffer)
            return
        }
        guard type == .audio else { return }
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription) else {
            appStats.reject(.format)
            return
        }

        var streamDescription = asbd.pointee
        appStats.received(frames: CMSampleBufferGetNumSamples(sampleBuffer), format: streamDescription)
        guard let inputFormat = AVAudioFormat(streamDescription: &streamDescription) else {
            appStats.reject(.format)
            return
        }

        if converterInputFormat != inputFormat {
            // First buffer, or the system changed format mid-stream (output
            // device switch). Rebuilding the converter is the correct response.
            converter = AVAudioConverter(from: inputFormat, to: outputFormat)
            converter?.sampleRateConverterQuality = AVAudioQuality.high.rawValue
            converterInputFormat = inputFormat
        }
        guard let converter else {
            appStats.reject(.convert)
            return
        }

        do {
            try sampleBuffer.withAudioBufferList { audioBufferList, _ in
                guard let input = AVAudioPCMBuffer(pcmFormat: inputFormat,
                                                   bufferListNoCopy: audioBufferList.unsafePointer)
                else {
                    appStats.reject(.bufferList)
                    return
                }
                enqueue(input, using: converter, into: ring, counting: appStats)
            }
        } catch {
            // A malformed buffer is not fatal; drop it and keep the stream
            // alive — but count it, so a stream of them is not invisible.
            appStats.reject(.bufferList, errorCode: (error as NSError).code)
        }
    }

    @available(macOS 15.0, *)
    private func handleMicrophone(_ sampleBuffer: CMSampleBuffer) {
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription)
        else {
            micStats.reject(.format)
            return
        }

        var streamDescription = asbd.pointee
        micStats.received(frames: CMSampleBufferGetNumSamples(sampleBuffer), format: streamDescription)
        guard let inputFormat = AVAudioFormat(streamDescription: &streamDescription) else {
            micStats.reject(.format)
            return
        }

        if micConverterInputFormat != inputFormat {
            micConverter = AVAudioConverter(from: inputFormat, to: outputFormat)
            micConverter?.sampleRateConverterQuality = AVAudioQuality.high.rawValue
            micConverterInputFormat = inputFormat
        }
        guard let micConverter else {
            micStats.reject(.convert)
            return
        }

        do {
            try sampleBuffer.withAudioBufferList { audioBufferList, _ in
                guard let input = AVAudioPCMBuffer(pcmFormat: inputFormat,
                                                   bufferListNoCopy: audioBufferList.unsafePointer)
                else {
                    micStats.reject(.bufferList)
                    return
                }
                enqueue(input, using: micConverter, into: micRing, counting: micStats)
            }
        } catch {
            micStats.reject(.bufferList, errorCode: (error as NSError).code)
        }
    }

    /// Convert one input buffer to 24 kHz mono int16 and append it to `ring`.
    /// Shared by both inputs, so they cannot drift apart in behaviour.
    private func enqueue(_ input: AVAudioPCMBuffer,
                         using converter: AVAudioConverter,
                         into ring: PCMRing,
                         counting stats: SourceStats) {
        let ratio = outputFormat.sampleRate / input.format.sampleRate
        // +32 frames of slack absorbs the resampler's fractional carry.
        let capacity = AVAudioFrameCount(Double(input.frameLength) * ratio) + 32
        guard let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity) else {
            stats.reject(.convert)
            return
        }

        var consumed = false
        var conversionError: NSError?
        let status = converter.convert(to: output, error: &conversionError) { _, outStatus in
            if consumed {
                outStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            outStatus.pointee = .haveData
            return input
        }

        guard status != .error, let channelData = output.int16ChannelData else {
            stats.reject(.convert, errorCode: conversionError?.code ?? 0)
            return
        }
        // A resampler may legitimately emit nothing for a very short input
        // while it fills its filter; that is not a failure.
        guard output.frameLength > 0 else { return }

        stats.converted(UnsafeBufferPointer(start: channelData[0], count: Int(output.frameLength)))
        let byteCount = Int(output.frameLength) * Self.bytesPerSample
        _ = channelData[0].withMemoryRebound(to: UInt8.self, capacity: byteCount) { raw in
            ring.write(UnsafeRawBufferPointer(start: raw, count: byteCount))
        }
    }

    // MARK: - Arrival statistics

    /// How long to wait before concluding a source is not delivering.
    private static let diagnoseAfter: TimeInterval = 8
    private static let statsInterval: TimeInterval = 5

    /// Called from the drain thread once per frame; does real work every
    /// `statsInterval` seconds only.
    private func reportIfDue() {
        let now = Date()
        guard now.timeIntervalSince(lastStatsEmit) >= Self.statsInterval else { return }
        lastStatsEmit = now

        let app = appStats.snapshot()
        let mic = micActive ? micStats.snapshot() : nil
        Control.stats(app: app.json, mic: mic?.json)

        let elapsed = now.timeIntervalSince(streamStartedAt)
        guard elapsed >= Self.diagnoseAfter else { return }
        diagnose(app, label: "app", elapsed: elapsed)
        if let mic { diagnose(mic, label: "mic", elapsed: elapsed) }
    }

    /// Name the failure, once. The three cases have different causes, so they
    /// get different codes and the message says what to try.
    private func diagnose(_ s: SourceStats.Snapshot, label: String, elapsed: TimeInterval) {
        let seconds = Int(elapsed)
        let code: String
        let message: String
        if s.buffers == 0 {
            code = "no_\(label)_buffers"
            message = label == "app"
                ? "ScreenCaptureKit has delivered no audio from the selected application in \(seconds)s: "
                  + "none of the processes it matched is playing sound. Try 'Entire system audio'."
                : "The microphone is on, but ScreenCaptureKit has delivered no microphone audio in \(seconds)s."
        } else if s.outputFrames == 0 {
            code = "\(label)_convert_failed"
            message = "Audio from the \(label == "app" ? "application" : "microphone") is arriving "
                    + "but cannot be converted to the transcription format (\(s.rate) Hz, "
                    + "\(s.channels) ch, \(s.bits)-bit)."
        } else if s.peak == 0 {
            code = "\(label)_all_zero"
            message = label == "app"
                ? "The selected application is delivering audio, but every sample has been zero for "
                  + "\(seconds)s. Try 'Entire system audio'."
                : "The microphone is delivering audio, but every sample has been zero for \(seconds)s. "
                  + "Check the input device and input volume in System Settings > Sound > Input."
        } else {
            return
        }
        guard diagnosed.insert(code).inserted else { return }
        Control.warning(code: code, message: message)
    }

    // MARK: - Level metering and silence detection

    private func emitLevelIfDue(_ samples: UnsafeBufferPointer<Int16>, frames: Int) {
        guard frames > 0 else { return }
        var sumSquares = 0.0
        for i in 0..<frames {
            let v = Double(samples[i]) / 32768.0
            sumSquares += v * v
        }
        let rms = (sumSquares / Double(frames)).squareRoot()
        let dbfs = rms > 0 ? 20 * log10(rms) : -120.0

        let now = Date()
        if dbfs > -70 {
            lastAudioAt = now
            warnedNoAudio = false
        } else if !warnedNoAudio, now.timeIntervalSince(lastAudioAt) > 10 {
            warnedNoAudio = true
            Control.warning(code: "no_audio",
                            message: "No audio from the selected source for 10s. "
                                   + "Check that it is actually playing sound.")
        }

        // ~4 Hz is enough for a meter and keeps the control channel quiet.
        if now.timeIntervalSince(lastLevelEmit) >= 0.25 {
            lastLevelEmit = now
            Control.level(rmsDBFS: dbfs)
        }
    }

    // MARK: - SCStreamDelegate

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        Control.error(code: "stream_stopped", message: error.localizedDescription)
        stopping.set(true)
        exit(ExitCode.failure.rawValue)
    }
}

/// Minimal thread-safe boolean. Avoids pulling in swift-atomics for one flag.
final class AtomicFlag {
    private let lock = NSLock()
    private var storage = false

    var value: Bool {
        lock.lock(); defer { lock.unlock() }
        return storage
    }

    func set(_ newValue: Bool) {
        lock.lock(); storage = newValue; lock.unlock()
    }

    /// Sets and returns the previous value, so callers can make stop idempotent.
    func exchange(_ newValue: Bool) -> Bool {
        lock.lock(); defer { lock.unlock() }
        let old = storage
        storage = newValue
        return old
    }
}
