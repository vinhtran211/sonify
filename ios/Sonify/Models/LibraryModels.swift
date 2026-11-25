import Foundation

struct Playlist: Codable, Identifiable, Equatable, Hashable {
    let id: String
    var name: String
    var description: String
    var coverColor: String
    var coverEmoji: String
    var coverUrl: String
    var trackCount: Int
    var covers: [String]
    let createdAt: Int
    var updatedAt: Int

    enum CodingKeys: String, CodingKey {
        case id, name, description, coverColor, coverEmoji, coverUrl, trackCount, covers, createdAt, updatedAt
    }

    init(
        id: String,
        name: String,
        description: String = "",
        coverColor: String = "",
        coverEmoji: String = "",
        coverUrl: String = "",
        trackCount: Int = 0,
        covers: [String] = [],
        createdAt: Int = 0,
        updatedAt: Int = 0
    ) {
        self.id = id
        self.name = name
        self.description = description
        self.coverColor = coverColor
        self.coverEmoji = coverEmoji
        self.coverUrl = coverUrl
        self.trackCount = trackCount
        self.covers = covers
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        description = (try? c.decodeIfPresent(String.self, forKey: .description)) ?? ""
        coverColor = (try? c.decodeIfPresent(String.self, forKey: .coverColor)) ?? ""
        coverEmoji = (try? c.decodeIfPresent(String.self, forKey: .coverEmoji)) ?? ""
        coverUrl = (try? c.decodeIfPresent(String.self, forKey: .coverUrl)) ?? ""
        trackCount = (try? c.decodeIfPresent(Int.self, forKey: .trackCount)) ?? 0
        covers = (try? c.decodeIfPresent([String].self, forKey: .covers)) ?? []
        createdAt = (try? c.decodeIfPresent(Int.self, forKey: .createdAt)) ?? 0
        updatedAt = (try? c.decodeIfPresent(Int.self, forKey: .updatedAt)) ?? 0
    }
}

struct LikesResponse: Decodable, Equatable {
    let tracks: [Track]
    let likedIds: [String]
}

struct LikeMutationResponse: Decodable, Equatable {
    let liked: Bool
    let trackId: String
}

struct PlaylistsResponse: Decodable, Equatable {
    let playlists: [Playlist]
}

struct PlaylistMutationResponse: Decodable, Equatable {
    let playlist: Playlist
}

struct PlaylistDuplicateResponse: Decodable, Equatable {
    let playlist: Playlist
}

struct PlaylistDetailResponse: Decodable, Equatable {
    let playlist: Playlist
    let tracks: [Track]
}

struct PlaylistDeleteResponse: Decodable, Equatable {
    let deleted: Bool
    let id: String
}

struct PlaylistAddTrackResponse: Decodable, Equatable {
    let playlistId: String
    let trackId: String
    let position: Int
    let trackCount: Int
    let alreadyInPlaylist: Bool
}

struct PlaylistRemoveTrackResponse: Decodable, Equatable {
    let removed: Bool
    let trackCount: Int
}

struct PlaylistReorderResponse: Decodable, Equatable {
    let reordered: Bool
}

struct HistoryResponse: Decodable, Equatable {
    let items: [HistoryItem]
}

struct HistoryRecordResponse: Decodable, Equatable {
    let recorded: Bool
    let trackId: String
}

struct HistoryClearResponse: Decodable, Equatable {
    let cleared: Bool
}

// MARK: - Followed Artists, Saved Albums & Pins

struct FollowedArtist: Codable, Identifiable, Equatable, Hashable {
    let id: String
    let name: String
    let image: String
    let fans: Int
    let followedAt: Int

    enum CodingKeys: String, CodingKey {
        case id, name, image, pictureSmall, pictureLarge, fans, followedAt
    }

