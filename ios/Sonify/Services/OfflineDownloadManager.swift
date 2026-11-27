import Combine
import Foundation

struct DownloadedTrackRecord: Codable, Identifiable, Equatable {
    var id: String { track.id }
    let track: Track
    let audioFilename: String
    let artworkFilename: String?
    let lyricsFilename: String?
    let contentType: String
    let fileSizeBytes: Int64
    let streamSource: String
    let downloadedAt: Date
}

enum OfflineDownloadError: Error, LocalizedError {
    case invalidStreamURL
    case httpStatus(Int)
    case emptyAudioFile
    case cancelled

    var errorDescription: String? {
        switch self {
        case .invalidStreamURL:
            return "Could not build offline download URL"
        case .httpStatus(let code):
            return "Download failed with HTTP \(code)"
        case .emptyAudioFile:
            return "Downloaded audio file was empty"
        case .cancelled:
            return "Download cancelled"
        }
    }
}

/// Thread-safe URLSessionDownloadDelegate bridge for streaming progress and atomic file moves.
private final class DownloadDelegateBridge: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private struct ActiveJob {
        let trackId: String
        let destinationURL: URL
        let onProgress: @Sendable (Double) -> Void
        let continuation: CheckedContinuation<Int64, Error>
    }

    private let lock = NSLock()
    private var jobsByTaskId: [Int: ActiveJob] = [:]
    private var tasksByTrackId: [String: URLSessionDownloadTask] = [:]
    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 45
        config.timeoutIntervalForResource = 300
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    func download(
        from url: URL,
        headers: [String: String] = [:],
        to destinationURL: URL,
        trackId: String,
        onProgress: @escaping @Sendable (Double) -> Void
    ) async throws -> Int64 {
        try await withCheckedThrowingContinuation { continuation in
            var request = URLRequest(url: url)
            for (k, v) in headers {
                request.setValue(v, forHTTPHeaderField: k)
            }
            let task = session.downloadTask(with: request)
            lock.lock()
            jobsByTaskId[task.taskIdentifier] = ActiveJob(
                trackId: trackId,
                destinationURL: destinationURL,
                onProgress: onProgress,
                continuation: continuation
            )
            tasksByTrackId[trackId] = task
            lock.unlock()
            task.resume()
        }
    }

    func cancel(trackId: String) {
        lock.lock()
        let task = tasksByTrackId[trackId]
        lock.unlock()
        task?.cancel()
    }

    func cancelAll() {
        lock.lock()
        let allTasks = Array(tasksByTrackId.values)
        lock.unlock()
        for task in allTasks {
            task.cancel()
        }
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        lock.lock()
        let job = jobsByTaskId[downloadTask.taskIdentifier]
        lock.unlock()
        guard let job else { return }

        if totalBytesExpectedToWrite > 0 {
            let fraction = min(max(Double(totalBytesWritten) / Double(totalBytesExpectedToWrite), 0.0), 1.0)
            job.onProgress(fraction)
        } else if totalBytesWritten > 0 {
            // Smooth indeterminate ramp up to 0.85 when server omits Content-Length
            let approx = min(0.85, Double(totalBytesWritten) / (6.0 * 1024.0 * 1024.0))
            job.onProgress(approx)
        }
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        lock.lock()
        let job = jobsByTaskId.removeValue(forKey: downloadTask.taskIdentifier)
        if let job {
            tasksByTrackId.removeValue(forKey: job.trackId)
        }
        lock.unlock()

        guard let job else { return }

        if let http = downloadTask.response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            try? FileManager.default.removeItem(at: location)
            job.continuation.resume(throwing: OfflineDownloadError.httpStatus(http.statusCode))
            return
        }

        do {
            let fm = FileManager.default
            let parent = job.destinationURL.deletingLastPathComponent()
            if !fm.fileExists(atPath: parent.path) {
                try fm.createDirectory(at: parent, withIntermediateDirectories: true)
            }
            if fm.fileExists(atPath: job.destinationURL.path) {
                try fm.removeItem(at: job.destinationURL)
            }
            try fm.moveItem(at: location, to: job.destinationURL)

            let attrs = try fm.attributesOfItem(atPath: job.destinationURL.path)
            let size = (attrs[.size] as? NSNumber)?.int64Value ?? 0
            if size <= 0 {
                try? fm.removeItem(at: job.destinationURL)
                job.continuation.resume(throwing: OfflineDownloadError.emptyAudioFile)
                return
            }

            var mutableURL = job.destinationURL
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try? mutableURL.setResourceValues(values)

            job.onProgress(1.0)
            job.continuation.resume(returning: size)
        } catch {
            try? FileManager.default.removeItem(at: location)
            job.continuation.resume(throwing: error)
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let error else { return }
        lock.lock()
        let job = jobsByTaskId.removeValue(forKey: task.taskIdentifier)
        if let job {
            tasksByTrackId.removeValue(forKey: job.trackId)
        }
        lock.unlock()

        guard let job else { return }
        if let urlErr = error as? URLError, urlErr.code == .cancelled {
            job.continuation.resume(throwing: OfflineDownloadError.cancelled)
        } else {
            job.continuation.resume(throwing: error)
        }
    }
}

