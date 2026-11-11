// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import XCTest
@testable import Rivulet

@MainActor
final class HomeAssistantReporterTests: XCTestCase {
    private var enabledBefore: Bool!
    private var addressBefore: String?

    override func setUp() {
        enabledBefore = HomeAssistantReporter.isEnabled
        addressBefore = UserDefaults.standard.string(forKey: HomeAssistantReporter.Keys.address)
        HomeAssistantReporter.isEnabled = true
        HomeAssistantReporter.address = "http://ha.example.invalid:8123"   // none is built in
    }

    override func tearDown() {
        HomeAssistantReporter.isEnabled = enabledBefore
        UserDefaults.standard.set(addressBefore, forKey: HomeAssistantReporter.Keys.address)
    }

    /// Records the `state` of every delivered report, with an optional delay per call.
    private final class Recording {
        var states: [String] = []
        var delays: [UInt64] = []
        var answer = true
    }

    private func reporter(_ recording: Recording) -> HomeAssistantReporter {
        HomeAssistantReporter { request in
            if !recording.delays.isEmpty {
                let nanoseconds = recording.delays.removeFirst()
                try? await Task.sleep(nanoseconds: nanoseconds)
            }
            let body = try? JSONSerialization.jsonObject(with: request.httpBody ?? Data()) as? [String: Any]
            recording.states.append(body?["state"] as? String ?? "?")
            return recording.answer
        }
    }

    private func report(_ state: String, title: String = "Example Movie") -> HomeAssistantPlaybackReport {
        var report = HomeAssistantPlaybackReport(state: state)
        report.title = title
        return report
    }

    func testWebhookURLNeedsAnHTTPAddressAndAPlainID() {
        XCTAssertEqual(HomeAssistantReporter.webhookURL(address: "http://ha.example.invalid:8123", id: "rivulet-abc123")?.absoluteString,
                       "http://ha.example.invalid:8123/api/webhook/rivulet-abc123")
        XCTAssertEqual(HomeAssistantReporter.webhookURL(address: " https://ha.example.net/ ", id: "x_1")?.absoluteString,
                       "https://ha.example.net/api/webhook/x_1")
        XCTAssertEqual(HomeAssistantReporter.webhookURL(address: "http://192.0.2.10:8123/lovelace?x=1", id: "a")?.absoluteString,
                       "http://192.0.2.10:8123/api/webhook/a")
        XCTAssertNil(HomeAssistantReporter.webhookURL(address: "ha.example.invalid:8123", id: "a"))
        XCTAssertNil(HomeAssistantReporter.webhookURL(address: "ftp://ha.example.invalid", id: "a"))
        XCTAssertNil(HomeAssistantReporter.webhookURL(address: "http://user:secret@ha.example.invalid", id: "a"))
        XCTAssertNil(HomeAssistantReporter.webhookURL(address: "http://ha.example.invalid", id: ""))
        XCTAssertNil(HomeAssistantReporter.webhookURL(address: "http://ha.example.invalid", id: "bad id/../x"))
        XCTAssertEqual(HomeAssistantReporter.defaultAddress, "", "no address is built in")
        XCTAssertNil(HomeAssistantReporter.webhookURL(address: HomeAssistantReporter.defaultAddress, id: "a"), "so nothing is sent")
    }

    func testNoAddressMeansNothingIsSent() async {
        HomeAssistantReporter.address = ""
        XCTAssertEqual(HomeAssistantReporter.address, "")
        let recording = Recording()
        let reporter = reporter(recording)
        reporter.begin { HomeAssistantPlaybackReport(state: "playing") }
        reporter.end()
        await reporter.drain()
        XCTAssertEqual(recording.states, [], "no address configured: no request is made")
    }

    func testReportEncodesSnakeCaseKeysAndOmitsNils() throws {
        var report = HomeAssistantPlaybackReport(state: "playing")
        report.videoCodec = "hevc"
        report.dolbyVisionProfile = 7
        report.heightSpeakers = "5.1.2"
        report.bitrateMbps = 52.5
        let data = try HomeAssistantPlaybackReport.makeEncoder().encode(report)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["state"] as? String, "playing")
        XCTAssertEqual(json["player"] as? String, "Reference Player")
        XCTAssertEqual(json["video_codec"] as? String, "hevc")
        XCTAssertEqual(json["dolby_vision_profile"] as? Int, 7)
        XCTAssertEqual(json["height_speakers"] as? String, "5.1.2")
        XCTAssertEqual(json["bitrate_mbps"] as? Double, 52.5)
        XCTAssertNil(json["title"])
        XCTAssertNil(json["videoCodec"])
    }

    func testIdleReportCarriesOnlyStateAndPlayer() throws {
        let data = try HomeAssistantPlaybackReport.makeEncoder().encode(HomeAssistantPlaybackReport.idle())
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(json["state"] as? String, "idle")
        XCTAssertEqual(Set(json.keys), ["state", "player"])
    }

    func testTimestampIsNotAChange() {
        var a = report("playing")
        var b = a
        b.reportedAt = "2026-11-08T20:00:00Z"
        XCTAssertTrue(a.sameContent(as: b))
        b.title = "Example Movie: Part Two"
        XCTAssertFalse(a.sameContent(as: b))
        a.reportedAt = nil
        XCTAssertFalse(a.sameContent(as: nil))
    }

    func testUnchangedRefreshIsSkippedButHeartbeatAndEndAreNot() async {
        let recording = Recording()
        let sut = reporter(recording)
        var current = report("playing")
        sut.begin { current }
        sut.refresh()                 // same content: skipped
        sut.refresh(force: true)      // heartbeat: sent again
        current = report("paused")
        sut.refresh()                 // changed: sent
        sut.end()                     // idle once
        sut.end()                     // and never twice
        await sut.drain()
        XCTAssertEqual(recording.states, ["playing", "playing", "paused", "idle"])
    }

    func testDeliveriesKeepTheirOrderWhenAnEarlierOneIsSlow() async {
        let recording = Recording()
        recording.delays = [200_000_000, 0]   // the "playing" post takes 0.2 s; idle must still follow it
        let sut = reporter(recording)
        sut.begin { self.report("playing") }
        sut.end()
        await sut.drain()
        XCTAssertEqual(recording.states, ["playing", "idle"])
    }

    func testNothingIsSentWhenDisabledOrBeforeAnyReport() async {
        let recording = Recording()
        let sut = reporter(recording)
        sut.end()                     // no session: nothing to end
        HomeAssistantReporter.isEnabled = false
        sut.begin { self.report("playing") }
        sut.end()
        await sut.drain()
        XCTAssertEqual(recording.states, [])
    }

    func testFailedDeliveryIsRetriedByTheNextHeartbeat() async {
        let recording = Recording()
        recording.answer = false
        let sut = reporter(recording)
        sut.begin { self.report("playing") }
        sut.refresh(force: true)
        await sut.drain()
        XCTAssertEqual(recording.states, ["playing", "playing"])
    }
}
