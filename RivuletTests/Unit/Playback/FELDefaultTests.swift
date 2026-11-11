import XCTest
@testable import Rivulet

@MainActor
final class FELDefaultTests: XCTestCase {
    private var defaults: UserDefaults!

    override func setUp() {
        defaults = UserDefaults(suiteName: "FELDefaultTests")
        defaults.removePersistentDomain(forName: "FELDefaultTests")
    }

    /// Reference Player: the registration domain is process-wide and the test host's `RivuletApp.init` has
    /// registered FEL on, so `bool(forKey:)` reads true whatever settling does, and settling finds the
    /// key already answered and writes nothing for it. What settling itself must do is record that it
    /// ran, once; read that from the persistent domain, which registration does not reach.
    func test_existingInstall_untouched_keepsFELOn() {
        AetherPlayer.settleFELDefault(existingInstall: true, defaults: defaults)
        let stored = defaults.persistentDomain(forName: "FELDefaultTests")
        XCTAssertEqual(stored?["dolbyVisionFELDefaultSettled"] as? Bool, true, "settling records that it ran")
        XCTAssertNil(stored?[AetherPlayer.dolbyVisionFELKey], "the registered on default answers; nothing is written")
        XCTAssertTrue(defaults.bool(forKey: AetherPlayer.dolbyVisionFELKey))
    }

    func test_existingInstall_explicitOff_staysOff() {
        defaults.set(false, forKey: AetherPlayer.dolbyVisionFELKey)
        AetherPlayer.settleFELDefault(existingInstall: true, defaults: defaults)
        XCTAssertFalse(defaults.bool(forKey: AetherPlayer.dolbyVisionFELKey))
    }

    /// Reference Player: `PrivatePlaybackDefaults.register()` makes FEL the default for every install
    /// through the registration domain (which every `UserDefaults` instance in the process reads),
    /// so a new install reads it on and settling writes nothing for it. Upstream's test asserted
    /// the off default this build reverses.
    func test_newInstall_readsThePrivateDefaultOn_andSettlingWritesNothing() {
        PrivatePlaybackDefaults.register(in: defaults)
        AetherPlayer.settleFELDefault(existingInstall: false, defaults: defaults)
        AetherPlayer.settleFELDefault(existingInstall: true, defaults: defaults)
        XCTAssertNil(defaults.persistentDomain(forName: "FELDefaultTests")?[AetherPlayer.dolbyVisionFELKey],
                     "nothing is written for the key; the on default is registered, not stored")
        XCTAssertTrue(defaults.bool(forKey: AetherPlayer.dolbyVisionFELKey))
    }
}
