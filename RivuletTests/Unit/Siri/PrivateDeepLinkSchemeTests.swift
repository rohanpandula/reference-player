// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
//  PrivateDeepLinkSchemeTests.swift
//  RivuletTests
//
//  Reference Player: the app answers only its own link scheme, never the stock app's.
//  Moved out of upstream's DeepLinkHandlerDetailTests.
//

import XCTest
@testable import Rivulet

final class PrivateDeepLinkSchemeTests: XCTestCase {

    func testPrivateBuildRejectsTheStockAppsScheme() {
        XCTAssertNotEqual(RivuletLink.scheme, "rivulet")
        XCTAssertNil(RivuletLink(url: URL(string: "rivulet://play/1")!))
    }
}
