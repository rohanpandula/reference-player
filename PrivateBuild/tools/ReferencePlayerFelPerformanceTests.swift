#if DualLayerDolbyVision
import CoreVideo
import CryptoKit
import Dovi
import Foundation
import Metal
import Testing
@testable import AetherEngine

/// Injected unchanged into isolated baseline/patched checkouts by fel_performance.py.
/// Production async completion records a cumulative GPU mean until frame 120. With
/// a fresh composer and 70 calls, differences of mean * callCount recover each duration.
@Suite(.offCooperativePoolOneLane)
struct ReferencePlayerFelPerformanceTests {
    static let reportPath = ProcessInfo.processInfo.environment["REFERENCE_FEL_PERFORMANCE_REPORT"]
    static let warmups = 10
    static let samples = 60

    struct Sample: Encodable {
        let index: Int, frame: Int
        let gpuMs: Double, completionMs: Double
        let thermalState: Int
    }

    struct Result: Encodable {
        let width: Int, height: Int
        let profile: String, route: String
        let flags: UInt32
        let lumaNlqScale: Int32
        let cbMmr: Bool, crMmr: Bool
        let inputSHA256: [String]
        let outputSHA256: String
        let calls: Int, inPlaceComposedFrames: Int, inPlaceLostFrames: Int
        let hasOutputPool: Bool
        let samples: [Sample]
    }

    struct Report: Encodable {
        let schemaVersion: Int
        let timestamp: String, device: String, operatingSystem: String
        let configuration: String, gpuTimingMethod: String, scope: String
        let warmups: Int, sampleCount: Int
        let thermalStateBefore: Int, thermalStateAfter: Int
        let lowPowerMode: Bool
        let results: [Result]
    }

    // One producer writes before signalling; only the waiting test reads these values.
    final class Completion: @unchecked Sendable {
        var output: CVPixelBuffer?
        var succeeded = false
        var end: UInt64 = 0
    }

    static func planeData(_ buffer: CVPixelBuffer) -> [Data] {
        (0..<2).map { plane in
            DoviFelInPlaceComposeTests.visibleWords(buffer, plane: plane).withUnsafeBytes { Data($0) }
        }
    }

