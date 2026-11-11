// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition. Names the box a report comes from, so a dashboard fed by two Apple TVs
// can tell them apart.

import Foundation

nonisolated enum DeviceIdentity {
    /// The hardware identifier, e.g. "AppleTV11,1"; nil when the kernel does not answer.
    static var modelIdentifier: String? {
        var size = 0
        guard sysctlbyname("hw.machine", nil, &size, nil, 0) == 0, size > 1 else { return nil }
        var buffer = [CChar](repeating: 0, count: size)
        guard sysctlbyname("hw.machine", &buffer, &size, nil, 0) == 0 else { return nil }
        let value = String(cString: buffer)
        return value.isEmpty ? nil : value
    }

    /// The marketing name for an Apple TV identifier, or the identifier itself for anything else
    /// (a model newer than this table, a simulator's host architecture, an iPhone).
    static func marketingName(for identifier: String) -> String {
        switch identifier {
        case "AppleTV5,3": "Apple TV HD"
        case "AppleTV6,2": "Apple TV 4K (1st generation)"
        case "AppleTV11,1": "Apple TV 4K (2nd generation)"
        case "AppleTV14,1": "Apple TV 4K (3rd generation)"
        default: identifier
        }
    }

    /// "tvOS 26.1" style, from Foundation so it reads the same on every platform.
    static var osVersion: String {
        let v = ProcessInfo.processInfo.operatingSystemVersion
        let patch = v.patchVersion > 0 ? ".\(v.patchVersion)" : ""
        return "\(osName) \(v.majorVersion).\(v.minorVersion)\(patch)"
    }

    /// Each app injects its platform name at launch (`PlexAPI.platform`: "tvOS" by default, "iOS" or
    /// "macOS" from the other apps), so shared code carries no platform conditional.
    private static var osName: String { PlexAPI.platform }
}
