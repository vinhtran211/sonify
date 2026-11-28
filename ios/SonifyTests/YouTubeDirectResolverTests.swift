import XCTest
@testable import Sonify

final class YouTubeDirectResolverTests: XCTestCase {
    func testNormalizeCleanAndTokenSimilarity() {
        XCTAssertEqual(YouTubeDirectResolver.normalizeKey("Đừng Làm Trái Tim Anh Đau"), "dung lam trai tim anh dau")
        XCTAssertEqual(YouTubeDirectResolver.cleanTitle("Blinding Lights (Official Video)"), "Blinding Lights")
        XCTAssertEqual(YouTubeDirectResolver.cleanTitle("Die With A Smile (with Bruno Mars)"), "Die With A Smile")
        XCTAssertEqual(YouTubeDirectResolver.primaryArtist("Lady Gaga, Bruno Mars"), "Lady Gaga")
        XCTAssertGreaterThan(YouTubeDirectResolver.tokenSimilarity("Blinding Lights", "blinding lights"), 0.99)
    }

    func testExtractSearchCandidatesAndStrictScoring() throws {
        let sampleJSON: [String: Any] = [
            "contents": [
                "tabbedSearchResultsRenderer": [
                    "tabs": [
                        [
                            "musicResponsiveListItemRenderer": [
                                "playlistItemData": ["videoId": "fHI8X4OXluQ"],
                                "flexColumns": [
                                    [
                                        "musicResponsiveListItemFlexColumnRenderer": [
                                            "text": ["runs": [["text": "Blinding Lights"]]]
                                        ]
                                    ],
                                    [
                                        "musicResponsiveListItemFlexColumnRenderer": [
                                            "text": [
                                                "runs": [
                                                    ["text": "Song"],
                                                    ["text": " • "],
                                                    ["text": "The Weeknd"],
                                                    ["text": " • "],
                                                    ["text": "3:20"],
                                                ]
                                            ]
                                        ]
                                    ],
                                ],
                            ]
                        ],
                        [
                            "musicResponsiveListItemRenderer": [
                                "playlistItemData": ["videoId": "badCover123"],
                                "flexColumns": [
                                    [
                                        "musicResponsiveListItemFlexColumnRenderer": [
                                            "text": ["runs": [["text": "Blinding Lights (Piano Cover Karaoke)"]]]
                                        ]
                                    ],
                                    [
                                        "musicResponsiveListItemFlexColumnRenderer": [
                                            "text": [
                                                "runs": [
                                                    ["text": "Random Piano Channel"],
                                                    ["text": " • "],
                                                    ["text": "3:22"],
                                                ]
                                            ]
                                        ]
                                    ],
                                ],
                            ]
                        ],
                    ]
                ]
            ]
        ]

        let candidates = YouTubeDirectResolver.extractSearchCandidates(from: sampleJSON)
        XCTAssertEqual(candidates.count, 2)
        XCTAssertEqual(candidates[0].videoId, "fHI8X4OXluQ")
        XCTAssertEqual(candidates[0].duration, 200)

        let goodScore = YouTubeDirectResolver.scoreCandidate(
            candidates[0],
            cleanTitle: "Blinding Lights",
            primaryArtist: "The Weeknd",
            fullArtist: "The Weeknd",
            targetDuration: 200,
            rawTargetTitle: "Blinding Lights"
        )
        XCTAssertGreaterThanOrEqual(goodScore, 0.85)

        let badScore = YouTubeDirectResolver.scoreCandidate(
            candidates[1],
            cleanTitle: "Blinding Lights",
            primaryArtist: "The Weeknd",
            fullArtist: "The Weeknd",
            targetDuration: 200,
            rawTargetTitle: "Blinding Lights"
        )
        XCTAssertLessThan(badScore, 0.58)

        // Word-boundary noise check: "Alive" / "Oliver" must NOT trigger "live" penalty
        let aliveCand = YouTubeSearchCandidate(videoId: "alive123", title: "Alive", artist: "Sia", duration: 263)
        let aliveScore = YouTubeDirectResolver.scoreCandidate(
            aliveCand,
            cleanTitle: "Alive",
            primaryArtist: "Sia",
            fullArtist: "Sia",
            targetDuration: 263,
            rawTargetTitle: "Alive"
        )
        XCTAssertGreaterThanOrEqual(aliveScore, 0.95)

        // Pass-2 relaxed rescue: label uploader (HYBE LABELS) + Official MV (+44s) rescued in relaxed=true,
        // while < 60s short clips, chopped edits (119s for 177s), and generic-title wrong artists remain rejected!
        let labelMV = YouTubeSearchCandidate(
            videoId: "hybeMV123",
            title: "ILLIT (아일릿) 'Magnetic' Official MV",
            artist: "HYBE LABELS",
            duration: 205
        )
        let strictMV = YouTubeDirectResolver.scoreCandidate(
            labelMV,
            cleanTitle: "Magnetic",
            primaryArtist: "ILLIT",
            fullArtist: "ILLIT",
            targetDuration: 161,
            rawTargetTitle: "Magnetic",
            relaxed: false
        )
        XCTAssertLessThan(strictMV, 0.58)
        let relaxedMV = YouTubeDirectResolver.scoreCandidate(
            labelMV,
            cleanTitle: "Magnetic",
            primaryArtist: "ILLIT",
            fullArtist: "ILLIT",
            targetDuration: 161,
            rawTargetTitle: "Magnetic",
            relaxed: true
        )
        XCTAssertGreaterThanOrEqual(relaxedMV, 0.45)

        let choppedEdit = YouTubeSearchCandidate(
            videoId: "chop119",
            title: "Như Một Người Rưng",
            artist: "OSAD",
            duration: 119
        )
        XCTAssertLessThan(
            YouTubeDirectResolver.scoreCandidate(
                choppedEdit,
                cleanTitle: "Như Một Người Rưng",
                primaryArtist: "OSAD",
                fullArtist: "OSAD, Hà An Huy",
                targetDuration: 177,
                rawTargetTitle: "Như Một Người Rưng",
                relaxed: false
            ),
            0.0
        )
        XCTAssertLessThan(
            YouTubeDirectResolver.scoreCandidate(
                choppedEdit,
                cleanTitle: "Như Một Người Rưng",
                primaryArtist: "OSAD",
                fullArtist: "OSAD, Hà An Huy",
                targetDuration: 177,
                rawTargetTitle: "Như Một Người Rưng",
                relaxed: true
            ),
            0.0
        )

        let genericWrongArtist = YouTubeSearchCandidate(
            videoId: "stayWrong",
            title: "Stay",
            artist: "Random Stranger",
            duration: 150
        )
        XCTAssertLessThan(
            YouTubeDirectResolver.scoreCandidate(
                genericWrongArtist,
                cleanTitle: "Stay",
                primaryArtist: "The Kid LAROI",
                fullArtist: "The Kid LAROI, Justin Bieber",
                targetDuration: 141,
                rawTargetTitle: "Stay",
                relaxed: true
            ),
            0.0
        )

        let shortClip = YouTubeSearchCandidate(
            videoId: "short30s",
            title: "Magnetic",
            artist: "ILLIT",
            duration: 30
        )
        XCTAssertLessThan(
            YouTubeDirectResolver.scoreCandidate(
                shortClip,
                cleanTitle: "Magnetic",
                primaryArtist: "ILLIT",
                fullArtist: "ILLIT",
                targetDuration: 161,
                rawTargetTitle: "Magnetic",
                relaxed: true
            ),
            0.0
        )
    }

