// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition: reading AetherEngine's Profile 7 route line ("DV P7 dual-layer route: ..."),
// which AetherPlayer captures during a load. Plain strings only; no engine type is named here.
//
// What stays in upstream's PlayerInfoPaneViews.swift, because it cannot be added from outside:
// the `.dualLayer` case and its title, and the dual-layer branches inside
// `DolbyVisionPlayback.init?(sourceProfile:...)`. Notes on that init:
// - A Profile 7 source shown as Dolby Vision without the 8.1 rewrite is on the engine's dual-layer
//   route, which only FEL takes.
// - videoFormat read SDR on the dual-layer route in #362; an unconverted P7 on the software route is it.
// - `dualLayerRouteTaken` is the engine's own route line. Engine 7.57+ can force a forward-only P7 onto
//   the software host and decode its base layer after the dual-layer decision refused it, so the
//   software route alone no longer proves composition: claim FEL only when the line says the route
//   was taken, and never when it says it was not.

import Foundation

extension DolbyVisionLayerType {
    /// The engine's route line also names the layer; only FEL reaches the budget check, so an
    /// over-budget line is FEL too.
    init?(routeLine: String) {
        if routeLine.contains("el_type=FEL") || routeLine.contains("over the real-time budget") {
            self = .full
        } else if routeLine.contains("el_type=MEL") {
            self = .minimal
        } else {
            return nil
        }
    }
}

extension DolbyVisionPlayback {
    /// Whether the route line says the dual-layer route was taken.
    static func dualLayerRouteTaken(routeLine: String) -> Bool {
        routeLine.contains("dual-layer route: taken")
    }
}
