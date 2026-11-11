// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
//  PrivateSettingsRowsTests.swift
//  RivuletTests
//
//  Reference Player: pins the private Settings pages and rows (where they sit, what they say,
//  which preference each one writes) and their description panel entries, so moving them
//  into a private file can be shown to change nothing.
//

import XCTest
@testable import Rivulet

@MainActor
final class PrivateSettingsRowsTests: XCTestCase {

    private func kind(_ row: SettingsRowItem) -> String {
        switch row.kind {
        case .navigation(let page): return "navigation(\(page))"
        case .navigationValue(let page, _): return "navigationValue(\(page))"
        case .navigationAction(let page, _, _): return "navigationAction(\(page))"
        case .toggle: return "toggle"
        case .cycle: return "cycle"
        case .action(let destructive, _): return destructive ? "action(destructive)" : "action"
        case .info: return row.isHeader ? "header" : "info"
        case .option: return "option"
        case .selectable: return "selectable"
        case .textEntry(_, _, _, _, _, let isSecure, _, _): return isSecure ? "textEntry(secure)" : "textEntry"
        }
    }

    /// "id | kind | title" per row, in order.
    private func snapshot(_ page: SettingsPage) -> [String] {
        SettingsContent.rows(for: page).map { "\($0.id) | \(kind($0)) | \($0.title)" }
    }

    private func row(_ id: String, on page: SettingsPage) throws -> SettingsRowItem {
        try XCTUnwrap(SettingsContent.rows(for: page).first { $0.id == id }, "\(id) is not on \(page)")
    }

    /// Runs `body`, then puts `keys` back as they were.
    private func preservingDefaults(_ keys: [String], _ body: () throws -> Void) rethrows {
        let saved = keys.map { UserDefaults.standard.object(forKey: $0) }
        defer {
            for (key, value) in zip(keys, saved) {
                if let value { UserDefaults.standard.set(value, forKey: key) } else { UserDefaults.standard.removeObject(forKey: key) }
            }
        }
        try body()
    }

    func test_rootPage() {
        XCTAssertEqual(snapshot(.root), [
            "cat_appearance | navigation(appearance) | Appearance",
            "cat_playback | navigation(playback) | Playback",
            "cat_liveTV | navigation(liveTV) | Live TV",
            "cat_photos | navigation(photos) | Photos",
            "cat_servers | navigation(servers) | Servers",
            "cache | navigation(cache) | Cache & Storage",
            "iCloudSync | cycle | iCloud Sync",
            "cat_about | navigation(about) | About",
        ])
    }

    func test_playbackPage() {
        XCTAssertEqual(snapshot(.playback), [
            "hdr_Audio & Subtitles | header | Audio & Subtitles",
            "audioLanguage | navigationValue(audioLanguagePicker) | Audio Language",
            "subtitleMode | navigationValue(subtitleModePicker) | Subtitles",
            "subtitleLanguage | navigationValue(subtitleLanguagePicker) | Subtitle Language",
            "subtitleSDH | toggle | SDH Subtitles",
            "generatedSubtitles | selectable | Generated Subtitles (English)",
            "hdr_Integrations | header | Integrations",
            "homeAssistant | navigationValue(homeAssistant) | Home Assistant",
            "formatTags | navigationValue(formatTags) | Format Tags",
            "metadataKeys | navigationValue(metadataKeys) | Discover and Scores",
            "hdr_Resume | header | Resume",
            "instantResume | toggle | Instant Resume",
            "promptResumeOrRestart | toggle | Resume or Restart Prompt",
            "hdr_Streaming Quality | header | Streaming Quality",
            "homeStreamingQuality | navigationValue(homeQualityPicker) | Home",
            "awayStreamingQuality | navigationValue(awayQualityPicker) | Away",
            "hdr_Remote | header | Remote",
            "skipLength | navigationValue(skipIntervalPicker) | Skip Forward",
            "skipBackLength | navigationValue(skipBackIntervalPicker) | Skip Back",
            "hdr_Skip Buttons | header | Skip Buttons",
            "autoSkipIntro | cycle | Intro",
            "autoSkipAds | cycle | Ads",
            "autoSkipRecap | cycle | Recap",
            "useIntroDB | toggle | Community Marker Database",
            "hdr_Autoplay | header | Autoplay",
            "autoplayCountdown | navigationValue(autoplayCountdownPicker) | Countdown",
            "autoplayLimit | navigationValue(autoplayLimitPicker) | Limit",
            "atTheCredits | cycle | Credits",
            "cinemaTrailers | cycle | Trailers Before Movies",
            "hdr_Home Theater | header | Home Theater",
            "upscaleLowResolution | toggle | Upscale SD and 720p",
            "dolbyVisionFEL | toggle | Dolby Vision FEL",
            "heightSpeakers | navigationValue(heightSpeakersPicker) | Height Speakers",
            "hdr_Filtering | header | Filtering",
            "cat_contentFilter | navigationValue(contentFilter) | Content Filtering",
        ])
    }

