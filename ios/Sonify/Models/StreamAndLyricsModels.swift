import Foundation

struct APIErrorPayload: Decodable, Equatable {
    let code: String
    let message: String
}

struct APIErrorEnvelope: Decodable, Equatable {
    let error: APIErrorPayload
}

struct HealthResponse: Decodable, Equatable {
    let status: String
    let version: String
    let adFree: Bool
    let dbReady: Bool
    let ytDlpAvailable: Bool
    let ytDlpConfigured: Bool?
    let cloudMode: Bool?
    let renderRegion: String?
    let offlineMode: Bool
    let timestamp: Int
}

struct StreamDiagnostic: Decodable, Equatable {
    let provider: String
    let outcome: String
    let ms: Int
    let detail: String?
}

struct StreamInfoResponse: Decodable, Equatable {
    let trackId: String
    let streamUrl: String
    let streamUrlWithExt: String?
    let streamMode: String
    let streamSource: String
    let contentType: String
    let adFree: Bool
    let diagnostics: [StreamDiagnostic]?
    let cloudMode: Bool?

    var isFullTrack: Bool {
        streamMode == "full"
    }

    var isPreview: Bool {
        streamMode == "preview"
    }

    var qualityBadgeLabel: String {
        if isPreview {
            return "30s Preview"
        }
        if contentType.lowercased().contains("flac") || streamSource == "monochrome" {
            return "Lossless FLAC"
        }
        if streamMode == "offline-synth" {
            return "Offline Demo"
        }
        return "Full Track"
    }

    func resolvedRelativeStreamPath() -> String {
        if let withExt = streamUrlWithExt, !withExt.isEmpty {
            return withExt
        }
        let ext = StreamInfoResponse.extensionForMIME(contentType)
        if streamUrl.lowercased().hasSuffix(ext) {
            return streamUrl
        }
        return streamUrl + ext
    }

    static func normalizedMIME(_ contentType: String) -> String {
        let base = contentType.split(separator: ";").first.map(String.init)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased() ?? "audio/mpeg"
        switch base {
        case "audio/x-flac", "application/flac":
            return "audio/flac"
        case "audio/x-m4a", "audio/x-m4p", "audio/m4a":
            return "audio/mp4"
        case "audio/mp3", "audio/x-mpeg":
            return "audio/mpeg"
        case "audio/x-wav", "audio/wave":
            return "audio/wav"
        default:
            return base.isEmpty ? "audio/mpeg" : base
        }
    }

    static func extensionForMIME(_ contentType: String) -> String {
        let mime = normalizedMIME(contentType)
        if mime.contains("flac") { return ".flac" }
        if mime.hasPrefix("video/mp4") { return ".mp4" }
        if mime.contains("mp4") || mime.contains("aac") { return ".m4a" }
        if mime.contains("mpeg") || mime.contains("mp3") { return ".mp3" }
        if mime.contains("wav") { return ".wav" }
        if mime.contains("webm") { return ".webm" }
        if mime.contains("ogg") { return ".ogg" }
        return ".m4a"
    }
}

struct LyricLine: Codable, Identifiable, Equatable {
    let time: Double
    let text: String
    let interlude: Bool?

    var id: String { "\(time)-\(text)" }

    var isInterlude: Bool {
        interlude == true || text.trimmingCharacters(in: .whitespacesAndNewlines) == "•••"
    }
}

struct LyricsResponse: Decodable, Equatable {
    let trackId: String
    let title: String
    let artist: String
    let status: String
    let source: String
    let instrumental: Bool
    let synced: Bool
    let lines: [LyricLine]
    let plainLyrics: String
    let rawLrc: String

    enum CodingKeys: String, CodingKey {
        case trackId, title, artist, status, source, instrumental, synced, lines, plainLyrics, rawLrc
    }

    init(
        trackId: String = "",
        title: String = "",
        artist: String = "",
        status: String = "not_found",
        source: String = "",
        instrumental: Bool = false,
        synced: Bool = false,
        lines: [LyricLine] = [],
        plainLyrics: String = "",
        rawLrc: String = ""
    ) {
        self.trackId = trackId
        self.title = title
        self.artist = artist
        self.status = status
        self.source = source
        self.instrumental = instrumental
        self.synced = synced
        self.lines = lines
        self.plainLyrics = plainLyrics
        self.rawLrc = rawLrc
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        trackId = (try? c.decodeIfPresent(String.self, forKey: .trackId)) ?? ""
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        artist = (try? c.decodeIfPresent(String.self, forKey: .artist)) ?? ""
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? "not_found"
        source = (try? c.decodeIfPresent(String.self, forKey: .source)) ?? ""
        instrumental = (try? c.decodeIfPresent(Bool.self, forKey: .instrumental)) ?? false
        synced = (try? c.decodeIfPresent(Bool.self, forKey: .synced)) ?? false
        lines = (try? c.decodeIfPresent([LyricLine].self, forKey: .lines)) ?? []
        plainLyrics = (try? c.decodeIfPresent(String.self, forKey: .plainLyrics)) ?? ""
        rawLrc = (try? c.decodeIfPresent(String.self, forKey: .rawLrc)) ?? ""
    }
}
