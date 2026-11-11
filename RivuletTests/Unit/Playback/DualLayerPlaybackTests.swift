// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
//  DualLayerPlaybackTests.swift
//  RivuletTests
//
//  Reference Player: the dual-layer (FEL) labels and the engine route line, moved out of upstream's
//  DolbyVisionPlaybackTests so upstream's file stays its own.
//

import XCTest
@testable import Rivulet

final class DualLayerPlaybackTests: XCTestCase {

    private func playback(_ profile: Int?, converted: Bool = false, dv: Bool = false, hdr: Bool = false) -> DolbyVisionPlayback? {
        DolbyVisionPlayback(sourceProfile: profile, convertedTo81: converted, presentsDolbyVision: dv, presentsHDR: hdr)
    }

    /// Was the first line of upstream's `test_profile7_eachRoute` in this build.
    func test_profile7_eachRoute_dualLayer() {
        XCTAssertEqual(playback(7, dv: true, hdr: true), .dualLayer)
    }

    func test_profile7_softwareRoute_isDualLayer() {
        let p = DolbyVisionPlayback(sourceProfile: 7, convertedTo81: false, onSoftwareRoute: true,
                                    presentsDolbyVision: false, presentsHDR: false, dualLayerRouteTaken: true)
        XCTAssertEqual(p, .dualLayer)
        XCTAssertEqual(DolbyVisionPlayback(sourceProfile: 8, convertedTo81: false, onSoftwareRoute: true,
                                           presentsDolbyVision: false, presentsHDR: false), .sdr)
    }

    // Engine 7.57+ can decode a forward-only P7's base layer on the software host after refusing
    // the dual-layer route; only the route line proves composition.
    func test_profile7_softwareRoute_needsTakenRouteEvidence() {
        func software(_ taken: Bool?) -> DolbyVisionPlayback? {
            DolbyVisionPlayback(sourceProfile: 7, convertedTo81: false, onSoftwareRoute: true,
                                presentsDolbyVision: false, presentsHDR: true, dualLayerRouteTaken: taken)
        }
        XCTAssertEqual(software(true), .dualLayer)
        XCTAssertEqual(software(false), .hdr10BaseLayer)
        XCTAssertEqual(software(nil), .hdr10BaseLayer)
        XCTAssertEqual(DolbyVisionPlayback(sourceProfile: 7, convertedTo81: false, presentsDolbyVision: true,
                                           presentsHDR: true, dualLayerRouteTaken: false), .dolbyVision)
        XCTAssertEqual(DolbyVisionPlayback(sourceProfile: 7, convertedTo81: false, presentsDolbyVision: true,
                                           presentsHDR: true, dualLayerRouteTaken: true), .dualLayer)
    }
}

final class DolbyVisionEnhancementLayerTests: XCTestCase {

    func test_readsTheEngineRouteLine() {
        let prefix = "[AetherEngine] DV P7 dual-layer route: "
        XCTAssertEqual(DolbyVisionLayerType(routeLine: prefix + "taken (el_type=FEL, 3840x2160@23.976, budget 80%)"), .full)
        XCTAssertEqual(DolbyVisionLayerType(routeLine: prefix + "not taken (el_type=MEL, nothing to compose), keeps the native route"), .minimal)
        XCTAssertEqual(DolbyVisionLayerType(routeLine: prefix + "not taken (over the real-time budget, 3840x2160@60 needs 160% of it), keeps the native route"), .full)
        XCTAssertNil(DolbyVisionLayerType(routeLine: prefix + "not taken (preferDualLayerDolbyVision is off), keeps the native route"))
    }

    /// The same three shapes: taken, not taken, and a line that is not the route line.
    func test_routeTaken_readsTheEngineRouteLine() {
        let prefix = "[AetherEngine] DV P7 dual-layer route: "
        XCTAssertTrue(DolbyVisionPlayback.dualLayerRouteTaken(routeLine: prefix + "taken (el_type=FEL, 3840x2160@23.976, budget 80%)"))
        XCTAssertFalse(DolbyVisionPlayback.dualLayerRouteTaken(routeLine: prefix + "not taken (el_type=MEL, nothing to compose), keeps the native route"))
        XCTAssertFalse(DolbyVisionPlayback.dualLayerRouteTaken(routeLine: "[AetherEngine] reload begin: position 0"))
    }
}