    func test_privatePages() {
        XCTAssertEqual(snapshot(.heightSpeakersPicker), [
            "hs_off | selectable | Off",
            "hs_5.1.2 | selectable | 5.1.2",
            "hs_5.1.4 | selectable | 5.1.4",
            "hs_7.1.2 | selectable | 7.1.2",
            "hs_7.1.4 | selectable | 7.1.4",
            "hs_9.1.6 | selectable | 9.1.6",
        ])
        XCTAssertEqual(snapshot(.formatTags), [
            "ft_enabled | toggle | FEL, MEL and TrueHD Atmos Tags",
            "ft_url | textEntry | Index URL",
            "ft_refresh | action | Refresh Now",
            "ft_info | info | Index",
        ])
        XCTAssertEqual(snapshot(.metadataKeys), [
            "mk_tmdb | textEntry(secure) | TMDB Key",
            "mk_mdblist | textEntry(secure) | MDBList Key",
            "mk_check | action | Check Keys",
        ])
        XCTAssertEqual(snapshot(.homeAssistant), [
            "ha_enabled | toggle | Report Playback",
            "ha_address | textEntry | Home Assistant Address",
            "ha_webhook | textEntry | Webhook ID",
            "ha_test | action | Send Test Report",
        ])
    }

    func test_privatePageTitlesAndPanelHeaders() {
        let pages: [(SettingsPage, String, String, UIColor)] = [
            (.heightSpeakersPicker, "Height Speakers", "hifispeaker.2", .systemCyan),
            (.homeAssistant, "Home Assistant", "house.fill", .systemTeal),
            (.formatTags, "Format Tags", "tag.fill", .systemIndigo),
            (.metadataKeys, "Discover and Scores", "key.horizontal.fill", .systemGreen),
        ]
        for (page, title, icon, color) in pages {
            XCTAssertEqual(page.title, title)
            XCTAssertEqual(SettingsDescriptorStore.pageInfo(for: page).icon, icon, "\(page)")
            XCTAssertEqual(SettingsDescriptorStore.pageInfo(for: page).color, color, "\(page)")
        }
    }

    /// Each toggle or text row writes the preference named here.
    func test_rowsWriteTheirPreferenceKeys() throws {
        let toggles: [(SettingsPage, String, String)] = [
            (.playback, "upscaleLowResolution", "upscaleLowResolution"),
            (.playback, "dolbyVisionFEL", "dolbyVisionFEL"),
            (.playback, "instantResume", "continueWatchingInstantResume"),
            (.playback, "promptResumeOrRestart", "promptResumeOrRestart"),
            (.playback, "useIntroDB", "useIntroDB"),
            (.formatTags, "ft_enabled", "formatTagsEnabled"),
            (.homeAssistant, "ha_enabled", "homeAssistantReportingEnabled"),
        ]
        XCTAssertEqual(AetherPlayer.dolbyVisionFELKey, "dolbyVisionFEL")
        for (page, id, key) in toggles {
            guard case .toggle(let get, let set, _) = try row(id, on: page).kind else { return XCTFail("\(id) is not a toggle") }
            preservingDefaults([key]) {
                for value in [true, false] {
                    set(value)
                    XCTAssertEqual(UserDefaults.standard.object(forKey: key) as? Bool, value, "\(id) -> \(key)")
                    XCTAssertEqual(get(), value, id)
                }
            }
        }
        let texts: [(SettingsPage, String, String)] = [
            (.formatTags, "ft_url", "formatIndexURL"),
            (.homeAssistant, "ha_address", "homeAssistantAddress"),
            (.homeAssistant, "ha_webhook", "homeAssistantWebhookID"),
        ]
        for (page, id, key) in texts {
            guard case .textEntry(_, _, _, _, _, _, _, let set) = try row(id, on: page).kind else { return XCTFail("\(id) is not text") }
            preservingDefaults([key]) {
                set("http://example.invalid/x")
                XCTAssertEqual(UserDefaults.standard.string(forKey: key), "http://example.invalid/x", "\(id) -> \(key)")
            }
        }
    }

    func test_heightSpeakersPicker_offWritesTheEmptyLayout() throws {
        let ids = SettingsContent.rows(for: .heightSpeakersPicker).map(\.id)
        XCTAssertTrue(ids.contains("hs_off"))
        XCTAssertTrue(ids.contains("hs_5.1.2"))
        let key = AetherPlayer.heightSpeakersKey
        XCTAssertEqual(key, "heightSpeakers")
        try preservingDefaults([key]) {
            guard case .selectable(let isSelected, _, let handler) = try row("hs_5.1.2", on: .heightSpeakersPicker).kind else {
                return XCTFail("hs_5.1.2 is not selectable")
            }
            handler(UIViewController())
            XCTAssertEqual(UserDefaults.standard.string(forKey: key), "5.1.2")
            XCTAssertTrue(isSelected())
            guard case .selectable(let offSelected, _, let off) = try row("hs_off", on: .heightSpeakersPicker).kind else {
                return XCTFail("hs_off is not selectable")
            }
            off(UIViewController())
            XCTAssertEqual(UserDefaults.standard.string(forKey: key), "")
            XCTAssertTrue(offSelected())
            XCTAssertFalse(isSelected())
            XCTAssertEqual(try row("heightSpeakers", on: .playback).valueText, "Off")
        }
    }

