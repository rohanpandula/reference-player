// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
//  PrivateSettingsContent.swift
//  Rivulet
//
//  Reference Player: every Settings page, row and description this fork adds lives here. Upstream's
//  Settings files carry only the enum cases and one-line calls into this file, each listed in
//  `HOOKS` in PrivateBuild/verify.py, so an upstream merge touches as few private lines as possible.
//
//  Text: every private literal is `String(localized: "…", table: "Private")`, passed through each
//  funnel's verbatim path, so Xcode extracts it into Rivulet/Private.xcstrings and never into
//  upstream's Localizable.xcstrings. A helper taking `String.LocalizationValue` would not do: the
//  extractor files a literal passed to one under Localizable. Shared words upstream's catalog
//  already owns (On, Off, OK) keep upstream's key and table.
//

import UIKit

@MainActor
enum PrivateSettings {

    // MARK: Hooks called from upstream's Settings files

    /// `SettingsPage.title`
    static func title(for page: SettingsPage) -> String {
        switch page {
        case .heightSpeakersPicker: return String(localized: "Height Speakers", table: "Private")
        case .homeAssistant: return String(localized: "Home Assistant", table: "Private")
        case .formatTags: return String(localized: "Format Tags", table: "Private")
        case .metadataKeys: return String(localized: "Discover and Scores", table: "Private")
        default: return ""
        }
    }

    /// `SettingsDescriptorStore.pageInfo(for:)`
    static func pageInfo(for page: SettingsPage) -> (icon: String, color: UIColor) {
        switch page {
        case .heightSpeakersPicker: return ("hifispeaker.2", .systemCyan)
        case .homeAssistant: return ("house.fill", .systemTeal)
        case .formatTags: return ("tag.fill", .systemIndigo)
        case .metadataKeys: return ("key.horizontal.fill", .systemGreen)
        default: return ("gearshape.fill", .systemGray)
        }
    }

    /// `SettingsContent.allRows(for:)`
    static func rows(for page: SettingsPage) -> [SettingsRowItem] {
        switch page {
        case .heightSpeakersPicker: return heightSpeakersPicker
        case .homeAssistant: return homeAssistant
        case .formatTags: return formatTags
        case .metadataKeys: return metadataKeys
        default: return []
        }
    }

    /// Playback page, after Generated Subtitles.
    static var integrationRows: [SettingsRowItem] {
        [
            .header(LocalizedStringResource("Integrations", table: "Private")),
            SettingsRowItem(id: "homeAssistant", verbatim: String(localized: "Home Assistant", table: "Private"),
                            kind: .navigationValue(.homeAssistant, value: {
                                HomeAssistantReporter.isEnabled ? String(localized: "On") : String(localized: "Off")
                            })),
            SettingsRowItem(id: "formatTags", verbatim: String(localized: "Format Tags", table: "Private"),
                            kind: .navigationValue(.formatTags, value: {
                                FormatIndexStore.showsTags ? String(localized: "On") : String(localized: "Off")
                            })),
            SettingsRowItem(id: "metadataKeys", verbatim: String(localized: "Discover and Scores", table: "Private"),
                            kind: .navigationValue(.metadataKeys, value: {
                                // Which keys are entered, not whether the services accept them (Check Keys says that).
                                let keys = DirectMetadata.storedKeys
                                let entered = [keys.tmdb.isEmpty ? nil : "TMDB", keys.mdblist.isEmpty ? nil : "MDBList"].compactMap { $0 }
                                return entered.isEmpty ? String(localized: "Off") : entered.joined(separator: ", ")
                            })),
        ]
    }

    /// Playback page, Home Theater, after Upscale SD and 720p.
    static var homeTheaterRows: [SettingsRowItem] {
        [
            // Built here rather than through SettingsContent's private `toggle` helper; the same row.
            SettingsRowItem(id: "dolbyVisionFEL", verbatim: String(localized: "Dolby Vision FEL", table: "Private"), kind: .toggle(
                get: { SettingsStore.bool(AetherPlayer.dolbyVisionFELKey, default: false) },
                set: { SettingsStore.setBool(AetherPlayer.dolbyVisionFELKey, $0) }
            )),
            SettingsRowItem(id: "heightSpeakers", verbatim: String(localized: "Height Speakers", table: "Private"),
                            kind: .navigationValue(.heightSpeakersPicker, value: { heightSpeakers.isEmpty ? String(localized: "Off") : heightSpeakers })),
        ]
    }

