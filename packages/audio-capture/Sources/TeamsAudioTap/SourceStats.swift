import AVFoundation
import Foundation

/// Counters for one input — application audio or the microphone.
///
/// Exists to tell three failures apart that otherwise all read as a level of
/// -120 dBFS:
///
///   * nothing arrived       (`buffers == 0`)           — wrong source, or the
///                                                         stream never fed us;
///   * it arrived, could not be converted
///                           (`outputFrames == 0`)      — a format we mishandle;
///   * it arrived and converted, but every sample is 0
///                           (`peak == 0`)              — the OS delivered silence.
///
/// The pipeline used to drop buffers it could not parse without a trace, and the
/// backend's `frames_in` counts the helper's paced output clock rather than
/// anything that arrived, so none of those could be distinguished from outside.
///
/// Only counts, formats and peak levels are kept — never samples.
final class SourceStats {

    enum Rejection: Int {
        /// The buffer's format description was missing or not representable.
        case format = 0
        /// The sample buffer could not be exposed as an `AVAudioPCMBuffer`.
        case bufferList
        /// `AVAudioConverter` was unavailable or reported an error.
        case convert
    }

    struct Snapshot {
        let buffers: Int
        let inputFrames: Int
        let outputFrames: Int
        let peak: Int
        let rejected: [Int]
        let lastErrorCode: Int
        let rate: Double
        let channels: Int
        let bits: Int
        let nonInterleaved: Bool

        var peakDBFS: Double {
            peak > 0 ? 20 * log10(Double(peak) / 32768.0) : -120.0
        }

        var json: [String: Any] {
            var out: [String: Any] = [
                "buffers": buffers,
                "input_frames": inputFrames,
                "output_frames": outputFrames,
                "peak_dbfs": (peakDBFS * 10).rounded() / 10,
                "rejected": [
                    "format": rejected[Rejection.format.rawValue],
                    "buffer_list": rejected[Rejection.bufferList.rawValue],
                    "convert": rejected[Rejection.convert.rawValue]
                ]
            ]
            if buffers > 0 {
                out["format"] = [
                    "rate": Int(rate),
                    "channels": channels,
                    "bits": bits,
                    "non_interleaved": nonInterleaved
                ]
            }
            if lastErrorCode != 0 { out["last_error_code"] = lastErrorCode }
            return out
        }
    }

    private let lock = NSLock()
    private var buffers = 0
    private var inputFrames = 0
    private var outputFrames = 0
    private var peak: Int32 = 0
    // Sized once here so that bumping a counter in the capture callback never
    // allocates.
    private var rejected = [Int](repeating: 0, count: 3)
    private var lastErrorCode = 0
    private var rate = 0.0
    private var channels = 0
    private var bits = 0
    private var nonInterleaved = false

    /// A sample buffer arrived from ScreenCaptureKit, before any parsing.
    func received(frames: Int, format asbd: AudioStreamBasicDescription) {
        lock.lock(); defer { lock.unlock() }
        buffers += 1
        inputFrames += max(0, frames)
        rate = asbd.mSampleRate
        channels = Int(asbd.mChannelsPerFrame)
        bits = Int(asbd.mBitsPerChannel)
        nonInterleaved = asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0
    }

    /// Samples that made it through conversion to 24 kHz mono int16.
    func converted(_ samples: UnsafeBufferPointer<Int16>) {
        var localPeak: Int32 = 0
        for sample in samples {
            let magnitude = abs(Int32(sample))
            if magnitude > localPeak { localPeak = magnitude }
        }
        lock.lock(); defer { lock.unlock() }
        outputFrames += samples.count
        if localPeak > peak { peak = localPeak }
    }

    func reject(_ reason: Rejection, errorCode: Int = 0) {
        lock.lock(); defer { lock.unlock() }
        rejected[reason.rawValue] += 1
        if errorCode != 0 { lastErrorCode = errorCode }
    }

    func snapshot() -> Snapshot {
        lock.lock(); defer { lock.unlock() }
        return Snapshot(buffers: buffers,
                        inputFrames: inputFrames,
                        outputFrames: outputFrames,
                        peak: Int(peak),
                        rejected: rejected,
                        lastErrorCode: lastErrorCode,
                        rate: rate,
                        channels: channels,
                        bits: bits,
                        nonInterleaved: nonInterleaved)
    }
}
