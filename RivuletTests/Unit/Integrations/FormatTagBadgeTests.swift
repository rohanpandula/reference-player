// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
//  FormatTagBadgeTests.swift
//  RivuletTests
//
//  Reference Player: the badge names Format Tags adds (DV FEL/MEL, TrueHD Atmos, DTS-X), moved out of
//  upstream's MediaSourceQualityBadgesTests and FormatIndexTests so upstream's file stays its own.
//

import XCTest
@testable import Rivulet

/// Main-actor: the badge extension reads `FormatIndexStore`.
@MainActor
final class FormatTagBadgeTests: XCTestCase {

    private func audio(codec: String, profile: String?, channels: Int = 8, layout: String = "7.1") -> AudioTrack {
        AudioTrack(
            id: "a1", index: 0, codec: codec, profile: profile,
            channels: channels, channelLayout: layout,
            language: "en", title: nil, extendedTitle: nil,
            bitrate: nil, samplingRate: 48_000,
            isDefault: true, isForced: false, isSelected: true
        )
    }

    /// Reference Player: Plex and ffprobe both write DTS:X as an extension of DTS-HD MA, so the label
    /// asks for it first; a bare "x" used to match DTS Express and never beat "ma".
    func test_dtsVariants_areNamedFromTheProfile() {
        func label(_ profile: String?) -> String { audio(codec: "dca", profile: profile).qualityLabel }
        XCTAssertEqual(label("ma + dts:x"), "DTS-X 7.1", "as Plex writes it")
        XCTAssertEqual(label("ma + dts:x imax"), "DTS-X 7.1")
        XCTAssertEqual(label("DTS-HD MA + DTS:X"), "DTS-X 7.1", "as ffprobe, Jellyfin and Emby write it")
        XCTAssertEqual(label("x"), "DTS-X 7.1")
        XCTAssertEqual(label("ma"), "DTS-HD MA 7.1")
        XCTAssertEqual(label("hra"), "DTS-HD HRA 7.1")
        XCTAssertEqual(label("express"), "DTS 7.1", "the x in express is not DTS:X")
        XCTAssertEqual(label("es"), "DTS 7.1")
        XCTAssertEqual(label("96/24"), "DTS 7.1")
        XCTAssertEqual(label(nil), "DTS 7.1")
    }

    /// The format index names the enhancement layer; which track is Atmos or DTS:X is the server's
    /// own word, from the stream profile it reports.
    func test_privateBadges_takeTheLayerFromTheIndexAndAudioFromTheServer() throws {
        let tagsBefore = FormatIndexStore.showsTags
        FormatIndexStore.showsTags = true
        defer {
            FormatIndexStore.shared.replace(with: FormatIndex(format: "rivulet-format-index", version: 1, generatedAt: nil, entries: []))
            FormatIndexStore.showsTags = tagsBefore
        }
        let index = """
        {"format":"rivulet-format-index","version":1,"entries":[
          {"path":"Example Movie (2022)/example.mkv","size":70000000000,
           "dolbyVisionProfile":7,"enhancementLayer":"FEL","enhancementLayerPresent":true,
           "audio":[{"kind":"TrueHD Atmos","channels":8}]}]}
        """
        FormatIndexStore.shared.replace(with: try FormatIndexStore.decode(Data(index.utf8)))
        let video = VideoTrack(
            id: "v1", codec: "hevc", profile: nil, level: nil,
            width: 3840, height: 2160, frameRate: 24,
            bitrate: nil, videoRange: .dolbyVision(profile: 7), isDefault: true, scanType: "progressive"
        )
        func source(_ track: AudioTrack) -> MediaSource {
            MediaSource(
                id: "1", container: "mkv", duration: 7200,
                bitrate: nil, fileSize: 70_000_000_000, fileName: "/movies/Example Movie (2022)/example.mkv",
                videoResolution: "4k",
                videoTracks: [video], audioTracks: [track], subtitleTracks: [],
                streamKind: .directPlay, streamURL: nil
            )
        }
        let atmos = source(audio(codec: "truehd", profile: "dolby truehd + dolby atmos"))
        XCTAssertEqual(atmos.qualityBadges(), ["4K", "DV FEL", "TrueHD 7.1 Atmos"])
        XCTAssertEqual(atmos.detailBadges, ["4K", "DV FEL", "TrueHD Atmos"])
        let plain = source(audio(codec: "truehd", profile: nil))
        XCTAssertEqual(plain.qualityBadges(), ["4K", "DV FEL", "TrueHD 7.1"],
                       "an index cached by an earlier build still names audio; it is read past, not acted on")
        XCTAssertEqual(plain.detailBadges, ["4K", "DV FEL"])
        let dtsx = source(audio(codec: "dca", profile: "ma + dts:x"))
        XCTAssertEqual(dtsx.detailBadges, ["4K", "DV FEL", "DTS:X"])
        let master = source(audio(codec: "dca", profile: "ma"))
        XCTAssertEqual(master.detailBadges, ["4K", "DV FEL"], "DTS-HD MA has no badge on the detail row")
        FormatIndexStore.showsTags = false
        XCTAssertEqual(atmos.detailBadges, ["4K", "DV", "Atmos"], "tags off: upstream's plain row")
        XCTAssertEqual(dtsx.detailBadges, ["4K", "DV", "DTS:X"], "tags off: DTS:X stays, the private label fix is not gated by Format Tags; upstream shows no DTS:X badge for this profile")
    }