    init(id: String, name: String, image: String = "", fans: Int = 0, followedAt: Int = 0) {
        self.id = id
        self.name = name
        self.image = image
        self.fans = fans
        self.followedAt = followedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? "Artist"
        let picLarge = (try? c.decodeIfPresent(String.self, forKey: .pictureLarge)) ?? ""
        let picSmall = (try? c.decodeIfPresent(String.self, forKey: .pictureSmall)) ?? ""
        let img = (try? c.decodeIfPresent(String.self, forKey: .image)) ?? ""
        image = !picLarge.isEmpty ? picLarge : (!picSmall.isEmpty ? picSmall : img)
        fans = (try? c.decodeIfPresent(Int.self, forKey: .fans)) ?? 0
        followedAt = (try? c.decodeIfPresent(Int.self, forKey: .followedAt)) ?? 0
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(name, forKey: .name)
        try c.encode(image, forKey: .pictureSmall)
        try c.encode(image, forKey: .pictureLarge)
        try c.encode(fans, forKey: .fans)
        try c.encode(followedAt, forKey: .followedAt)
    }
}

struct FollowedArtistsResponse: Decodable, Equatable {
    let artists: [FollowedArtist]
    let artistIds: [String]
}

struct FollowMutationResponse: Decodable, Equatable {
    let followed: Bool
    let artistId: String

    var following: Bool { followed }

    enum CodingKeys: String, CodingKey {
        case followed, following, artistId
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let f = try? c.decodeIfPresent(Bool.self, forKey: .followed) {
            followed = f
        } else {
            followed = (try? c.decodeIfPresent(Bool.self, forKey: .following)) ?? false
        }
        artistId = (try? c.decodeIfPresent(String.self, forKey: .artistId)) ?? ""
    }
}

struct SavedAlbum: Codable, Identifiable, Equatable, Hashable {
    let id: String
    let title: String
    let artist: String
    let artistId: String
    let cover: String
    let releaseDate: String
    let savedAt: Int

    enum CodingKeys: String, CodingKey {
        case id, title, artist, artistId, cover, coverSmall, coverLarge, releaseDate, savedAt
    }

    init(
        id: String,
        title: String,
        artist: String = "",
        artistId: String = "",
        cover: String = "",
        releaseDate: String = "",
        savedAt: Int = 0
    ) {
        self.id = id
        self.title = title
        self.artist = artist
        self.artistId = artistId
        self.cover = cover
        self.releaseDate = releaseDate
        self.savedAt = savedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? "Album"
        artist = (try? c.decodeIfPresent(String.self, forKey: .artist)) ?? ""
        artistId = (try? c.decodeIfPresent(String.self, forKey: .artistId)) ?? ""
        let cLarge = (try? c.decodeIfPresent(String.self, forKey: .coverLarge)) ?? ""
        let cSmall = (try? c.decodeIfPresent(String.self, forKey: .coverSmall)) ?? ""
        let rawCov = (try? c.decodeIfPresent(String.self, forKey: .cover)) ?? ""
        cover = !cLarge.isEmpty ? cLarge : (!cSmall.isEmpty ? cSmall : rawCov)
        releaseDate = (try? c.decodeIfPresent(String.self, forKey: .releaseDate)) ?? ""
        savedAt = (try? c.decodeIfPresent(Int.self, forKey: .savedAt)) ?? 0
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(title, forKey: .title)
        try c.encode(artist, forKey: .artist)
        try c.encode(artistId, forKey: .artistId)
        try c.encode(cover, forKey: .coverSmall)
        try c.encode(cover, forKey: .coverLarge)
        try c.encode(releaseDate, forKey: .releaseDate)
        try c.encode(savedAt, forKey: .savedAt)
    }
}

struct SavedAlbumsResponse: Decodable, Equatable {
    let albums: [SavedAlbum]
    let albumIds: [String]
}

struct SaveAlbumMutationResponse: Decodable, Equatable {
    let saved: Bool
    let albumId: String
}

