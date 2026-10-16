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
    private let ring: PCMRing
    private let outputFormat: AVAudioFormat

    private var stream: SCStream?
    private var converter: AVAudioConverter?
    private var converterInputFormat: AVAudioFormat?

    private let sampleQueue = DispatchQueue(label: "jlt.audiotap.samples",
                                            qos: .userInitiated)
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

    init(frameMS: Int, ringSeconds: Double = 8.0) {
        self.frameMS = frameMS
        let capacity = Int(Self.targetSampleRate * Double(Self.bytesPerSample) * ringSeconds)
        self.ring = PCMRing(capacity: capacity)
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

        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: sampleQueue)
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
                        dropped: ring.droppedBytes / max(1, frameBytes))
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
            while !Thread.current.isCancelled && !self.stopping.value {
                let chunk = self.ring.read(max: self.frameBytes * 8)
                if chunk.isEmpty {
                    usleep(useconds_t(self.frameMS * 500)) // half a frame
                    continue
                }
                handle.write(Data(chunk))
                self.framesWritten += chunk.count / self.frameBytes
            }
        }
        thread.name = "jlt.audiotap.drain"
        thread.qualityOfService = .userInitiated
        thread.start()
        drainThread = thread
    }

    private func flushRemaining() {
        let handle = FileHandle.standardOutput
        while true {
            let chunk = ring.read(max: frameBytes * 8)
            if chunk.isEmpty { break }
            handle.write(Data(chunk))
            framesWritten += chunk.count / frameBytes
        }
    }

    // MARK: - SCStreamOutput

    func stream(_ stream: SCStream,
                didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, CMSampleBufferDataIsReady(sampleBuffer) else { return }
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription) else {
            return
        }

        var streamDescription = asbd.pointee
        guard let inputFormat = AVAudioFormat(streamDescription: &streamDescription) else { return }

        if converterInputFormat != inputFormat {
            // First buffer, or the system changed format mid-stream (output
            // device switch). Rebuilding the converter is the correct response.
            converter = AVAudioConverter(from: inputFormat, to: outputFormat)
            converter?.sampleRateConverterQuality = AVAudioQuality.high.rawValue
            converterInputFormat = inputFormat
        }
        guard let converter else { return }

        do {
            try sampleBuffer.withAudioBufferList { audioBufferList, _ in
                guard let input = AVAudioPCMBuffer(pcmFormat: inputFormat,
                                                   bufferListNoCopy: audioBufferList.unsafePointer)
                else { return }
                convertAndEnqueue(input, using: converter)
            }
        } catch {
            // A malformed buffer is not fatal; drop it and keep the stream alive.
            return
        }
    }

    private func convertAndEnqueue(_ input: AVAudioPCMBuffer, using converter: AVAudioConverter) {
        let ratio = outputFormat.sampleRate / input.format.sampleRate
        // +32 frames of slack absorbs the resampler's fractional carry.
        let capacity = AVAudioFrameCount(Double(input.frameLength) * ratio) + 32
        guard capacity > 0,
              let output = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: capacity)
        else { return }

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

        guard status != .error, output.frameLength > 0,
              let channelData = output.int16ChannelData else { return }

        let byteCount = Int(output.frameLength) * Self.bytesPerSample
        _ = channelData[0].withMemoryRebound(to: UInt8.self, capacity: byteCount) { raw in
            ring.write(UnsafeRawBufferPointer(start: raw, count: byteCount))
        }

        emitLevelIfDue(channelData[0], frames: Int(output.frameLength))
    }

    // MARK: - Level metering and silence detection

    private func emitLevelIfDue(_ samples: UnsafeMutablePointer<Int16>, frames: Int) {
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
