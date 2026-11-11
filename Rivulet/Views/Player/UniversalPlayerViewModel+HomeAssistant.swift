// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Reference Player addition: Home Assistant reporting for the player, kept out of upstream's
// UniversalPlayerViewModel.swift. The class file keeps only the hook lines (the state call in
// `updatePlaybackState`, the end calls in `stopPlayback`, `stopProviderReporter` and `deinit`) and
// the `homeAssistantSessionBegun` flag, which an extension cannot store.

import CoreGraphics
import Foundation

extension UniversalPlayerViewModel {

    // MARK: Home Assistant (Reference Player)

    /// `isSwapping` reads the class's private `isSwappingItem` when a snapshot is taken.
    func reportToHomeAssistant(_ state: UniversalPlaybackState, isSwapping: @escaping @MainActor () -> Bool) {
        guard HomeAssistantReporter.isEnabled else { return }
        switch state {
        case .playing, .paused, .buffering:
            if homeAssistantSessionBegun {
                HomeAssistantReporter.shared.refresh()
            } else {
                homeAssistantSessionBegun = true
                HomeAssistantReporter.shared.begin { [weak self] in self?.homeAssistantSnapshot(isSwapping: isSwapping()) }
            }
        case .ended, .failed, .idle:
            endHomeAssistantSession()
        default:
            break
        }
    }

    /// Idempotent: the reporter sends idle once if it sent anything. Called from every way a
    /// play session ends (state, Menu, provider stop, deinit), since Plex sessions never reach
    /// the provider reporter's stop.
    func endHomeAssistantSession() {
        homeAssistantSessionBegun = false
        HomeAssistantReporter.shared.end()
    }

    /// The player's own account of the current item: what the Details pane shows, as data.
    private func homeAssistantSnapshot(isSwapping: Bool) -> HomeAssistantPlaybackReport? {
        // Mid-swap the metadata is already the next item's while the engine still plays the outgoing one.
        guard !isSwapping else { return nil }
        let stateName: String
        switch playbackState {
        case .playing: stateName = "playing"
        case .paused: stateName = "paused"
        case .buffering, .loading: stateName = "buffering"
        default: return nil
        }
        var report = HomeAssistantPlaybackReport(state: stateName)
        report.appVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
        report.deviceModel = DeviceIdentity.modelIdentifier
        report.device = report.deviceModel.map(DeviceIdentity.marketingName)
        report.osVersion = DeviceIdentity.osVersion
        report.title = metadata.title
        report.series = metadata.grandparentTitle
        if let season = metadata.parentIndex, let episode = metadata.index { report.episode = "S\(season) E\(episode)" }
        report.year = metadata.year
        let modes = streamingModeInfo
        report.videoMode = modes.video.rawValue
        report.audioMode = modes.audio.rawValue
        let track = audioTracks.first { $0.id == currentAudioTrackId }
        report.audioCodec = track?.codec ?? metadata.Media?.first?.audioCodec
        report.audioChannels = track?.channels
        report.audioLanguage = track?.language
        let heightSetting = UserDefaults.standard.string(forKey: AetherPlayer.heightSpeakersKey) ?? ""
        report.heightSpeakers = Self.homeAssistantHeightSpeakers(layoutName: AetherPlayer.heightLayoutName(forSetting: heightSetting))
        if videoSize != .zero { report.resolution = "\(Int(videoSize.width))x\(Int(videoSize.height))" }
        guard let player = aetherPlayer else {
            return report
        }
        report.engine = "AetherEngine \(AetherPlayer.engineVersion)"
        report.videoCodec = player.sourceVideoCodecName
        // The Details pane's reading: the engine's videoFormat reads SDR on the dual-layer route
        // until the panel proves HDR, so a Profile 7 source takes its label from the route.
        report.hdrFormat = player.dolbyVisionPlayback.map(Self.homeAssistantHDRFormat) ?? player.presentedVideoFormatName
        if let size = player.sourceVideoDimensions, size != .zero {
            report.resolution = "\(Int(size.width))x\(Int(size.height))"
        }
        report.frameRate = player.sourceVideoFrameRate
        report.dolbyVisionProfile = player.sourceDolbyVisionProfile
        report.dolbyVisionLayer = player.dolbyVisionEnhancementLayer?.rawValue
        report.dolbyVisionRoute = player.dolbyVisionPlayback?.rawValue
        report.dualLayerRouteTaken = player.dolbyVisionDualLayerRouteTaken
        report.videoRoute = player.videoRouteName
        let stats = player.advancedStats()
        report.audioDecoder = stats.audioBridge
        report.audioDelivery = stats.audioDelivery
        report.bitrateMbps = stats.averageBitrateMbps
        report.observedFps = stats.observedFps
        report.droppedFrames = stats.droppedFrameCount
        Self.applyHomeAssistantHeights(player.objectAudioSnapshot, logUnread: player.heightsLogUnread, to: &report)
        report.heightsRoute = player.heightsRoute
        return report
    }

    // MARK: Pure mapping (tested in HomeAssistantSnapshotTests)

    /// The Height Speakers setting as reported: the layout the engine knows, or "Off".
    static func homeAssistantHeightSpeakers(layoutName: String?) -> String {
        layoutName ?? "Off"
    }

    /// The picture as the Details pane names it, from how a Dolby Vision source plays.
    static func homeAssistantHDRFormat(_ playback: DolbyVisionPlayback) -> String {
        switch playback {
        case .dualLayer, .dolbyVision, .convertedTo81: "Dolby Vision"
        case .dolbyVisionUnconfirmed, .convertedTo81Unconfirmed: "Dolby Vision (TV not confirmed)"
        case .hdr10BaseLayer: "HDR10"
        case .sdr: "SDR"
        }
    }

    /// The object renderer's activity, or, when there is none and the bridge's log went unread,
    /// the note that heights are unknown. `logUnread` is read only when there is no activity.
    static func applyHomeAssistantHeights(_ snapshot: ObjectAudioSnapshot?, logUnread: @autoclosure () -> Bool,
                                          to report: inout HomeAssistantPlaybackReport) {
        if let activity = snapshot {
            report.heightLayoutActive = activity.layout
            report.objectsTotal = activity.objects
            report.objectsActive = activity.activeObjects
            report.objectsElevated = activity.elevatedObjects
            report.heightChannels = activity.heightChannels
            report.heightsSource = activity.source.rawValue
            report.heightsWindowSeconds = activity.windowSeconds
        } else if logUnread() {
            report.heightsNote = HomeAssistantPlaybackReport.heightsLogUnreadNote
        }
    }
}
