// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
//
//  PrivateStringTableTests.swift
//  RivuletTests
//
//  Reference Player: the private text lives in its own string table (Rivulet/Private.xcstrings), so
//  upstream's Localizable.xcstrings stays upstream's. The keys that moved there kept their German
//  translations; this reads them from the built app's German localization.
//

import XCTest
@testable import Rivulet

final class PrivateStringTableTests: XCTestCase {

    private static let movedGerman = [
        "Dolby Vision FEL": "Dolby Vision FEL",
        "Dual Layer (FEL)": "Zwei Ebenen (FEL)",
        "Height Speakers": "Höhenlautsprecher",
        "Keeps the height channels of object-based soundtracks, lossless over HDMI. Off plays 7.1 without them. Some soundtracks need 7.1.4 or 9.1.6.":
            "Behält die Höhenkanäle objektbasierter Tonspuren bei, verlustfrei über HDMI. Bei „Aus“ wird 7.1 ohne sie wiedergegeben. Manche Tonspuren benötigen 7.1.4 oder 9.1.6.",
        "Plays the full Dolby Vision Profile 7 layer on a Dolby Vision TV, without AirPlay or Picture in Picture. Off plays it as 8.1.":
            "Gibt die vollständige Ebene von Dolby Vision Profil 7 auf einem Dolby-Vision-Fernseher wieder, ohne AirPlay oder Bild-in-Bild. Bei „Aus“ wird Profil 7 als 8.1 wiedergegeben.",
    ]

    private func german() throws -> Bundle {
        // Hosted tests run inside the app, so the main bundle is the built Rivulet.app.
        let path = try XCTUnwrap(Bundle.main.path(forResource: "de", ofType: "lproj"), "the app has no German localization")
        return try XCTUnwrap(Bundle(path: path))
    }

    func test_movedKeys_resolveInGerman_fromThePrivateTable() throws {
        let de = try german()
        for (key, value) in Self.movedGerman {
            XCTAssertEqual(de.localizedString(forKey: key, value: nil, table: "Private"), value, key)
            // And not from upstream's table any more: there the key comes back as itself.
            XCTAssertEqual(de.localizedString(forKey: key, value: nil, table: "Localizable"), key, key)
        }
    }

    func test_sharedWords_stayOnUpstreamsTable() throws {
        let de = try german()
        XCTAssertEqual(de.localizedString(forKey: "Off", value: nil, table: "Localizable"), "Aus")
        XCTAssertEqual(de.localizedString(forKey: "Off", value: nil, table: "Private"), "Off")
    }

    func test_englishResolvesToTheKey() {
        XCTAssertEqual(String(localized: "Height Speakers", table: "Private", locale: Locale(identifier: "en")), "Height Speakers")
        XCTAssertEqual(DolbyVisionPlayback.dualLayer.title, "Dual Layer (FEL)")
    }
}
