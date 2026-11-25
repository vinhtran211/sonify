import XCTest
@testable import Sonify

final class ModelDecodingTests: XCTestCase {
    private let decoder = JSONDecoder()

    func testDecodeHomeResponsePolymorphicSections() throws {
        let json = """
        {
          "mode": "online",
          "region": "global",
          "generatedAt": 1764000000,
          "sections": [
            {
              "id": "trending",
              "title": "Trending Worldwide",
              "type": "tracks",
              "items": [
                {
                  "id": "dz:908604612",
                  "source": "deezer",
                  "title": "Blinding Lights",
                  "artist": "The Weeknd",
                  "duration": 200,
                  "coverLarge": "https://cdn.example.com/bl.jpg",
                  "explicit": false,
                  "liked": true
                }
              ]
            },
            {
              "id": "albums",
              "title": "New Releases",
              "type": "albums",
              "items": [
                {
                  "id": "dz:alb1",
                  "title": "After Hours",
                  "artist": "The Weeknd",
                  "coverLarge": "https://cdn.example.com/ah.jpg",
                  "releaseDate": "2020-03-20"
                }
              ]
            },
            {
              "id": "artists",
              "title": "Popular Artists",
              "type": "artists",
              "items": [
                {
                  "id": "dz:4050205",
                  "name": "The Weeknd",
                  "pictureLarge": "https://cdn.example.com/tw.jpg"
                }
              ]
            }
          ]
        }
        """
        let home = try decoder.decode(HomeResponse.self, from: Data(json.utf8))
        XCTAssertEqual(home.mode, "online")
        XCTAssertEqual(home.region, "global")
        XCTAssertEqual(home.sections.count, 3)
        XCTAssertEqual(home.sections[0].tracks.first?.id, "dz:908604612")
        XCTAssertEqual(home.sections[0].tracks.first?.liked, true)
        XCTAssertEqual(home.sections[1].albums.first?.title, "After Hours")
        XCTAssertEqual(home.sections[2].artists.first?.name, "The Weeknd")
    }

    func testDecodeSearchResponseWithTopResult() throws {
        let json = """
        {
          "query": "sơn tùng",
          "mode": "online",
          "topResult": {
            "kind": "track",
            "item": {
              "id": "dz:2830386932",
              "title": "Đừng Làm Trái Tim Anh Đau",
              "artist": "Sơn Tùng M-TP",
              "duration": 279
            }
          },
          "tracks": [
            {
              "id": "dz:2830386932",
              "title": "Đừng Làm Trái Tim Anh Đau",
              "artist": "Sơn Tùng M-TP",
              "duration": 279
            }
          ],
          "artists": [],
          "albums": []
        }
        """
        let res = try decoder.decode(SearchResponse.self, from: Data(json.utf8))
        XCTAssertEqual(res.query, "sơn tùng")
        XCTAssertEqual(res.tracks.count, 1)
        if case .track(let topTrack) = res.topResult {
            XCTAssertEqual(topTrack.id, "dz:2830386932")
        } else {
            XCTFail("Expected topResult to decode as .track")
        }
    }

    func testDecodeStreamInfoAndLyricsAndSettingsAndError() throws {
        let streamJson = """
        {
          "trackId": "dz:908604612",
          "streamUrl": "/api/stream/dz:908604612",
          "streamUrlWithExt": "/api/stream/dz:908604612.flac",
          "streamMode": "full",
          "streamSource": "monochrome",
          "contentType": "audio/flac",
          "adFree": true
        }
        """
        let info = try decoder.decode(StreamInfoResponse.self, from: Data(streamJson.utf8))
        XCTAssertTrue(info.isFullTrack)
        XCTAssertEqual(info.qualityBadgeLabel, "Lossless FLAC")
        XCTAssertEqual(info.resolvedRelativeStreamPath(), "/api/stream/dz:908604612.flac")

        let lyricsJson = """
        {
          "trackId": "dz:908604612",
          "title": "Blinding Lights",
          "artist": "The Weeknd",
          "status": "synced",
          "source": "lrclib",
          "instrumental": false,
          "synced": true,
          "lines": [
            {"time": 13.45, "text": "I've been tryna call"},
            {"time": 16.0, "text": "•••", "interlude": true}
          ],
          "plainLyrics": "I've been tryna call",
          "rawLrc": "[00:13.45] I've been tryna call"
        }
        """
        let lyrics = try decoder.decode(LyricsResponse.self, from: Data(lyricsJson.utf8))
        XCTAssertEqual(lyrics.lines.count, 2)
        XCTAssertEqual(lyrics.lines[0].time, 13.45, accuracy: 0.001)
        XCTAssertTrue(lyrics.lines[1].isInterlude)

        let settingsJson = """
        {
          "volume": 0.75,
          "muted": false,
          "shuffle": true,
          "repeat": "all",
          "region": "vn",
          "lastTrackId": "dz:2830386932",
          "lastPosition": 42.5
        }
        """
        let settings = try decoder.decode(AppSettings.self, from: Data(settingsJson.utf8))
        XCTAssertEqual(settings.repeatMode, .all)
        XCTAssertEqual(settings.region, "vn")
        XCTAssertTrue(settings.shuffle)

        let playlistJson = """
        {
          "id": "pl_123",
          "name": "Late Night Drive",
          "description": "Synth & V-Pop",
          "trackCount": 4,
          "covers": ["https://cdn.example.com/1.jpg"],
          "createdAt": 1764000000,
          "updatedAt": 1764000100
        }
        """
        let pl = try decoder.decode(Playlist.self, from: Data(playlistJson.utf8))
        XCTAssertEqual(pl.id, "pl_123")
        XCTAssertEqual(pl.trackCount, 4)

        let errJson = """
        {"error": {"code": "STREAM_UNAVAILABLE", "message": "Unable to stream right now."}}
        """
        let envelope = try decoder.decode(APIErrorEnvelope.self, from: Data(errJson.utf8))
        XCTAssertEqual(envelope.error.code, "STREAM_UNAVAILABLE")
    }
}