struct PinnedItem: Codable, Identifiable, Equatable, Hashable {
    let itemType: String
    let itemId: String
    let pinnedAt: Int

    var id: String { "\(itemType):\(itemId)" }
}

struct PinsResponse: Decodable, Equatable {
    let pins: [PinnedItem]
}

struct PinMutationResponse: Decodable, Equatable {
    let pinned: Bool
    let itemType: String
    let itemId: String
}

enum RepeatMode: String, Codable, CaseIterable, Equatable {
    case off
    case all
    case one

    var next: RepeatMode {
        switch self {
        case .off: return .all
        case .all: return .one
        case .one: return .off
        }
    }
}

struct AppSettings: Codable, Equatable {
    var volume: Double
    var muted: Bool
    var shuffle: Bool
    var repeatMode: RepeatMode
    var region: String
    var lastTrackId: String?
    var lastPosition: Double
    var autoplay: Bool
    var crossfade: Int
    var playbackRate: Double
    var audioQuality: String
    var eqPreset: String
    var eqBands: [Int]

    enum CodingKeys: String, CodingKey {
        case volume
        case muted
        case shuffle
        case repeatMode = "repeat"
        case region
        case lastTrackId
        case lastPosition
        case autoplay
        case crossfade
        case playbackRate
        case audioQuality
        case eqPreset
        case eqBands
    }

    init(
        volume: Double = 0.85,
        muted: Bool = false,
        shuffle: Bool = false,
        repeatMode: RepeatMode = .off,
        region: String = "global",
        lastTrackId: String? = nil,
        lastPosition: Double = 0,
        autoplay: Bool = true,
        crossfade: Int = 0,
        playbackRate: Double = 1.0,
        audioQuality: String = "auto",
        eqPreset: String = "flat",
        eqBands: [Int] = [0, 0, 0, 0, 0]
    ) {
        self.volume = volume
        self.muted = muted
        self.shuffle = shuffle
        self.repeatMode = repeatMode
        self.region = region
        self.lastTrackId = lastTrackId
        self.lastPosition = lastPosition
        self.autoplay = autoplay
        self.crossfade = crossfade
        self.playbackRate = playbackRate
        self.audioQuality = audioQuality
        self.eqPreset = eqPreset
        self.eqBands = eqBands
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        volume = (try? c.decodeIfPresent(Double.self, forKey: .volume)) ?? 0.85
        muted = (try? c.decodeIfPresent(Bool.self, forKey: .muted)) ?? false
        shuffle = (try? c.decodeIfPresent(Bool.self, forKey: .shuffle)) ?? false
        repeatMode = (try? c.decodeIfPresent(RepeatMode.self, forKey: .repeatMode)) ?? .off
        region = (try? c.decodeIfPresent(String.self, forKey: .region)) ?? "global"
        lastTrackId = try? c.decodeIfPresent(String.self, forKey: .lastTrackId)
        lastPosition = (try? c.decodeIfPresent(Double.self, forKey: .lastPosition)) ?? 0
        autoplay = (try? c.decodeIfPresent(Bool.self, forKey: .autoplay)) ?? true
        crossfade = (try? c.decodeIfPresent(Int.self, forKey: .crossfade)) ?? 0
        playbackRate = (try? c.decodeIfPresent(Double.self, forKey: .playbackRate)) ?? 1.0
        audioQuality = (try? c.decodeIfPresent(String.self, forKey: .audioQuality)) ?? "auto"
        eqPreset = (try? c.decodeIfPresent(String.self, forKey: .eqPreset)) ?? "flat"
        if let intBands = try? c.decodeIfPresent([Int].self, forKey: .eqBands) {
            eqBands = intBands
        } else if let dblBands = try? c.decodeIfPresent([Double].self, forKey: .eqBands) {
            eqBands = dblBands.map { Int($0.rounded()) }
        } else {
            eqBands = [0, 0, 0, 0, 0]
        }
    }
}