@MainActor
final class OfflineDownloadManager: ObservableObject {
    private static let offlineModeDefaultsKey = "sonify.offlineModeEnabled"

    @Published private(set) var downloadedTracks: [DownloadedTrackRecord] = []
    @Published private(set) var downloadedIds: Set<String> = []
    @Published private(set) var activeDownloads: [String: Double] = [:]
    @Published private(set) var activeTracksById: [String: Track] = [:]
    @Published var offlineModeEnabled: Bool {
        didSet {
            userDefaults.set(offlineModeEnabled, forKey: Self.offlineModeDefaultsKey)
        }
    }

    let baseDirectory: URL
    private let audioDirectory: URL
    private let artworkDirectory: URL
    private let lyricsDirectory: URL
    private let indexFileURL: URL
    private let userDefaults: UserDefaults
    private let fileManager: FileManager
    private let bridge = DownloadDelegateBridge()
    private let artworkMemoryCache = NSCache<NSString, NSData>()
    private var recordsById: [String: DownloadedTrackRecord] = [:]

    init(
        baseDirectory: URL? = nil,
        userDefaults: UserDefaults = .standard,
        fileManager: FileManager = .default
    ) {
        self.userDefaults = userDefaults
        self.fileManager = fileManager
        self.offlineModeEnabled = userDefaults.bool(forKey: Self.offlineModeDefaultsKey)

        let rootDir: URL
        if let baseDirectory {
            rootDir = baseDirectory
        } else {
            let appSupport = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
                ?? fileManager.temporaryDirectory
            rootDir = appSupport.appendingPathComponent("SonifyOffline", isDirectory: true)
        }

        self.baseDirectory = rootDir
        self.audioDirectory = rootDir.appendingPathComponent("audio", isDirectory: true)
        self.artworkDirectory = rootDir.appendingPathComponent("artwork", isDirectory: true)
        self.lyricsDirectory = rootDir.appendingPathComponent("lyrics", isDirectory: true)
        self.indexFileURL = rootDir.appendingPathComponent("index.json", isDirectory: false)

        artworkMemoryCache.countLimit = 80
        ensureDirectoriesExist()
        loadIndexFromDisk()
    }

    // MARK: - Queries & Storage Info

    func isDownloaded(_ trackId: String) -> Bool {
        downloadedIds.contains(trackId)
    }

    func isDownloading(_ trackId: String) -> Bool {
        activeDownloads[trackId] != nil
    }

    func progress(for trackId: String) -> Double? {
        activeDownloads[trackId]
    }

    func record(for trackId: String) -> DownloadedTrackRecord? {
        recordsById[trackId]
    }

    func localAudioURL(for trackId: String) -> URL? {
        guard let rec = recordsById[trackId] else { return nil }
        let url = audioDirectory.appendingPathComponent(rec.audioFilename, isDirectory: false)
        guard fileManager.fileExists(atPath: url.path) else { return nil }
        return url
    }

