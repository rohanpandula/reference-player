// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import XCTest
@testable import Rivulet

@MainActor
final class FormatIndexTests: XCTestCase {
    private var enabledBefore: Bool!
    private var urlBefore: String!
    private var scratch: URL!

    override func setUp() {
        enabledBefore = FormatIndexStore.showsTags
        urlBefore = UserDefaults.standard.string(forKey: FormatIndexStore.Keys.url) ?? ""
        FormatIndexStore.showsTags = true
        FormatIndexStore.indexURLString = "http://index.invalid/format-index.json"   // none is built in
        scratch = FileManager.default.temporaryDirectory.appendingPathComponent("FormatIndexTests-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: scratch, withIntermediateDirectories: true)
    }

    override func tearDown() {
        FormatIndexStore.showsTags = enabledBefore
        UserDefaults.standard.set(urlBefore, forKey: FormatIndexStore.Keys.url)
        try? FileManager.default.removeItem(at: scratch)
    }

    private let fel = "Example Movie (2019)/Example Movie (2019) 2160p.mkv"
    private let mel = "Another Example (2021)/Another Example (2021) 2160p.mkv"

    private var sample: Data {
        """
        {"format":"rivulet-format-index","version":1,"generatedAt":"2026-11-08T23:00:00Z","root":"Movies","entries":[
          {"path":"\(fel)","size":80000000000,"dolbyVisionProfile":7,"enhancementLayer":"FEL","enhancementLayerPresent":true},
          {"path":"\(mel)","size":70000000000,"dolbyVisionProfile":7,"enhancementLayer":"MEL","enhancementLayerPresent":true},
          {"path":"Third Example (2013)/Third Example (2013) 2160p.mkv","size":20000000000,
           "dolbyVisionProfile":7,"enhancementLayer":"FEL","enhancementLayerPresent":false},
          {"path":"Fourth Example (2019)/Fourth Example (2019) 2160p.mkv","size":30000000000,"dolbyVisionProfile":5},
          {"path":"Box Set/Movies/Nested Example (2020)/nested.mkv","size":111,"dolbyVisionProfile":7,"enhancementLayer":"FEL","enhancementLayerPresent":true},
          {"path":"Same Name/a.mkv","size":1,"dolbyVisionProfile":7,"enhancementLayer":"FEL","enhancementLayerPresent":true},
          {"path":"Same name/a.mkv","size":2,"dolbyVisionProfile":7,"enhancementLayer":"MEL","enhancementLayerPresent":true}
        ]}
        """.data(using: .utf8)!
    }

    private func store() throws -> FormatIndexStore {
        let s = FormatIndexStore()
        s.cacheURL = scratch.appendingPathComponent("format-index.json")
        s.replace(with: try FormatIndexStore.decode(sample))
        return s
    }

    func testDecodeRejectsAnUnrelatedDocument() {
        XCTAssertThrowsError(try FormatIndexStore.decode(#"{"format":"something-else","version":1,"entries":[]}"#.data(using: .utf8)!))
        XCTAssertNoThrow(try FormatIndexStore.decode(sample))
    }

    func testPlexServerPathsMatchIndexPaths() throws {
        let s = try store()
        XCTAssertEqual(s.entryCount, 7)
        XCTAssertEqual(s.entry(forFile: "/movies/" + fel, size: nil)?.enhancementLayer, .full)
        XCTAssertEqual(s.entry(forFile: "/srv/media/Movies/" + mel, size: nil)?.enhancementLayer, .minimal)
        XCTAssertEqual(s.entry(forFile: "/MOVIES/" + fel.uppercased(), size: nil)?.enhancementLayer, .full, "case does not matter")
        XCTAssertNil(s.entry(forFile: "/movies/Missing Example (2020)/missing.mkv", size: 1))
    }

    func testAFolderNamedMoviesInsideTheLibraryStillMatches() throws {
        let s = try store()
        XCTAssertEqual(s.entry(forFile: "/movies/Box Set/Movies/Nested Example (2020)/nested.mkv", size: nil)?.size, 111,
                       "only the first /movies/ segment is the library root")
    }

    func testAPathHitMustAgreeOnSizeWhenTheCallerKnowsIt() throws {
        let s = try store()
        XCTAssertEqual(s.entry(forFile: "/movies/" + fel, size: 80_000_000_000)?.enhancementLayer, .full)
        XCTAssertNil(s.entry(forFile: "/movies/" + fel, size: 12_345), "a replaced file is not described by its predecessor")
    }

    func testSizeAndNameFallbackSurvivesADifferentMountAndWindowsPaths() throws {
        let s = try store()
        XCTAssertEqual(s.entry(forFile: "/data/films/x/" + fel.split(separator: "/").last!, size: 80_000_000_000)?.enhancementLayer, .full)
        XCTAssertEqual(s.entry(forFile: #"D:\Films\x\"# + fel.split(separator: "/").last!, size: 80_000_000_000)?.enhancementLayer, .full)
        XCTAssertNil(s.entry(forFile: "/data/films/x/" + fel.split(separator: "/").last!, size: 1))
    }

    func testPathsThatCollapseToOneKeyAreOnlyFoundBySize() throws {
        let s = try store()
        XCTAssertNil(s.entry(forFile: "/movies/Same Name/a.mkv", size: nil), "two entries share this key once case is folded")
        XCTAssertEqual(s.entry(forFile: "/movies/Same Name/a.mkv", size: 1)?.enhancementLayer, .full)
        XCTAssertEqual(s.entry(forFile: "/movies/Same name/a.mkv", size: 2)?.enhancementLayer, .minimal)
    }

    func testBadgeSaysFELOrMELOnlyWithRealLayerData() throws {
        let s = try store()
        XCTAssertEqual(s.enhancementLayerBadge(forFile: "/movies/" + fel, size: nil), "FEL")
        XCTAssertEqual(s.enhancementLayerBadge(forFile: "/movies/" + mel, size: nil), "MEL")
        XCTAssertNil(s.enhancementLayerBadge(forFile: "/movies/Third Example (2013)/Third Example (2013) 2160p.mkv", size: nil),
                     "a FEL RPU with no enhancement layer in the stream is not FEL to the viewer")
        XCTAssertNil(s.enhancementLayerBadge(forFile: "/movies/Fourth Example (2019)/Fourth Example (2019) 2160p.mkv", size: nil))
        FormatIndexStore.showsTags = false
        XCTAssertNil(s.enhancementLayerBadge(forFile: "/movies/" + fel, size: nil))
    }

    /// A dual-track file: the base layer is the first video track, the Dolby Vision layer a track of
    /// its own. The engine plays the base layer alone, so the layer gets a badge of its own to stand
    /// beside the range that plays, and only a marked entry earns it.
    func testADualTrackFileNamesItsLayerAsASecondTrack() throws {
        let s = FormatIndexStore()
        let remux = "Dual Track Example (2021)/Dual Track Example (2021) 2160p.mkv"
        let clip = "Demos/Example Demo Disc/clip.mp4"
        s.replace(with: try FormatIndexStore.decode(Data("""
        {"format":"rivulet-format-index","version":1,"entries":[
          {"path":"\(remux)","size":48000000000,"dolbyVisionProfile":7,
           "enhancementLayer":"MEL","enhancementLayerPresent":true,"enhancementLayerSeparateTrack":true},
          {"path":"\(clip)","size":500000000,"dolbyVisionProfile":7,
           "enhancementLayer":"FEL","enhancementLayerPresent":true,"enhancementLayerSeparateTrack":true},
          {"path":"\(fel)","size":80000000000,"dolbyVisionProfile":7,"enhancementLayer":"FEL","enhancementLayerPresent":true}]}
        """.utf8)))
        XCTAssertEqual(s.separateTrackLayerBadge(forFile: "/movies/" + remux, size: 48_000_000_000), "DV MEL 2-track")
        XCTAssertEqual(s.separateTrackLayerBadge(forFile: "/" + clip, size: 500_000_000), "DV FEL 2-track",
                       "a share mounted at the server's root matches by its path under the media share")
        XCTAssertNil(s.separateTrackLayerBadge(forFile: "/movies/" + fel, size: 80_000_000_000), "a single-track file has no such badge")
        XCTAssertNil(s.separateTrackLayerBadge(forFile: "/movies/Missing Example (2020)/missing.mkv", size: 1))
        XCTAssertNil(s.separateTrackLayerBadge(forFile: "/movies/" + remux, size: 12_345), "a replaced file is not its predecessor")
        FormatIndexStore.showsTags = false
        XCTAssertNil(s.separateTrackLayerBadge(forFile: "/" + clip, size: 500_000_000))
    }

    func testRefreshUsesTheInjectedTransportAndReportsFailures() async throws {
        let s = FormatIndexStore()
        s.cacheURL = scratch.appendingPathComponent("format-index.json")
        s.fetch = { _ in self.sample }
        let first = await s.refresh()
        XCTAssertTrue(first)
        XCTAssertEqual(s.entryCount, 7)
        XCTAssertNil(s.lastError)
        XCTAssertTrue(FileManager.default.fileExists(atPath: s.cacheURL.path), "the fetched index is cached")
        s.fetch = { _ in throw URLError(.cannotConnectToHost) }
        let second = await s.refresh()
        XCTAssertFalse(second)
        XCTAssertNotNil(s.lastError)
        XCTAssertEqual(s.entryCount, 7, "a failed refresh keeps the index it had")
    }

    func testAStaleCopyIsFetchedAgainAndAFreshOneIsNot() async {
        let s = FormatIndexStore()
        s.cacheURL = scratch.appendingPathComponent("format-index.json")
        var fetches = 0
        s.fetch = { _ in fetches += 1; return self.sample }
        let first = await s.refreshIfStale()?.value      // nothing in memory yet
        XCTAssertEqual(first, true)
        XCTAssertEqual(fetches, 1)
        XCTAssertNil(s.refreshIfStale(), "a copy fetched a moment ago is not stale")
        XCTAssertEqual(fetches, 1)
    }

    func testACopyOlderThanTheStalenessWindowIsFetchedAgain() async {
        let s = FormatIndexStore()
        s.cacheURL = scratch.appendingPathComponent("format-index.json")
        var fetches = 0
        s.fetch = { _ in fetches += 1; return self.sample }
        _ = await s.refresh()
        XCTAssertEqual(fetches, 1)
        s.now = { Date().addingTimeInterval(FormatIndexStore.staleAfter - 60) }
        XCTAssertNil(s.refreshIfStale(), "just inside the window: still fresh")
        s.now = { Date().addingTimeInterval(FormatIndexStore.staleAfter + 60) }
        let again = await s.refreshIfStale()?.value
        XCTAssertEqual(again, true, "past the window: fetched again")
        XCTAssertEqual(fetches, 2)
    }

    /// A cached index as an earlier launch left it: the data, the URL it came from, and an age.
    private func cachedStore(from source: String, age: TimeInterval) throws -> FormatIndexStore {
        let s = FormatIndexStore()
        s.cacheURL = scratch.appendingPathComponent("format-index.json")
        try sample.write(to: s.cacheURL)
        try source.write(to: s.cacheURL.appendingPathExtension("source"), atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(-age)], ofItemAtPath: s.cacheURL.path)
        return s
    }

    func testStartupKeepsTheCachedIndexWhenTheFetchFails() async throws {
        FormatIndexStore.indexURLString = "http://index.invalid/format-index.json"
        let s = try cachedStore(from: "http://index.invalid/format-index.json", age: FormatIndexStore.staleAfter + 3600)
        var fetches = 0
        s.fetch = { _ in fetches += 1; throw URLError(.cannotConnectToHost) }
        await s.startup().value
        XCTAssertEqual(fetches, 1, "a stale cache is fetched again")
        XCTAssertEqual(s.entryCount, 7, "and kept when the fetch fails")
        XCTAssertNotNil(s.lastError)
    }

    func testStartupIgnoresACacheFromAnotherURL() async throws {
        FormatIndexStore.indexURLString = "http://index.invalid/format-index.json"
        let s = try cachedStore(from: "http://elsewhere.invalid/format-index.json", age: 60)
        s.fetch = { _ in throw URLError(.cannotConnectToHost) }
        await s.startup().value
        XCTAssertEqual(s.entryCount, 0, "another source's copy is not this one's")
    }

    /// Nothing is built in: a fresh install shows no tags and, with no URL configured, fetches nothing
    /// and reports nothing wrong.
    func testNoIndexURLAndNoTagsUntilTheUserSetsThem() async {
        UserDefaults.standard.removeObject(forKey: FormatIndexStore.Keys.enabled)
        XCTAssertFalse(FormatIndexStore.showsTags, "Format Tags default Off")
        XCTAssertEqual(FormatIndexStore.defaultURL, "")
        FormatIndexStore.indexURLString = ""
        XCTAssertEqual(FormatIndexStore.indexURLString, "")
        let s = FormatIndexStore()
        s.cacheURL = scratch.appendingPathComponent("format-index.json")
        var fetches = 0
        s.fetch = { _ in fetches += 1; return self.sample }
        let result = await s.refresh()
        XCTAssertFalse(result)
        XCTAssertNil(s.lastError)
        XCTAssertEqual(fetches, 0)
    }

    func testANonHTTPIndexURLIsRefusedWithoutAFetch() async {
        let s = FormatIndexStore()
        s.cacheURL = scratch.appendingPathComponent("format-index.json")
        var fetches = 0
        s.fetch = { _ in fetches += 1; return self.sample }
        for url in ["file:///etc/hosts", "ftp://index.invalid/x.json", "not a url"] {
            FormatIndexStore.indexURLString = url
            let result = await s.refresh()
            XCTAssertFalse(result, url)
            XCTAssertNotNil(s.lastError, url)
        }
        XCTAssertEqual(fetches, 0)
        XCTAssertEqual(s.entryCount, 0)
    }

    /// The tests run inside the app, and `RivuletApp` starts the shared index only outside a test
    /// host, so no test run fetches from the user's network.
    func testTheTestHostNeverStartsTheSharedIndex() {
        XCTAssertNotNil(ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"], "the guard's condition holds here")
        let shared = FormatIndexStore.shared
        XCTAssertNil(shared.lastRefreshed)
        XCTAssertNil(shared.lastError)
        XCTAssertFalse(shared.isRefreshing)
    }

    func testOtherLibrariesMatchByTheirPathUnderTheMediaShare() throws {
        let s = FormatIndexStore()
        s.replace(with: try FormatIndexStore.decode(Data(#"""
        {"format":"rivulet-format-index","version":1,"entries":[
          {"path":"TV/Show (2020)/Season 01/Show S01E01.mkv","size":5,"dolbyVisionProfile":7,"enhancementLayer":"FEL","enhancementLayerPresent":true},
          {"path":"Demos/Example/clip.mkv","size":6,"dolbyVisionProfile":7,"enhancementLayer":"MEL","enhancementLayerPresent":true}]}
        """#.utf8)))
        XCTAssertEqual(s.enhancementLayerBadge(forFile: "/tv/Show (2020)/Season 01/Show S01E01.mkv", size: 5), "FEL",
                       "a TV library mounted at /tv")
        XCTAssertEqual(s.enhancementLayerBadge(forFile: "/Demos/Example/clip.mkv", size: 6), "MEL")
        XCTAssertEqual(s.enhancementLayerBadge(forFile: "/data/shows/Show S01E01.mkv", size: 5), "FEL", "another mount: size and name")
    }

    func testASecondRefreshWhileOneIsInFlightIsRefusedQuietly() async {
        let s = FormatIndexStore()
        s.cacheURL = scratch.appendingPathComponent("format-index.json")
        s.fetch = { _ in try await Task.sleep(nanoseconds: 150_000_000); return self.sample }
        async let slow = s.refresh()
        try? await Task.sleep(nanoseconds: 20_000_000)
        let overlapping = await s.refresh()
        XCTAssertFalse(overlapping)
        XCTAssertNil(s.lastError, "an in-flight refresh is not an error")
        let slowResult = await slow
        XCTAssertTrue(slowResult)
    }

    func testAResponseForASupersededURLIsDiscarded() async {
        let s = FormatIndexStore()
        s.cacheURL = scratch.appendingPathComponent("format-index.json")
        FormatIndexStore.indexURLString = "http://first.local/index.json"
        s.fetch = { _ in
            FormatIndexStore.indexURLString = "http://second.local/index.json"   // the user changed it mid-fetch
            return self.sample
        }
        let result = await s.refresh()
        XCTAssertFalse(result)
        XCTAssertEqual(s.entryCount, 0)
    }

    func testReplacePostsAChangeNotification() throws {
        let s = FormatIndexStore()
        let expectation = expectation(forNotification: FormatIndexStore.didChange, object: s)
        s.replace(with: try FormatIndexStore.decode(sample))
        wait(for: [expectation], timeout: 1)
    }

    func testNormalizeRules() {
        XCTAssertEqual(FormatIndexStore.normalize(serverPath: "/movies/A (2020)/a.mkv"), "a (2020)/a.mkv")
        XCTAssertEqual(FormatIndexStore.normalize(serverPath: "/srv/media/Movies/A (2020)/a.mkv"), "a (2020)/a.mkv")
        XCTAssertEqual(FormatIndexStore.normalize(serverPath: "/other/A (2020)/a.mkv"), "other/a (2020)/a.mkv")
        XCTAssertEqual(FormatIndexStore.normalize(serverPath: #"\\server\Movies\A\a.mkv"#), "a/a.mkv")
        XCTAssertEqual(FormatIndexStore.normalize(indexPath: "Movies/f.mkv"), "movies/f.mkv", "an index key keeps its own segments")
    }
}
