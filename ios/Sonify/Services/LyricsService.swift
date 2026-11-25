import Combine
import Foundation

enum LyricsSyncEngine {
    /// Binary-search active lyric line index for `currentTime` (seconds).
    /// Returns `nil` before the first timestamp or when `lines` is empty.
    static func activeLineIndex(in lines: [LyricLine], at currentTime: Double) -> Int? {
        guard !lines.isEmpty else { return nil }
        let t = currentTime + 0.08
        if t < lines[0].time {
            return nil
        }

        var low = 0
        var high = lines.count - 1
        var result = 0
        while low <= high {
            let mid = low + (high - low) / 2
            if lines[mid].time <= t {
                result = mid
                low = mid + 1
            } else {
                high = mid - 1
            }
        }
        return result
    }

    /// Parses standard `.lrc` text (supporting multi-timestamp lines and `[offset:ms]`).
    static func parseLRC(_ rawLrc: String) -> [LyricLine] {
        guard !rawLrc.isEmpty else { return [] }
        var offsetSeconds: Double = 0.0
        var entries: [(Double, String)] = []

        let tagPattern = #"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]"#
        let offsetPattern = #"^\s*\[offset\s*:\s*([+-]?\d+)\s*\]\s*$"#
        let tagRegex = try? NSRegularExpression(pattern: tagPattern)
        let offsetRegex = try? NSRegularExpression(pattern: offsetPattern, options: [.caseInsensitive])

        for rawLine in rawLrc.components(separatedBy: .newlines) {
            let nsLine = rawLine as NSString
            let fullRange = NSRange(location: 0, length: nsLine.length)

            if let match = offsetRegex?.firstMatch(in: rawLine, range: fullRange),
               match.numberOfRanges >= 2 {
                let valStr = nsLine.substring(with: match.range(at: 1))
                if let ms = Double(valStr) {
                    offsetSeconds = ms / 1000.0
                }
                continue
            }

            guard let matches = tagRegex?.matches(in: rawLine, range: fullRange), !matches.isEmpty else {
                continue
            }

            let text = tagRegex?
                .stringByReplacingMatches(in: rawLine, range: fullRange, withTemplate: "")
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""

            for m in matches {
                let mins = Double(nsLine.substring(with: m.range(at: 1))) ?? 0
                let secs = Double(nsLine.substring(with: m.range(at: 2))) ?? 0
                var frac = 0.0
                if m.range(at: 3).location != NSNotFound {
                    let fracStr = nsLine.substring(with: m.range(at: 3))
                    if fracStr.count == 1 {
                        frac = (Double(fracStr) ?? 0) / 10.0
                    } else if fracStr.count == 2 {
                        frac = (Double(fracStr) ?? 0) / 100.0
                    } else {
                        frac = (Double(fracStr) ?? 0) / 1000.0
                    }
                }
                let ts = max(0.0, mins * 60.0 + secs + frac)
                entries.append((ts, text))
            }
        }

        let shifted = entries.map { (max(0.0, $0.0 + offsetSeconds), $0.1) }
            .sorted { $0.0 < $1.0 }

        return shifted.map { ts, text in
            if text.isEmpty {
                return LyricLine(time: ts, text: "•••", interlude: true)
            }
            return LyricLine(time: ts, text: text, interlude: nil)
        }
    }
}

@MainActor
final class LyricsStore: ObservableObject {
    @Published private(set) var lyrics: LyricsResponse?
    @Published private(set) var resolvedLines: [LyricLine] = []
    @Published private(set) var isLoading: Bool = false
    @Published private(set) var loadedTrackId: String?

    private let api: APIClient
    private weak var offlineDownloads: OfflineDownloadManager?
    private var fetchTask: Task<Void, Never>?

    init(api: APIClient, offlineDownloads: OfflineDownloadManager? = nil) {
        self.api = api
        self.offlineDownloads = offlineDownloads
    }

    func attachOfflineDownloads(_ manager: OfflineDownloadManager) {
        self.offlineDownloads = manager
    }

    private func applyLyricsResponse(_ response: LyricsResponse) {
        self.lyrics = response
        if !response.lines.isEmpty {
            self.resolvedLines = response.lines
        } else if !response.rawLrc.isEmpty {
            self.resolvedLines = LyricsSyncEngine.parseLRC(response.rawLrc)
        } else {
            self.resolvedLines = []
        }
        self.isLoading = false
    }

    func loadLyrics(for track: Track?, forceRefresh: Bool = false) {
        fetchTask?.cancel()
        guard let track else {
            lyrics = nil
            resolvedLines = []
            loadedTrackId = nil
            isLoading = false
            return
        }
        if !forceRefresh && loadedTrackId == track.id && lyrics != nil {
            return
        }

        loadedTrackId = track.id

        // 1. Offline-first: check cached offline lyrics immediately (0ms network wait, works in Airplane Mode)
        if !forceRefresh, let cached = offlineDownloads?.localLyrics(for: track.id) {
            applyLyricsResponse(cached)
            return
        }

        if offlineDownloads?.offlineModeEnabled == true {
            lyrics = LyricsResponse(
                trackId: track.id,
                title: track.title,
                artist: track.artist,
                status: "not_found"
            )
            resolvedLines = []
            isLoading = false
            return
        }

        isLoading = true

        fetchTask = Task { [weak self] in
            guard let self else { return }
            do {
                let response = try await api.getLyrics(for: track, refresh: forceRefresh)
                guard !Task.isCancelled, self.loadedTrackId == track.id else { return }
                self.applyLyricsResponse(response)
            } catch {
                guard !Task.isCancelled, self.loadedTrackId == track.id else { return }
                if let fallbackCached = self.offlineDownloads?.localLyrics(for: track.id) {
                    self.applyLyricsResponse(fallbackCached)
                    return
                }
                self.lyrics = LyricsResponse(
                    trackId: track.id,
                    title: track.title,
                    artist: track.artist,
                    status: "not_found"
                )
                self.resolvedLines = []
                self.isLoading = false
            }
        }
    }
}
