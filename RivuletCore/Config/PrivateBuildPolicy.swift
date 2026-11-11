// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition. Do not impersonate the upstream app's signing identity.

/// The upstream metadata/trivia/scores Workers require the maintainer's App Attest
/// identity. A fork is not that app. Fail those optional requests locally;
/// Plex/Jellyfin authentication and media delivery do not use the attested wrapper.
/// Revisit only if a builder deliberately configures an authorized replacement
/// backend. Removing attestation or copying a maintainer key is not a solution.
/// The replacement that exists is `DirectMetadata`: the user's own TMDB and MDBList
/// keys answer the metadata routes, and this constant stays false.
// This immutable policy is safe to read from URLSession's non-main contexts.
// Do not inherit an app target's default MainActor isolation for this constant.
nonisolated enum PrivateBuildPolicy {
    static let allowsUpstreamHostedServices = false
    /// The name media servers are told (`PlexAPI.productName`: X-Plex-Product, the Jellyfin and Emby
    /// client name), so a server's dashboard does not credit upstream's app with this one.
    static let productName = "Reference Player"
    /// The product token of a User-Agent (`name/version`). A token cannot hold a space (RFC 9110), so it
    /// is the display name without one; headers that carry a name, not a token, use `productName`.
    static let userAgentProduct = "ReferencePlayer"
}
