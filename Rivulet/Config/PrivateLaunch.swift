// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition: launch work for the format index, called once from RivuletApp.init
// (after PrivatePlaybackDefaults.register(), where these lines used to sit).

import UIKit

@MainActor
enum PrivateLaunch {
    static func startFormatIndex() {
        // Reference Player: badges read FEL/MEL from the format index; the cache loads now, the network later.
        // Not in a hosted test run: the tests launch this app, and must not fetch from the user's network.
        if ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil {
            // A builder's own addresses (Rivulet/PersonalDefaults.plist, when present), filled in once.
            PersonalDefaults.seed(PersonalDefaults.bundled())
            FormatIndexStore.shared.startup()
            // tvOS keeps the app alive for days while the index may be rebuilt, so look again each
            // time the app comes back to the front.
            NotificationCenter.default.addObserver(forName: UIApplication.willEnterForegroundNotification,
                                                   object: nil, queue: .main) { _ in
                MainActor.assumeIsolated { _ = FormatIndexStore.shared.refreshIfStale() }
            }
        }
    }
}
