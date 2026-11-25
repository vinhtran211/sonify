import XCTest
@testable import Sonify

final class QueueLogicTests: XCTestCase {
    private func makeTrack(_ id: String) -> Track {
        Track(id: id, title: "Title \(id)", artist: "Artist \(id)", duration: 200)
    }

    func testPlayNextPrevAndRepeatModes() {
        let tracks = [makeTrack("dz:1"), makeTrack("dz:2"), makeTrack("dz:3")]
        var engine = PlaybackQueueEngine()

        let first = engine.play(track: tracks[0], context: tracks)
        XCTAssertEqual(first.id, "dz:1")
        XCTAssertEqual(engine.currentIndex, 0)

        // Repeat One keeps current track on auto-end
        engine.repeatMode = .one
        XCTAssertEqual(engine.nextTrack(fromAutoEnd: true)?.id, "dz:1")

        // Next advances to track 2 and 3
        engine.repeatMode = .off
        XCTAssertEqual(engine.nextTrack(fromAutoEnd: false)?.id, "dz:2")
        XCTAssertEqual(engine.nextTrack(fromAutoEnd: false)?.id, "dz:3")
        XCTAssertNil(engine.nextTrack(fromAutoEnd: true))

        // Repeat All wraps back to index 0
        engine.repeatMode = .all
        XCTAssertEqual(engine.nextTrack(fromAutoEnd: true)?.id, "dz:1")

        // Previous restarts when currentTime > 3s, otherwise wraps to end on repeatAll
        let restart = engine.previousTrack(currentTime: 8.5)
        XCTAssertTrue(restart.restartCurrent)
        XCTAssertEqual(restart.track?.id, "dz:1")

        let wrapped = engine.previousTrack(currentTime: 1.0)
        XCTAssertFalse(wrapped.restartCurrent)
        XCTAssertEqual(wrapped.track?.id, "dz:3")
    }

    func testUpNextPriorityAndShuffleNeverRepeatsSameTrack() {
        let tracks = [makeTrack("dz:1"), makeTrack("dz:2"), makeTrack("dz:3")]
        var engine = PlaybackQueueEngine()
        _ = engine.play(track: tracks[0], context: tracks)

        let priorityTrack = makeTrack("dz:99")
        engine.enqueueNext(priorityTrack)
        XCTAssertEqual(engine.nextTrack(fromAutoEnd: false)?.id, "dz:99")

        engine.shuffle = true
        let currentIdx = engine.currentIndex
        let nextShuffled = engine.nextTrack(fromAutoEnd: false, randomIndex: { _ in currentIdx })
        XCTAssertNotNil(nextShuffled)
        XCTAssertNotEqual(nextShuffled?.id, "dz:99")
    }

    func testStreamURLWithExtensionAndPercentEncoding() {
        let client = APIClient(baseURLString: "https://sonify-music.onrender.com/")
        let info = StreamInfoResponse(
            trackId: "dz:908604612",
            streamUrl: "/api/stream/dz:908604612",
            streamUrlWithExt: "/api/stream/dz:908604612.flac",
            streamMode: "full",
            streamSource: "monochrome",
            contentType: "audio/x-flac",
            adFree: true,
            hlsUrl: nil,
            directUrl: nil,
            diagnostics: nil,
            cloudMode: true
        )
        let url = client.streamURL(for: info)
        XCTAssertEqual(url?.absoluteString, "https://sonify-music.onrender.com/api/stream/dz%3A908604612.flac")
        XCTAssertEqual(info.preferredPlaybackRoute(proxyURL: url), url.map { .proxy($0) })
        XCTAssertEqual(StreamInfoResponse.normalizedMIME("audio/x-m4p"), "audio/mp4")
        XCTAssertEqual(StreamInfoResponse.extensionForMIME("audio/x-m4p"), ".m4a")
        XCTAssertEqual(StreamInfoResponse.extensionForMIME("video/mp4"), ".mp4")

        // Verify route priority: hlsUrl > directUrl > proxy
        let hlsInfo = StreamInfoResponse(
            trackId: "dz:1",
            streamUrl: "/api/stream/dz:1",
            streamUrlWithExt: "/api/stream/dz:1.m4a",
            streamMode: "full",
            streamSource: "soundcloud",
            contentType: "audio/mp4",
            adFree: true,
            hlsUrl: "https://playback.media-streaming.soundcloud.cloud/a.m3u8",
            directUrl: "https://cdn.example.com/fallback.m4a",
            diagnostics: nil,
            cloudMode: true
        )
        let hlsRoute = hlsInfo.preferredPlaybackRoute(proxyURL: url)
        XCTAssertEqual(hlsRoute?.isHLS, true)
        XCTAssertEqual(hlsRoute?.url.absoluteString, "https://playback.media-streaming.soundcloud.cloud/a.m3u8")

        let directInfo = StreamInfoResponse(
            trackId: "js:1",
            streamUrl: "/api/stream/js:1",
            streamUrlWithExt: "/api/stream/js:1.m4a",
            streamMode: "full",
            streamSource: "jiosaavn",
            contentType: "audio/mp4",
            adFree: true,
            hlsUrl: nil,
            directUrl: "https://aac.saavncdn.com/track_320.mp4",
            diagnostics: nil,
            cloudMode: true
        )
        let directRoute = directInfo.preferredPlaybackRoute(proxyURL: url)
        XCTAssertEqual(directRoute?.isProxy, false)
        XCTAssertEqual(directRoute?.isHLS, false)
        XCTAssertEqual(directRoute?.url.absoluteString, "https://aac.saavncdn.com/track_320.mp4")
    }
}
