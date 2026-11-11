// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import XCTest
@testable import Rivulet

/// The lines are the engine's own formats (AetherEngine `SpatialAudioBridge`, `BedLevelMeter`,
/// `SoftwarePlaybackHost+AudioProducer`, `HLSVideoEngine`), identical in 7.56.0 and 7.61.0.
final class SpatialBridgeLogReaderTests: XCTestCase {
    private let levels = "[SpatialAudioBridge] bed levels 5.1.2 at 3725.4 s over 30.0 s, rms/peak dBFS: "
        + "L -22.4/-3.1, R -21.0/-2.9, C -18.0/-4.0, LFE -30.1/-10.0, Ls -40.0/-12.0, Rs -39.0/-11.5, "
        + "Ltm -35.2/-9.9, Rtm -34.8/-9.1; objects: up to 12 active, 5 elevated; "
        + "LFE input -18.2 dBFS, metadata gain 0.0 dB"

    func testNothingUntilTheBridgeAnnouncesItself() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SWHost] frame 1234 enqueued")
        reader.ingest("[SpatialAudioBridge] EOF flush emitted 3 tail packet(s)")
        XCTAssertNil(reader.snapshot)
        XCTAssertNil(reader.heightsRoute)
    }

    func testInitLineGivesLayoutAndFormat() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SpatialAudioBridge] init: layout=5.1.2 format=lpcm bitRate=0 kbps codecs=lpcm priming=0 frames")
        let snapshot = reader.snapshot
        XCTAssertEqual(snapshot?.layout, "5.1.2")
        XCTAssertEqual(snapshot?.source, .sessionLog)
        XCTAssertNil(snapshot?.objects)
        XCTAssertNil(snapshot?.activeObjects)
        XCTAssertEqual(reader.current.bedFormat, "lpcm")
    }

    func testConfigurationLineCountsObjectsAndHeightBeds() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SpatialAudioBridge] init: layout=7.1.4 format=apac bitRate=1536 kbps codecs=apac priming=2048 frames")
        reader.ingest("[SpatialAudioBridge] object configuration: LFE + 15 objects")
        XCTAssertEqual(reader.snapshot?.objects, 15)
        XCTAssertEqual(reader.snapshot?.heightChannels, 0)
        reader.ingest("[SpatialAudioBridge] object configuration changed, placement rebuilt: "
                      + "beds L R C Ls Rs Lss Rss Vhl Vhr Ltr Rtr + LFE + 0 objects")
        XCTAssertEqual(reader.snapshot?.objects, 0)
        XCTAssertEqual(reader.snapshot?.heightChannels, 4)
    }

    func testBedLevelsLineGivesWindowMaxima() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SpatialAudioBridge] init: layout=5.1.2 format=lpcm bitRate=0 kbps codecs=lpcm priming=0 frames")
        reader.ingest(levels)
        let snapshot = reader.snapshot
        XCTAssertEqual(snapshot?.activeObjects, 12)
        XCTAssertEqual(snapshot?.elevatedObjects, 5)
        XCTAssertEqual(snapshot?.windowSeconds, 30)
        XCTAssertEqual(snapshot?.layout, "5.1.2")
    }

    func testBedLevelsLineWithoutPositionOrLimiterNote() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SpatialAudioBridge] init: layout=5.1.2 format=lpcm bitRate=0 kbps codecs=lpcm priming=0 frames")
        reader.ingest("[SpatialAudioBridge] bed levels 5.1.2 over 5.0 s, rms/peak dBFS: L -inf/-inf, R -inf/-inf; "
                      + "objects: up to 3 active, 0 elevated; no LFE element; limiter reduced 12 frames, deepest -1.2 dB")
        XCTAssertEqual(reader.snapshot?.layout, "5.1.2")
        XCTAssertEqual(reader.snapshot?.activeObjects, 3)
        XCTAssertEqual(reader.snapshot?.elevatedObjects, 0)
        XCTAssertEqual(reader.snapshot?.windowSeconds, 5)
    }

    func testSoftwareHostRouteLines() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SWHost] audio: TrueHD Atmos rendered into 5.1.2 as PCM for the renderer")
        XCTAssertEqual(reader.heightsRoute, "TrueHD Atmos rendered into 5.1.2 as PCM")
        reader.ingest("[SWHost] audio: stays PCM (DTS:X is rendered into 7.1.4 or 9.1.6 only, not 5.1.2)")
        XCTAssertEqual(reader.heightsRoute, "stays PCM (DTS:X is rendered into 7.1.4 or 9.1.6 only, not 5.1.2)")
        reader.ingest("[SWHost] audio: E-AC-3 stays PCM (not JOC)")
        XCTAssertEqual(reader.heightsRoute, "E-AC-3 stays PCM (not JOC)")
        reader.ingest("[SWHost] audio: built without the TrueHDObjectAudio trait; PCM decode")
        XCTAssertEqual(reader.heightsRoute, "built without the TrueHDObjectAudio trait; PCM decode",
                       "every audio line is the host's word on the pipeline; the last one is the route")
        reader.ingest("[SWHost] audio: E-AC-3 JOC decoded by the system into 5.1.2 as PCM for the renderer")
        XCTAssertEqual(reader.heightsRoute, "E-AC-3 JOC decoded by the system into 5.1.2 as PCM")
        XCTAssertFalse(reader.current.renderingAnnounced, "the system decodes that one; the object bridge does not run")
    }

    func testNativeRouteLines() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[HLSVideoEngine] audio: codec=truehd (bridge required), decoding + "
                      + "Atmos object render into 5.1.2, APAC re-encode")
        XCTAssertEqual(reader.heightsRoute, "Atmos object render into 5.1.2, APAC")
        reader.ingest("[HLSVideoEngine] DTS:X is rendered into 7.1.4 or 9.1.6 only, not 5.1.2; keeping the channel bridge")
        XCTAssertEqual(reader.heightsRoute, "DTS:X is rendered into 7.1.4 or 9.1.6 only, not 5.1.2; keeping the channel bridge")
        reader.ingest("[HLSVideoEngine] TrueHD Atmos with objectAudioRendering off; the channel bridge "
                      + "carries the 7.1 presentation, with the objects mixed into the floor channels")
        XCTAssertTrue(reader.heightsRoute?.hasPrefix("TrueHD Atmos with objectAudioRendering off") == true)
    }

    func testResetForgetsThePreviousItem() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SWHost] audio: TrueHD Atmos rendered into 5.1.2 as PCM for the renderer")
        reader.ingest("[SpatialAudioBridge] init: layout=5.1.2 format=lpcm bitRate=0 kbps codecs=lpcm priming=0 frames")
        reader.ingest(levels)
        XCTAssertNotNil(reader.snapshot)
        reader.reset()
        XCTAssertNil(reader.snapshot)
        XCTAssertNil(reader.heightsRoute)
        XCTAssertEqual(reader.current, SpatialBridgeLogReader.State())
    }

    private let initLine = "[SpatialAudioBridge] init: layout=5.1.2 format=lpcm bitRate=0 kbps codecs=lpcm priming=0 frames"

    func testABridgeThatKeepsLoggingInUnknownWordingIsReportedAsUnread() {
        let reader = SpatialBridgeLogReader()
        XCTAssertFalse(reader.logUnread, "silence is a track without heights, not a reading failure")
        // A later engine's wording: the host's verdict still reads, the bridge's own lines do not.
        reader.ingest("[SpatialAudioBridge] start: bed=5.1.2 codec=lpcm")
        reader.ingest("[SWHost] audio: TrueHD Atmos rendered into 5.1.2 as PCM for the renderer")
        XCTAssertFalse(reader.logUnread, "one unread line is not yet a pattern")
        reader.ingest("[SpatialAudioBridge] objects: 12 dynamic")
        XCTAssertTrue(reader.logUnread)
        XCTAssertNil(reader.snapshot)
        reader.ingest(initLine)
        XCTAssertFalse(reader.logUnread)
        reader.reset()
        XCTAssertFalse(reader.logUnread)
    }

    func testWithoutAVerdictThreeUnreadBridgeLinesMakeThePattern() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SpatialAudioBridge] start: bed=5.1.2")
        reader.ingest("[SpatialAudioBridge] objects: 12 dynamic")
        XCTAssertFalse(reader.logUnread)
        reader.ingest("[SpatialAudioBridge] levels 5.1.2: quiet")
        XCTAssertTrue(reader.logUnread)
    }

    /// The bridge logs its open level window as it is torn down, and the player has already reset
    /// this reader for the next title by then.
    func testAStoppedTitlesLastLineDoesNotDescribeTheNextTitle() {
        let reader = SpatialBridgeLogReader()
        reader.ingest(initLine)
        reader.ingest(levels)
        XCTAssertEqual(reader.snapshot?.activeObjects, 12)
        reader.reset()                       // the next title's load
        reader.ingest(levels)                // the old bridge's close()
        XCTAssertNil(reader.snapshot, "a level line never introduces a bridge")
        XCTAssertFalse(reader.logUnread, "and one late line is not a change of wording")
        reader.ingest("[SWHost] audio: stays PCM (2 channels)")
        XCTAssertNil(reader.snapshot)
        XCTAssertEqual(reader.heightsRoute, "stays PCM (2 channels)")
    }

    /// Another audio track rebuilds the session in place. The engine says so before the teardown;
    /// a plain track then logs nothing at all on either route.
    func testARebuildInPlaceStartsOver() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SWHost] audio: TrueHD Atmos rendered into 5.1.2 as PCM for the renderer")
        reader.ingest(initLine)
        reader.ingest(levels)
        XCTAssertNotNil(reader.snapshot)
        reader.ingest("[AetherEngine] reload begin: audioStream=2 resumeAt=3725.40s embeddedSub=-1 sidecar=nil")
        XCTAssertNil(reader.snapshot)
        XCTAssertNil(reader.heightsRoute)
        reader.ingest(levels)                // the old bridge's close()
        XCTAssertNil(reader.snapshot)
        XCTAssertFalse(reader.logUnread)
        XCTAssertEqual(reader.current.orphanBridgeLines, 1)
    }

    func testAVerdictThatThereIsNoObjectBridgeForgetsTheBridge() {
        let software = SpatialBridgeLogReader()
        software.ingest(initLine)
        software.ingest("[SWHost] audio: TrueHD Atmos rendered into 5.1.2 as PCM for the renderer")
        software.ingest(levels)
        XCTAssertNotNil(software.snapshot)
        XCTAssertTrue(software.current.renderingAnnounced)
        software.ingest("[SWHost] audio: E-AC-3 stays PCM (not JOC)")
        XCTAssertNil(software.snapshot)
        XCTAssertFalse(software.current.renderingAnnounced)

        let native = SpatialBridgeLogReader()
        native.ingest("[HLSVideoEngine] audio: codec=truehd (bridge required), decoding + Atmos object render into 5.1.2, APAC re-encode")
        native.ingest(initLine)
        native.ingest(levels)
        XCTAssertNotNil(native.snapshot)
        native.ingest("[HLSVideoEngine] audio: codec=dts (bridge required), decoding + FLAC re-encode")
        XCTAssertNil(native.snapshot, "a plain channel bridge for the new track")
        native.ingest(initLine)
        native.ingest("[HLSVideoEngine] PCM producer header write failed (EIO); keeping the channel bridge")
        XCTAssertNil(native.snapshot, "the bridge was built and then given up")
    }

    func testARefusalThatQuotesTheRenderingWordsIsStillARefusal() {
        let reader = SpatialBridgeLogReader()
        reader.ingest(initLine)
        reader.ingest("[SWHost] audio: stays PCM (DTS:X is rendered into 7.1.4 or 9.1.6 only, not 5.1.2)")
        XCTAssertNil(reader.snapshot)
        XCTAssertFalse(reader.current.renderingAnnounced)
    }

    func testANewBridgeStartsItsCountsOver() {
        let reader = SpatialBridgeLogReader()
        reader.ingest(initLine)
        reader.ingest("[SpatialAudioBridge] object configuration: LFE + 15 objects")
        reader.ingest(levels)
        reader.ingest(initLine)              // another track's bridge, same layout
        XCTAssertEqual(reader.snapshot?.layout, "5.1.2")
        XCTAssertNil(reader.snapshot?.objects)
        XCTAssertNil(reader.snapshot?.activeObjects)
    }

    func testCountsNeedTheirOwnBridge() {
        let reader = SpatialBridgeLogReader()
        reader.ingest("[SpatialAudioBridge] object configuration: LFE + 15 objects")
        XCTAssertNil(reader.snapshot)
        reader.ingest("[SpatialAudioBridge] init: layout=7.1.4 format=apac bitRate=1536 kbps codecs=apac priming=2048 frames")
        reader.ingest(levels)                // a 5.1.2 line is some other bridge's
        XCTAssertNil(reader.snapshot?.activeObjects)
    }

    func testMalformedLinesAreIgnored() {
        XCTAssertNil(SpatialBridgeLogReader.parse("[SpatialAudioBridge] init: format=lpcm"))
        XCTAssertNil(SpatialBridgeLogReader.parse("[SpatialAudioBridge] object configuration: beds L R"))
        XCTAssertNil(SpatialBridgeLogReader.parse("[SpatialAudioBridge] bed levels 5.1.2 over x s, rms/peak dBFS: ; objects: up to 1 active, 0 elevated"))
        XCTAssertNil(SpatialBridgeLogReader.parse("[SpatialAudioBridge] bed levels 5.1.2 over 5.0 s, rms/peak dBFS: L -1/-1; LFE input -18.2 dBFS"))
        XCTAssertNil(SpatialBridgeLogReader.parse("[HLSVideoEngine] audio: AAC stream-copy"))
    }
}
