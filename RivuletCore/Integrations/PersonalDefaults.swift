// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition. A builder's own addresses for the two integrations, filled into
// Settings once so nobody types a long URL with the remote. They come from
// Rivulet/PersonalDefaults.plist, which is gitignored and bundled only when it exists
// (template: PrivateBuild/PersonalDefaults.example.plist). Without the file nothing is built in.

import Foundation

@MainActor
enum PersonalDefaults {
    static let seededKey = "personalDefaultsSeeded"
    /// The only keys read from the file; each is the Settings key it fills.
    static let keys = [FormatIndexStore.Keys.url, HomeAssistantReporter.Keys.address, HomeAssistantReporter.Keys.webhookID]

    /// Writes the file's values into `defaults` and turns each integration on, once per distinct
    /// set of values: a rebuild with changed values fills in again, and edits made in Settings
    /// afterwards stay. Returns whether anything was written.
    @discardableResult
    static func seed(_ file: [String: Any]?, into defaults: UserDefaults = .standard) -> Bool {
        var values: [String: String] = [:]
        for key in keys {
            if let value = (file?[key] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty {
                values[key] = value
            }
        }
        guard !values.isEmpty, defaults.dictionary(forKey: seededKey) as? [String: String] != values else { return false }
        for (key, value) in values { defaults.set(value, forKey: key) }
        if values[FormatIndexStore.Keys.url] != nil { defaults.set(true, forKey: FormatIndexStore.Keys.enabled) }
        if values[HomeAssistantReporter.Keys.address] != nil { defaults.set(true, forKey: HomeAssistantReporter.Keys.enabled) }
        defaults.set(values, forKey: seededKey)
        return true
    }

    static func bundled(_ bundle: Bundle = .main) -> [String: Any]? {
        bundle.url(forResource: "PersonalDefaults", withExtension: "plist")
            .flatMap { NSDictionary(contentsOf: $0) as? [String: Any] }
    }
}