    // MARK: Pages

    /// "" is Off; the rest are the layouts the engine renders heights into.
    private static var heightSpeakersPicker: [SettingsRowItem] {
        ["", "5.1.2", "5.1.4", "7.1.2", "7.1.4", "9.1.6"].map { layout in
            SettingsRowItem(id: "hs_\(layout.isEmpty ? "off" : layout)", verbatim: layout.isEmpty ? String(localized: "Off") : layout,
                            kind: .selectable(isSelected: { heightSpeakers == layout }, value: { nil }, handler: { vc in
                SettingsStore.setString(AetherPlayer.heightSpeakersKey, layout)
                (vc as? SettingsPageViewController)?.onPop?()
            }))
        }
    }

    private static var heightSpeakers: String {
        SettingsStore.string(AetherPlayer.heightSpeakersKey, default: "")
    }

    /// Reference Player: FEL/MEL and lossless-Atmos badges from the pre-computed format index
    /// (RivuletCore/Integrations/FormatIndex.swift, built by PrivateBuild/tools/format-index).
    private static var formatTags: [SettingsRowItem] {
        var rows = [
            SettingsRowItem(id: "ft_enabled", verbatim: String(localized: "FEL, MEL and TrueHD Atmos Tags", table: "Private"), kind: .toggle(
                get: { FormatIndexStore.showsTags },
                set: { FormatIndexStore.showsTags = $0 })),
            SettingsRowItem(id: "ft_url", verbatim: String(localized: "Index URL", table: "Private"), kind: .textEntry(
                value: { FormatIndexStore.indexURLString },
                placeholder: String(localized: "Index URL", table: "Private"),
                hint: String(localized: "Where the Apple TV fetches format-index.json, built from your library.", table: "Private"),
                suggestions: [],
                keyboardType: .URL,
                set: { FormatIndexStore.indexURLString = $0 })),
            SettingsRowItem(id: "ft_refresh", verbatim: String(localized: "Refresh Now", table: "Private"), kind: .action(destructive: false, handler: { vc in
                Task { @MainActor in
                    let ok = await FormatIndexStore.shared.refresh()
                    let store = FormatIndexStore.shared
                    let alert = UIAlertController(
                        title: ok ? String(localized: "Index Updated", table: "Private") : String(localized: "Index Not Updated", table: "Private"),
                        message: ok
                            ? String(localized: "\(store.entryCount) files, built \(store.generatedAt ?? "-").", table: "Private")
                            : (store.lastError ?? String(localized: "The index could not be fetched.", table: "Private")),
                        preferredStyle: .alert)
                    alert.addAction(UIAlertAction(title: String(localized: "OK"), style: .default))
                    vc.present(alert, animated: true)
                    (vc as? SettingsPageViewController)?.reloadRows()
                }
            })),
            SettingsRowItem(id: "ft_info", verbatim: String(localized: "Index", table: "Private"), kind: .info(value: {
                let store = FormatIndexStore.shared
                return store.entryCount == 0 ? String(localized: "Not loaded", table: "Private")
                    : String(localized: "\(store.entryCount) files, built \(store.generatedAt?.prefix(10) ?? "-")", table: "Private")
            })),
        ]
        for i in rows.indices.dropFirst() {
            rows[i].isEnabled = { FormatIndexStore.showsTags }
        }
        return rows
    }