    func localArtworkData(for trackId: String) -> Data? {
        if let cached = artworkMemoryCache.object(forKey: trackId as NSString) {
            return cached as Data
        }
        guard let rec = recordsById[trackId], let name = rec.artworkFilename else { return nil }
        let url = artworkDirectory.appendingPathComponent(name, isDirectory: false)
        guard let data = try? Data(contentsOf: url), !data.isEmpty else { return nil }
        artworkMemoryCache.setObject(data as NSData, forKey: trackId as NSString)
        return data
    }

    func localLyrics(for trackId: String) -> LyricsResponse? {
        guard let rec = recordsById[trackId], let name = rec.lyricsFilename else { return nil }
        let url = lyricsDirectory.appendingPathComponent(name, isDirectory: false)
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(LyricsResponse.self, from: data)
    }

    var totalStorageBytes: Int64 {
        downloadedTracks.reduce(0) { $0 + max($1.fileSizeBytes, 0) }
    }

    var formattedTotalSize: String {
        ByteCountFormatter.string(fromByteCount: totalStorageBytes, countStyle: .file)
    }

    // MARK: - Download Operations

    @discardableResult
    func downloadTrack(_ track: Track, api: APIClient, quality: String = "auto") async -> Bool {
        if isDownloaded(track.id) { return true }
        if isDownloading(track.id) { return false }

        ensureDirectoriesExist()
        activeDownloads[track.id] = 0.02
        activeTracksById[track.id] = track

        defer {
            activeDownloads.removeValue(forKey: track.id)
            activeTracksById.removeValue(forKey: track.id)
        }

        do {
            // 1. Resolve stream metadata & ALWAYS download a full-length file (never ephemeral .m3u8 HLS)
            var resolvedContentType: String
            var resolvedSource: String
            var downloadURL: URL
            var downloadHeaders: [String: String] = [:]
            var usedYouTubeDirect = false

            let backendInfo = try? await api.getStreamInfo(trackId: track.id, quality: quality)
            if let info = backendInfo, !info.isPreview, let proxyURL = api.streamURL(for: info) {
                downloadURL = proxyURL
                resolvedContentType = info.contentType
                resolvedSource = info.streamSource
            } else if let yt = await YouTubeDirectResolver.shared.resolveFullTrack(for: track) {
                downloadURL = yt.url
                downloadHeaders = yt.headers
                resolvedContentType = yt.mimeType
                resolvedSource = "youtube-direct"
                usedYouTubeDirect = true
            } else if let info = backendInfo, let proxyURL = api.streamURL(for: info) {
                downloadURL = proxyURL
                resolvedContentType = info.contentType
                resolvedSource = info.streamSource
            } else {
                throw OfflineDownloadError.invalidStreamURL
            }

            let safeId = Self.safeFilename(for: track.id)
            var ext = StreamInfoResponse.extensionForMIME(resolvedContentType)
            var audioFilename = "\(safeId)\(ext)"
            var audioDestURL = audioDirectory.appendingPathComponent(audioFilename, isDirectory: false)

            // 2. Kick off best-effort artwork & lyrics fetches concurrently with audio download
            async let artworkTask: Data? = Self.fetchArtworkData(for: track)
            async let lyricsTask: LyricsResponse? = try? api.getLyrics(for: track, refresh: false)

            let trackId = track.id
            let minNetworkBytes: Int64 = 350_000
            let progressHandler: @Sendable (Double) -> Void = { [weak self] fraction in
                Task { @MainActor [weak self] in
                    guard let self, self.activeDownloads[trackId] != nil else { return }
                    self.activeDownloads[trackId] = max(0.05, min(0.96, fraction))
                }
            }

            var audioBytes: Int64
            do {
                let downloaded = try await bridge.download(
                    from: downloadURL,
                    headers: downloadHeaders,
                    to: audioDestURL,
                    trackId: trackId,
                    onProgress: progressHandler
                )
                guard downloaded >= minNetworkBytes else {
                    try? fileManager.removeItem(at: audioDestURL)
                    throw OfflineDownloadError.emptyAudioFile
                }
                audioBytes = downloaded
            } catch {
                // If backend proxy download failed (e.g. HTTP 502/timeout), fall back to YouTubeDirectResolver!
                if !usedYouTubeDirect, let yt = await YouTubeDirectResolver.shared.resolveFullTrack(for: track) {
                    try? fileManager.removeItem(at: audioDestURL)
                    resolvedContentType = yt.mimeType
                    resolvedSource = "youtube-direct"
                    ext = StreamInfoResponse.extensionForMIME(resolvedContentType)
                    audioFilename = "\(safeId)\(ext)"
                    audioDestURL = audioDirectory.appendingPathComponent(audioFilename, isDirectory: false)
                    let ytBytes = try await bridge.download(
                        from: yt.url,
                        headers: yt.headers,
                        to: audioDestURL,
                        trackId: trackId,
                        onProgress: progressHandler
                    )
                    guard ytBytes >= minNetworkBytes else {
                        try? fileManager.removeItem(at: audioDestURL)
                        throw OfflineDownloadError.emptyAudioFile
                    }
                    audioBytes = ytBytes
                } else {
                    throw error
                }
            }

            // 3. Sniff actual downloaded magic bytes so extension & contentType ALWAYS match the bytes on disk
            if let handle = try? FileHandle(forReadingFrom: audioDestURL) {
                let headData = (try? handle.read(upToCount: 16)) ?? Data()
                try? handle.close()
                let sniffedMIME = Self.sniffAudioMIME(head: headData, fallback: resolvedContentType)
                resolvedContentType = sniffedMIME
                let trueExt = StreamInfoResponse.extensionForMIME(sniffedMIME)
                if trueExt != ext {
                    let trueFilename = "\(safeId)\(trueExt)"
                    let trueURL = audioDirectory.appendingPathComponent(trueFilename, isDirectory: false)
                    if fileManager.fileExists(atPath: trueURL.path) {
                        try? fileManager.removeItem(at: trueURL)
                    }
                    if (try? fileManager.moveItem(at: audioDestURL, to: trueURL)) != nil {
                        audioFilename = trueFilename
                        audioDestURL = trueURL
                    }
                }
            }

            let artData = await artworkTask
            let lyricsResp = await lyricsTask

            try saveDownloadedTrack(
                track: track,
                audioFilename: audioFilename,
                audioSizeBytes: audioBytes,
                contentType: resolvedContentType,
                streamSource: resolvedSource,
                artworkData: artData,
                lyrics: lyricsResp
            )
            return true
        } catch {
            return false
        }
    }

