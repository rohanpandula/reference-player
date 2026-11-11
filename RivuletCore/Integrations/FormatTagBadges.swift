// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition: the badge names this fork adds, kept out of upstream's MediaSource.swift.
// That file keeps two hook lines (`rangeBadges` and the Atmos badge in `detailBadges`) and its
// extension's `@MainActor`, which these need because they read `FormatIndexStore`.

import Foundation

@MainActor
extension MediaSource {
    /// TrueHD Atmos is the lossless, object-rendered case Height Speakers is for; Dolby Digital Plus
    /// Atmos keeps the plain badge. Which track is Atmos is the server's own word (its stream
    /// profile), not the format index's.
    var atmosDetailBadge: String {
        let track = audioTracks.first(where: { $0.isDefault }) ?? audioTracks.first
        let lossless = track?.codec.lowercased() == "truehd" && FormatIndexStore.showsTags
        return lossless ? "TrueHD Atmos" : "Atmos"
    }

    /// Upstream's range badges (`plain`) with what the format index knows. A Profile 7 file reads
    /// "DV FEL" or "DV MEL" when the index knows its enhancement layer; no server reports that, and
    /// the two play differently here. A dual-track file whose first track is a bare HDR10 base layer,
    /// with the Dolby Vision layer on a video track of its own, keeps the first track's badge, because
    /// the engine loads such a file as HDR10 (see `separateTrackLayerBadge`), and names the layer after
    /// it: "HDR", "DV FEL 2-track". A first track that is itself Profile 7 is Dolby Vision to the
    /// engine and takes the plain path.
    func formatIndexRangeBadges(plain: [String]) -> [String] {
        guard let video = videoTracks.first else { return plain }
        if case .dolbyVision(let profile) = video.videoRange {
            guard profile == 7, let layer = FormatIndexStore.shared.enhancementLayerBadge(forFile: fileName, size: fileSize) else {
                return plain
            }
            let dv = "DV \(layer)"
            return video.hasHDR10Plus ? [dv, "HDR10+"] : [dv]
        }
        guard let layer = FormatIndexStore.shared.separateTrackLayerBadge(forFile: fileName, size: fileSize) else { return plain }
        return plain + [layer]
    }
}
