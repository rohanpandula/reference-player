// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
import XCTest
@testable import Rivulet

/// Even a failing network response is too late: the private policy must reject
/// before enrollment and before URLSession reaches the protocol implementation.
private final class PrivateBuildNoNetworkProtocol: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        XCTFail("A disabled upstream service request reached the network stack")
        client?.urlProtocol(self, didFailWithError: URLError(.unsupportedURL))
    }
    override func stopLoading() {}
}

final class PrivateBuildServicePolicyTests: XCTestCase {
    func testOptionalUpstreamServicesFailLocally() async throws {
        XCTAssertFalse(PrivateBuildPolicy.allowsUpstreamHostedServices)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PrivateBuildNoNetworkProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let url = try XCTUnwrap(URL(string: "https://example.invalid/private-policy-test"))
        do {
            _ = try await session.attestedData(for: URLRequest(url: url))
            XCTFail("The upstream service policy unexpectedly allowed a request")
        } catch let error as URLError {
            XCTAssertEqual(error.code, .userAuthenticationRequired)
        }
    }

    /// Servers are told this fork's name, never upstream's: Plex's X-Plex-Product and the Jellyfin and
    /// Emby client name read `PlexAPI.productName`; a User-Agent's product token cannot hold a space,
    /// so the Live TV and Plex direct-play user agents read `PrivateBuildPolicy.userAgentProduct`.
    func testServersAreToldTheForksName() {
        XCTAssertEqual(PlexAPI.productName, "Reference Player")
        XCTAssertEqual(PrivateBuildPolicy.userAgentProduct, "ReferencePlayer")
        XCTAssertTrue(LiveTVClientIdentity.userAgent.hasPrefix("ReferencePlayer/"), LiveTVClientIdentity.userAgent)
        XCTAssertFalse(LiveTVClientIdentity.userAgent.contains(" "), LiveTVClientIdentity.userAgent)
    }

    /// The space in the name survives every place it is sent: as a header value (X-Plex-Product), as a
    /// query value (percent-encoded by URLComponents), and inside the quoted `Client="…"` of the
    /// Jellyfin and Emby `Authorization: MediaBrowser` header, where only a comma would end the value.
    @MainActor
    func testTheNameWithASpaceIsCarriedIntact() throws {
        let headers = PlexProvider.directPlayHeaders(token: "tok")
        XCTAssertEqual(headers["X-Plex-Product"], "Reference Player")
        let agent = try XCTUnwrap(headers["User-Agent"])
        XCTAssertEqual(agent, "ReferencePlayer/\(PlexAPI.platform)")

        let url = try XCTUnwrap(PlexNetworkManager.shared.buildPlaybackDirectPlayURL(
            serverURL: "http://plex.local:32400", authToken: "tok", partKey: "/library/parts/1/0/file.mkv"))
        XCTAssertTrue(url.absoluteString.contains("X-Plex-Product=Reference%20Player"), url.absoluteString)
        let query = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems)
        XCTAssertEqual(query.first { $0.name == "X-Plex-Product" }?.value, "Reference Player")

        for header in [JellyfinClient.authorizationHeader(token: "tok"), EmbyClient.authorizationHeader(token: "tok")] {
            XCTAssertTrue(header.hasPrefix(#"MediaBrowser Client="Reference Player", "#), header)
            let fields = header.dropFirst("MediaBrowser ".count).components(separatedBy: ", ")
            let client = try XCTUnwrap(fields.first { $0.hasPrefix("Client=") })
            XCTAssertEqual(client, #"Client="Reference Player""#, header)
        }
    }
}
