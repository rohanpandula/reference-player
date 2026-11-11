// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import XCTest
@testable import Rivulet

/// Stands in for TMDB and MDBList: answers by host and path, and keeps what it was asked.
private final class DirectMetadataStub: URLProtocol {
    nonisolated(unsafe) static var answers: [String: (status: Int, body: Data)] = [:]
    /// Addresses that answer with a redirect to another.
    nonisolated(unsafe) static var redirects: [String: URL] = [:]
    nonisolated(unsafe) static var failure: Error?
    nonisolated(unsafe) static var asked: [URLRequest] = []

    static func reset() {
        answers = [:]
        redirects = [:]
        failure = nil
        asked = []
    }

    /// The production session's configuration and redirect rule, with this stub as its transport.
    static func session() -> URLSession {
        DirectMetadata.makeSession(protocolClasses: [DirectMetadataStub.self])
    }

    /// "api.themoviedb.org/3/movie/7001": the host and the path as sent, a trailing slash included.
    static func address(_ url: URL) -> String {
        let components = URLComponents(url: url, resolvingAgainstBaseURL: true)
        return (components?.host ?? "") + (components?.path ?? "")
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        Self.asked.append(request)
        if let failure = Self.failure {
            client?.urlProtocol(self, didFailWithError: failure)
            return
        }
        let url = request.url!
        if let target = Self.redirects[Self.address(url)] {
            // As a real redirect does: the same request, headers and all, aimed at the new address.
            var next = request
            next.url = target
            let moved = HTTPURLResponse(url: url, statusCode: 302, httpVersion: "HTTP/1.1",
                                        headerFields: ["Location": target.absoluteString])!
            client?.urlProtocol(self, wasRedirectedTo: next, redirectResponse: moved)
            // A redirect that is not followed ends as the redirect answer itself, with no body. One
            // that is followed has already moved to a new load, and these two calls are ignored.
            // (Measured on a Mac: finishing without the answer traps inside Foundation's
            // `data(for:)`, and not finishing at all never ends.)
            client?.urlProtocol(self, didReceive: moved, cacheStoragePolicy: .notAllowed)
            client?.urlProtocolDidFinishLoading(self)
            return
        }
        let (status, body) = Self.answers[Self.address(url)] ?? (404, Data())
        // Cacheable on purpose: a session that kept a cache would store this under the keyed address.
        let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: "HTTP/1.1",
                                       headerFields: ["Cache-Control": "public, max-age=3600", "Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .allowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

/// A caller's own session. A keyed request never rides it, and a refused one never reaches it.
private final class CallerSessionProbe: URLProtocol {
    nonisolated(unsafe) static var reached = 0

    static func session() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [CallerSessionProbe.self]
        return URLSession(configuration: configuration)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        Self.reached += 1
        client?.urlProtocol(self, didFailWithError: URLError(.unsupportedURL))
    }
    override func stopLoading() {}
}

/// The fixtures follow the two services' published references (TMDB v3, MDBList's API schema),
/// read on 2026-11-10. None is a recording of a live answer, and MDBList's schema leaves a rating
/// entry untyped: its `source` and `value` here are the names and scales upstream's decoder reads.
@MainActor
final class DirectMetadataTests: XCTestCase {
    private let keys = DirectMetadata.Keys(tmdb: "tmdbk3y", mdblist: "mdbk3y")
    private var keysBefore: DirectMetadata.Keys!
    private var sessionBefore: URLSession!

    /// MDBList's title answer: one entry per source, a null value where it has no score.
    private let mdblistTitle = Data(#"""
    {"id": 7001, "imdb_id": "tt9000001", "title": "Example Film", "year": 2001, "type": "movie", "score": 87,
     "ratings": [{"source": "imdb", "value": 8.7, "score": 87, "votes": 2100000, "url": "tt9000001"},
                 {"source": "metacritic", "value": 73, "score": 73, "votes": 36, "url": null},
                 {"source": "trakt", "value": 87, "score": 87, "votes": 60000, "url": null},
                 {"source": "tomatoes", "value": 83, "score": 83, "votes": 207, "url": null},
                 {"source": "popcorn", "value": 85, "score": 85, "votes": 250000, "url": null},
                 {"source": "tmdb", "value": 82, "score": 82, "votes": 26000, "url": null},
                 {"source": "letterboxd", "value": 4.2, "score": 84, "votes": 2000000, "url": null},
                 {"source": "rogerebert", "value": null, "score": null, "votes": null, "url": null}],
     "streams": []}
    """#.utf8)

    override func setUp() {
        keysBefore = DirectMetadata.storedKeys
        sessionBefore = DirectMetadata.session
        DirectMetadata.tmdbKey = ""
        DirectMetadata.mdblistKey = ""
        DirectMetadata.session = DirectMetadataStub.session()
        DirectMetadataStub.reset()
        CallerSessionProbe.reached = 0
        // The Discover service keeps its lists and details on disk between runs.
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first!
        for name in ["TMDBDiscoverDetailCache", "TMDBDiscoverListCache"] {
            try? FileManager.default.removeItem(at: caches.appendingPathComponent(name, isDirectory: true))
        }
    }

    override func tearDown() {
        DirectMetadata.tmdbKey = keysBefore.tmdb
        DirectMetadata.mdblistKey = keysBefore.mdblist
        DirectMetadata.session.invalidateAndCancel()
        DirectMetadata.session = sessionBefore
        DirectMetadataStub.reset()
    }

    /// A Worker address built the way the app's clients build theirs.
    private func worker(_ endpoint: String, _ query: KeyValuePairs<String, String> = [:], base: URL? = nil) -> URL {
        let base = base ?? TMDBConfig.proxyBaseURL
        var components = URLComponents(url: URL(string: endpoint, relativeTo: base)!, resolvingAgainstBaseURL: true)!
        if !query.isEmpty {
            components.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) }
        }
        return components.url!
    }

    /// The ratings address as `RatingsClient` builds it.
    private func ratings(_ kindAndID: String) -> URL {
        InsightsConfig.apiBaseURL.appendingPathComponent("v1/ratings/\(kindAndID)")
    }

    /// Host with path, and the query, of the direct request planned for a Worker address.
    private func direct(_ url: URL, keys: DirectMetadata.Keys? = nil, region: String? = "US")
        -> (address: String, query: [String: String])? {
        guard let planned = DirectMetadata.plan(for: url, keys: keys ?? self.keys, region: region)?.request.url,
              let components = URLComponents(url: planned, resolvingAgainstBaseURL: false) else { return nil }
        XCTAssertEqual(components.scheme, "https")
        let query = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value ?? "") })
        return (DirectMetadataStub.address(planned), query)
    }

    /// A Worker address planned with the fixture keys and sent on the stubbed session.
    private func answer(_ url: URL) async throws -> (Data, URLResponse) {
        let plan = try XCTUnwrap(DirectMetadata.plan(for: url, keys: keys, region: nil))
        return try await DirectMetadata.perform(plan, as: url, session: DirectMetadata.session)
    }

    /// How a keyed request ended: "HTTP 302, 0 bytes", "error -1001", or "no end" after five seconds.
    private func ending(of url: URL) async -> String {
        guard let plan = DirectMetadata.plan(for: url, keys: keys, region: nil) else { return "no route" }
        let session = DirectMetadata.session
        return await withTaskGroup(of: String.self) { group in
            group.addTask {
                do {
                    let (data, response) = try await DirectMetadata.perform(plan, as: url, session: session)
                    return "HTTP \((response as? HTTPURLResponse)?.statusCode ?? -1), \(data.count) bytes"
                } catch {
                    return "error \((error as? URLError)?.code.rawValue ?? 0)"
                }
            }
            group.addTask {
                try? await Task.sleep(for: .seconds(5))
                return "no end"
            }
            let first = await group.next() ?? "no end"
            group.cancelAll()
            return first
        }
    }

    private func refused(_ url: URL, method: String = "GET", file: StaticString = #filePath, line: UInt = #line) async {
        var request = URLRequest(url: url)
        request.httpMethod = method
        do {
            _ = try await CallerSessionProbe.session().attestedData(for: request)
            XCTFail("answered: \(url.absoluteString)", file: file, line: line)
        } catch {
            XCTAssertEqual((error as? URLError)?.code, .userAuthenticationRequired, url.absoluteString, file: file, line: line)
        }
    }

    // MARK: Routes

    func testEachTMDBRouteBecomesItsDocumentedCall() {
        let key = ["api_key": "tmdbk3y"]
        let cases: [(URL, String, [String: String], DirectMetadata.Reshape)] = [
            (worker("tmdb/details/7001", ["type": "movie", "append": "keywords"]), "api.themoviedb.org/3/movie/7001",
             ["append_to_response": "credits,keywords"], .details),
            (worker("tmdb/details/7002", ["type": "tv"]), "api.themoviedb.org/3/tv/7002",
             ["append_to_response": "credits"], .details),
            (worker("tmdb/credits/7001", ["type": "movie"]), "api.themoviedb.org/3/movie/7001/credits", [:], .none),
            (worker("tmdb/credits/7002", ["type": "tv"]), "api.themoviedb.org/3/tv/7002/credits", [:], .none),
            (worker("tmdb/images/7001", ["type": "movie"]), "api.themoviedb.org/3/movie/7001/images", [:], .none),
            (worker("tmdb/images/7002", ["type": "tv"]), "api.themoviedb.org/3/tv/7002/images", [:], .none),
            (worker("tmdb/person/7101"), "api.themoviedb.org/3/person/7101", [:], .none),
            (worker("tmdb/episode_credits/7002", ["season": "1", "episode": "2"]),
             "api.themoviedb.org/3/tv/7002/season/1/episode/2/credits", [:], .none),
            (worker("tmdb/find/tt9000002", ["source": "imdb_id"]), "api.themoviedb.org/3/find/tt9000002",
             ["external_source": "imdb_id"], .none),
            (worker("tmdb/find/81189", ["source": "tvdb_id"]), "api.themoviedb.org/3/find/81189",
             ["external_source": "tvdb_id"], .none),
            (worker("tmdb/discover/movie", ["type": "movie", "with_genres": "28|12", "with_keywords": "9715|4379",
                                            "sort_by": "popularity.desc"]),
             "api.themoviedb.org/3/discover/movie",
             ["with_genres": "28|12", "with_keywords": "9715|4379", "sort_by": "popularity.desc"], .none),
            (worker("tmdb/discover/tv", ["type": "tv", "sort_by": "popularity.desc"]), "api.themoviedb.org/3/discover/tv",
             ["sort_by": "popularity.desc"], .none),
        ]
        for (url, address, query, reshape) in cases {
            let planned = direct(url)
            XCTAssertEqual(planned?.address, address, url.absoluteString)
            XCTAssertEqual(planned?.query, query.merging(key) { first, _ in first }, url.absoluteString)
            XCTAssertEqual(DirectMetadata.plan(for: url, keys: keys, region: "US")?.reshape, reshape, url.absoluteString)
        }
    }

    /// Asked with each section's own `proxyPath`, so a list upstream adds later fails here until it has a route.
    func testEveryListTheAppAsksForHasARoute() {
        let expected: [TMDBDiscoverSection: String] = [
            .movieTrending: "api.themoviedb.org/3/trending/movie/week",
            .moviePopular: "api.themoviedb.org/3/movie/popular",
            .movieNowPlaying: "api.themoviedb.org/3/movie/now_playing",
            .movieUpcoming: "api.themoviedb.org/3/movie/upcoming",
            .movieTopRated: "api.themoviedb.org/3/movie/top_rated",
            .tvTrending: "api.themoviedb.org/3/trending/tv/week",
            .tvPopular: "api.themoviedb.org/3/tv/popular",
            .tvAiringToday: "api.themoviedb.org/3/tv/airing_today",
            .tvOnTheAir: "api.themoviedb.org/3/tv/on_the_air",
            .tvTopRated: "api.themoviedb.org/3/tv/top_rated",
        ]
        for section in TMDBDiscoverSection.allCases + [.movieTrending, .tvTrending] {
            let url = worker("tmdb/list/\(section.proxyPath)", ["type": section.mediaType.rawValue])
            let planned = direct(url)
            XCTAssertNotNil(expected[section], "\(section) has no expected route")
            XCTAssertEqual(planned?.address, expected[section], "\(section)")
            // Only the two lists that depend on release dates name the region.
            let regional = section == .movieNowPlaying || section == .movieUpcoming
            XCTAssertEqual(planned?.query, regional ? ["region": "US", "api_key": "tmdbk3y"] : ["api_key": "tmdbk3y"], "\(section)")
        }
    }

    func testARegionThatIsNotTwoLettersIsLeftOut() {
        let upcoming = worker("tmdb/list/upcoming", ["type": "movie"])
        XCTAssertEqual(direct(upcoming, region: "gb")?.query["region"], "GB")
        for region in [nil, "", "USA", "U", "U&", "419"] as [String?] {
            XCTAssertEqual(direct(upcoming, region: region)?.query, ["api_key": "tmdbk3y"], region ?? "nil")
        }
    }

    func testAReadAccessTokenRidesTheHeaderAndAKeyTheQuery() throws {
        let token = "eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiJ4In0.c2ln"
        let withToken = try XCTUnwrap(DirectMetadata.plan(for: worker("tmdb/person/7101"), keys: .init(tmdb: token), region: nil)).request
        XCTAssertEqual(withToken.url?.absoluteString, "https://api.themoviedb.org/3/person/7101")
        XCTAssertEqual(withToken.value(forHTTPHeaderField: "Authorization"), "Bearer \(token)")

        let withKey = try XCTUnwrap(DirectMetadata.plan(for: worker("tmdb/person/7101"), keys: keys, region: nil)).request
        XCTAssertEqual(withKey.url?.absoluteString, "https://api.themoviedb.org/3/person/7101?api_key=tmdbk3y")
        XCTAssertNil(withKey.value(forHTTPHeaderField: "Authorization"))
    }

    func testScoresAskMDBListByTMDBID() {
        for kind in ["movie", "show"] {
            let planned = direct(ratings("\(kind)/7001"))
            XCTAssertEqual(planned?.address, "api.mdblist.com/tmdb/\(kind)/7001/")
            XCTAssertEqual(planned?.query, ["apikey": "mdbk3y"])
            XCTAssertEqual(DirectMetadata.plan(for: ratings("\(kind)/7001"), keys: keys, region: nil)?.reshape, .ratings)
        }
    }

    func testARouteWithoutItsOwnKeyIsNotAnswered() {
        XCTAssertNil(direct(worker("tmdb/person/7101"), keys: .init(mdblist: "mdbk3y")))
        XCTAssertNil(direct(ratings("movie/7001"), keys: .init(tmdb: "tmdbk3y")))
        XCTAssertNil(direct(worker("tmdb/person/7101"), keys: .init()))
    }

    func testOtherRoutesAndOddAddressesAreNotAnswered() {
        let insights = InsightsConfig.apiBaseURL
        let unanswered: [URL] = [
            // Upstream's own content and service.
            insights.appendingPathComponent("v1/scores/nfl"),
            insights.appendingPathComponent("insights/movie/7001"),
            insights.appendingPathComponent("insights/request"),
            insights.appendingPathComponent("v1/diagnostics"),
            // A known route on the wrong host.
            worker("v1/ratings/movie/7001"),
            worker("tmdb/person/7101", base: insights),
            worker("tmdb/person/7101", base: URL(string: "https://example.invalid")!),
            // Pieces that are not what the route takes.
            ratings("episode/7001"),
            ratings("movie/abc"),
            ratings("movie/7001/extra"),
            worker("tmdb/details/7001"),
            worker("tmdb/details/7001", ["type": "person"]),
            worker("tmdb/details/abc", ["type": "movie"]),
            worker("tmdb/details/7001/extra", ["type": "movie"]),
            worker("tmdb/details/99999999999999999999", ["type": "movie"]),
            worker("tmdb/credits/7001"),
            worker("tmdb/keywords/7001", ["type": "movie"]),
            worker("tmdb/list/latest", ["type": "movie"]),
            worker("tmdb/list/popular"),
            worker("tmdb/episode_credits/7002", ["season": "1"]),
            worker("tmdb/episode_credits/7002", ["episode": "2"]),
            worker("tmdb/episode_credits/7002", ["season": "1", "episode": "x"]),
            worker("tmdb/episode_credits/7002", ["season": "x", "episode": "2"]),
            worker("tmdb/find/tt9000002", ["source": "facebook_id"]),
            worker("tmdb/find/tt09..03747", ["source": "imdb_id"]),
            worker("tmdb/find/tt9000002"),
            worker("tmdb/discover/person", ["type": "movie"]),
        ]
        for url in unanswered {
            XCTAssertNil(direct(url), url.absoluteString)
        }
    }

    // MARK: Reshaping

    func testDetailsComeBackInTheWorkersFlatForm() throws {
        let movie = Data(#"""
        {"id": 7001, "title": "Example Film", "genres": [{"id": 28, "name": "Action"}], "vote_average": 8.2, "vote_count": 26000,
         "runtime": 136,
         "credits": {"cast": [{"id": 7101, "name": "Avery Example", "character": "The Lead", "profile_path": "/k.jpg"}],
                     "crew": [{"id": 7102, "name": "Jordan Example", "job": "Director", "department": "Directing"}]},
         "keywords": {"keywords": [{"id": 83, "name": "saving the world"}]}}
        """#.utf8)
        let show = Data(#"""
        {"id": 7002, "name": "Example Series", "genres": [{"id": 18, "name": "Drama"}], "vote_average": 8.9, "vote_count": 15000,
         "credits": {"cast": [{"id": 7103, "name": "Casey Example", "character": "The Teacher"}],
                     "crew": [{"id": 7104, "name": "Morgan Example", "job": "Executive Producer"}]},
         "keywords": {"results": [{"id": 2231, "name": "drug dealer"}]}}
        """#.utf8)
        for (body, actor, keyword) in [(movie, "Avery Example", "saving the world"), (show, "Casey Example", "drug dealer")] {
            let flat = try XCTUnwrap(JSONSerialization.jsonObject(with: DirectMetadata.reshaped(body, .details)) as? [String: Any])
            XCTAssertNil(flat["credits"])
            XCTAssertEqual((flat["cast"] as? [[String: Any]])?.first?["name"] as? String, actor)
            XCTAssertEqual((flat["crew"] as? [[String: Any]])?.count, 1)
            XCTAssertEqual((flat["keywords"] as? [[String: Any]])?.first?["name"] as? String, keyword)
            XCTAssertNotNil(flat["vote_average"])
        }
        // The Discover page's own decoder reads the cast from the top level, where TMDB does not put it.
        XCTAssertEqual(try JSONDecoder().decode(TMDBItemDetail.self, from: DirectMetadata.reshaped(movie, .details)).cast.first?.name,
                       "Avery Example")
        XCTAssertTrue(try JSONDecoder().decode(TMDBItemDetail.self, from: movie).cast.isEmpty)
    }

    func testDetailsWithoutTheExtrasAndUnreadableAnswersPassThrough() throws {
        // Asked without keywords: none are invented, so the features cache is not fed an empty list.
        let plain = Data(#"{"id": 7001, "title": "Example Film", "credits": {"cast": [], "crew": []}}"#.utf8)
        let flat = try XCTUnwrap(JSONSerialization.jsonObject(with: DirectMetadata.reshaped(plain, .details)) as? [String: Any])
        XCTAssertNil(flat["keywords"])
        XCTAssertEqual((flat["cast"] as? [Any])?.count, 0)

        for body in [Data("not json".utf8), Data("[1, 2]".utf8), Data()] {
            XCTAssertEqual(DirectMetadata.reshaped(body, .details), body)
            XCTAssertEqual(DirectMetadata.reshaped(body, .ratings), body)
        }
        XCTAssertEqual(DirectMetadata.reshaped(plain, .none), plain)
    }

    func testScoresComeBackInTheWorkersForm() {
        // As MDBList sends it, the one null value costs the callers' decoder every score.
        XCTAssertEqual(RatingsClient.decode(mdblistTitle), [])

        let scores = RatingsClient.decode(DirectMetadata.reshaped(mdblistTitle, .ratings))
        XCTAssertEqual(scores.map(\.source), [.rottenTomatoes, .rottenTomatoesAudience, .imdb, .metacritic, .letterboxd, .tmdb])
        XCTAssertEqual(scores.first { $0.source == .metacritic }?.badgeText, "73")
        XCTAssertEqual(scores.first { $0.source == .letterboxd }?.badgeText, "4.2")
        XCTAssertEqual(scores.first { $0.source == .rottenTomatoes }?.badgeText, "83%")

        // A title MDBList holds no scores for is an empty list, and a value that is not a number is dropped.
        XCTAssertEqual(RatingsClient.decode(DirectMetadata.reshaped(Data(#"{"id": 1}"#.utf8), .ratings)), [])
        let odd = Data(#"{"ratings": [{"source": "imdb", "value": true}, {"source": "tmdb", "value": "82"}, {"value": 7}, {"source": "metacritic", "value": 61}]}"#.utf8)
        XCTAssertEqual(RatingsClient.decode(DirectMetadata.reshaped(odd, .ratings)).map(\.source), [.metacritic])
    }

    // MARK: Through the app's own clients

    func testWithAKeyStoredTheDiscoverServiceIsAnsweredByTMDB() async {
        DirectMetadata.tmdbKey = " tmdbk3y\n"
        DirectMetadataStub.answers["api.themoviedb.org/3/movie/popular"] =
            (200, Data(#"{"page": 1, "results": [{"id": 7001, "title": "Example Film", "poster_path": "/p.jpg"}], "total_pages": 1}"#.utf8))
        DirectMetadataStub.answers["api.themoviedb.org/3/trending/tv/week"] =
            (200, Data(#"{"page": 1, "results": [{"id": 7002, "name": "Example Series", "media_type": "tv"}]}"#.utf8))
        DirectMetadataStub.answers["api.themoviedb.org/3/tv/7002"] =
            (200, Data(#"{"id": 7002, "name": "Example Series", "genres": [], "credits": {"cast": [{"id": 7103, "name": "Casey Example"}], "crew": []}}"#.utf8))

        let service = TMDBDiscoverService(session: CallerSessionProbe.session())
        let popular = await service.fetchSection(.moviePopular)
        XCTAssertEqual(popular.map(\.title), ["Example Film"])
        let trending = await service.fetchSection(.tvTrending)
        XCTAssertEqual(trending.map(\.title), ["Example Series"])
        XCTAssertEqual(trending.first?.mediaType, .tv)
        let detail = await service.fetchDetail(tmdbId: 7002, type: .tv)
        XCTAssertEqual(detail?.cast.first?.name, "Casey Example")

        // Three requests, all to TMDB with the stored key as cleaned; none on the service's own
        // session, which is the one that would have gone to upstream's Worker.
        XCTAssertEqual(DirectMetadataStub.asked.count, 3)
        XCTAssertEqual(Set(DirectMetadataStub.asked.compactMap { $0.url?.host }), ["api.themoviedb.org"])
        XCTAssertTrue(DirectMetadataStub.asked.allSatisfy { $0.url?.query?.contains("api_key=tmdbk3y") == true })
        XCTAssertEqual(CallerSessionProbe.reached, 0)
    }

    func testWithAKeyStoredLogosAndShowIDsAreAnsweredByTMDB() async {
        DirectMetadata.tmdbKey = "tmdbk3y"
        DirectMetadataStub.answers["api.themoviedb.org/3/movie/7001/images"] =
            (200, Data(#"{"id": 7001, "backdrops": [], "posters": [], "logos": [{"file_path": "/fr.png", "iso_639_1": "fr", "vote_average": 9.0}, {"file_path": "/logo.png", "iso_639_1": "en", "vote_average": 5.3}]}"#.utf8))
        DirectMetadataStub.answers["api.themoviedb.org/3/find/81189"] =
            (200, Data(#"{"movie_results": [], "person_results": [], "tv_results": [{"id": 7002, "name": "Example Series"}], "tv_episode_results": [], "tv_season_results": []}"#.utf8))

        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("DirectMetadataTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let logo = await TMDBLogoCache(session: CallerSessionProbe.session(), directory: directory).logoURL(tmdbId: 7001, type: .movie)
        XCTAssertEqual(logo?.absoluteString, "https://image.tmdb.org/t/p/w500/logo.png")

        let show = await InsightsShowIDResolver(session: CallerSessionProbe.session())
            .resolve(ShowExternalIDs(tmdb: nil, tvdb: 81189, imdb: nil))
        XCTAssertEqual(show, 7002)
        XCTAssertEqual(DirectMetadataStub.asked.last?.url?.query, "external_source=tvdb_id&api_key=tmdbk3y")
        XCTAssertEqual(CallerSessionProbe.reached, 0)
    }

    func testWithAKeyStoredScoresAreAnsweredByMDBList() async throws {
        DirectMetadata.mdblistKey = "mdbk3y"
        DirectMetadataStub.answers["api.mdblist.com/tmdb/movie/7001/"] = (200, mdblistTitle)

        let url = ratings("movie/7001")
        let (data, response) = try await CallerSessionProbe.session().attestedData(for: URLRequest(url: url))
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        // The caller is handed the address it asked for; the keyed one stays inside.
        XCTAssertEqual(response.url, url)
        XCTAssertEqual(RatingsClient.decode(data).map(\.source),
                       [.rottenTomatoes, .rottenTomatoesAudience, .imdb, .metacritic, .letterboxd, .tmdb])
        XCTAssertEqual(DirectMetadataStub.asked.map { $0.url?.absoluteString }, ["https://api.mdblist.com/tmdb/movie/7001/?apikey=mdbk3y"])
        XCTAssertEqual(CallerSessionProbe.reached, 0)
    }

    // MARK: What stays refused

    func testWithoutKeysNothingLeavesForAWorkerAddress() async {
        await refused(worker("tmdb/person/7101"))
        await refused(worker("tmdb/list/popular", ["type": "movie"]))
        await refused(ratings("movie/7001"))
        XCTAssertTrue(DirectMetadataStub.asked.isEmpty)
        XCTAssertEqual(CallerSessionProbe.reached, 0)
    }

    func testWithBothKeysUpstreamsOwnContentIsStillRefused() async {
        DirectMetadata.tmdbKey = "tmdbk3y"
        DirectMetadata.mdblistKey = "mdbk3y"
        let insights = InsightsConfig.apiBaseURL
        await refused(insights.appendingPathComponent("v1/scores/nfl"))
        await refused(insights.appendingPathComponent("insights/movie/7001"))
        await refused(insights.appendingPathComponent("insights/request"), method: "POST")
        await refused(insights.appendingPathComponent("v1/diagnostics"), method: "POST")
        // A route answered for a read is not answered for a write.
        await refused(ratings("movie/7001"), method: "POST")
        await refused(worker("tmdb/person/7101"), method: "POST")
        XCTAssertTrue(DirectMetadataStub.asked.isEmpty)
        XCTAssertEqual(CallerSessionProbe.reached, 0)
    }

    // MARK: Where a keyed request may go

    func testOnlyTheTwoServicesAreEverSentTo() async {
        let elsewhere = [
            worker("tmdb/person/7101"),                                            // upstream's TMDB Worker
            ratings("movie/7001"),                                                  // upstream's insights Worker
            URL(string: "https://example.invalid/3/person/7101?api_key=tmdbk3y")!,
            URL(string: "http://api.themoviedb.org/3/person/7101?api_key=tmdbk3y")!,   // the right host, not HTTPS
            URL(string: "https://api.themoviedb.org.example.invalid/3/person/7101")!,
        ]
        for url in elsewhere {
            do {
                _ = try await DirectMetadata.perform(.init(request: URLRequest(url: url), reshape: .none),
                                                     as: worker("tmdb/person/7101"), session: DirectMetadata.session)
                XCTFail("sent to \(url.absoluteString)")
            } catch {
                XCTAssertEqual((error as? URLError)?.code, .badURL, url.absoluteString)
            }
        }
        XCTAssertTrue(DirectMetadataStub.asked.isEmpty)
    }

    func testARedirectStaysOnItsHostOverHTTPS() {
        let asked = URL(string: "https://api.mdblist.com/tmdb/movie/7001?apikey=mdbk3y")
        XCTAssertTrue(DirectMetadata.staysOnHost(from: asked, to: URL(string: "https://api.mdblist.com/tmdb/movie/7001/?apikey=mdbk3y")))
        XCTAssertTrue(DirectMetadata.staysOnHost(from: asked, to: URL(string: "HTTPS://API.MDBLIST.COM/elsewhere")))
        XCTAssertTrue(DirectMetadata.staysOnHost(from: asked, to: URL(string: "https://api.mdblist.com:443/tmdb/movie/7001/")))
        let away = [
            "http://api.mdblist.com/tmdb/movie/7001/?apikey=mdbk3y",
            "https://api.mdblist.com:8443/tmdb/movie/7001/?apikey=mdbk3y",
            "https://someone@api.mdblist.com/tmdb/movie/7001/",
            "https://:secret@api.mdblist.com/tmdb/movie/7001/",
            "https://api.mdblist.com@example.invalid/tmdb/movie/7001/",
            "https://mdblist.com/tmdb/movie/7001/?apikey=mdbk3y",
            "https://api.mdblist.com.example.invalid/tmdb/movie/7001/",
            "https://api.themoviedb.org/3/movie/7001",
            TMDBConfig.proxyBaseURL.absoluteString,
            InsightsConfig.apiBaseURL.absoluteString,
            "/tmdb/movie/7001/",
        ]
        for target in away {
            XCTAssertFalse(DirectMetadata.staysOnHost(from: asked, to: URL(string: target)), target)
        }
        XCTAssertFalse(DirectMetadata.staysOnHost(from: asked, to: nil))
        XCTAssertFalse(DirectMetadata.staysOnHost(from: nil, to: asked))
    }

    func testARedirectAwayIsNotFollowed() async throws {
        // The control: on a session without the rule, the stand-in's redirect is followed to the new host.
        let unruled = URLSessionConfiguration.ephemeral
        unruled.protocolClasses = [DirectMetadataStub.self]
        let plain = URLSession(configuration: unruled)
        defer { plain.invalidateAndCancel() }
        DirectMetadataStub.redirects["api.themoviedb.org/3/person/7101"] = URL(string: "https://example.invalid/3/person/7101")!
        DirectMetadataStub.answers["example.invalid/3/person/7101"] = (200, Data(#"{"biography": "followed"}"#.utf8))
        let (_, followed) = try await plain.data(from: URL(string: "https://api.themoviedb.org/3/person/7101")!)
        XCTAssertEqual((followed as? HTTPURLResponse)?.statusCode, 200)
        XCTAssertEqual(DirectMetadataStub.asked.compactMap { $0.url.map(DirectMetadataStub.address) },
                       ["api.themoviedb.org/3/person/7101", "example.invalid/3/person/7101"])

        let targets = [
            worker("tmdb/person/7101", ["api_key": "tmdbk3y"]),                        // upstream's TMDB Worker
            ratings("movie/7001"),                                                      // upstream's insights Worker
            URL(string: "https://example.invalid/3/person/7101?api_key=tmdbk3y")!,
            URL(string: "http://api.themoviedb.org/3/person/7101?api_key=tmdbk3y")!,
        ]
        for target in targets {
            DirectMetadataStub.reset()
            DirectMetadataStub.redirects["api.themoviedb.org/3/person/7101"] = target
            // Were it followed, the stub would answer the new address like any other.
            DirectMetadataStub.answers[DirectMetadataStub.address(target)] = (200, Data(#"{"biography": "followed"}"#.utf8))
            let end = await ending(of: worker("tmdb/person/7101"))
            XCTAssertEqual(DirectMetadataStub.asked.compactMap { $0.url.map(DirectMetadataStub.address) },
                           ["api.themoviedb.org/3/person/7101"], target.absoluteString)
            // The service's own redirect answer comes back as the failure it is.
            XCTAssertEqual(end, "HTTP 302, 0 bytes", target.absoluteString)
        }
    }

    func testARedirectOnTheSameHostIsFollowed() async throws {
        DirectMetadataStub.redirects["api.themoviedb.org/3/person/7101"] =
            URL(string: "https://api.themoviedb.org/3/person/7105?api_key=tmdbk3y")!
        DirectMetadataStub.answers["api.themoviedb.org/3/person/7105"] = (200, Data(#"{"biography": "moved"}"#.utf8))
        let (data, response) = try await answer(worker("tmdb/person/7101"))
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        XCTAssertEqual(try JSONDecoder().decode(TMDBPersonDetails.self, from: data).biography, "moved")
        XCTAssertEqual(DirectMetadataStub.asked.compactMap { $0.url.map(DirectMetadataStub.address) },
                       ["api.themoviedb.org/3/person/7101", "api.themoviedb.org/3/person/7105"])
    }

    func testKeyedRequestsLeaveNothingOnDisk() async throws {
        // No URL cache, and the cookie and credential stores are the session's own in-memory ones,
        // not the shared ones that are kept on disk. The stub's answers are cacheable.
        let made = DirectMetadata.makeSession()
        defer { made.invalidateAndCancel() }
        for configuration in [made.configuration, sessionBefore.configuration, DirectMetadata.session.configuration] {
            XCTAssertNil(configuration.urlCache)
            XCTAssertFalse(configuration.httpCookieStorage === HTTPCookieStorage.shared)
            XCTAssertFalse(configuration.urlCredentialStorage === URLCredentialStorage.shared)
        }

        DirectMetadataStub.answers["api.themoviedb.org/3/person/7101"] = (200, Data(#"{"biography": "x"}"#.utf8))
        let url = worker("tmdb/person/7101")
        _ = try await answer(url)
        let keyed = try XCTUnwrap(DirectMetadata.plan(for: url, keys: keys, region: nil)).request
        XCTAssertNil(URLCache.shared.cachedResponse(for: keyed))
    }

    // MARK: Failures

    func testAFailureOfAnyKindCarriesNoKeyedAddress() async {
        let keyed = URL(string: "https://api.themoviedb.org/3/person/7101?api_key=tmdbk3y")!
        let failures: [(Error, URLError.Code)] = [
            (URLError(.timedOut, userInfo: [NSURLErrorFailingURLErrorKey: keyed]), .timedOut),
            (NSError(domain: "DirectMetadataTests", code: 7, userInfo: ["address": keyed, NSLocalizedDescriptionKey: keyed.absoluteString]), .unknown),
        ]
        for (failure, expected) in failures {
            DirectMetadataStub.reset()
            DirectMetadataStub.failure = failure
            do {
                _ = try await answer(worker("tmdb/person/7101"))
                XCTFail("a failed request returned an answer")
            } catch {
                XCTAssertEqual((error as? URLError)?.code, expected)
                XCTAssertFalse("\(error) \((error as NSError).userInfo) \(error.localizedDescription)".contains("tmdbk3y"))
            }
            XCTAssertEqual(DirectMetadataStub.asked.count, 1)
        }
    }

    func testARefusalByTheServicePassesItsStatusAndNoBody() async throws {
        DirectMetadataStub.answers["api.themoviedb.org/3/person/7101"] =
            (401, Data(#"{"status_code": 7, "status_message": "Invalid API key: You must be granted a valid key.", "success": false}"#.utf8))
        let url = worker("tmdb/person/7101")
        let (data, response) = try await answer(url)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 401)
        XCTAssertEqual(response.url, url)
        XCTAssertEqual(data, Data())
    }

    // MARK: Settings

    func testCheckingKeysSaysWhatEachServiceAnswered() async {
        DirectMetadataStub.answers["api.themoviedb.org/3/authentication"] = (200, Data(#"{"success": true}"#.utf8))
        DirectMetadataStub.answers["api.mdblist.com/user"] = (401, Data())
        var result = await DirectMetadata.check(keys: keys, session: DirectMetadata.session)
        XCTAssertEqual(result.tmdb, .accepted)
        XCTAssertEqual(result.mdblist, .refused(401))
        XCTAssertEqual(Set(DirectMetadataStub.asked.compactMap { $0.url?.absoluteString }),
                       ["https://api.themoviedb.org/3/authentication?api_key=tmdbk3y", "https://api.mdblist.com/user?apikey=mdbk3y"])

        // Busy or broken is not a verdict on the key.
        DirectMetadataStub.answers["api.themoviedb.org/3/authentication"] = (429, Data())
        DirectMetadataStub.answers["api.mdblist.com/user"] = (503, Data())
        result = await DirectMetadata.check(keys: keys, session: DirectMetadata.session)
        XCTAssertEqual(result.tmdb, .failed(429))
        XCTAssertEqual(result.mdblist, .failed(503))
        DirectMetadataStub.answers["api.themoviedb.org/3/authentication"] = (403, Data())
        result = await DirectMetadata.check(keys: .init(tmdb: "tmdbk3y"), session: DirectMetadata.session)
        XCTAssertEqual(result.tmdb, .refused(403))
        XCTAssertEqual(result.mdblist, .notSet)

        DirectMetadataStub.reset()
        result = await DirectMetadata.check(keys: .init(), session: DirectMetadata.session)
        XCTAssertEqual(result.tmdb, .notSet)
        XCTAssertEqual(result.mdblist, .notSet)
        XCTAssertTrue(DirectMetadataStub.asked.isEmpty)

        DirectMetadataStub.failure = URLError(.notConnectedToInternet)
        result = await DirectMetadata.check(keys: keys, session: DirectMetadata.session)
        XCTAssertEqual(result.tmdb, .noAnswer)
        XCTAssertEqual(result.mdblist, .noAnswer)
    }

    func testTheSettingsRowChecksTheStoredKeysOnTheKeyedSession() async {
        DirectMetadata.tmdbKey = "tmdbk3y"
        DirectMetadataStub.answers["api.themoviedb.org/3/authentication"] = (200, Data(#"{"success": true}"#.utf8))
        let result = await DirectMetadata.checkStoredKeys()
        XCTAssertEqual(result.tmdb, .accepted)
        XCTAssertEqual(result.mdblist, .notSet)
        XCTAssertEqual(DirectMetadataStub.asked.map { $0.url?.absoluteString }, ["https://api.themoviedb.org/3/authentication?api_key=tmdbk3y"])
    }

    func testAStoredKeyLosesItsWhitespaceAndAnEmptyOneIsRemoved() {
        DirectMetadata.tmdbKey = "  tmd\nbk 3y \n"
        DirectMetadata.mdblistKey = "mdbk3y"
        XCTAssertEqual(DirectMetadata.storedKeys, keys)
        XCTAssertEqual(KeychainHelper.get("directMetadataTMDBKey"), "tmdbk3y")
        DirectMetadata.tmdbKey = " \n  "
        DirectMetadata.mdblistKey = ""
        XCTAssertEqual(DirectMetadata.storedKeys, DirectMetadata.Keys())
        // Cleared means gone from the Keychain, not kept as an empty item.
        XCTAssertNil(KeychainHelper.get("directMetadataTMDBKey"))
        XCTAssertNil(KeychainHelper.get("directMetadataMDBListKey"))
    }
}