    /// Reference Player: your own TMDB and MDBList keys (RivuletCore/Integrations/DirectMetadata.swift).
    private static var metadataKeys: [SettingsRowItem] {
        [
            SettingsRowItem(id: "mk_tmdb", verbatim: String(localized: "TMDB Key", table: "Private"), kind: .textEntry(
                value: { DirectMetadata.tmdbKey },
                placeholder: "",
                hint: String(localized: "Your TMDB API Key or API Read Access Token.", table: "Private"),
                suggestions: [],
                keyboardType: .asciiCapable, isSecure: true,
                set: { DirectMetadata.tmdbKey = $0 })),
            SettingsRowItem(id: "mk_mdblist", verbatim: String(localized: "MDBList Key", table: "Private"), kind: .textEntry(
                value: { DirectMetadata.mdblistKey },
                placeholder: "",
                hint: String(localized: "Your MDBList API key.", table: "Private"),
                suggestions: [],
                keyboardType: .asciiCapable, isSecure: true,
                set: { DirectMetadata.mdblistKey = $0 })),
            SettingsRowItem(id: "mk_check", verbatim: String(localized: "Check Keys", table: "Private"), kind: .action(destructive: false, handler: { vc in
                Task { @MainActor in
                    let result = await DirectMetadata.checkStoredKeys()
                    let accepted = result.tmdb == .accepted || result.mdblist == .accepted
                    let alert = UIAlertController(
                        title: String(localized: "Key Check", table: "Private"),
                        message: keyCheckLine("TMDB", result.tmdb) + "\n" + keyCheckLine("MDBList", result.mdblist)
                            + (accepted ? "\n\n" + String(localized: "Close and reopen the app to load what a new key turns on.", table: "Private") : ""),
                        preferredStyle: .alert)
                    alert.addAction(UIAlertAction(title: String(localized: "OK"), style: .default))
                    vc.present(alert, animated: true)
                }
            })),
        ]
    }

    private static func keyCheckLine(_ service: String, _ check: DirectMetadata.KeyCheck) -> String {
        switch check {
        case .notSet: return String(localized: "\(service): no key entered.", table: "Private")
        case .accepted: return String(localized: "\(service): key accepted.", table: "Private")
        case .refused(let status): return String(localized: "\(service): key refused (HTTP \(status)).", table: "Private")
        case .failed(let status): return String(localized: "\(service): could not check (HTTP \(status)). Try again later.", table: "Private")
        case .noAnswer: return String(localized: "\(service): no answer. Check the network.", table: "Private")
        }
    }

    /// Reference Player: playback reports to Home Assistant (RivuletCore/Integrations/HomeAssistantReporter.swift).
    private static var homeAssistant: [SettingsRowItem] {
        var rows = [
            SettingsRowItem(id: "ha_enabled", verbatim: String(localized: "Report Playback", table: "Private"), kind: .toggle(
                get: { HomeAssistantReporter.isEnabled },
                set: { HomeAssistantReporter.isEnabled = $0 })),
            SettingsRowItem(id: "ha_address", verbatim: String(localized: "Home Assistant Address", table: "Private"), kind: .textEntry(
                value: { HomeAssistantReporter.address },
                placeholder: String(localized: "Home Assistant Address", table: "Private"),
                hint: String(localized: "Where Home Assistant answers on your network, with its port.", table: "Private"),
                suggestions: [],
                keyboardType: .URL,
                set: { HomeAssistantReporter.address = $0 })),
            SettingsRowItem(id: "ha_webhook", verbatim: String(localized: "Webhook ID", table: "Private"), kind: .textEntry(
                value: { HomeAssistantReporter.webhookID },
                placeholder: String(localized: "Webhook ID", table: "Private"),
                hint: String(localized: "Home Assistant's rivulet_playback package must use the same id. Clear it to make a new one.", table: "Private"),
                suggestions: [],
                keyboardType: .asciiCapable,
                set: { HomeAssistantReporter.webhookID = $0 })),
            SettingsRowItem(id: "ha_test", verbatim: String(localized: "Send Test Report", table: "Private"), kind: .action(destructive: false, handler: { vc in
                Task { @MainActor in
                    let answered = await HomeAssistantReporter.shared.sendTest()
                    // Home Assistant answers OK for an unknown webhook id too, so an answer is not acceptance.
                    let alert = UIAlertController(
                        title: answered ? String(localized: "Report Sent", table: "Private") : String(localized: "Report Not Sent", table: "Private"),
                        message: answered
                            ? String(localized: "Home Assistant answered. Check that sensor.rivulet_playback now shows a test report; the webhook id must match its rivulet_playback package.", table: "Private")
                            : String(localized: "Home Assistant did not answer at that address.", table: "Private"),
                        preferredStyle: .alert)
                    alert.addAction(UIAlertAction(title: String(localized: "OK"), style: .default))
                    vc.present(alert, animated: true)
                }
            })),
        ]
        for i in rows.indices.dropFirst() {
            rows[i].isEnabled = { HomeAssistantReporter.isEnabled }
        }
        return rows
    }

    // MARK: Description panel (merged into `SettingsDescriptorStore.descriptors`)