    /// The rows under Format Tags and Home Assistant dim while their switch is off.
    func test_dependentRowsFollowTheirSwitch() throws {
        try preservingDefaults(["formatTagsEnabled", "homeAssistantReportingEnabled"]) {
            for (page, toggleID) in [(SettingsPage.formatTags, "ft_enabled"), (.homeAssistant, "ha_enabled")] {
                guard case .toggle(_, let set, _) = try row(toggleID, on: page).kind else { return XCTFail(toggleID) }
                let rows = SettingsContent.rows(for: page)
                for value in [true, false] {
                    set(value)
                    XCTAssertEqual(rows.map { $0.isEnabled() }, [true] + Array(repeating: value, count: rows.count - 1), "\(page)")
                }
            }
        }
    }

    /// Moved from SettingsUsageTelemetryTests: the private Home Theater rows are reported by page.
    func test_snapshot_keysPreferenceRowsByPage() {
        let snapshot = SettingsUsageTelemetry.snapshot()
        XCTAssertNotNil(snapshot["playback.dolbyVisionFEL"])
        XCTAssertNotNil(snapshot["playback.heightSpeakers"])
    }

    func test_privateDescriptors() {
        let expected: [(String, String, String)] = [
            ("heightSpeakers", "hifispeaker.2", "Keeps the height channels of object-based soundtracks, lossless over HDMI. Off plays 7.1 without them. Some soundtracks need 7.1.4 or 9.1.6."),
            ("formatTags", "tag.fill", "Names what a server cannot: whether a Dolby Vision Profile 7 file is FEL or MEL, read from an index built from your library files. Also names lossless Atmos as TrueHD Atmos."),
            ("ft_enabled", "tag.fill", "Names DV FEL, DV MEL and TrueHD Atmos. Dolby Vision on a second video track plays as HDR and reads HDR beside DV FEL 2-track or DV MEL 2-track. Off shows plain badges."),
            ("ft_url", "network", "The address of format-index.json, built from your library files with the index tools in PrivateBuild. With no address, nothing is fetched."),
            ("ft_refresh", "arrow.clockwise", "Fetches the index again now. It also refreshes on its own at launch when the cached copy is older than six hours."),
            ("ft_info", "info.circle", "How many files the loaded index describes and when it was built."),
            ("homeAssistant", "house.fill", "Tells Home Assistant what is playing and how: codec, resolution, Dolby Vision route, audio format and height rendering. Off sends nothing."),
            ("ha_enabled", "house.fill", "Posts a report when playback starts, changes or stops, and every ten seconds while it plays. Reports go to the address below, so give it Home Assistant's local address."),
            ("ha_address", "network", "Where Home Assistant answers on your network, with its port. With no address, nothing is sent."),
            ("ha_webhook", "key.horizontal", "The webhook id Home Assistant listens on. Put the same id in its rivulet_playback package; clear it to make a new one."),
            ("ha_test", "paperplane", "Sends one test report now and says whether Home Assistant accepted it."),
            ("metadataKeys", "key.horizontal.fill", "Discover, recommendations and scores come from TMDB and MDBList with your own free keys. Without a key nothing is fetched from them. Your server's details show either way."),
            ("mk_tmdb", "film", "Your TMDB API Key or Read Access Token (themoviedb.org, Settings, API). It turns on Discover, recommendations and actor biographies. Reopen the app after."),
            ("mk_mdblist", "star.leadinghalf.filled", "Your MDBList key, from mdblist.com under Preferences. It adds Metacritic and Letterboxd scores to a title's details. The free plan allows 1,000 lookups a day."),
            ("mk_check", "checkmark.seal", "Asks each service once whether it accepts your key, and says what each answered: accepted, refused, or not checked because the service was busy or out of reach."),
            ("dolbyVisionFEL", "4k.tv", "Plays the full Dolby Vision Profile 7 layer on a Dolby Vision TV, without AirPlay or Picture in Picture. Off plays it as 8.1."),
        ]
        for (id, icon, description) in expected {
            XCTAssertEqual(SettingsDescriptorStore.descriptor(for: id)?.icon, icon, id)
            XCTAssertEqual(SettingsDescriptorStore.descriptor(for: id)?.description, description, id)
            XCTAssertNotNil(SettingsDescriptorStore.descriptors[id], "\(id) is checked by the panel length test only from this table")
        }
        for id in ["hs_off", "hs_5.1.2", "hs_9.1.6"] {
            XCTAssertEqual(SettingsDescriptorStore.descriptor(for: id)?.description, expected[0].2, id)
        }
    }
}
