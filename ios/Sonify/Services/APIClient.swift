import Combine
import Foundation

enum SonifyAPIError: Error, LocalizedError, Equatable {
    case invalidURL(String)
    case httpError(status: Int, code: String, message: String)
    case decodingError(String)
    case networkError(String)

    var errorDescription: String? {
        switch self {
        case .invalidURL(let url):
            return "Invalid server URL: \(url)"
        case .httpError(_, _, let message):
            return message
        case .decodingError(let detail):
            return "Unable to read server response (\(detail))"
        case .networkError(let detail):
            return detail
        }
    }
}

final class APIClient: ObservableObject, @unchecked Sendable {
    static let defaultServerURL = "https://sonify-music.onrender.com"
    private static let serverURLDefaultsKey = "sonify.serverBaseURL"
    private static let pathSegmentAllowed: CharacterSet = {
        var cs = CharacterSet.urlPathAllowed
        cs.remove(charactersIn: "/:?#")
        return cs
    }()

    @Published private(set) var baseURLString: String
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init(baseURLString: String? = nil, session: URLSession = .shared) {
        let saved = UserDefaults.standard.string(forKey: Self.serverURLDefaultsKey)
        self.baseURLString = Self.sanitizeBaseURL(baseURLString ?? saved ?? Self.defaultServerURL)
        self.session = session
        self.decoder = JSONDecoder()
        self.encoder = JSONEncoder()
    }

    static func sanitizeBaseURL(_ raw: String) -> String {
        var trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while trimmed.hasSuffix("/") {
            trimmed.removeLast()
        }
        if trimmed.isEmpty {
            return defaultServerURL
        }
        if !trimmed.lowercased().hasPrefix("http://") && !trimmed.lowercased().hasPrefix("https://") {
            trimmed = "https://" + trimmed
        }
        return trimmed
    }

    func updateBaseURL(_ raw: String) {
        let sanitized = Self.sanitizeBaseURL(raw)
        UserDefaults.standard.set(sanitized, forKey: Self.serverURLDefaultsKey)
        DispatchQueue.main.async {
            self.baseURLString = sanitized
        }
    }

    func resetBaseURL() {
        updateBaseURL(Self.defaultServerURL)
    }

