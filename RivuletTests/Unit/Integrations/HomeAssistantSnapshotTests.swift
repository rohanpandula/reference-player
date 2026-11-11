// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player: the pure mapping inside the player's Home Assistant snapshot
// (UniversalPlayerViewModel+HomeAssistant.swift).

import XCTest
@testable import Rivulet

@MainActor
final class HomeAssistantSnapshotTests: XCTestCase {

    func testHDRFormatForEachDolbyVisionPlayback() {
        let hdr = UniversalPlayerViewModel.homeAssistantHDRFormat
        XCTAssertEqual(hdr(.dualLayer), "Dolby Vision")
        XCTAssertEqual(hdr(.dolbyVision), "Dolby Vision")
        XCTAssertEqual(hdr(.convertedTo81), "Dolby Vision")
        XCTAssertEqual(hdr(.dolbyVisionUnconfirmed), "Dolby Vision (TV not confirmed)")
        XCTAssertEqual(hdr(.convertedTo81Unconfirmed), "Dolby Vision (TV not confirmed)")
        XCTAssertEqual(hdr(.hdr10BaseLayer), "HDR10")
        XCTAssertEqual(hdr(.sdr), "SDR")
    }

    func testEmptyLayoutReportsOff() {
        XCTAssertEqual(UniversalPlayerViewModel.homeAssistantHeightSpeakers(
            layoutName: AetherPlayer.heightLayoutName(forSetting: "")), "Off")
        XCTAssertEqual(UniversalPlayerViewModel.homeAssistantHeightSpeakers(
            layoutName: AetherPlayer.heightLayoutName(forSetting: "5.1.2")), "5.1.2")
    }

    func testUnreadLogNotesHeightsUnknownOnlyWithoutActivity() {
        var report = HomeAssistantPlaybackReport(state: "playing")
        UniversalPlayerViewModel.applyHomeAssistantHeights(nil, logUnread: true, to: &report)
        XCTAssertEqual(report.heightsNote, HomeAssistantPlaybackReport.heightsLogUnreadNote)
        XCTAssertNil(report.heightLayoutActive)

        var quiet = HomeAssistantPlaybackReport(state: "playing")
        UniversalPlayerViewModel.applyHomeAssistantHeights(nil, logUnread: false, to: &quiet)
        XCTAssertEqual(quiet, HomeAssistantPlaybackReport(state: "playing"))

        let activity = ObjectAudioSnapshot(layout: "5.1.2", objects: 12, activeObjects: 7, elevatedObjects: 3,
                                           heightChannels: 2, source: .sessionLog, windowSeconds: 5)
        var active = HomeAssistantPlaybackReport(state: "playing")
        var logRead = false
        UniversalPlayerViewModel.applyHomeAssistantHeights(activity, logUnread: { logRead = true; return true }(), to: &active)
        XCTAssertFalse(logRead, "the unread flag is read only when there is no activity")
        XCTAssertNil(active.heightsNote)
        XCTAssertEqual(active.heightLayoutActive, "5.1.2")
        XCTAssertEqual(active.objectsTotal, 12)
        XCTAssertEqual(active.objectsActive, 7)
        XCTAssertEqual(active.objectsElevated, 3)
        XCTAssertEqual(active.heightChannels, 2)
        XCTAssertEqual(active.heightsSource, "session log")
        XCTAssertEqual(active.heightsWindowSeconds, 5)
    }
}
