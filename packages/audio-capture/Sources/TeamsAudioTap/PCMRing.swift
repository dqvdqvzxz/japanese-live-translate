import Foundation

/// A fixed-capacity byte ring shared between the ScreenCaptureKit callback
/// (single producer) and the stdout drain thread (single consumer).
///
/// Design note — this is *not* lock-free. It uses a mutex that is held only for
/// `memcpy` of at most a few kilobytes and is **never** held across a write
/// syscall: the consumer copies out, unlocks, and only then writes to stdout.
/// A lock-free SPSC ring would need `swift-atomics`, an external dependency we
/// chose not to add. The critical section is bounded and allocation-free, so
/// the audio callback cannot be blocked by slow I/O, which is the actual
/// requirement (see docs/architecture.md §5).
///
/// Overflow policy is drop-oldest: stale meeting audio is worthless, and
/// dropping it is far better than stalling the capture thread.
final class PCMRing {
    private let lock = NSLock()
    private var buffer: [UInt8]
    private let capacity: Int
    private var head = 0      // next write index
    private var available = 0 // bytes currently stored
    private(set) var droppedBytes = 0

    init(capacity: Int) {
        self.capacity = capacity
        self.buffer = [UInt8](repeating: 0, count: capacity)
    }

    /// Producer side. Returns the number of bytes dropped to make room.
    @discardableResult
    func write(_ bytes: UnsafeRawBufferPointer) -> Int {
        guard !bytes.isEmpty else { return 0 }
        lock.lock()
        defer { lock.unlock() }

        // A single write larger than the whole ring can only keep its tail.
        var source = bytes
        var dropped = 0
        if source.count > capacity {
            let skip = source.count - capacity
            source = UnsafeRawBufferPointer(rebasing: source[skip...])
            dropped += skip
        }

        let overflow = (available + source.count) - capacity
        if overflow > 0 {
            available -= overflow
            dropped += overflow
        }

        buffer.withUnsafeMutableBytes { dest in
            let first = min(source.count, capacity - head)
            dest.baseAddress!.advanced(by: head)
                .copyMemory(from: source.baseAddress!, byteCount: first)
            if first < source.count {
                dest.baseAddress!
                    .copyMemory(from: source.baseAddress!.advanced(by: first),
                                byteCount: source.count - first)
            }
        }
        head = (head + source.count) % capacity
        available = min(capacity, available + source.count)
        droppedBytes += dropped
        return dropped
    }

    /// Consumer side. Returns up to `max` bytes, or an empty array if idle.
    func read(max maxBytes: Int) -> [UInt8] {
        lock.lock()
        defer { lock.unlock() }
        let count = min(maxBytes, available)
        guard count > 0 else { return [] }

        let tail = ((head - available) % capacity + capacity) % capacity
        var out = [UInt8](repeating: 0, count: count)
        out.withUnsafeMutableBytes { dest in
            buffer.withUnsafeBytes { src in
                let first = min(count, capacity - tail)
                dest.baseAddress!.copyMemory(from: src.baseAddress!.advanced(by: tail),
                                             byteCount: first)
                if first < count {
                    dest.baseAddress!.advanced(by: first)
                        .copyMemory(from: src.baseAddress!, byteCount: count - first)
                }
            }
        }
        available -= count
        return out
    }

    var count: Int {
        lock.lock(); defer { lock.unlock() }
        return available
    }
}
