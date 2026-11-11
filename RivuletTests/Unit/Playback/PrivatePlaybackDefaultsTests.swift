// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import XCTest
@testable import Rivulet

@MainActor
final class PrivatePlaybackDefaultsTests: XCTestCase {
    private var suiteName: String!
    private var defaults: UserDefaults!

    override func setUp() {
        suiteName = "PrivatePlaybackDefaultsTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        suiteName = nil
    }

    /// FEL starts on; Height Speakers starts Off (no layout registered), as upstream's setting did.
    func testFreshInstallUsesFELAndNoHeightLayout() {
        PrivatePlaybackDefaults.register(in: defaults)
        AetherPlayer.settleFELDefault(existingInstall: false, defaults: defaults)
        XCTAssertTrue(defaults.bool(forKey: AetherPlayer.dolbyVisionFELKey))
        XCTAssertNil(defaults.string(forKey: AetherPlayer.heightSpeakersKey))
        XCTAssertNil(AetherPlayer.heightLayoutName(forSetting: defaults.string(forKey: AetherPlayer.heightSpeakersKey) ?? ""),
                     "no layout: object audio is not rendered")
    }

    func testExplicitOffIsNotOverwrittenByRegistrationOrMigration() {
        defaults.set(false, forKey: AetherPlayer.dolbyVisionFELKey)
        defaults.set("", forKey: AetherPlayer.heightSpeakersKey)
        PrivatePlaybackDefaults.register(in: defaults)
        AetherPlayer.settleFELDefault(existingInstall: true, defaults: defaults)
        XCTAssertFalse(defaults.bool(forKey: AetherPlayer.dolbyVisionFELKey))
        XCTAssertEqual(defaults.string(forKey: AetherPlayer.heightSpeakersKey), "")
    }

    func testExplicitDifferentLayoutSurvivesRepeatedRegistration() {
        defaults.set("7.1.4", forKey: AetherPlayer.heightSpeakersKey)
        PrivatePlaybackDefaults.register(in: defaults)
        PrivatePlaybackDefaults.register(in: defaults)
        XCTAssertEqual(defaults.string(forKey: AetherPlayer.heightSpeakersKey), "7.1.4")
    }
}
