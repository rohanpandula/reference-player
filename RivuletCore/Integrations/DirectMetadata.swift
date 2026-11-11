// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition. A replacement for the metadata routes of upstream's two Workers: the
// same questions, asked of TMDB and MDBList directly with the user's own keys.

import Foundation

/// Upstream's app asks its own Workers (a TMDB proxy, and an insights API with MDBList behind it)
/// and signs every request with App Attest. This build is not that app, so `attestedData(for:)`
/// refuses them (`PrivateBuildPolicy`). Once the user has put their own TMDB and MDBList keys in
/// Settings, the routes below are answered by those two services instead. Nothing here contacts
/// upstream's hosts, and trivia, sports scores and diagnostics stay refused: those are upstream's
/// own content and service.
///
/// Each Worker route becomes the documented TMDB v3 or MDBList call, and the answer is put back
/// in the form the Worker's callers decode. A route this file does not know, or one whose key is
/// not set, is left to the refusal.
nonisolated enum DirectMetadata {
    /// The two services, and the only hosts a request of this file may go to.
    private enum Host {
        static let tmdb = "api.themoviedb.org"
        static let mdblist = "api.mdblist.com"
        static let all: Set<String> = [tmdb, mdblist]
    }

    struct Keys: Equatable, Sendable {
        /// A TMDB v3 API key, or its API Read Access Token (the long one with dots).
        var tmdb = ""
        var mdblist = ""
    }

    /// What a service's answer needs before the Worker's callers can decode it.
    enum Reshape: Equatable, Sendable {
        case none
        /// TMDB nests `credits` and `keywords`; the Worker's details route sends cast, crew and
        /// keywords at the top level.
        case details
        /// MDBList sends a whole title; the Worker sends `{"ratings": [{"source", "value"}]}`.
        case ratings
    }

    struct Plan: Equatable, Sendable {
        var request: URLRequest
        var reshape: Reshape
    }

    enum KeyCheck: Equatable, Sendable {
        case notSet
        case accepted
        /// The service turned the key down (HTTP 401 or 403).
        case refused(Int)
        /// The service answered something else, such as a rate limit or an outage: no verdict on the key.
        case failed(Int)
        case noAnswer
    }

    private enum Storage {
        static let tmdb = "directMetadataTMDBKey"
        static let mdblist = "directMetadataMDBListKey"
    }

    // MARK: Keys (Keychain)

    @MainActor static var tmdbKey: String {
        get { KeychainHelper.get(Storage.tmdb) ?? "" }
        set { store(newValue, as: Storage.tmdb) }
    }

    @MainActor static var mdblistKey: String {
        get { KeychainHelper.get(Storage.mdblist) ?? "" }
        set { store(newValue, as: Storage.mdblist) }
    }

    @MainActor static var storedKeys: Keys { Keys(tmdb: tmdbKey, mdblist: mdblistKey) }

    /// Neither kind of key holds a space, and a pasted one can arrive with spaces or line breaks.
    @MainActor private static func store(_ value: String, as key: String) {
        let cleaned = value.filter { !$0.isWhitespace }
        if cleaned.isEmpty {
            KeychainHelper.delete(key)
        } else {
            KeychainHelper.set(cleaned, forKey: key)
        }
    }

    // MARK: The keyed requests' own session

    /// Every keyed request goes out on this session, never on a caller's: it writes nothing to
    /// disk (no URL cache, and an ephemeral session keeps cookies in memory only), so a keyed
    /// address is not left in a cache file, and it follows a redirect only where `staysOnHost`
    /// allows. Tests replace it.
    @MainActor static var session = makeSession()

    static func makeSession(protocolClasses: [AnyClass]? = nil) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 30
        if let protocolClasses {
            configuration.protocolClasses = protocolClasses
        }
        return URLSession(configuration: configuration, delegate: Redirects(), delegateQueue: nil)
    }

    /// A redirect is followed only to where the request was made: HTTPS, the same host and port,
    /// and no name or password in the address. A service that pointed anywhere else, upstream's
    /// Workers included, would otherwise be followed there, and an address it built could carry
    /// the key along.
    static func staysOnHost(from original: URL?, to target: URL?) -> Bool {
        // An address with a password always has a name as well, if only an empty one.
        guard let original, let host = original.host?.lowercased(), let target,
              target.scheme?.lowercased() == "https", target.user == nil,
              (target.port ?? 443) == (original.port ?? 443) else { return false }
        return target.host?.lowercased() == host
    }

    nonisolated private final class Redirects: NSObject, URLSessionTaskDelegate {
        func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                        newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
            completionHandler(DirectMetadata.staysOnHost(from: task.originalRequest?.url, to: request.url) ? request : nil)
        }
    }

    /// The one place a request leaves this file: HTTPS to one of the two services, or not at all.
    /// A failure passes on its reason alone, because the failing address can carry the key.
    private static func send(_ request: URLRequest, session: URLSession) async throws -> (body: Data, status: Int) {
        guard let url = request.url, url.scheme == "https", let host = url.host, Host.all.contains(host) else {
            throw URLError(.badURL)
        }
        do {
            let (data, response) = try await session.data(for: request)
            return (data, (response as? HTTPURLResponse)?.statusCode ?? 0)
        } catch let error as URLError {
            throw URLError(error.code)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw URLError(.unknown)
        }
    }

    // MARK: Answering a Worker request

    /// The Worker request answered by TMDB or MDBList. Nil when the request is not for one of the
    /// two Workers, the route is not one answered here, or its key is not set: the caller's
    /// refusal then stands as it was.
    static func answer(_ request: URLRequest) async throws -> (Data, URLResponse)? {
        // Every route answered here is a read.
        guard (request.httpMethod ?? "GET") == "GET", let url = request.url,
              let (plan, session) = await planned(for: url) else { return nil }
        return try await perform(plan, as: url, session: session)
    }

    /// The route for the stored keys, with the session to send it on.
    @MainActor private static func planned(for url: URL) -> (Plan, URLSession)? {
        plan(for: url, keys: storedKeys, region: Locale.current.region?.identifier).map { ($0, session) }
    }

    /// Sends a planned request. The caller is handed the address it asked for, never the keyed
    /// one, and no body from a service that did not succeed. Off the caller's actor: several
    /// callers are on the main one, and the reshaping parses and rewrites the whole answer.
    @concurrent static func perform(_ plan: Plan, as url: URL, session: URLSession) async throws -> (Data, URLResponse) {
        let (body, status) = try await send(plan.request, session: session)
        guard let response = HTTPURLResponse(url: url, statusCode: status, httpVersion: nil, headerFields: nil) else {
            throw URLError(.badServerResponse)
        }
        let succeeded = (200...299).contains(status)
        return (succeeded ? reshaped(body, plan.reshape) : Data(), response)
    }

    // MARK: Routes

    /// The direct request for a Worker address, or nil. Every piece taken from the address is
    /// checked before it is placed in the new one. On the main actor because the two Worker
    /// addresses it compares against are the app's own configuration.
    @MainActor static func plan(for url: URL, keys: Keys, region: String?) -> Plan? {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: true),
              let host = components.host?.lowercased() else { return nil }
        let parts = components.path.split(separator: "/").map(String.init)
        let query = Dictionary((components.queryItems ?? []).map { ($0.name, $0.value ?? "") },
                               uniquingKeysWith: { first, _ in first })

        if host == TMDBConfig.proxyBaseURL.host, parts.count == 3, parts[0] == "tmdb", !keys.tmdb.isEmpty {
            guard let route = tmdbRoute(kind: parts[1], id: parts[2], query: query, region: region),
                  let request = tmdbRequest(path: route.path, items: route.items, key: keys.tmdb) else { return nil }
            return Plan(request: request, reshape: route.reshape)
        }
        if host == InsightsConfig.apiBaseURL.host, parts.count == 4, parts[0] == "v1", parts[1] == "ratings",
           ["movie", "show"].contains(parts[2]), isDigits(parts[3]), !keys.mdblist.isEmpty {
            // MDBList names the two kinds as the Worker does, and its route ends in a slash.
            // ponytail: one lookup per title per launch (RatingsClient keeps answers in memory only).
            // Keep them on disk for a few days if the free plan's 1,000 a day is ever reached.
            guard let request = mdblistRequest(path: "/tmdb/\(parts[2])/\(parts[3])/", key: keys.mdblist) else { return nil }
            return Plan(request: request, reshape: .ratings)
        }
        return nil
    }

    private static let listSections: Set<String> = ["popular", "now_playing", "upcoming", "top_rated", "airing_today", "on_the_air"]

    private static func tmdbRoute(kind: String, id: String, query: [String: String], region: String?)
        -> (path: String, items: [URLQueryItem], reshape: Reshape)? {
        let type = query["type"].flatMap { ["movie", "tv"].contains($0) ? $0 : nil }
        switch kind {
        case "details":
            guard let type, isDigits(id) else { return nil }
            let extras = query["append"] == "keywords" ? "credits,keywords" : "credits"
            return ("\(type)/\(id)", [URLQueryItem(name: "append_to_response", value: extras)], .details)
        case "credits", "images":
            guard let type, isDigits(id) else { return nil }
            return ("\(type)/\(id)/\(kind)", [], .none)
        case "person":
            guard isDigits(id) else { return nil }
            return ("person/\(id)", [], .none)
        case "episode_credits":
            guard isDigits(id), let season = query["season"], isDigits(season),
                  let episode = query["episode"], isDigits(episode) else { return nil }
            return ("tv/\(id)/season/\(season)/episode/\(episode)/credits", [], .none)
        case "find":
            guard isLettersAndDigits(id), let source = query["source"], ["imdb_id", "tvdb_id"].contains(source) else { return nil }
            return ("find/\(id)", [URLQueryItem(name: "external_source", value: source)], .none)
        case "list":
            guard let type else { return nil }
            // TMDB offers a day and a week; the week is the steadier list for a hero that pages.
            if id == "trending" { return ("trending/\(type)/week", [], .none) }
            guard listSections.contains(id) else { return nil }
            // Without a region these two count a release anywhere in the world, so a film that
            // opened here months ago returns as "upcoming" when it reaches another country.
            var items: [URLQueryItem] = []
            if ["now_playing", "upcoming"].contains(id), let region, region.count == 2, isLettersAndDigits(region) {
                items.append(URLQueryItem(name: "region", value: region.uppercased()))
            }
            return ("\(type)/\(id)", items, .none)
        case "discover":
            guard ["movie", "tv"].contains(id) else { return nil }
            let items = ["with_genres", "with_keywords", "sort_by"].compactMap { name in
                query[name].map { URLQueryItem(name: name, value: $0) }
            }
            return ("discover/\(id)", items, .none)
        default:
            return nil
        }
    }

    /// A v3 API key rides the query string, as TMDB documents it; an API Read Access Token (a
    /// JWT, so it has dots) rides the Authorization header. Either may be what the user pasted.
    private static func tmdbRequest(path: String, items: [URLQueryItem], key: String) -> URLRequest? {
        let isToken = key.contains(".")
        var components = URLComponents()
        components.scheme = "https"
        components.host = Host.tmdb
        components.path = "/3/" + path
        let all = isToken ? items : items + [URLQueryItem(name: "api_key", value: key)]
        components.queryItems = all.isEmpty ? nil : all
        guard let url = components.url else { return nil }
        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if isToken { request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization") }
        return request
    }

    private static func mdblistRequest(path: String, key: String) -> URLRequest? {
        var components = URLComponents()
        components.scheme = "https"
        components.host = Host.mdblist
        components.path = path
        components.queryItems = [URLQueryItem(name: "apikey", value: key)]
        guard let url = components.url else { return nil }
        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        return request
    }

    private static func isDigits(_ text: String) -> Bool {
        !text.isEmpty && text.count <= 12 && text.allSatisfy { ("0"..."9").contains($0) }
    }

    private static func isLettersAndDigits(_ text: String) -> Bool {
        !text.isEmpty && text.count <= 24 && text.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber) }
    }

    // MARK: Reshaping

    /// The service's answer in the Worker's form. An answer that cannot be read is passed on as
    /// it is, and the caller's decoder turns it into its usual soft failure.
    static func reshaped(_ data: Data, _ reshape: Reshape) -> Data {
        guard reshape != .none, var object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return data }
        switch reshape {
        case .none:
            return data
        case .details:
            if let credits = object.removeValue(forKey: "credits") as? [String: Any] {
                object["cast"] = credits["cast"] ?? []
                object["crew"] = credits["crew"] ?? []
            }
            // A movie's keywords come as `keywords.keywords`, a show's as `keywords.results`.
            if let keywords = object["keywords"] as? [String: Any] {
                object["keywords"] = keywords["keywords"] ?? keywords["results"] ?? []
            }
        case .ratings:
            // A source MDBList has no score for arrives with a null value, which the callers'
            // decoder would take as a reason to drop every score.
            let scores = (object["ratings"] as? [[String: Any]] ?? []).compactMap { entry -> [String: Any]? in
                guard let source = entry["source"] as? String, let value = entry["value"] as? NSNumber,
                      CFGetTypeID(value) != CFBooleanGetTypeID() else { return nil }
                return ["source": source, "value": value]
            }
            object = ["ratings": scores]
        }
        return (try? JSONSerialization.data(withJSONObject: object)) ?? data
    }

    // MARK: Checking the keys (Settings)

    /// One small authenticated request to each service: TMDB's key check and MDBList's account
    /// page. MDBList counts it against the day's 1,000 requests.
    static func check(keys: Keys, session: URLSession) async -> (tmdb: KeyCheck, mdblist: KeyCheck) {
        async let tmdb = verdict(keys.tmdb.isEmpty ? nil : tmdbRequest(path: "authentication", items: [], key: keys.tmdb), session: session)
        async let mdblist = verdict(keys.mdblist.isEmpty ? nil : mdblistRequest(path: "/user", key: keys.mdblist), session: session)
        return await (tmdb, mdblist)
    }

    @MainActor static func checkStoredKeys() async -> (tmdb: KeyCheck, mdblist: KeyCheck) {
        await check(keys: storedKeys, session: session)
    }

    private static func verdict(_ request: URLRequest?, session: URLSession) async -> KeyCheck {
        guard let request else { return .notSet }
        guard let status = try? await send(request, session: session).status else { return .noAnswer }
        switch status {
        case 200...299: return .accepted
        case 401, 403: return .refused(status)
        default: return .failed(status)
        }
    }
}
