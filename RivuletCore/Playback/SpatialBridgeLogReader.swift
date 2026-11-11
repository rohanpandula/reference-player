// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition. Reads what the engine's spatial audio bridge says about itself.

import Foundation
import os

/// Height rendering as the engine reports it, in host types.
///
/// Two sources. The engine's live telemetry carries the object renderer's counts, but reads them
/// from its native session only; the Dolby Vision Profile 7 dual-layer route is the software host,
/// where the same bridge runs and logs the same lines. `SpatialBridgeLogReader` reads those, so the
/// FEL route reports its heights too. Telemetry counts are as of the latest metadata update; the
/// log's are the maxima over the window its level line closed (`windowSeconds`).
nonisolated struct ObjectAudioSnapshot: Equatable, Sendable {
    enum Source: String, Sendable {
        case telemetry
        case sessionLog = "session log"
    }

    /// The speaker bed the presentation is rendered into (the Height Speakers layout).
    let layout: String
    /// Dynamic objects the presentation declares; nil until the bridge has logged its configuration.
    let objects: Int?
    let activeObjects: Int?
    let elevatedObjects: Int?
    /// Bed channels on height speakers (DTS:X carries its heights this way).
    let heightChannels: Int?
    let source: Source
    let windowSeconds: Double?
}

