import XCTest
@testable import Sonify

final class LyricsSyncTests: XCTestCase {
    func testActiveLineBinarySearch() {
        let lines = [
            LyricLine(time: 5.0, text: "First line", interlude: nil),
            LyricLine(time: 12.5, text: "Second line", interlude: nil),
            LyricLine(time: 20.0, text: "•••", interlude: true),
            LyricLine(time: 31.2, text: "Final chorus", interlude: nil),
        ]

        XCTAssertNil(LyricsSyncEngine.activeLineIndex(in: lines, at: 0.0))
        XCTAssertEqual(LyricsSyncEngine.activeLineIndex(in: lines, at: 5.0), 0)
        XCTAssertEqual(LyricsSyncEngine.activeLineIndex(in: lines, at: 12.45), 1)
        XCTAssertEqual(LyricsSyncEngine.activeLineIndex(in: lines, at: 22.0), 2)
        XCTAssertEqual(LyricsSyncEngine.activeLineIndex(in: lines, at: 99.0), 3)
        XCTAssertNil(LyricsSyncEngine.activeLineIndex(in: [], at: 10.0))
    }

    func testParseLocalLRCWithOffsetAndMultiTimestamps() {
        let raw = """
        [offset:500]
        [00:10.00][00:30.50]Repeated chorus line
        [00:20.25]Middle verse
        [00:25.00]
        """
        let parsed = LyricsSyncEngine.parseLRC(raw)
        XCTAssertEqual(parsed.count, 4)
        XCTAssertEqual(parsed[0].time, 10.5, accuracy: 0.01)
        XCTAssertEqual(parsed[0].text, "Repeated chorus line")
        XCTAssertEqual(parsed[1].time, 20.75, accuracy: 0.01)
        XCTAssertTrue(parsed[2].isInterlude)
        XCTAssertEqual(parsed[3].time, 31.0, accuracy: 0.01)
    }
}