    static func encodePathSegment(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: pathSegmentAllowed) ?? value
    }

    func streamURL(for info: StreamInfoResponse) -> URL? {
        let rawRelative = info.resolvedRelativeStreamPath()
        let prefix = "/api/stream/"
        let encodedPath: String
        if rawRelative.hasPrefix(prefix) {
            let tail = String(rawRelative.dropFirst(prefix.count))
            encodedPath = prefix + Self.encodePathSegment(tail)
        } else {
            encodedPath = rawRelative
        }
        return URL(string: baseURLString + encodedPath)
    }

    // MARK: - Core Request Engine

    private func makeURL(path: String, queryItems: [URLQueryItem] = []) throws -> URL {
        guard var components = URLComponents(string: baseURLString + path) else {
            throw SonifyAPIError.invalidURL(baseURLString + path)
        }
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        guard let url = components.url else {
            throw SonifyAPIError.invalidURL(baseURLString + path)
        }
        return url
    }

    private func send<T: Decodable>(
        path: String,
        method: String = "GET",
        queryItems: [URLQueryItem] = [],
        body: Data? = nil,
        timeout: TimeInterval = 25
    ) async throws -> T {
        let url = try makeURL(path: path, queryItems: queryItems)
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json; charset=utf-8", forHTTPHeaderField: "Content-Type")
        }

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch let urlErr as URLError where urlErr.code == .cancelled {
            throw CancellationError()
        } catch {
            throw SonifyAPIError.networkError(error.localizedDescription)
        }

        guard let httpResponse = response as? HTTPURLResponse else {
            throw SonifyAPIError.networkError("Invalid server response")
        }

        guard (200..<300).contains(httpResponse.statusCode) else {
            if let envelope = try? decoder.decode(APIErrorEnvelope.self, from: data) {
                throw SonifyAPIError.httpError(
                    status: httpResponse.statusCode,
                    code: envelope.error.code,
                    message: envelope.error.message
                )
            }
            throw SonifyAPIError.httpError(
                status: httpResponse.statusCode,
                code: "HTTP_\(httpResponse.statusCode)",
                message: "Server returned HTTP \(httpResponse.statusCode)"
            )
        }

        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw SonifyAPIError.decodingError(error.localizedDescription)
        }
    }

    private func encodeJSON<U: Encodable>(_ value: U) throws -> Data {
        try encoder.encode(value)
    }

    // MARK: - Health & Catalog

    func health() async throws -> HealthResponse {
        try await send(path: "/api/health", timeout: 15)
    }

    func getHome(region: String = "global", refresh: Bool = false) async throws -> HomeResponse {
        try await send(
            path: "/api/home",
            queryItems: [
                URLQueryItem(name: "region", value: region),
                URLQueryItem(name: "refresh", value: refresh ? "1" : "0"),
            ]
        )
    }

    func search(query: String, type: String = "all", limit: Int = 20) async throws -> SearchResponse {
        try await send(
            path: "/api/search",
            queryItems: [
                URLQueryItem(name: "q", value: query),
                URLQueryItem(name: "type", value: type),
                URLQueryItem(name: "limit", value: String(limit)),
            ]
        )
    }

    func getArtist(id: String) async throws -> ArtistDetailResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(path: "/api/artist/\(encoded)")
    }

    func getAlbum(id: String) async throws -> AlbumDetailResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(path: "/api/album/\(encoded)")
    }

    func getTrackDetail(id: String) async throws -> TrackDetailResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(path: "/api/track/\(encoded)")
    }

    func getRadio(trackId: String? = nil, artistId: String? = nil, limit: Int = 18) async throws -> RadioResponse {
        var items = [URLQueryItem(name: "limit", value: String(limit))]
        if let trackId, !trackId.isEmpty {
            items.append(URLQueryItem(name: "trackId", value: trackId))
        }
        if let artistId, !artistId.isEmpty {
            items.append(URLQueryItem(name: "artistId", value: artistId))
        }
        return try await send(path: "/api/radio", queryItems: items)
    }

    func getMadeForYou(region: String = "global") async throws -> MadeForYouResponse {
        try await send(
            path: "/api/made-for-you",
            queryItems: [URLQueryItem(name: "region", value: region)]
        )
    }

    func getGenreHub(slug: String, region: String = "global") async throws -> GenreHubResponse {
        let encoded = Self.encodePathSegment(slug)
        return try await send(
            path: "/api/genre/\(encoded)",
            queryItems: [URLQueryItem(name: "region", value: region)]
        )
    }

    func getStats() async throws -> SoundCapsuleStats {
        try await send(path: "/api/stats")
    }

    // MARK: - Stream & Lyrics

    func getStreamInfo(
        trackId: String,
        debug: Bool = false,
        quality: String? = nil,
        prewarm: Bool = false
    ) async throws -> StreamInfoResponse {
        let encoded = Self.encodePathSegment(trackId)
        var items: [URLQueryItem] = []
        if debug {
            items.append(URLQueryItem(name: "debug", value: "1"))
        }
        if let quality, !quality.isEmpty, quality != "auto" {
            items.append(URLQueryItem(name: "quality", value: quality))
        }
        if prewarm {
            items.append(URLQueryItem(name: "prewarm", value: "1"))
        }
        return try await send(path: "/api/stream/\(encoded)/info", queryItems: items, timeout: 30)
    }

    func getLyrics(for track: Track, refresh: Bool = false) async throws -> LyricsResponse {
        var items = [
            URLQueryItem(name: "trackId", value: track.id),
            URLQueryItem(name: "title", value: track.title),
            URLQueryItem(name: "artist", value: track.artist),
        ]
        if let album = track.album, !album.isEmpty {
            items.append(URLQueryItem(name: "album", value: album))
        }
        if track.duration > 0 {
            items.append(URLQueryItem(name: "duration", value: String(track.duration)))
        }
        if refresh {
            items.append(URLQueryItem(name: "refresh", value: "1"))
        }
        return try await send(path: "/api/lyrics", queryItems: items, timeout: 20)
    }

    // MARK: - Likes, Followed Artists, Saved Albums & Pins

    private struct TrackBody: Encodable {
        let track: Track
    }

    private struct ArtistBody: Encodable {
        let artist: FollowedArtist
    }

    private struct AlbumBody: Encodable {
        let album: SavedAlbum
    }

    private struct PinBody: Encodable {
        let itemType: String
        let itemId: String
    }

    func getLikes() async throws -> LikesResponse {
        try await send(path: "/api/library/likes")
    }

    @discardableResult
    func likeTrack(_ track: Track) async throws -> LikeMutationResponse {
        try await send(
            path: "/api/library/likes",
            method: "POST",
            body: try encodeJSON(TrackBody(track: track))
        )
    }

    @discardableResult
    func unlikeTrack(id: String) async throws -> LikeMutationResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(path: "/api/library/likes/\(encoded)", method: "DELETE")
    }

    func getFollowedArtists() async throws -> FollowedArtistsResponse {
        try await send(path: "/api/library/artists")
    }

    @discardableResult
    func followArtist(_ artist: FollowedArtist) async throws -> FollowMutationResponse {
        try await send(
            path: "/api/library/artists",
            method: "POST",
            body: try encodeJSON(ArtistBody(artist: artist))
        )
    }

    @discardableResult
    func unfollowArtist(id: String) async throws -> FollowMutationResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(path: "/api/library/artists/\(encoded)", method: "DELETE")
    }

    func getSavedAlbums() async throws -> SavedAlbumsResponse {
        try await send(path: "/api/library/albums")
    }

    @discardableResult
    func saveAlbum(_ album: SavedAlbum) async throws -> SaveAlbumMutationResponse {
        try await send(
            path: "/api/library/albums",
            method: "POST",
            body: try encodeJSON(AlbumBody(album: album))
        )
    }

    @discardableResult
    func unsaveAlbum(id: String) async throws -> SaveAlbumMutationResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(path: "/api/library/albums/\(encoded)", method: "DELETE")
    }

    func getPins() async throws -> PinsResponse {
        try await send(path: "/api/library/pins")
    }

    @discardableResult
    func pinItem(itemType: String, itemId: String) async throws -> PinMutationResponse {
        try await send(
            path: "/api/library/pins",
            method: "POST",
            body: try encodeJSON(PinBody(itemType: itemType, itemId: itemId))
        )
    }

    @discardableResult
    func unpinItem(itemType: String, itemId: String) async throws -> PinMutationResponse {
        let encType = Self.encodePathSegment(itemType)
        let encId = Self.encodePathSegment(itemId)
        return try await send(path: "/api/library/pins/\(encType)/\(encId)", method: "DELETE")
    }

    // MARK: - Playlists

    private struct PlaylistCreateBody: Encodable {
        let name: String
        let description: String
    }

    private struct PlaylistPatchBody: Encodable {
        let name: String?
        let description: String?
        let coverColor: String?
        let coverEmoji: String?
        let coverUrl: String?
    }

    private struct ReorderBody: Encodable {
        let trackIds: [String]
    }

    func listPlaylists() async throws -> PlaylistsResponse {
        try await send(path: "/api/playlists")
    }

    func createPlaylist(name: String, description: String = "") async throws -> PlaylistMutationResponse {
        try await send(
            path: "/api/playlists",
            method: "POST",
            body: try encodeJSON(PlaylistCreateBody(name: name, description: description))
        )
    }

    func getPlaylist(id: String) async throws -> PlaylistDetailResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(path: "/api/playlists/\(encoded)")
    }

    func updatePlaylist(
        id: String,
        name: String?,
        description: String?,
        coverColor: String? = nil,
        coverEmoji: String? = nil,
        coverUrl: String? = nil
    ) async throws -> PlaylistMutationResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(
            path: "/api/playlists/\(encoded)",
            method: "PATCH",
            body: try encodeJSON(
                PlaylistPatchBody(
                    name: name,
                    description: description,
                    coverColor: coverColor,
                    coverEmoji: coverEmoji,
                    coverUrl: coverUrl
                )
            )
        )
    }

    func duplicatePlaylist(id: String) async throws -> PlaylistDuplicateResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(path: "/api/playlists/\(encoded)/duplicate", method: "POST")
    }

    func getPlaylistRecommendations(id: String, limit: Int = 10) async throws -> PlaylistRecommendationsResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(
            path: "/api/playlists/\(encoded)/recommendations",
            queryItems: [URLQueryItem(name: "limit", value: String(limit))]
        )
    }

    @discardableResult
    func deletePlaylist(id: String) async throws -> PlaylistDeleteResponse {
        let encoded = Self.encodePathSegment(id)
        return try await send(path: "/api/playlists/\(encoded)", method: "DELETE")
    }

    @discardableResult
    func addTrackToPlaylist(playlistId: String, track: Track) async throws -> PlaylistAddTrackResponse {
        let encoded = Self.encodePathSegment(playlistId)
        return try await send(
            path: "/api/playlists/\(encoded)/tracks",
            method: "POST",
            body: try encodeJSON(TrackBody(track: track))
        )
    }

    @discardableResult
    func removeTrackFromPlaylist(playlistId: String, trackId: String) async throws -> PlaylistRemoveTrackResponse {
        let encP = Self.encodePathSegment(playlistId)
        let encT = Self.encodePathSegment(trackId)
        return try await send(path: "/api/playlists/\(encP)/tracks/\(encT)", method: "DELETE")
    }

    @discardableResult
    func reorderPlaylistTracks(playlistId: String, trackIds: [String]) async throws -> PlaylistReorderResponse {
        let encoded = Self.encodePathSegment(playlistId)
        return try await send(
            path: "/api/playlists/\(encoded)/tracks/reorder",
            method: "PATCH",
            body: try encodeJSON(ReorderBody(trackIds: trackIds))
        )
    }

    // MARK: - History & Settings

    func getHistory(limit: Int = 50) async throws -> HistoryResponse {
        try await send(path: "/api/history", queryItems: [URLQueryItem(name: "limit", value: String(limit))])
    }

    @discardableResult
    func recordHistory(track: Track) async throws -> HistoryRecordResponse {
        try await send(
            path: "/api/history",
            method: "POST",
            body: try encodeJSON(TrackBody(track: track))
        )
    }

    @discardableResult
    func clearHistory() async throws -> HistoryClearResponse {
        try await send(path: "/api/history", method: "DELETE")
    }

    func getSettings() async throws -> AppSettings {
        try await send(path: "/api/settings")
    }

    @discardableResult
    func patchSettings(_ settings: AppSettings) async throws -> AppSettings {
        try await send(
            path: "/api/settings",
            method: "PATCH",
            body: try encodeJSON(settings)
        )
    }
}