    static func hash(_ planes: [Data]) -> String {
        var hash = SHA256()
        for plane in planes { hash.update(data: plane) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    static func restore(_ planes: [Data], into buffer: CVPixelBuffer) throws {
        try #require(CVPixelBufferLockBaseAddress(buffer, []) == kCVReturnSuccess)
        defer { CVPixelBufferUnlockBaseAddress(buffer, []) }
        for plane in 0..<2 {
            let destination = try #require(CVPixelBufferGetBaseAddressOfPlane(buffer, plane))
            let rows = CVPixelBufferGetHeightOfPlane(buffer, plane)
            let rowBytes = CVPixelBufferGetWidthOfPlane(buffer, plane) * (plane == 0 ? 2 : 4)
            let stride = CVPixelBufferGetBytesPerRowOfPlane(buffer, plane)
            planes[plane].withUnsafeBytes { source in
                for row in 0..<rows {
                    (destination + row * stride).copyMemory(from: source.baseAddress! + row * rowBytes,
                                                           byteCount: rowBytes)
                }
            }
        }
    }

    static func parameters(_ profile: String) throws -> DoviMetalParams {
        var params: DoviMetalParams
        if profile == "identity" {
            params = DoviGpuEdgeCaseTests.params()
        } else {
            try #require(profile == "fel_fixture")
            params = try DoviComposedFrameRpuTests.felNAL.withUnsafeBufferPointer { bytes in
                let rpu = try #require(DoviRpuGuard.parse(bytes.baseAddress!, bytes.count))
                defer { dovi_rpu_free(rpu) }
                return try #require(DoviFelComposer.parseRpuParameters(rpu: rpu))
            }
            try #require(params.polyCb.usesMmr && params.polyCr.usesMmr, "FEL fixture must exercise chroma MMR")
        }
        try #require(params.flags.isEmpty && params.nlqY.nlqScale != 0, "Need ordinary luma and active residual")
        params.ditherMode = .bayer8x8
        return params
    }

    @Test("compare the complete production FEL composer against the pinned baseline",
          .enabled(if: ReferencePlayerFelPerformanceTests.reportPath != nil, "set REFERENCE_FEL_PERFORMANCE_REPORT"))
    func benchmark() throws {
        #if DEBUG
        Issue.record("Performance comparison requires swift test -c release")
        return
        #else
        let path = try #require(Self.reportPath)
        let device = try #require(MTLCreateSystemDefaultDevice(), "Needs a physical Metal device")
        try #require(Self.warmups + Self.samples < 120, "Cumulative GPU timing must not close its 120-frame window")
        let initialThermal = ProcessInfo.processInfo.thermalState.rawValue
        var results = [Result]()
        for (width, height) in [(1920, 1080), (3840, 2160)] {
            let frames = try (0..<2).map { frame in
                try #require(DoviFelInPlaceComposeTests.makeBaseAndEnhancement(width: width, height: height, seed: frame + 1))
            }
            let originals = frames.map { Self.planeData($0.bl) }
            let inputHashes = frames.enumerated().map { index, frame in
                Self.hash(originals[index] + Self.planeData(frame.el))
            }
            for profile in ["identity", "fel_fixture"] {
                let params = try Self.parameters(profile)
                for route in ["pool_async", "in_place_async"] {
                    let composer = try #require(DoviFelComposer())
                    composer.chromaFormat = .yuv420
                    composer.inPlaceComposeEnabled = true
                    try #require(DoviFelComposer.residualEnabled)
                    if route == "in_place_async" {
                        for frame in frames {
                            try #require(composer.canComposeInPlace(baseBuffer: frame.bl, params: params),
                                         "In-place route unavailable: \(String(describing: composer.inPlaceDecision(baseBuffer: frame.bl, params: params)))")
                        }
                    }
                    var samples = [Sample]()
                    var previousGpuTotal = 0.0
                    var lastOutput: CVPixelBuffer?
                    var calls = 0
                    for sequence in 0..<(Self.warmups + Self.samples) {
                        try autoreleasepool {
                        let frame = sequence % frames.count
                        // Restore both routes to the same source/cache state outside the timed region.
                        // In-place composition must never consume its own previous output.
                        try Self.restore(originals[frame], into: frames[frame].bl)
                        let completion = Completion()
                        let semaphore = DispatchSemaphore(value: 0)
                        let start = DispatchTime.now().uptimeNanoseconds
                        composer.acquireSlot()
                        if route == "pool_async" {
                            composer.composeAsyncWithAcquiredSlot(baseBuffer: frames[frame].bl, enhancementBuffer: frames[frame].el,
                                                                 params: params, frameIndex: UInt32(sequence)) { output in
                                completion.end = DispatchTime.now().uptimeNanoseconds
                                completion.output = output
                                completion.succeeded = output != nil
                                semaphore.signal()
                            }
                        } else {
                            composer.composeInPlaceAsyncWithAcquiredSlot(baseBuffer: frames[frame].bl, enhancementBuffer: frames[frame].el,
                                                                        params: params, frameIndex: UInt32(sequence)) { result in
                                completion.end = DispatchTime.now().uptimeNanoseconds
                                completion.succeeded = result == .composed
                                semaphore.signal()
                            }
                        }
                        try #require(semaphore.wait(timeout: .now() + 30) == .success, "Compose completion timed out")
                        try #require(completion.succeeded, "Production route failed: \(route)")
                        calls += 1
                        let mean = try #require(composer.meanGPUTimeMs, "GPU timestamps unavailable")
                        let total = mean * Double(calls)
                        let gpuMs = total - previousGpuTotal
                        previousGpuTotal = total
                        try #require(gpuMs.isFinite && gpuMs > 0, "Invalid derived GPU timestamp")
                        if sequence >= Self.warmups {
                            samples.append(Sample(index: sequence - Self.warmups, frame: frame,
                                                  gpuMs: gpuMs, completionMs: Double(completion.end - start) / 1_000_000,
                                                  thermalState: ProcessInfo.processInfo.thermalState.rawValue))
                        }
                        if sequence == Self.warmups + Self.samples - 1 {
                            lastOutput = route == "pool_async" ? completion.output : frames[frame].bl
                        }
                        }
                    }
                    try #require(calls == 70 && samples.count == Self.samples && composer.inPlaceLostFrames == 0)
                    try #require(composer.inPlaceComposedFrames == (route == "in_place_async" ? calls : 0))
                    try #require(composer.hasOutputPool == (route == "pool_async"))
                    let output = try #require(lastOutput)
                    results.append(Result(width: width, height: height, profile: profile, route: route,
                                          flags: params.flags.rawValue, lumaNlqScale: params.nlqY.nlqScale,
                                          cbMmr: params.polyCb.usesMmr, crMmr: params.polyCr.usesMmr,
                                          inputSHA256: inputHashes, outputSHA256: Self.hash(Self.planeData(output)), calls: calls,
                                          inPlaceComposedFrames: composer.inPlaceComposedFrames, inPlaceLostFrames: composer.inPlaceLostFrames,
                                          hasOutputPool: composer.hasOutputPool, samples: samples))
                }
            }
        }
        #if os(tvOS)
        let lowPowerMode = false
        #else
        let lowPowerMode = ProcessInfo.processInfo.isLowPowerModeEnabled
        #endif
        let report = Report(schemaVersion: 1, timestamp: ISO8601DateFormatter().string(from: Date()),
                            device: device.name, operatingSystem: ProcessInfo.processInfo.operatingSystemVersionString,
                            configuration: "Release", gpuTimingMethod: "difference of cumulative meanGPUTimeMs * successful call count, before frame 120",
                            scope: "single in-flight production async composition; completion latency includes encode and callback delivery; excludes reset, decode, rendering and IO",
                            warmups: Self.warmups, sampleCount: Self.samples, thermalStateBefore: initialThermal,
                            thermalStateAfter: ProcessInfo.processInfo.thermalState.rawValue, lowPowerMode: lowPowerMode, results: results)
        let encoder = JSONEncoder(); encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(report).write(to: URL(fileURLWithPath: path))
        #endif
    }
}
#endif
