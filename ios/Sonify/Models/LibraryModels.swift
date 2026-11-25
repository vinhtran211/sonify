import Foundation

struct Playlist: Codable, Identifiable, Equatable, Hashable {
    let id: String
    var name: String
    var description: String
    var trackCount: Int
    var covers: [String]
    let createdAt: Int
    var updatedAt: Int

    enum CodingKeys: String, CodingKey {
        case id, name, description, trackCount, covers, createdAt, updatedAt
    }

    init(
        id: String,
        name: String,
        description: String = "",
        trackCount: Int = 0,
        covers: [String] = [],
        createdAt: Int = 0,
        updatedAt: Int = 0
    ) {
        self.id = id
        self.name = name
        self.description = description
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

    enum CodingKeys: String, CodingKey {
        case volume
        case muted
        case shuffle
        case repeatMode = "repeat"
        case region
        case lastTrackId
        case lastPosition
    }

    init(
        volume: Double = 0.85,
        muted: Bool = false,
        shuffle: Bool = false,
        repeatMode: RepeatMode = .off,
        region: String = "global",
        lastTrackId: String? = nil,
        lastPosition: Double = 0
    ) {
        self.volume = volume
        self.muted = muted
        self.shuffle = shuffle
        self.repeatMode = repeatMode
        self.region = region
        self.lastTrackId = lastTrackId
        self.lastPosition = lastPosition
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
    }
}
