// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (C) 2025-2026 Bain Gurley

//
//  SearchMediaIntent.swift
//  Rivulet
//
//  App Intent for "Search Rivulet for X" Siri commands.
//  Returns rich results with thumbnails that the user can tap to open.
//

import AppIntents
import Foundation

struct SearchMediaIntent: AppIntent {
    static var title: LocalizedStringResource = "Search Media"
    static var description = IntentDescription(LocalizedStringResource("Search for movies and TV shows on Reference Player", table: "Private"))
    static var openAppWhenRun: Bool = false

    @Parameter(title: "Query")
    var query: String

    @MainActor
    func perform() async throws -> some IntentResult & ReturnsValue<[MediaItemEntity]> & ProvidesDialog {
        guard let serverURL = PlexAuthManager.shared.selectedServerURL,
              let token = PlexAuthManager.shared.selectedServerToken else {
            return .result(value: [], dialog: IntentDialog(LocalizedStringResource("Please sign in to Reference Player first", table: "Private")))
        }

        do {
            let results = try await PlexNetworkManager.shared.search(
                serverURL: serverURL,
                authToken: token,
                query: query,
                size: 10
            )

            let entities = results.map { MediaItemEntity(from: $0, serverURL: serverURL, token: token) }

            if entities.isEmpty {
                return .result(value: [], dialog: "No results found for \"\(query)\"")
            }

            let count = entities.count
            return .result(
                value: entities,
                dialog: "Found \(count) results for \"\(query)\""
            )
        } catch {
            return .result(value: [], dialog: "Couldn't reach your Plex server")
        }
    }
}
