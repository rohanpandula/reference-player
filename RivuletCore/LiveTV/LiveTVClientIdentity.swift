// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Copyright (C) 2025-2026 Bain Gurley

//
//  LiveTVClientIdentity.swift
//  Rivulet
//
//  The HTTP identity Rivulet presents when it pulls a Live TV stream.
//

import Foundation

/// How Rivulet identifies itself to an IPTV server on a stream request.
/// Dispatcharr's `stream_ts` names users only from a session cookie, so the token
/// header leaves streams anonymous, keyed on IP plus User-Agent. A stable
/// User-Agent makes Rivulet recognisable there and keeps that key stable (#246).
enum LiveTVClientIdentity {

    /// The User-Agent header value, for example `Rivulet/1.0.3`.
    ///
    /// Read from the bundle rather than hardcoded so it tracks releases without
    /// anyone remembering to update it. Contains no user, device, or token data,
    /// so it is safe to send to any server and safe to log.
    nonisolated static let userAgent: String = {
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
        return "\(PrivateBuildPolicy.userAgentProduct)/\(version)"  // Reference Player: the fork's name as a token, not upstream's.
    }()

    /// Headers attached to every Live TV stream load. Kept as a single value so
    /// the player call sites cannot drift apart from one another.
    nonisolated static let streamHeaders: [String: String] = ["User-Agent": userAgent]

    /// Headers for one channel's stream: the base identity, overridden by any
    /// headers the playlist authored for that channel (`url|User-Agent=...`).
    /// The only place the two are merged, so the call sites cannot drift.
    static func streamHeaders(for channel: UnifiedChannel) -> [String: String] {
        streamHeaders.merging(channel.httpHeaders ?? [:]) { _, custom in custom }
    }

    /// Splits a playlist stream line of the form `url|Header=value&Header=value`
    /// (the Kodi / TiviMate convention) into the URL and its headers.
    ///
    /// Only a literal `|` is a delimiter. An encoded `%7C` is ordinary URL data
    /// (a signed token, an Xtream password) and is left in the URL untouched.
    private nonisolated static let streamSchemes: Set<String> = ["http", "https", "rtsp", "rtsps", "rtp", "udp", "rtmp", "rtmps", "srt"]

    nonisolated static func parseStreamURL(_ rawString: String) -> (url: URL, headers: [String: String])? {
        let trimmed = rawString.trimmingCharacters(in: .whitespacesAndNewlines)
        let parts = trimmed.split(separator: "|", maxSplits: 1, omittingEmptySubsequences: false)
        guard let first = parts.first,
              let url = URL(string: first.trimmingCharacters(in: .whitespaces)),
              // A playlist names network streams only, never `file:` or FFmpeg's `concat:`/`subfile:`.
              let scheme = url.scheme?.lowercased(), streamSchemes.contains(scheme) else {
            return nil
        }

        var headers: [String: String] = [:]
        if parts.count > 1 {
            for pair in parts[1].split(separator: "&") {
                let kv = pair.split(separator: "=", maxSplits: 1).map {
                    $0.trimmingCharacters(in: .whitespaces)
                }
                guard kv.count == 2, !kv[0].isEmpty else { continue }
                let value = kv[1].removingPercentEncoding ?? kv[1]
                guard !value.isEmpty else { continue }
                headers[canonicalHeaderName(kv[0])] = value
            }
        }
        return (url, headers)
    }

    /// Normalises the case of the headers Rivulet itself sends, so a playlist's
    /// `user-agent=` replaces the base `User-Agent` instead of sending both.
    private nonisolated static func canonicalHeaderName(_ name: String) -> String {
        for known in ["User-Agent", "Referer", "Authorization"]
        where name.caseInsensitiveCompare(known) == .orderedSame {
            return known
        }
        return name
    }
}
