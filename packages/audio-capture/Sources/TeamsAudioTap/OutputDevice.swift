import CoreAudio
import Foundation

/// Where the Mac is playing sound right now, and what kind of thing that is.
///
/// Reading this needs no permission, unlike capture, so the UI can show whether
/// the user is on headphones or speakers before anything is recorded and
/// without asking them. It does not change how audio is captured: application
/// audio is tapped before it reaches the output device.
struct OutputDeviceInfo: Encodable {
    let name: String
    /// "built-in", "bluetooth", "usb", "hdmi", … or "unknown".
    let transport: String
    /// The built-in device's active output (`ispk` speakers, `hdpn` headphone
    /// jack); nil for devices that have no selectable data source.
    let data_source: String?
    /// "headphones", "speakers" or "other".
    let kind: String

    enum CodingKeys: String, CodingKey { case name, transport, data_source, kind }

    /// Explicit nulls, for the same reason as `SourceDescriptor`: a key that is
    /// sometimes absent makes the protocol harder to rely on.
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(name, forKey: .name)
        try container.encode(transport, forKey: .transport)
        try container.encode(data_source, forKey: .data_source)
        try container.encode(kind, forKey: .kind)
    }
}

enum OutputDevice {

    /// Pure, so it can be checked without any hardware attached.
    ///
    /// The built-in device says outright whether the jack is in use. For an
    /// external device nothing says "headphones", so a Bluetooth or USB output
    /// is taken to be a headset — right far more often than not, and only a
    /// label: nothing downstream depends on it.
    static func classify(transport: String, dataSource: String?) -> String {
        switch transport {
        case "built-in":
            return dataSource == "hdpn" ? "headphones" : "speakers"
        case "bluetooth", "bluetooth-le", "usb":
            return "headphones"
        case "hdmi", "displayport", "airplay", "thunderbolt":
            return "speakers"
        default:
            return "other"
        }
    }

    static func current() -> OutputDeviceInfo? {
        guard let deviceID = readUInt32(AudioObjectID(kAudioObjectSystemObject),
                                        selector: kAudioHardwarePropertyDefaultOutputDevice),
              deviceID != AudioObjectID(kAudioObjectUnknown) else {
            return nil
        }

        let transport = transportName(of: deviceID)
        let dataSource = fourCC(of: deviceID, selector: kAudioDevicePropertyDataSource)
        return OutputDeviceInfo(name: name(of: deviceID) ?? "Unknown device",
                                transport: transport,
                                data_source: dataSource,
                                kind: classify(transport: transport, dataSource: dataSource))
    }

    // MARK: - CoreAudio plumbing

    private static func read(_ object: AudioObjectID,
                             selector: AudioObjectPropertySelector,
                             scope: AudioObjectPropertyScope,
                             into pointer: UnsafeMutableRawPointer,
                             size byteCount: Int) -> Bool {
        var addr = AudioObjectPropertyAddress(mSelector: selector,
                                              mScope: scope,
                                              mElement: kAudioObjectPropertyElementMain)
        guard AudioObjectHasProperty(object, &addr) else { return false }
        var size = UInt32(byteCount)
        return AudioObjectGetPropertyData(object, &addr, 0, nil, &size, pointer) == noErr
    }

    private static func readUInt32(_ object: AudioObjectID,
                                   selector: AudioObjectPropertySelector,
                                   scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> UInt32? {
        var value: UInt32 = 0
        let ok = withUnsafeMutablePointer(to: &value) {
            read(object, selector: selector, scope: scope, into: $0,
                 size: MemoryLayout<UInt32>.size)
        }
        return ok ? value : nil
    }

    private static func name(of device: AudioObjectID) -> String? {
        var cfName: Unmanaged<CFString>?
        let ok = withUnsafeMutablePointer(to: &cfName) {
            read(device, selector: kAudioObjectPropertyName,
                 scope: kAudioObjectPropertyScopeGlobal, into: $0,
                 size: MemoryLayout<Unmanaged<CFString>?>.size)
        }
        guard ok, let cfName else { return nil }
        return cfName.takeRetainedValue() as String
    }

    private static func transportName(of device: AudioObjectID) -> String {
        guard let raw = readUInt32(device, selector: kAudioDevicePropertyTransportType) else {
            return "unknown"
        }
        switch raw {
        case kAudioDeviceTransportTypeBuiltIn: return "built-in"
        case kAudioDeviceTransportTypeBluetooth: return "bluetooth"
        case kAudioDeviceTransportTypeBluetoothLE: return "bluetooth-le"
        case kAudioDeviceTransportTypeUSB: return "usb"
        case kAudioDeviceTransportTypeHDMI: return "hdmi"
        case kAudioDeviceTransportTypeDisplayPort: return "displayport"
        case kAudioDeviceTransportTypeAirPlay: return "airplay"
        case kAudioDeviceTransportTypeThunderbolt: return "thunderbolt"
        case kAudioDeviceTransportTypeVirtual: return "virtual"
        case kAudioDeviceTransportTypeAggregate: return "aggregate"
        default: return "unknown"
        }
    }

    /// A four-character code property on the output scope, as text ("hdpn").
    private static func fourCC(of device: AudioObjectID,
                               selector: AudioObjectPropertySelector) -> String? {
        guard let raw = readUInt32(device, selector: selector,
                                   scope: kAudioDevicePropertyScopeOutput) else { return nil }
        let bytes = [UInt8((raw >> 24) & 0xFF), UInt8((raw >> 16) & 0xFF),
                     UInt8((raw >> 8) & 0xFF), UInt8(raw & 0xFF)]
        return String(bytes: bytes, encoding: .ascii)
    }
}