    @discardableResult
    func downloadTracks(_ tracks: [Track], api: APIClient, quality: String = "auto") async -> Int {
        let pending = tracks.filter { !isDownloaded($0.id) && !isDownloading($0.id) }
        guard !pending.isEmpty else { return 0 }

        let maxConcurrent = 2
        var completed = 0
        var nextIndex = 0

        await withTaskGroup(of: Bool.self) { group in
            let initialBatch = min(maxConcurrent, pending.count)
            for _ in 0..<initialBatch {
                let track = pending[nextIndex]
                nextIndex += 1
                group.addTask {
                    await self.downloadTrack(track, api: api, quality: quality)
                }
            }

            for await succeeded in group {
                if succeeded { completed += 1 }
                if nextIndex < pending.count {
                    let track = pending[nextIndex]
                    nextIndex += 1
                    group.addTask {
                        await self.downloadTrack(track, api: api, quality: quality)
                    }
                }
            }
        }

        return completed
    }

    func cancelDownload(trackId: String) {
        bridge.cancel(trackId: trackId)
        activeDownloads.removeValue(forKey: trackId)
        activeTracksById.removeValue(forKey: trackId)
    }

    func removeDownload(trackId: String) {
        if let rec = recordsById.removeValue(forKey: trackId) {
            let audioURL = audioDirectory.appendingPathComponent(rec.audioFilename, isDirectory: false)
            try? fileManager.removeItem(at: audioURL)
            if let artName = rec.artworkFilename {
                try? fileManager.removeItem(at: artworkDirectory.appendingPathComponent(artName, isDirectory: false))
            }
            if let lyrName = rec.lyricsFilename {
                try? fileManager.removeItem(at: lyricsDirectory.appendingPathComponent(lyrName, isDirectory: false))
            }
            artworkMemoryCache.removeObject(forKey: trackId as NSString)
            syncPublishedState()
            saveIndexToDisk()
        }
    }