    /// A dual-track Profile 7 file as Plex reports it: the base layer first (HDR10, no Dolby Vision),
    /// the enhancement layer as a second video track that says Profile 7. The engine plays the first
    /// track, so the row keeps HDR and names the layer beside it; it never calls the title Dolby Vision.
    func test_dualTrackProfile7_keepsHDRAndNamesTheLayerBesideIt() throws {
        let tagsBefore = FormatIndexStore.showsTags
        FormatIndexStore.showsTags = true
        defer {
            FormatIndexStore.shared.replace(with: FormatIndex(format: "rivulet-format-index", version: 1, generatedAt: nil, entries: []))
            FormatIndexStore.showsTags = tagsBefore
        }
        let index = """
        {"format":"rivulet-format-index","version":1,"entries":[
          {"path":"Demos/Disc/clip.mp4","size":500000000,"dolbyVisionProfile":7,
           "enhancementLayer":"FEL","enhancementLayerPresent":true,"enhancementLayerSeparateTrack":true},
          {"path":"Dual Track Example (2021)/d.mkv","size":48000000000,"dolbyVisionProfile":7,
           "enhancementLayer":"MEL","enhancementLayerPresent":true,"enhancementLayerSeparateTrack":true}]}
        """
        FormatIndexStore.shared.replace(with: try FormatIndexStore.decode(Data(index.utf8)))
        func track(_ id: String, _ width: Int, _ height: Int, _ range: VideoTrack.VideoRange) -> VideoTrack {
            VideoTrack(id: id, codec: "hevc", profile: "main 10", level: nil, width: width, height: height, frameRate: 24,
                       bitrate: nil, videoRange: range, isDefault: true, scanType: "progressive")
        }
        func source(_ file: String?, _ size: Int64?, first: VideoTrack.VideoRange = .hdr10) -> MediaSource {
            MediaSource(
                id: "1", container: "mp4", duration: 60,
                bitrate: nil, fileSize: size, fileName: file, videoResolution: "4k",
                videoTracks: [track("v1", 3840, 2160, first), track("v2", 1920, 1080, .dolbyVision(profile: 7))],
                audioTracks: [], subtitleTracks: [], streamKind: .directPlay, streamURL: nil
            )
        }
        let clip = source("/Demos/Disc/clip.mp4", 500_000_000)
        XCTAssertEqual(clip.qualityBadges(), ["4K", "HDR", "DV FEL 2-track"])
        XCTAssertEqual(clip.rangeBadge, "HDR · DV FEL 2-track")
        XCTAssertEqual(clip.detailBadges, ["4K", "HDR", "DV FEL 2-track"])
        XCTAssertEqual(source("/movies/Dual Track Example (2021)/d.mkv", 48_000_000_000).qualityBadges(), ["4K", "HDR", "DV MEL 2-track"])

        XCTAssertEqual(source("/movies/Other (2020)/o.mkv", 5).qualityBadges(), ["4K", "HDR"], "a file the index does not know")
        XCTAssertEqual(source("/Demos/Disc/clip.mp4", 500_000_000, first: .dolbyVision(profile: 7)).qualityBadges(),
                       ["4K", "DV FEL"], "a base track that is itself Dolby Vision plays as Dolby Vision")
        FormatIndexStore.showsTags = false
        XCTAssertEqual(clip.qualityBadges(), ["4K", "HDR"], "tags off: the server's own badges")
    }

    /// Only lossless TrueHD Atmos earns the long name; Dolby Digital Plus Atmos keeps upstream's badge.
    func testDolbyDigitalPlusAtmosStaysAtmosWithTagsOn() {
        // FormatIndexTests' setUp turned the tags on; this test does it itself now.
        let tagsBefore = FormatIndexStore.showsTags
        FormatIndexStore.showsTags = true
        defer { FormatIndexStore.showsTags = tagsBefore }
        func source(codec: String, profile: String) -> MediaSource {
            let audio = AudioTrack(id: "a1", index: 0, codec: codec, profile: profile, channels: 8, channelLayout: "7.1",
                                   language: "en", title: nil, extendedTitle: nil, bitrate: nil, samplingRate: 48_000,
                                   isDefault: true, isForced: false, isSelected: true)
            let video = VideoTrack(id: "v1", codec: "hevc", profile: nil, level: nil, width: 3840, height: 2160, frameRate: 24,
                                   bitrate: nil, videoRange: .hdr10, isDefault: true, scanType: "progressive")
            return MediaSource(id: "1", container: "mkv", duration: 7200, bitrate: nil, fileSize: 1, fileName: "/movies/X (2020)/x.mkv",
                               videoResolution: "4k", videoTracks: [video], audioTracks: [audio], subtitleTracks: [],
                               streamKind: .directPlay, streamURL: nil)
        }
        XCTAssertTrue(FormatIndexStore.showsTags)
        XCTAssertEqual(source(codec: "eac3", profile: "dolby digital plus + dolby atmos").detailBadges, ["4K", "HDR", "Atmos"])
        XCTAssertEqual(source(codec: "truehd", profile: "dolby truehd + dolby atmos").detailBadges, ["4K", "HDR", "TrueHD Atmos"])
    }
}
