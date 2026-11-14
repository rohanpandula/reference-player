// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import XCTest
@testable import Rivulet

@MainActor
final class PersonalDefaultsTests: XCTestCase {
    private let suite = "PersonalDefaultsTests"
    private var defaults: UserDefaults!
    private let file: [String: Any] = [
        FormatIndexStore.Keys.url: " https://ha.example.invalid/local/x/index.json ",
        HomeAssistantReporter.Keys.address: "http://ha.example.invalid:8123",
        HomeAssistantReporter.Keys.webhookID: "player-1",
        "sentryDSN": "not one of ours",
    ]

    override func setUp() {
        UserDefaults().removePersistentDomain(forName: suite)
        defaults = UserDefaults(suiteName: suite)
    }

    override func tearDown() { UserDefaults().removePersistentDomain(forName: suite) }

    func test_fillsSettingsAndTurnsBothIntegrationsOn() {
        defaults.set("homeassistant.local:8123", forKey: HomeAssistantReporter.Keys.address)   // a mistyped earlier value
        XCTAssertTrue(PersonalDefaults.seed(file, into: defaults))
        XCTAssertEqual(defaults.string(forKey: FormatIndexStore.Keys.url), "https://ha.example.invalid/local/x/index.json")
        XCTAssertEqual(defaults.string(forKey: HomeAssistantReporter.Keys.address), "http://ha.example.invalid:8123")
        XCTAssertEqual(defaults.string(forKey: HomeAssistantReporter.Keys.webhookID), "player-1")
        XCTAssertTrue(defaults.bool(forKey: FormatIndexStore.Keys.enabled))
        XCTAssertTrue(defaults.bool(forKey: HomeAssistantReporter.Keys.enabled))
        XCTAssertNil(defaults.object(forKey: "sentryDSN"))
    }

    func test_fillsOncePerSetOfValues_soSettingsEditsStay() {
        PersonalDefaults.seed(file, into: defaults)
        defaults.set("mine", forKey: HomeAssistantReporter.Keys.webhookID)
        defaults.set(false, forKey: FormatIndexStore.Keys.enabled)
        XCTAssertFalse(PersonalDefaults.seed(file, into: defaults))
        XCTAssertEqual(defaults.string(forKey: HomeAssistantReporter.Keys.webhookID), "mine")
        XCTAssertFalse(defaults.bool(forKey: FormatIndexStore.Keys.enabled))

        var changed = file
        changed[HomeAssistantReporter.Keys.webhookID] = "player-2"
        XCTAssertTrue(PersonalDefaults.seed(changed, into: defaults))
        XCTAssertEqual(defaults.string(forKey: HomeAssistantReporter.Keys.webhookID), "player-2")
    }

    func test_noFileOrEmptyValuesWriteNothing() {
        XCTAssertFalse(PersonalDefaults.seed(nil, into: defaults))
        XCTAssertFalse(PersonalDefaults.seed([FormatIndexStore.Keys.url: "  "], into: defaults))
        XCTAssertNil(defaults.object(forKey: FormatIndexStore.Keys.enabled))
        XCTAssertNil(defaults.object(forKey: PersonalDefaults.seededKey))
    }

    func test_indexAloneLeavesHomeAssistantOff() {
        PersonalDefaults.seed([FormatIndexStore.Keys.url: "https://ha.example.invalid/i.json"], into: defaults)
        XCTAssertTrue(defaults.bool(forKey: FormatIndexStore.Keys.enabled))
        XCTAssertNil(defaults.object(forKey: HomeAssistantReporter.Keys.enabled))
    }
}
