// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition. What the server cannot tell the app about a file, read from a
// pre-computed index: the Dolby Vision enhancement-layer type above all.

import Foundation

/// One Dolby Vision Profile 7 file of the library as `PrivateBuild/tools/format-index/build_index.py`
/// describes it. The index lists those files and nothing else: the enhancement-layer type is the one
/// thing the server cannot tell, while audio is the server's to name (its stream profile).
nonisolated struct FormatIndexEntry: Codable, Equatable, Sendable {
    enum EnhancementLayer: String, Codable, Sendable {
        case full = "FEL"
        case minimal = "MEL"
    }

    /// Path under the library's Movies folder, as the index was built: "Title (2024)/file.mkv".
    let path: String
    let size: Int64
    var dolbyVisionProfile: Int? = nil
    var enhancementLayer: EnhancementLayer? = nil
    /// False when the RPU says FEL but the stream carries no enhancement-layer data to compose.
    var enhancementLayerPresent: Bool? = nil
    /// True for a dual-track file: the enhancement layer, and with it every RPU, is a video track of
    /// its own beside the base layer's.
    var enhancementLayerSeparateTrack: Bool? = nil
}

nonisolated struct FormatIndex: Codable, Sendable {
    let format: String
    let version: Int
    let generatedAt: String?
    let entries: [FormatIndexEntry]
}

/// Holds the index in memory, caches it on disk, fetches it from the configured URL, and answers
/// the one question the badges ask: for this file, what is the enhancement layer?
///
/// Plex names a file by the server's own path ("/movies/Title (2024)/file.mkv"); the index by the
/// path under the Movies folder. The match strips the server path through its first "/movies/"
/// (any case), so both sides apply the same rule the index builder does, and falls back to file
/// size plus name when the prefix is something else. A path hit must agree on size when the
/// caller knows one, so a replaced file is never described by its predecessor's entry.
@MainActor
final class FormatIndexStore {
    static let shared = FormatIndexStore()
    /// Posted after the index in memory changes or the tags setting flips; badge rows rebuild on it.
    static let didChange = Notification.Name("FormatIndexStoreDidChange")

    enum Keys {
        static let enabled = "formatTagsEnabled"
        static let url = "formatIndexURL"
    }

    /// None built in: with no URL configured, nothing is fetched. A file served without a login
    /// (Home Assistant's /local/ folder, for one) is readable by anyone who can reach that server;
    /// a long random path segment is then the only thing keeping it private.
    static let defaultURL = ""
    /// A copy older than this is fetched again at launch and whenever the app comes back to the front.
    static let staleAfter: TimeInterval = 6 * 3600

    static var showsTags: Bool {
        get { UserDefaults.standard.object(forKey: Keys.enabled) as? Bool ?? false }
        set {
            UserDefaults.standard.set(newValue, forKey: Keys.enabled)
            NotificationCenter.default.post(name: didChange, object: nil)
        }
    }

    static var indexURLString: String {
        get {
            let saved = UserDefaults.standard.string(forKey: Keys.url)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return saved.isEmpty ? defaultURL : saved
        }
        set { UserDefaults.standard.set(newValue.trimmingCharacters(in: .whitespacesAndNewlines), forKey: Keys.url) }
    }

