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
        XCTAssertNil(info.hlsUrl)
        XCTAssertNil(info.directUrl)

        let scStreamJson = """
        {
          "trackId": "dz:4045817471",
          "streamUrl": "/api/stream/dz:4045817471",
          "streamUrlWithExt": "/api/stream/dz:4045817471.m4a",
          "streamMode": "full",
          "streamSource": "soundcloud",
          "contentType": "audio/mp4",
          "adFree": true,
          "hlsUrl": "https://playback.media-streaming.soundcloud.cloud/playlist.m3u8"
        }
        """
        let scInfo = try decoder.decode(StreamInfoResponse.self, from: Data(scStreamJson.utf8))
        XCTAssertEqual(scInfo.hlsUrl, "https://playback.media-streaming.soundcloud.cloud/playlist.m3u8")
        XCTAssertNil(scInfo.directUrl)

        let jsStreamJson = """
        {
          "trackId": "js:123",
          "streamUrl": "/api/stream/js:123",
          "streamUrlWithExt": "/api/stream/js:123.m4a",
          "streamMode": "full",
          "streamSource": "jiosaavn",
          "contentType": "audio/mp4",
          "adFree": true,
          "directUrl": "https://aac.saavncdn.com/track_320.mp4"
        }
        """
        let jsInfo = try decoder.decode(StreamInfoResponse.self, from: Data(jsStreamJson.utf8))
        XCTAssertEqual(jsInfo.directUrl, "https://aac.saavncdn.com/track_320.mp4")

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
          "lastPosition": 42.5,
          "autoplay": true,
          "crossfade": 4,
          "playbackRate": 1.25,
          "audioQuality": "lossless",
          "eqPreset": "bass",
          "eqBands": [5, 3, 0, 1, 2]
        }
        """
        let settings = try decoder.decode(AppSettings.self, from: Data(settingsJson.utf8))
        XCTAssertEqual(settings.repeatMode, .all)
        XCTAssertEqual(settings.region, "vn")
        XCTAssertTrue(settings.shuffle)
        XCTAssertTrue(settings.autoplay)
        XCTAssertEqual(settings.crossfade, 4)
        XCTAssertEqual(settings.playbackRate, 1.25, accuracy: 0.001)
        XCTAssertEqual(settings.audioQuality, "lossless")
        XCTAssertEqual(settings.eqBands, [5, 3, 0, 1, 2])

        let playlistJson = """
        {
          "id": "pl_123",
          "name": "Late Night Drive",
          "description": "Synth & V-Pop",
          "coverColor": "#1db954",
          "coverEmoji": "🔥",
          "coverUrl": "",
          "trackCount": 4,
          "covers": ["https://cdn.example.com/1.jpg"],
          "createdAt": 1764000000,
          "updatedAt": 1764000100
        }
        """
        let pl = try decoder.decode(Playlist.self, from: Data(playlistJson.utf8))
        XCTAssertEqual(pl.id, "pl_123")
        XCTAssertEqual(pl.trackCount, 4)
        XCTAssertEqual(pl.coverColor, "#1db954")
        XCTAssertEqual(pl.coverEmoji, "🔥")

        let errJson = """
        {"error": {"code": "STREAM_UNAVAILABLE", "message": "Unable to stream right now."}}
        """
        let envelope = try decoder.decode(APIErrorEnvelope.self, from: Data(errJson.utf8))
        XCTAssertEqual(envelope.error.code, "STREAM_UNAVAILABLE")
    }

    func testDecodeSpotifyParityModels() throws {
        let radioJson = """
        {
          "seedType": "track",
          "seedId": "dz:908604612",
          "tracks": [
            {"id": "dz:1", "title": "Save Your Tears", "artist": "The Weeknd", "duration": 215}
          ]
        }
        """
        let radio = try decoder.decode(RadioResponse.self, from: Data(radioJson.utf8))
        XCTAssertEqual(radio.seedType, "track")
        XCTAssertEqual(radio.tracks.count, 1)

        let mixesJson = """
        {
          "mixes": [
            {
              "id": "daily-mix-1",
              "title": "Daily Mix 1",
              "subtitle": "The Weeknd & more",
              "gradient": ["#1DB954", "#0D3B22"],
              "cover": "https://cdn.example.com/m1.jpg",
              "tracks": []
            }
          ]
        }
        """
        let mixes = try decoder.decode(MadeForYouResponse.self, from: Data(mixesJson.utf8))
        XCTAssertEqual(mixes.mixes.first?.id, "daily-mix-1")
        XCTAssertEqual(mixes.mixes.first?.color, "#1DB954")

        let genreJson = """
        {
          "genre": {"slug": "vpop", "name": "V-Pop", "color": "#dc148c"},
          "tracks": [],
          "albums": [],
          "artists": []
        }
        """
        let hub = try decoder.decode(GenreHubResponse.self, from: Data(genreJson.utf8))
        XCTAssertEqual(hub.genre.slug, "vpop")

        let trackDetailJson = """
        {
          "track": {"id": "dz:908604612", "title": "Blinding Lights", "artist": "The Weeknd", "duration": 200},
          "relatedTracks": [{"id": "dz:1", "title": "Save Your Tears", "artist": "The Weeknd", "duration": 215}]
        }
        """
        let trackDetail = try decoder.decode(TrackDetailResponse.self, from: Data(trackDetailJson.utf8))
        XCTAssertEqual(trackDetail.track.id, "dz:908604612")
        XCTAssertEqual(trackDetail.related.count, 1)

        let statsJson = """
        {
          "totalPlays": 42,
          "totalMinutes": 148,
          "likedCount": 12,
          "playlistCount": 3,
          "followedArtistsCount": 5,
          "savedAlbumsCount": 4,
          "streakDays": 3,
          "mostActiveHour": 21,
          "hourlyCounts": [0,0,0,0,0,0,0,0,0,0,0,0,1,2,0,0,0,0,3,4,5,10,8,9],
          "topTracks": [
            {"track": {"id": "dz:908604612", "title": "Blinding Lights", "artist": "The Weeknd", "duration": 200}, "playCount": 9}
          ],
          "topArtists": [
            {"artist": "The Weeknd", "artistId": "dz:4050205", "playCount": 14, "cover": "https://cdn.example.com/tw.jpg"}
          ]
        }
        """
        let stats = try decoder.decode(SoundCapsuleStats.self, from: Data(statsJson.utf8))
        XCTAssertEqual(stats.totalMinutes, 148)
        XCTAssertEqual(stats.hourlyCounts.count, 24)
        XCTAssertEqual(stats.topTracks.first?.id, "dz:908604612")
        XCTAssertEqual(stats.topArtists.first?.playCount, 14)
        XCTAssertEqual(stats.topArtists.first?.coverSmall, "https://cdn.example.com/tw.jpg")

        let artistsJson = """
        {
          "artists": [{"id": "dz:4050205", "name": "The Weeknd", "pictureSmall": "https://cdn.example.com/s.jpg", "pictureLarge": "https://cdn.example.com/l.jpg", "fans": 1000, "followedAt": 1764000000}],
          "artistIds": ["dz:4050205"]
        }
        """
        let followed = try decoder.decode(FollowedArtistsResponse.self, from: Data(artistsJson.utf8))
        XCTAssertEqual(followed.artistIds, ["dz:4050205"])
        XCTAssertEqual(followed.artists.first?.image, "https://cdn.example.com/l.jpg")

        let followMutJson = """
        {"followed": true, "artistId": "dz:4050205"}
        """
        let followMut = try decoder.decode(FollowMutationResponse.self, from: Data(followMutJson.utf8))
        XCTAssertTrue(followMut.followed)

        let albumsJson = """
        {
          "albums": [{"id": "dz:alb1", "title": "After Hours", "artist": "The Weeknd", "artistId": "dz:4050205", "coverSmall": "https://cdn.example.com/as.jpg", "coverLarge": "https://cdn.example.com/al.jpg", "releaseDate": "2020", "savedAt": 1764000000}],
          "albumIds": ["dz:alb1"]
        }
        """
        let saved = try decoder.decode(SavedAlbumsResponse.self, from: Data(albumsJson.utf8))
        XCTAssertEqual(saved.albumIds, ["dz:alb1"])
        XCTAssertEqual(saved.albums.first?.cover, "https://cdn.example.com/al.jpg")
    }
}
