import XCTest
@testable import Sonify

@MainActor
final class OfflineDownloadTests: XCTestCase {
    private var tempDir: URL!
    private var defaultsSuiteName: String!
    private var testDefaults: UserDefaults!

    override func setUpWithError() throws {
        try super.setUpWithError()
        let base = FileManager.default.temporaryDirectory
            .appendingPathComponent("SonifyOfflineTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        tempDir = base
        defaultsSuiteName = "com.sonify.tests.offline.\(UUID().uuidString)"
        testDefaults = UserDefaults(suiteName: defaultsSuiteName)!
    }

    override func tearDownWithError() throws {
        if let suite = defaultsSuiteName {
            testDefaults.removePersistentDomain(forName: suite)
        }
        if let dir = tempDir {
            try? FileManager.default.removeItem(at: dir)
        }
        try super.tearDownWithError()
    }

    func testPersistReloadAndBackupExclusion() throws {
        let track = Track(
            id: "dz:908604612",
            title: "Blinding Lights",
            artist: "The Weeknd",
            album: "After Hours",
            duration: 200
        )
        let audioBytes = Data(repeating: 0x5A, count: 4096)
        let artworkBytes = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        let lyrics = LyricsResponse(
            trackId: track.id,
            title: track.title,
            artist: track.artist,
            status: "synced",
            source: "lrclib",
            instrumental: false,
            synced: true,
            lines: [LyricLine(time: 13.45, text: "I've been tryna call", interlude: nil)],
            plainLyrics: "I've been tryna call",
            rawLrc: "[00:13.45] I've been tryna call"
        )

        let manager = OfflineDownloadManager(baseDirectory: tempDir, userDefaults: testDefaults)
        let record = try manager.storeOfflineAsset(
            track: track,
            audioData: audioBytes,
            contentType: "audio/flac",
            streamSource: "monochrome",
            artworkData: artworkBytes,
            lyrics: lyrics
        )

        XCTAssertTrue(manager.isDownloaded(track.id))
        XCTAssertEqual(record.fileSizeBytes, 4096)
        XCTAssertEqual(manager.totalStorageBytes, 4096)
        XCTAssertFalse(manager.formattedTotalSize.isEmpty)

        // Verify iCloud backup exclusion on offline directory & stored audio file (App Store Guideline 2.23)
        let dirValues = try tempDir.resourceValues(forKeys: [.isExcludedFromBackupKey])
        XCTAssertEqual(dirValues.isExcludedFromBackup, true)

        let audioURL = try XCTUnwrap(manager.localAudioURL(for: track.id))
        XCTAssertTrue(audioURL.lastPathComponent.hasSuffix(".flac"))
        let fileValues = try audioURL.resourceValues(forKeys: [.isExcludedFromBackupKey])
        XCTAssertEqual(fileValues.isExcludedFromBackup, true)

        // Reload a brand new instance from index.json to verify full disk persistence across restarts
        let reloaded = OfflineDownloadManager(baseDirectory: tempDir, userDefaults: testDefaults)
        XCTAssertTrue(reloaded.isDownloaded(track.id))
        XCTAssertEqual(reloaded.downloadedTracks.count, 1)
        XCTAssertEqual(reloaded.totalStorageBytes, 4096)
        XCTAssertEqual(reloaded.localArtworkData(for: track.id), artworkBytes)

        let reloadedLyrics = try XCTUnwrap(reloaded.localLyrics(for: track.id))
        XCTAssertTrue(reloadedLyrics.synced)
        XCTAssertEqual(reloadedLyrics.lines.first?.text, "I've been tryna call")

        // Remove single download
        reloaded.removeDownload(trackId: track.id)
        XCTAssertFalse(reloaded.isDownloaded(track.id))
        XCTAssertNil(reloaded.localAudioURL(for: track.id))
        XCTAssertNil(reloaded.localArtworkData(for: track.id))
        XCTAssertNil(reloaded.localLyrics(for: track.id))
        XCTAssertEqual(reloaded.totalStorageBytes, 0)
    }

    func testClearAllDownloadsAndOfflineModePersistence() throws {
        let manager = OfflineDownloadManager(baseDirectory: tempDir, userDefaults: testDefaults)
        let t1 = Track(id: "dz:1001", title: "Song One", artist: "Artist A", duration: 180)
        let t2 = Track(id: "dz:1002", title: "Song Two", artist: "Artist B", duration: 210)

        try manager.storeOfflineAsset(track: t1, audioData: Data(repeating: 1, count: 1024), contentType: "audio/mp4")
        try manager.storeOfflineAsset(track: t2, audioData: Data(repeating: 2, count: 2048), contentType: "audio/mpeg")
        XCTAssertEqual(manager.downloadedTracks.count, 2)
        XCTAssertEqual(manager.totalStorageBytes, 3072)

        manager.offlineModeEnabled = true
        let reloaded = OfflineDownloadManager(baseDirectory: tempDir, userDefaults: testDefaults)
        XCTAssertTrue(reloaded.offlineModeEnabled)
        XCTAssertEqual(reloaded.downloadedTracks.count, 2)

        reloaded.clearAllDownloads()
        XCTAssertTrue(reloaded.downloadedTracks.isEmpty)
        XCTAssertTrue(reloaded.downloadedIds.isEmpty)
        XCTAssertEqual(reloaded.totalStorageBytes, 0)
        XCTAssertNil(reloaded.localAudioURL(for: t1.id))
        XCTAssertNil(reloaded.localAudioURL(for: t2.id))
    }

    func testOfflineStreamInfoQualityBadgesAndLocalRoute() {
        let flacInfo = StreamInfoResponse(
            trackId: "dz:1",
            streamUrl: "file:///tmp/a.flac",
            streamMode: "offline",
            streamSource: "downloaded",
            contentType: "audio/flac",
            adFree: true
        )
        XCTAssertTrue(flacInfo.isOfflineDownload)
        XCTAssertTrue(flacInfo.isFullTrack)
        XCTAssertEqual(flacInfo.qualityBadgeLabel, "Offline • FLAC")

        let m4aInfo = StreamInfoResponse(
            trackId: "dz:2",
            streamUrl: "file:///tmp/b.m4a",
            streamMode: "offline",
            streamSource: "downloaded",
            contentType: "audio/mp4",
            adFree: true
        )
        XCTAssertEqual(m4aInfo.qualityBadgeLabel, "Offline • Downloaded")

        let localRoute = PlaybackRoute.local(URL(fileURLWithPath: "/tmp/b.m4a"))
        XCTAssertTrue(localRoute.isLocal)
        XCTAssertFalse(localRoute.isProxy)
        XCTAssertFalse(localRoute.isHLS)
    }
}