    private(set) var entryCount = 0
    private(set) var generatedAt: String?
    private(set) var lastRefreshed: Date?
    private(set) var lastError: String?
    private(set) var isRefreshing = false
    private var byPath: [String: FormatIndexEntry] = [:]
    private var bySizeAndName: [String: FormatIndexEntry] = [:]
    /// Replaceable for tests; the default fetches with a 15 s timeout.
    var fetch: @MainActor (URL) async throws -> Data = { url in
        var request = URLRequest(url: url)
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw URLError(.badServerResponse, userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode)"])
        }
        return data
    }
    /// The clock staleness is judged by; tests move it.
    var now: () -> Date = Date.init
    /// Where the fetched index is kept between launches; tests point it at a scratch file.
    var cacheURL: URL = {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first ?? FileManager.default.temporaryDirectory
        return caches.appendingPathComponent("format-index.json")
    }()
    /// The URL the cached index came from, so a changed setting does not keep serving the old source.
    private var cacheSourceURL: URL { cacheURL.appendingPathExtension("source") }

    init() {}

    // MARK: Lookups

    /// The index entry for a server file path and size, or nil when the index does not know it.
    func entry(forFile path: String?, size: Int64?) -> FormatIndexEntry? {
        guard let path else { return nil }
        if let hit = byPath[Self.normalize(serverPath: path)], size == nil || hit.size == size { return hit }
        if let size, let hit = bySizeAndName[Self.sizeAndName(size: size, path: path)] { return hit }
        return nil
    }

    /// "FEL" or "MEL" for a Profile 7 file, nil when tags are off, the file is unknown, or the RPU
    /// says FEL without any enhancement-layer data to compose.
    func enhancementLayerBadge(forFile path: String?, size: Int64?) -> String? {
        guard Self.showsTags, let entry = entry(forFile: path, size: size), let layer = entry.enhancementLayer else { return nil }
        if layer == .full, entry.enhancementLayerPresent == false { return nil }
        return layer.rawValue
    }

    /// "DV FEL 2-track" or "DV MEL 2-track" for a dual-track file, nil for every other and when tags
    /// are off. Asked only for a file whose first video track is not Dolby Vision: the bare base layer.
    /// The engine loads such a file as HDR10 (its own tool, run on the first 24 MB of such files, on
    /// 7.56.0 and 7.61.0), so this names what the file holds beside the range that plays and never
    /// replaces it.
    func separateTrackLayerBadge(forFile path: String?, size: Int64?) -> String? {
        guard Self.showsTags, let entry = entry(forFile: path, size: size),
              entry.enhancementLayerSeparateTrack == true, let layer = entry.enhancementLayer else { return nil }
        return "DV \(layer.rawValue) 2-track"
    }

    // MARK: Loading

    /// Loads the cached index (decoded off the main actor) and refreshes it from the URL when it is
    /// missing, stale, or came from a different URL than the one configured now.
    @discardableResult
    func startup() -> Task<Void, Never> {
        let cache = cacheURL, source = cacheSourceURL, configured = Self.indexURLString
        return Task { [weak self] in
            let loaded = await Task.detached(priority: .utility) { () -> (FormatIndex, Date)? in
                guard let data = try? Data(contentsOf: cache), let index = try? FormatIndexStore.decode(data) else { return nil }
                let from = (try? String(contentsOf: source, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
                guard from == nil || from == configured else { return nil }
                let modified = (try? FileManager.default.attributesOfItem(atPath: cache.path)[.modificationDate] as? Date) ?? .distantPast
                return (index, modified)
            }.value
            guard let self else { return }
            // A refresh that finished while the cache was being read installed something newer, and a
            // URL changed meanwhile makes the cached copy another source's.
            if let loaded, self.lastRefreshed == nil, Self.indexURLString == configured {
                self.replace(with: loaded.0)
                self.lastRefreshed = loaded.1
            }
            if self.isStale {
                await self.refresh()
            }
        }
    }

    /// No copy yet, or one older than `staleAfter`.
    private var isStale: Bool {
        lastRefreshed.map { now().timeIntervalSince($0) > Self.staleAfter } ?? true
    }

    /// Fetches again when the copy in memory is stale; nil when it is fresh or a fetch is running.
    /// For the moment the app returns to the foreground: tvOS keeps it alive for days while the index
    /// may be rebuilt meanwhile, so a launch-only refresh left new titles untagged until a relaunch.
    @discardableResult
    func refreshIfStale() -> Task<Bool, Never>? {
        guard isStale, !isRefreshing else { return nil }
        return Task { await refresh() }
    }

    /// Fetches the index from the configured URL; true when it replaced the one in memory. A second
    /// call while one is in flight returns false without touching `lastError`; a response for a URL
    /// that changed meanwhile is discarded.
    @discardableResult
    func refresh() async -> Bool {
        guard !isRefreshing else { return false }
        let urlString = Self.indexURLString
        guard !urlString.isEmpty else {   // nothing configured: nothing to fetch, and nothing wrong
            lastError = nil
            return false
        }
        guard let url = URL(string: urlString), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", url.host != nil else {
            lastError = "The index URL is not an http(s) address."
            return false
        }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            let data = try await fetch(url)
            guard Self.indexURLString == urlString else { return false }
            let index = try Self.decode(data)
            replace(with: index)
            lastRefreshed = Date()
            lastError = nil
            try? data.write(to: cacheURL, options: .atomic)
            try? urlString.write(to: cacheSourceURL, atomically: true, encoding: .utf8)
            return true
        } catch {
            lastError = error.localizedDescription
            return false
        }
    }

    /// Installs an index without touching the cache or the network (tests, and `refresh`). Two
    /// entries whose paths collapse to one key are left out of the path table, so neither can be
    /// mistaken for the other; size plus name still finds them.
    func replace(with index: FormatIndex) {
        var paths: [String: FormatIndexEntry] = [:]
        var ambiguous: Set<String> = []
        var sizes: [String: FormatIndexEntry] = [:]
        for entry in index.entries {
            let key = Self.normalize(indexPath: entry.path)
            if paths[key] != nil { ambiguous.insert(key) } else { paths[key] = entry }
            sizes[Self.sizeAndName(size: entry.size, path: entry.path)] = entry
        }
        for key in ambiguous { paths[key] = nil }
        byPath = paths
        bySizeAndName = sizes
        entryCount = index.entries.count
        generatedAt = index.generatedAt
        NotificationCenter.default.post(name: Self.didChange, object: self)
    }

    nonisolated static func decode(_ data: Data) throws -> FormatIndex {
        let index = try JSONDecoder().decode(FormatIndex.self, from: data)
        guard index.format == "rivulet-format-index" else {
            throw DecodingError.dataCorrupted(.init(codingPath: [], debugDescription: "not a rivulet format index"))
        }
        return index
    }

    /// A server path, "/movies/Title (2024)/file.mkv", becomes "title (2024)/file.mkv": the part
    /// after the first "/movies/" segment, the same cut the index builder makes.
    nonisolated static func normalize(serverPath path: String) -> String {
        var p = path.replacingOccurrences(of: "\\", with: "/")
        if let range = p.range(of: "/movies/", options: [.caseInsensitive]) {
            p = String(p[range.upperBound...])
        }
        return normalize(indexPath: p)
    }

    /// An index key is already relative; only separators and case are unified.
    nonisolated static func normalize(indexPath path: String) -> String {
        var p = path.replacingOccurrences(of: "\\", with: "/")
        while p.hasPrefix("/") { p.removeFirst() }
        return p.lowercased()
    }

    nonisolated static func sizeAndName(size: Int64, path: String) -> String {
        let unified = path.replacingOccurrences(of: "\\", with: "/")
        return "\(size)|" + (unified.split(separator: "/").last.map(String.init) ?? unified).lowercased()
    }
}
