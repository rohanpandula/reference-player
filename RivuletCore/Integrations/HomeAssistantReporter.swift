// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition. Tells Home Assistant what the player is doing, so a dashboard can show
// the Apple TV's source quality next to what a receiver or TV integration reports receiving.
// Off by default, with no address: nothing is sent until a user sets both in Settings.

import Foundation
import os

/// One playback snapshot, posted as JSON to a Home Assistant webhook. The snake_case
/// keys are the attribute names of the `sensor.rivulet_playback` template sensor on
/// the Home Assistant side (`PrivateBuild/homeassistant/rivulet_playback.yaml`).
nonisolated struct HomeAssistantPlaybackReport: Codable, Equatable, Sendable {
    /// playing, paused, buffering, idle or test.
    var state: String
    var player: String = "Reference Player"
    var appVersion: String? = nil
    /// "Apple TV 4K (2nd generation)", so two boxes feeding one dashboard can be told apart.
    var device: String? = nil
    /// The hardware identifier behind it, e.g. "AppleTV11,1".
    var deviceModel: String? = nil
    var osVersion: String? = nil
    var engine: String? = nil
    var title: String? = nil
    var series: String? = nil
    /// "S2 E5"
    var episode: String? = nil
    var year: Int? = nil
    /// Direct Play, Direct Stream or Transcode, as the Details pane shows them.
    var videoMode: String? = nil
    var audioMode: String? = nil
    var videoCodec: String? = nil
    /// "3840x2160", the source picture.
    var resolution: String? = nil
    /// The source's frame rate; `observedFps` is what the software path measures.
    var frameRate: Double? = nil
    /// What the picture is: SDR, HDR10, HDR10+, HLG, Dolby Vision, or Dolby Vision with
    /// the TV not yet confirmed. Follows the Details pane, which knows the engine reads
    /// SDR on the dual-layer route until the panel proves HDR.
    var hdrFormat: String? = nil
    var dolbyVisionProfile: Int? = nil
    /// Full (FEL) or Minimal (MEL), from the engine's route line.
    var dolbyVisionLayer: String? = nil
    /// The Details pane label: Dual Layer (FEL), Profile 8.1 (converted), HDR10 Base Layer, ...
    var dolbyVisionRoute: String? = nil
    var dualLayerRouteTaken: Bool? = nil
    var videoRoute: String? = nil
    var audioCodec: String? = nil
    var audioChannels: Int? = nil
    var audioLanguage: String? = nil
    /// The engine's active audio decoder.
    var audioDecoder: String? = nil
    var audioDelivery: String? = nil
    /// The Height Speakers setting as the engine understands it: a layout such as 5.1.2, or Off.
    var heightSpeakers: String? = nil
    /// The layout the object renderer is driving: from the engine's telemetry on its native
    /// session, from the bridge's session log on the software (FEL) route.
    var heightLayoutActive: String? = nil
    var objectsTotal: Int? = nil
    /// Telemetry: objects playing as of the latest metadata update. Session log: the most that
    /// played at once over the last `heightsWindowSeconds`.
    var objectsActive: Int? = nil
    var objectsElevated: Int? = nil
    var heightChannels: Int? = nil
    /// "telemetry" or "session log".
    var heightsSource: String? = nil
    var heightsWindowSeconds: Double? = nil
    /// The engine's verdict on the track: "TrueHD Atmos rendered into 5.1.2 as PCM", or why it
    /// stayed on the plain decode.
    var heightsRoute: String? = nil
    /// Set when the bridge is logging but this build cannot read its lines (an engine update
    /// changed them): the heights fields are then unknown, not off.
    var heightsNote: String? = nil
    /// The engine's lifetime average for the played media, not network throughput.
    var bitrateMbps: Double? = nil
    var observedFps: Double? = nil
    var droppedFrames: Int? = nil
    var reportedAt: String? = nil

    static func idle() -> HomeAssistantPlaybackReport { HomeAssistantPlaybackReport(state: "idle") }

    static let heightsLogUnreadNote =
        "The engine's spatial bridge is logging, but this build does not read its lines; heights are unknown, not off."

    /// snake_case keys, sorted, nil fields omitted.
    static func makeEncoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }

    /// Equality for "did anything change": the timestamp is not a change.
    func sameContent(as other: HomeAssistantPlaybackReport?) -> Bool {
        guard var other else { return false }
        var mine = self
        mine.reportedAt = nil
        other.reportedAt = nil
        return mine == other
    }
}

/// Posts playback reports to a Home Assistant webhook: no token in the app, and Home
/// Assistant only accepts the webhook from its own network (`local_only`). A report on
/// every state change (when something changed), every ten seconds regardless (so the
/// dashboard can tell a live report from a stale one by `reported_at`), and "idle" when
/// the session ends. Posts are delivered in order, one at a time, so a slow "playing"
/// can never land after "idle". Failures are logged, never shown; the dashboard is a
/// convenience and must not touch playback.
@MainActor
final class HomeAssistantReporter {
    static let shared = HomeAssistantReporter()

    enum Keys {
        static let enabled = "homeAssistantReportingEnabled"
        static let address = "homeAssistantAddress"
        static let webhookID = "homeAssistantWebhookID"
    }

    /// None built in: with no address configured, every report is dropped before a request is made.
    static let defaultAddress = ""
    static let heartbeatSeconds: TimeInterval = 10
    nonisolated private static let log = Logger(subsystem: "ReferencePlayer", category: "HomeAssistant")

    static var isEnabled: Bool {
        get { UserDefaults.standard.bool(forKey: Keys.enabled) }
        set { UserDefaults.standard.set(newValue, forKey: Keys.enabled) }
    }

