// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import XCTest
@testable import Rivulet

final class DeviceIdentityTests: XCTestCase {
    func testKnownAppleTVIdentifiersGetTheirNames() {
        XCTAssertEqual(DeviceIdentity.marketingName(for: "AppleTV11,1"), "Apple TV 4K (2nd generation)")
        XCTAssertEqual(DeviceIdentity.marketingName(for: "AppleTV14,1"), "Apple TV 4K (3rd generation)")
        XCTAssertEqual(DeviceIdentity.marketingName(for: "AppleTV6,2"), "Apple TV 4K (1st generation)")
        XCTAssertEqual(DeviceIdentity.marketingName(for: "AppleTV5,3"), "Apple TV HD")
    }

    func testUnknownIdentifierPassesThrough() {
        XCTAssertEqual(DeviceIdentity.marketingName(for: "AppleTV15,1"), "AppleTV15,1")
        XCTAssertEqual(DeviceIdentity.marketingName(for: "arm64"), "arm64")
    }

    func testTheRunningDeviceAnswers() {
        XCTAssertNotNil(DeviceIdentity.modelIdentifier)
        XCTAssertTrue(DeviceIdentity.osVersion.hasPrefix("tvOS "), DeviceIdentity.osVersion)
    }
}
