// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition. Playback remains entirely in upstream AetherPlayer.

import Foundation

/// Initial preferences, not a second playback policy or renderer. Dolby Vision FEL starts on;
/// Height Speakers is not registered, so it starts Off (no layout) until a user picks one.
/// Registration defaults never overwrite an explicit choice in Settings. Keeping
/// the original keys also keeps upstream's settings UI and migrations authoritative.
@MainActor
enum PrivatePlaybackDefaults {
    static func register(in defaults: UserDefaults = .standard) {
        defaults.register(defaults: [
            AetherPlayer.dolbyVisionFELKey: true
        ])
    }
}