    static let descriptors: [String: SettingDescriptor] = [
        "heightSpeakers": SettingDescriptor(
            icon: "hifispeaker.2",
            verbatim: String(localized: "Keeps the height channels of object-based soundtracks, lossless over HDMI. Off plays 7.1 without them. Some soundtracks need 7.1.4 or 9.1.6.", table: "Private")
        ),
        "formatTags": SettingDescriptor(
            icon: "tag.fill",
            verbatim: String(localized: "Names what a server cannot: whether a Dolby Vision Profile 7 file is FEL or MEL, read from an index built from your library files. Also names lossless Atmos as TrueHD Atmos.", table: "Private")
        ),
        "ft_enabled": SettingDescriptor(
            icon: "tag.fill",
            verbatim: String(localized: "Names DV FEL, DV MEL and TrueHD Atmos. Dolby Vision on a second video track plays as HDR and reads HDR beside DV FEL 2-track or DV MEL 2-track. Off shows plain badges.", table: "Private")
        ),
        "ft_url": SettingDescriptor(
            icon: "network",
            verbatim: String(localized: "The address of format-index.json, built from your library files with the index tools in PrivateBuild. With no address, nothing is fetched.", table: "Private")
        ),
        "ft_refresh": SettingDescriptor(
            icon: "arrow.clockwise",
            verbatim: String(localized: "Fetches the index again now. It also refreshes on its own at launch when the cached copy is older than six hours.", table: "Private")
        ),
        "ft_info": SettingDescriptor(
            icon: "info.circle",
            verbatim: String(localized: "How many files the loaded index describes and when it was built.", table: "Private")
        ),
        "homeAssistant": SettingDescriptor(
            icon: "house.fill",
            verbatim: String(localized: "Tells Home Assistant what is playing and how: codec, resolution, Dolby Vision route, audio format and height rendering. Off sends nothing.", table: "Private")
        ),
        "ha_enabled": SettingDescriptor(
            icon: "house.fill",
            verbatim: String(localized: "Posts a report when playback starts, changes or stops, and every ten seconds while it plays. Reports go to the address below, so give it Home Assistant's local address.", table: "Private")
        ),
        "ha_address": SettingDescriptor(
            icon: "network",
            verbatim: String(localized: "Where Home Assistant answers on your network, with its port. With no address, nothing is sent.", table: "Private")
        ),
        "ha_webhook": SettingDescriptor(
            icon: "key.horizontal",
            verbatim: String(localized: "The webhook id Home Assistant listens on. Put the same id in its rivulet_playback package; clear it to make a new one.", table: "Private")
        ),
        "ha_test": SettingDescriptor(
            icon: "paperplane",
            verbatim: String(localized: "Sends one test report now and says whether Home Assistant accepted it.", table: "Private")
        ),
        "metadataKeys": SettingDescriptor(
            icon: "key.horizontal.fill",
            verbatim: String(localized: "Discover, recommendations and scores come from TMDB and MDBList with your own free keys. Without a key nothing is fetched from them. Your server's details show either way.", table: "Private")
        ),
        "mk_tmdb": SettingDescriptor(
            icon: "film",
            verbatim: String(localized: "Your TMDB API Key or Read Access Token (themoviedb.org, Settings, API). It turns on Discover, recommendations and actor biographies. Reopen the app after.", table: "Private")
        ),
        "mk_mdblist": SettingDescriptor(
            icon: "star.leadinghalf.filled",
            verbatim: String(localized: "Your MDBList key, from mdblist.com under Preferences. It adds Metacritic and Letterboxd scores to a title's details. The free plan allows 1,000 lookups a day.", table: "Private")
        ),
        "mk_check": SettingDescriptor(
            icon: "checkmark.seal",
            verbatim: String(localized: "Asks each service once whether it accepts your key, and says what each answered: accepted, refused, or not checked because the service was busy or out of reach.", table: "Private")
        ),
        "dolbyVisionFEL": SettingDescriptor(
            icon: "4k.tv",
            verbatim: String(localized: "Plays the full Dolby Vision Profile 7 layer on a Dolby Vision TV, without AirPlay or Picture in Picture. Off plays it as 8.1.", table: "Private")
        ),
    ]
}

extension SettingDescriptor {
    /// Text already resolved (from the Private table). Upstream's init takes a catalog literal, which
    /// Xcode would extract into upstream's Localizable table.
    init(icon: String, verbatim description: String) {
        self.icon = icon
        self.description = description
    }
}