/// Parses the `[SpatialAudioBridge]`, `[SWHost] audio:` and `[HLSVideoEngine]` lines of the engine's
/// session log into the current height-rendering state. Fed from `EngineLog.structuredHandler`
/// (installed by `AetherPlayer`), on the engine's own threads; read on the main actor. One reader per
/// process: only one VOD session plays at a time, and live sessions never render object audio.
///
/// The state belongs to one bridge, and a log line does not say whose it is, so three rules keep a
/// stopped bridge from describing the next track:
/// - Only the bridge's `init:` line introduces a layout, and it starts the counts over. The bridge
///   logs its open level window as it is torn down (`close()`, in 7.56.0 and 7.61.0 alike), which is
///   after `AetherPlayer` reset this reader for the next title. A level line that could introduce a
///   layout handed the old title's heights to a title that has none.
/// - A verdict that the track gets no object bridge (`stays PCM`, `keeping the channel bridge`, a
///   plain re-encode) forgets the bridge.
/// - `AetherPlayer` resets the reader before every load, and the engine's own `reload begin:` line
///   does the same for a rebuild in place (another audio track, a return to the foreground). A
///   selection the engine ignores or folds into none logs no such line, so a live bridge is left alone.
nonisolated final class SpatialBridgeLogReader: Sendable {
    static let shared = SpatialBridgeLogReader()

    struct State: Equatable, Sendable {
        var layout: String?
        /// lpcm or apac, from the bridge's init line.
        var bedFormat: String?
        var configuredObjects: Int?
        var heightBedChannels: Int?
        var windowActiveObjects: Int?
        var windowElevatedObjects: Int?
        var windowSeconds: Double?
        /// The engine's one-line verdict on the track: rendered into a layout, or why it stayed on
        /// the plain decode / channel bridge.
        var route: String?
        /// The verdict said this track renders heights through the bridge.
        var renderingAnnounced = false
        /// Bridge lines that arrived while no layout was known. One can be a stopped title's last
        /// word; a bridge that keeps talking without ever giving a layout has changed its wording.
        var orphanBridgeLines = 0

        /// Everything one bridge said. A new bridge, or a verdict that there is none, starts over.
        mutating func forgetBridge() {
            layout = nil
            bedFormat = nil
            configuredObjects = nil
            heightBedChannels = nil
            windowActiveObjects = nil
            windowElevatedObjects = nil
            windowSeconds = nil
            orphanBridgeLines = 0
        }
    }

    /// What one line does to the state. Sendable: it runs inside the lock.
    typealias Edit = @Sendable (inout State) -> Void

    /// CoreAudio labels the engine prints for channels above ear level.
    static let heightLabels: Set<String> = ["Vhl", "Vhc", "Vhr", "Tbl", "Tbc", "Tbr", "Ltm", "Rtm",
                                            "Ltr", "Ctr", "Rtr", "Lts", "Rts", "Ts"]

    /// The native route's verdicts that the track keeps its plain decode or channel bridge.
    private static let nativeRefusals = ["keeping the channel bridge", "objectAudioRendering off",
                                         "object rendering requested but", "is not DTS:X this engine renders",
                                         "no DTS:X verdict"]

    private let state = OSAllocatedUnfairLock(initialState: State())

    init() {}

    /// Forget the previous track: every line after this describes the pipeline that logs it.
    func reset() {
        state.withLock { $0 = State() }
    }

    var current: State { state.withLock { $0 } }

    /// nil until the bridge has announced itself for this track.
    var snapshot: ObjectAudioSnapshot? {
        let s = current
        guard let layout = s.layout else { return nil }
        return ObjectAudioSnapshot(layout: layout, objects: s.configuredObjects,
                                   activeObjects: s.windowActiveObjects, elevatedObjects: s.windowElevatedObjects,
                                   heightChannels: s.heightBedChannels, source: .sessionLog,
                                   windowSeconds: s.windowSeconds)
    }

    var heightsRoute: String? { current.route }

    /// True when the bridge keeps logging for this track but no line gave a layout: the engine's
    /// wording has moved past this reader, so heights are unknown rather than off. Two unread lines
    /// once the verdict announced rendering, three without one. A single line proves nothing: a
    /// stopped title's bridge logs once more after the reset.
    var logUnread: Bool {
        let s = current
        return s.layout == nil && s.orphanBridgeLines >= (s.renderingAnnounced ? 2 : 3)
    }

    /// One log line; anything that is not one of the known lines is ignored, but a bridge line that
    /// leaves the layout unknown is counted (see `logUnread`).
    func ingest(_ line: String) {
        let fromBridge = line.hasPrefix("[SpatialAudioBridge]")
        let edit = Self.parse(line)
        guard fromBridge || edit != nil else { return }
        state.withLock { s in
            edit?(&s)
            if fromBridge, s.layout == nil { s.orphanBridgeLines += 1 }
        }
    }

    /// The edit a line makes, or nil for a line this reader does not read. Pure, for the tests.
    static func parse(_ line: String) -> Edit? {
        if line.hasPrefix("[AetherEngine] reload begin:") {
            // Logged right before the engine tears the session down to rebuild it in place.
            return { $0 = State() }
        }
        if let rest = Self.rest(of: line, after: "[SpatialAudioBridge] init: ") {
            guard let layout = value(for: "layout", in: rest) else { return nil }
            let format = value(for: "format", in: rest)
            // A new bridge: nothing the previous one counted describes it.
            return { $0.forgetBridge(); $0.layout = layout; $0.bedFormat = format }
        }
        if let rest = Self.rest(of: line, after: "[SpatialAudioBridge] object configuration"),
           let colon = rest.range(of: ": ") {
            // `beds L R C Ltm Rtm + LFE + 12 objects`, each part optional but the last.
            var objects: Int?
            var heights = 0
            for part in rest[colon.upperBound...].components(separatedBy: " + ") {
                if part.hasPrefix("beds ") {
                    heights = part.dropFirst(5).split(separator: " ").filter { heightLabels.contains(String($0)) }.count
                } else if part.hasSuffix(" objects") {
                    objects = Int(part.dropLast(8))
                }
            }
            guard let objectCount = objects else { return nil }
            let heightCount = heights
            return {
                guard $0.layout != nil else { return }
                $0.configuredObjects = objectCount
                $0.heightBedChannels = heightCount
            }
        }
        if let rest = Self.rest(of: line, after: "[SpatialAudioBridge] bed levels ") {
            // `5.1.2 at 3725.4 s over 30.0 s, rms/peak dBFS: …; objects: up to 12 active, 5 elevated; …`
            guard let layout = rest.split(separator: " ").first.map(String.init),
                  let overRange = rest.range(of: " over "),
                  let secondsEnd = rest.range(of: " s,", range: overRange.upperBound..<rest.endIndex),
                  let seconds = Double(rest[overRange.upperBound..<secondsEnd.lowerBound]),
                  let objectsRange = rest.range(of: "; objects: up to "),
                  let activeEnd = rest.range(of: " active, ", range: objectsRange.upperBound..<rest.endIndex),
                  let active = Int(rest[objectsRange.upperBound..<activeEnd.lowerBound]),
                  let elevatedEnd = rest.range(of: " elevated", range: activeEnd.upperBound..<rest.endIndex),
                  let elevated = Int(rest[activeEnd.upperBound..<elevatedEnd.lowerBound])
            else { return nil }
            return {
                // Counts for the bridge already known, never an introduction (see the type's notes).
                guard $0.layout == layout else { return }
                $0.windowSeconds = seconds
                $0.windowActiveObjects = active
                $0.windowElevatedObjects = elevated
            }
        }
        if let rest = Self.rest(of: line, after: "[SWHost] audio: ") {
            // Every line here is the software host's word on the track's audio pipeline, so the last
            // one is the route. Only `<source> rendered into 5.1.2 as PCM for the renderer` means
            // the object bridge runs (it follows the bridge's init line); a refusal can quote the
            // same words, `stays PCM (DTS:X is rendered into 7.1.4 or 9.1.6 only, not 5.1.2)`.
            let renders = rest.hasSuffix(" for the renderer") && rest.contains(" rendered into ")
            let route = Self.clip(rest.hasSuffix(" for the renderer") ? String(rest.dropLast(17)) : rest)
            if renders {
                return { $0.route = route; $0.renderingAnnounced = true }
            }
            return { $0.route = route; $0.renderingAnnounced = false; $0.forgetBridge() }
        }
        if let rest = Self.rest(of: line, after: "[HLSVideoEngine] ") {
            // `audio: codec=… (bridge required), decoding + Atmos object render into 5.1.2, APAC re-encode`
            // is the plan and precedes the bridge's init line; the refusals keep the channel bridge.
            if let start = rest.range(of: "Atmos object render into") ?? rest.range(of: "DTS:X render into") {
                var route = String(rest[start.lowerBound...])
                if let end = route.range(of: " re-encode") { route = String(route[..<end.lowerBound]) }
                let clipped = Self.clip(route)
                return { $0.route = clipped; $0.renderingAnnounced = true }
            }
            if nativeRefusals.contains(where: { rest.contains($0) }) {
                let clipped = Self.clip(rest)
                return { $0.route = clipped; $0.renderingAnnounced = false; $0.forgetBridge() }
            }
            if rest.hasPrefix("audio: codec="), rest.contains("(bridge required)") {
                // A plain channel bridge (`decoding + FLAC re-encode`): no object bridge follows.
                return { $0.renderingAnnounced = false; $0.forgetBridge() }
            }
        }
        return nil
    }

    private static func rest(of line: String, after prefix: String) -> String? {
        line.hasPrefix(prefix) ? String(line.dropFirst(prefix.count)) : nil
    }

    /// `key=value` out of a space-separated line.
    private static func value(for key: String, in text: String) -> String? {
        guard let range = text.range(of: key + "=") else { return nil }
        let token = text[range.upperBound...].prefix { $0 != " " }
        return token.isEmpty ? nil : String(token)
    }

    private static func clip(_ text: String) -> String {
        text.count <= 200 ? text : String(text.prefix(199)) + "…"
    }
}
