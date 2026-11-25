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
            diagnostics: nil,
            cloudMode: true
        )
        let url = client.streamURL(for: info)
        XCTAssertEqual(url?.absoluteString, "https://sonify-music.onrender.com/api/stream/dz%3A908604612.flac")
        XCTAssertEqual(StreamInfoResponse.normalizedMIME("audio/x-m4p"), "audio/mp4")
        XCTAssertEqual(StreamInfoResponse.extensionForMIME("audio/x-m4p"), ".m4a")
        XCTAssertEqual(StreamInfoResponse.extensionForMIME("video/mp4"), ".mp4")
    }
}