    func clearAllDownloads() {
        bridge.cancelAll()
        activeDownloads.removeAll()
        activeTracksById.removeAll()
        recordsById.removeAll()
        artworkMemoryCache.removeAllObjects()

        for dir in [audioDirectory, artworkDirectory, lyricsDirectory] {
            if let contents = try? fileManager.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) {
                for url in contents {
                    try? fileManager.removeItem(at: url)
                }
            }
        }
        syncPublishedState()
        saveIndexToDisk()
    }

    // MARK: - Persistence & Direct Registration Helper (also used by XCTest)

    @discardableResult
    func saveDownloadedTrack(
        track: Track,
        audioFilename: String,
        audioSizeBytes: Int64,
        contentType: String,
        streamSource: String,
        artworkData: Data? = nil,
        lyrics: LyricsResponse? = nil
    ) throws -> DownloadedTrackRecord {
        ensureDirectoriesExist()
        let safeId = Self.safeFilename(for: track.id)

        var artworkFilename: String?
        if let artworkData, !artworkData.isEmpty {
            let name = "\(safeId).img"
            let url = artworkDirectory.appendingPathComponent(name, isDirectory: false)
            try artworkData.write(to: url, options: .atomic)
            applyBackupExclusion(to: url)
            artworkFilename = name
            artworkMemoryCache.setObject(artworkData as NSData, forKey: track.id as NSString)
        }

        var lyricsFilename: String?
        if let lyrics, lyrics.status != "not_found" {
            let name = "\(safeId).json"
            let url = lyricsDirectory.appendingPathComponent(name, isDirectory: false)
            let encoded = try JSONEncoder().encode(lyrics)
            try encoded.write(to: url, options: .atomic)
            applyBackupExclusion(to: url)
            lyricsFilename = name
        }

        let audioURL = audioDirectory.appendingPathComponent(audioFilename, isDirectory: false)
        if fileManager.fileExists(atPath: audioURL.path) {
            applyBackupExclusion(to: audioURL)
        }

        let record = DownloadedTrackRecord(
            track: track,
            audioFilename: audioFilename,
            artworkFilename: artworkFilename,
            lyricsFilename: lyricsFilename,
            contentType: contentType,
            fileSizeBytes: audioSizeBytes,
            streamSource: streamSource,
            downloadedAt: Date()
        )
        recordsById[track.id] = record
        syncPublishedState()
        saveIndexToDisk()
        return record
    }

    /// Writes audio bytes directly to the offline audio directory and registers the record (convenience for tests).
    @discardableResult
    func storeOfflineAsset(
        track: Track,
        audioData: Data,
        contentType: String = "audio/mp4",
        streamSource: String = "downloaded",
        artworkData: Data? = nil,
        lyrics: LyricsResponse? = nil
    ) throws -> DownloadedTrackRecord {
        ensureDirectoriesExist()
        let ext = StreamInfoResponse.extensionForMIME(contentType)
        let audioFilename = "\(Self.safeFilename(for: track.id))\(ext)"
        let dest = audioDirectory.appendingPathComponent(audioFilename, isDirectory: false)
        try audioData.write(to: dest, options: .atomic)
        applyBackupExclusion(to: dest)
        return try saveDownloadedTrack(
            track: track,
            audioFilename: audioFilename,
            audioSizeBytes: Int64(audioData.count),
            contentType: contentType,
            streamSource: streamSource,
            artworkData: artworkData,
            lyrics: lyrics
        )
    }

    // MARK: - Internal Helpers

    static func sniffAudioMIME(head: Data, fallback: String) -> String {
        let bytes = [UInt8](head)
        if bytes.count >= 4 {
            // fLaC
            if bytes[0] == 0x66 && bytes[1] == 0x4C && bytes[2] == 0x61 && bytes[3] == 0x43 {
                return "audio/flac"
            }
            // ID3
            if bytes[0] == 0x49 && bytes[1] == 0x44 && bytes[2] == 0x33 {
                return "audio/mpeg"
            }
            // OggS
            if bytes[0] == 0x4F && bytes[1] == 0x67 && bytes[2] == 0x67 && bytes[3] == 0x53 {
                return "audio/ogg"
            }
        }
        if bytes.count >= 8 {
            // MP4/M4A ftyp box at offset 4
            if bytes[4] == 0x66 && bytes[5] == 0x74 && bytes[6] == 0x79 && bytes[7] == 0x70 {
                let fb = StreamInfoResponse.normalizedMIME(fallback)
                return fb == "video/mp4" ? "video/mp4" : "audio/mp4"
            }
        }
        if bytes.count >= 12 {
            // RIFF....WAVE
            if bytes[0] == 0x52 && bytes[1] == 0x49 && bytes[2] == 0x46 && bytes[3] == 0x46
                && bytes[8] == 0x57 && bytes[9] == 0x41 && bytes[10] == 0x56 && bytes[11] == 0x45 {
                return "audio/wav"
            }
        }
        if bytes.count >= 2 && bytes[0] == 0xFF && (bytes[1] & 0xE0) == 0xE0 {
            return "audio/mpeg"
        }
        return StreamInfoResponse.normalizedMIME(fallback)
    }

    private static func safeFilename(for trackId: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_"))
        let mapped = trackId.unicodeScalars.map { allowed.contains($0) ? Character($0) : "_" }
        let cleaned = String(mapped)
        return cleaned.isEmpty ? UUID().uuidString : cleaned
    }

    private static func fetchArtworkData(for track: Track) async -> Data? {
        guard let raw = track.bestCoverURL,
              raw.lowercased().hasPrefix("http://") || raw.lowercased().hasPrefix("https://"),
              let url = URL(string: raw) else {
            return nil
        }
        guard let (data, response) = try? await URLSession.shared.data(from: url),
              let http = response as? HTTPURLResponse,
              (200..<300).contains(http.statusCode),
              !data.isEmpty else {
            return nil
        }
        return data
    }

    private func ensureDirectoriesExist() {
        for dir in [baseDirectory, audioDirectory, artworkDirectory, lyricsDirectory] {
            if !fileManager.fileExists(atPath: dir.path) {
                try? fileManager.createDirectory(at: dir, withIntermediateDirectories: true)
            }
            applyBackupExclusion(to: dir)
        }
    }

    private func applyBackupExclusion(to url: URL) {
        var mutableURL = url
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? mutableURL.setResourceValues(values)
    }

    private func syncPublishedState() {
        let sorted = recordsById.values.sorted { $0.downloadedAt > $1.downloadedAt }
        downloadedTracks = sorted
        downloadedIds = Set(recordsById.keys)
    }

    private func loadIndexFromDisk() {
        guard let data = try? Data(contentsOf: indexFileURL) else {
            recordsById = [:]
            syncPublishedState()
            return
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard let decoded = try? decoder.decode([DownloadedTrackRecord].self, from: data) else {
            recordsById = [:]
            syncPublishedState()
            return
        }

        var valid: [String: DownloadedTrackRecord] = [:]
        for rec in decoded {
            let audioURL = audioDirectory.appendingPathComponent(rec.audioFilename, isDirectory: false)
            if fileManager.fileExists(atPath: audioURL.path) {
                valid[rec.id] = rec
            }
        }
        recordsById = valid
        syncPublishedState()
    }

    private func saveIndexToDisk() {
        ensureDirectoriesExist()
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(downloadedTracks) else { return }
        try? data.write(to: indexFileURL, options: .atomic)
        applyBackupExclusion(to: indexFileURL)
    }
}