    /// Scheme, host and port of the Home Assistant frontend, e.g. http://<host>:8123.
    static var address: String {
        get {
            let saved = UserDefaults.standard.string(forKey: Keys.address)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return saved.isEmpty ? defaultAddress : saved
        }
        set { UserDefaults.standard.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: Keys.address) }
    }

    /// Generated once per install, so the Home Assistant package can name it; editable
    /// to match a package that already exists. Clearing it generates a new one.
    static var webhookID: String {
        get {
            if let saved = UserDefaults.standard.string(forKey: Keys.webhookID), !saved.isEmpty { return saved }
            let generated = "rivulet-" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased().prefix(16)
            UserDefaults.standard.set(generated, forKey: Keys.webhookID)
            return generated
        }
        set {
            let trimmed = newValue.trimmingCharacters(in: .whitespacesAndNewlines)
            if trimmed.isEmpty {
                UserDefaults.standard.removeObject(forKey: Keys.webhookID)
            } else {
                UserDefaults.standard.set(trimmed, forKey: Keys.webhookID)
            }
        }
    }

    /// `<address>/api/webhook/<id>`; nil unless the address is a plain http(s) URL with a
    /// host and no embedded credentials, and the id is a token of letters, digits, dash
    /// and underscore.
    nonisolated static func webhookURL(address: String, id: String) -> URL? {
        guard var components = URLComponents(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
              let scheme = components.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = components.host, !host.isEmpty,
              components.user == nil, components.password == nil else { return nil }
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        guard !id.isEmpty, id.rangeOfCharacter(from: allowed.inverted) == nil else { return nil }
        components.path = "/api/webhook/" + id
        components.query = nil
        components.fragment = nil
        return components.url
    }

    /// Sends one encoded report; true on a 2xx. Tests replace it to watch delivery.
    var transport: @MainActor (URLRequest) async -> Bool

    private var snapshot: (@MainActor () -> HomeAssistantPlaybackReport?)?
    private var timer: Timer?
    /// The last report handed to the chain, successful or not; a forced send resends it.
    private var lastQueued: HomeAssistantPlaybackReport?
    private var sentThisSession = false
    /// Deliveries run on this chain, one after another, in the order they were queued.
    private var chain: Task<Void, Never>?

    init(transport: @escaping @MainActor (URLRequest) async -> Bool = HomeAssistantReporter.makeURLSessionTransport()) {
        self.transport = transport
    }

    /// Starts a session: `snapshot` describes the current playback whenever asked. The
    /// first report always goes out; the heartbeat then resends every ten seconds.
    func begin(snapshot: @escaping @MainActor () -> HomeAssistantPlaybackReport?) {
        self.snapshot = snapshot
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: Self.heartbeatSeconds, repeats: true) { _ in
            Task { @MainActor in HomeAssistantReporter.shared.refresh(force: true) }
        }
        refresh(force: true)
    }

    /// Queues the current snapshot: always when forced (the heartbeat), otherwise only
    /// when it differs from the last one queued.
    func refresh(force: Bool = false) {
        guard Self.isEnabled, let report = snapshot?() else { return }
        enqueue(report, force: force)
    }

    /// Ends the session. Safe to call more than once: Home Assistant hears "idle" once if
    /// it heard anything this session, and a later call is a no-op.
    func end() {
        timer?.invalidate()
        timer = nil
        snapshot = nil
        if sentThisSession, Self.isEnabled { enqueue(.idle(), force: true) }
        sentThisSession = false
        lastQueued = nil
    }

    /// For the Settings row: one "test" report, true when the address answered 2xx. Home
    /// Assistant answers 200 for an unknown webhook id too, so the caller says "answered",
    /// not "accepted".
    func sendTest() async -> Bool {
        var report = HomeAssistantPlaybackReport(state: "test")
        report.title = "Reference Player test report"
        return await deliver(report)
    }

    /// Waits for every queued delivery. Tests use it; production never needs to.
    func drain() async {
        await chain?.value
    }

    private func enqueue(_ report: HomeAssistantPlaybackReport, force: Bool) {
        guard force || !report.sameContent(as: lastQueued) else { return }
        lastQueued = report
        sentThisSession = true
        let previous = chain
        chain = Task { @MainActor [weak self] in
            await previous?.value
            guard let self else { return }
            _ = await self.deliver(report)
        }
    }

    private func deliver(_ report: HomeAssistantPlaybackReport) async -> Bool {
        guard let url = Self.webhookURL(address: Self.address, id: Self.webhookID) else {
            Self.log.error("Home Assistant address or webhook id is not usable; report dropped")
            return false
        }
        var stamped = report
        stamped.reportedAt = ISO8601DateFormatter().string(from: Date())
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        do {
            request.httpBody = try HomeAssistantPlaybackReport.makeEncoder().encode(stamped)
        } catch {
            Self.log.error("Home Assistant report could not be encoded: \(error.localizedDescription, privacy: .public)")
            return false
        }
        return await transport(request)
    }

    /// The production transport: a short-timeout ephemeral session, 2xx is success. Nonisolated so
    /// the initializer's default argument can build it from any context.
    nonisolated static func makeURLSessionTransport() -> @MainActor (URLRequest) async -> Bool {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 5
        configuration.waitsForConnectivity = false
        let session = URLSession(configuration: configuration)
        return { request in
            do {
                let (_, response) = try await session.data(for: request)
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                guard (200..<300).contains(status) else {
                    log.error("Home Assistant webhook answered HTTP \(status, privacy: .public)")
                    return false
                }
                return true
            } catch {
                log.error("Home Assistant report failed: \(error.localizedDescription, privacy: .public)")
                return false
            }
        }
    }
}