    func testPickProgressiveItag18FormatAndRejectsLoginRequired() {
        let okPlayer: [String: Any] = [
            "playabilityStatus": ["status": "OK"],
            "streamingData": [
                "formats": [
                    [
                        "itag": 18,
                        "url": "https://rr1---sn-example.googlevideo.com/videoplayback?itag=18",
                        "mimeType": "video/mp4; codecs=\"avc1.42001E, mp4a.40.2\"",
                    ]
                ]
            ],
        ]
        let picked = YouTubeDirectResolver.pickProgressiveFormat(from: okPlayer)
        XCTAssertEqual(picked?.url.absoluteString, "https://rr1---sn-example.googlevideo.com/videoplayback?itag=18")
        XCTAssertEqual(picked?.mimeType, "video/mp4")

        let blockedPlayer: [String: Any] = [
            "playabilityStatus": ["status": "LOGIN_REQUIRED"],
            "streamingData": ["formats": []],
        ]
        XCTAssertNil(YouTubeDirectResolver.pickProgressiveFormat(from: blockedPlayer))
    }

    func testPickAdaptiveAudioItag140AndHLSManifest() {
        let adaptivePlayer: [String: Any] = [
            "playabilityStatus": ["status": "OK"],
            "streamingData": [
                "adaptiveFormats": [
                    [
                        "itag": 251,
                        "url": "https://rr1---sn-example.googlevideo.com/videoplayback?itag=251",
                        "mimeType": "audio/webm; codecs=\"opus\"",
                        "bitrate": 160000,
                    ],
                    [
                        "itag": 140,
                        "url": "https://rr1---sn-example.googlevideo.com/videoplayback?itag=140",
                        "mimeType": "audio/mp4; codecs=\"mp4a.40.2\"",
                        "bitrate": 128000,
                    ],
                ],
                "hlsManifestUrl": "https://manifest.googlevideo.com/api/manifest/hls_variant/example.m3u8",
            ],
        ]
        let adaptive = YouTubeDirectResolver.pickAdaptiveAudioFormat(from: adaptivePlayer)
        XCTAssertEqual(adaptive?.url.absoluteString, "https://rr1---sn-example.googlevideo.com/videoplayback?itag=140")
        XCTAssertEqual(adaptive?.mimeType, "audio/mp4")

        let hls = YouTubeDirectResolver.pickHLSManifestURL(from: adaptivePlayer)
        XCTAssertEqual(hls?.absoluteString, "https://manifest.googlevideo.com/api/manifest/hls_variant/example.m3u8")

        let blocked: [String: Any] = [
            "playabilityStatus": ["status": "LOGIN_REQUIRED"],
            "streamingData": [
                "hlsManifestUrl": "https://manifest.googlevideo.com/api/manifest/hls_variant/blocked.m3u8",
            ],
        ]
        XCTAssertNil(YouTubeDirectResolver.pickAdaptiveAudioFormat(from: blocked))
        XCTAssertNil(YouTubeDirectResolver.pickHLSManifestURL(from: blocked))
    }
}
