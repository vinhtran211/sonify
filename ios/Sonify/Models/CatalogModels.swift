import Foundation

struct HomeResponse: Decodable, Equatable {
    let mode: String
    let region: String
    let generatedAt: Int
    let sections: [HomeSection]

    enum CodingKeys: String, CodingKey {
        case mode, region, generatedAt, sections
    }

    init(mode: String, region: String, generatedAt: Int, sections: [HomeSection]) {
        self.mode = mode
        self.region = region
        self.generatedAt = generatedAt
        self.sections = sections
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mode = (try? c.decodeIfPresent(String.self, forKey: .mode)) ?? "online"
        region = (try? c.decodeIfPresent(String.self, forKey: .region)) ?? "global"
        generatedAt = (try? c.decodeIfPresent(Int.self, forKey: .generatedAt)) ?? 0
        sections = (try? c.decodeIfPresent([HomeSection].self, forKey: .sections)) ?? []
    }
}

struct HomeSection: Decodable, Identifiable, Equatable {
    let id: String
    let title: String
    let subtitle: String?
    let type: String
    let tracks: [Track]
    let albums: [Album]
    let artists: [Artist]

    enum CodingKeys: String, CodingKey {
        case id, title, subtitle, type, items
    }

    init(
        id: String,
        title: String,
        subtitle: String? = nil,
        type: String,
        tracks: [Track] = [],
        albums: [Album] = [],
        artists: [Artist] = []
    ) {
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.type = type
        self.tracks = tracks
        self.albums = albums
        self.artists = artists
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = (try? c.decodeIfPresent(String.self, forKey: .id)) ?? UUID().uuidString
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? ""
        subtitle = try c.decodeIfPresent(String.self, forKey: .subtitle)
        let sectionType = (try? c.decodeIfPresent(String.self, forKey: .type)) ?? "tracks"
        type = sectionType

        switch sectionType {
        case "albums":
            albums = (try? c.decodeIfPresent([Album].self, forKey: .items)) ?? []
            tracks = []
            artists = []
        case "artists":
            artists = (try? c.decodeIfPresent([Artist].self, forKey: .items)) ?? []
            tracks = []
            albums = []
        default:
            tracks = (try? c.decodeIfPresent([Track].self, forKey: .items)) ?? []
            albums = []
            artists = []
        }
    }

    var isEmpty: Bool {
        tracks.isEmpty && albums.isEmpty && artists.isEmpty
    }
}

enum TopResult: Decodable, Equatable {
    case track(Track)
    case artist(Artist)
    case album(Album)

    enum CodingKeys: String, CodingKey {
        case kind, item
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let kind = try c.decode(String.self, forKey: .kind)
        switch kind {
        case "artist":
            self = .artist(try c.decode(Artist.self, forKey: .item))
        case "album":
            self = .album(try c.decode(Album.self, forKey: .item))
        default:
            self = .track(try c.decode(Track.self, forKey: .item))
        }
    }
}

struct SearchResponse: Decodable, Equatable {
    let query: String
    let mode: String
    let topResult: TopResult?
    let tracks: [Track]
    let artists: [Artist]
    let albums: [Album]

    enum CodingKeys: String, CodingKey {
        case query, mode, topResult, tracks, artists, albums
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        query = (try? c.decodeIfPresent(String.self, forKey: .query)) ?? ""
        mode = (try? c.decodeIfPresent(String.self, forKey: .mode)) ?? "online"
        topResult = try? c.decodeIfPresent(TopResult.self, forKey: .topResult)
        tracks = (try? c.decodeIfPresent([Track].self, forKey: .tracks)) ?? []
        artists = (try? c.decodeIfPresent([Artist].self, forKey: .artists)) ?? []
        albums = (try? c.decodeIfPresent([Album].self, forKey: .albums)) ?? []
    }
}

struct ArtistDetailResponse: Decodable, Equatable {
    let artist: Artist
    let topTracks: [Track]
    let albums: [Album]

    enum CodingKeys: String, CodingKey {
        case artist, topTracks, albums
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        artist = try c.decode(Artist.self, forKey: .artist)
        topTracks = (try? c.decodeIfPresent([Track].self, forKey: .topTracks)) ?? []
        albums = (try? c.decodeIfPresent([Album].self, forKey: .albums)) ?? []
    }
}

struct AlbumDetailResponse: Decodable, Equatable {
    let album: Album
    let tracks: [Track]

    enum CodingKeys: String, CodingKey {
        case album, tracks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        album = try c.decode(Album.self, forKey: .album)
        tracks = (try? c.decodeIfPresent([Track].self, forKey: .tracks)) ?? []
    }
}

// MARK: - Radio, Made For You, Genre Hubs, Playlist Recommendations & Sound Capsule Stats

struct RadioResponse: Decodable, Equatable {
    let seedType: String
    let seedId: String
    let tracks: [Track]

    enum CodingKeys: String, CodingKey {
        case seedType, seedId, tracks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        seedType = (try? c.decodeIfPresent(String.self, forKey: .seedType)) ?? "track"
        seedId = (try? c.decodeIfPresent(String.self, forKey: .seedId)) ?? ""
        tracks = (try? c.decodeIfPresent([Track].self, forKey: .tracks)) ?? []
    }
}

struct MadeForYouMix: Decodable, Identifiable, Equatable, Hashable {
    let id: String
    let title: String
    let subtitle: String
    let color: String
    let cover: String
    let tracks: [Track]

    enum CodingKeys: String, CodingKey {
        case id, title, subtitle, color, gradient, cover, tracks
    }

    init(
        id: String,
        title: String,
        subtitle: String = "",
        color: String = "#1e3264",
        cover: String = "",
        tracks: [Track] = []
    ) {
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.color = color
        self.cover = cover
        self.tracks = tracks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? "Daily Mix"
        subtitle = (try? c.decodeIfPresent(String.self, forKey: .subtitle)) ?? ""
        let gradFirst = ((try? c.decodeIfPresent([String].self, forKey: .gradient)) ?? []).first ?? ""
        let rawColor = (try? c.decodeIfPresent(String.self, forKey: .color)) ?? ""
        color = !rawColor.isEmpty ? rawColor : (!gradFirst.isEmpty ? gradFirst : "#1e3264")
        cover = (try? c.decodeIfPresent(String.self, forKey: .cover)) ?? ""
        tracks = (try? c.decodeIfPresent([Track].self, forKey: .tracks)) ?? []
    }
}

struct MadeForYouResponse: Decodable, Equatable {
    let region: String
    let generatedAt: Int
    let mixes: [MadeForYouMix]

    enum CodingKeys: String, CodingKey {
        case region, generatedAt, mixes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        region = (try? c.decodeIfPresent(String.self, forKey: .region)) ?? "global"
        generatedAt = (try? c.decodeIfPresent(Int.self, forKey: .generatedAt)) ?? 0
        mixes = (try? c.decodeIfPresent([MadeForYouMix].self, forKey: .mixes)) ?? []
    }
}

struct GenreInfo: Decodable, Equatable, Hashable {
    let slug: String
    let name: String
    let color: String

    enum CodingKeys: String, CodingKey {
        case slug, name, color
    }

    init(slug: String, name: String, color: String = "#1e3264") {
        self.slug = slug
        self.name = name
        self.color = color
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        slug = (try? c.decodeIfPresent(String.self, forKey: .slug)) ?? ""
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? ""
        color = (try? c.decodeIfPresent(String.self, forKey: .color)) ?? "#1e3264"
    }
}

struct GenreHubResponse: Decodable, Equatable {
    let genre: GenreInfo
    let tracks: [Track]
    let albums: [Album]
    let artists: [Artist]

    enum CodingKeys: String, CodingKey {
        case genre, tracks, albums, artists
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        genre = try c.decode(GenreInfo.self, forKey: .genre)
        tracks = (try? c.decodeIfPresent([Track].self, forKey: .tracks)) ?? []
        albums = (try? c.decodeIfPresent([Album].self, forKey: .albums)) ?? []
        artists = (try? c.decodeIfPresent([Artist].self, forKey: .artists)) ?? []
    }
}

struct PlaylistRecommendationsResponse: Decodable, Equatable {
    let playlistId: String
    let tracks: [Track]

    enum CodingKeys: String, CodingKey {
        case playlistId, tracks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        playlistId = (try? c.decodeIfPresent(String.self, forKey: .playlistId)) ?? ""
        tracks = (try? c.decodeIfPresent([Track].self, forKey: .tracks)) ?? []
    }
}

struct TrackDetailResponse: Decodable, Equatable {
    let track: Track
    let related: [Track]

    enum CodingKeys: String, CodingKey {
        case track, related, relatedTracks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        track = try c.decode(Track.self, forKey: .track)
        if let relTracks = try? c.decodeIfPresent([Track].self, forKey: .relatedTracks) {
            related = relTracks
        } else {
            related = (try? c.decodeIfPresent([Track].self, forKey: .related)) ?? []
        }
    }
}

struct TopTrackStat: Decodable, Equatable {
    let track: Track
    let playCount: Int
}

struct TopArtistStat: Decodable, Identifiable, Equatable {
    let artist: String
    let artistId: String
    let playCount: Int
    let coverSmall: String

    var id: String { artistId.isEmpty ? artist : artistId }

    enum CodingKeys: String, CodingKey {
        case artist, artistId, playCount, cover, coverSmall
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        artist = (try? c.decodeIfPresent(String.self, forKey: .artist)) ?? "Artist"
        artistId = (try? c.decodeIfPresent(String.self, forKey: .artistId)) ?? ""
        playCount = (try? c.decodeIfPresent(Int.self, forKey: .playCount)) ?? 0
        let cov = (try? c.decodeIfPresent(String.self, forKey: .cover)) ?? ""
        let covSmall = (try? c.decodeIfPresent(String.self, forKey: .coverSmall)) ?? ""
        coverSmall = !cov.isEmpty ? cov : covSmall
    }
}

struct SoundCapsuleStats: Decodable, Equatable {
    let totalPlays: Int
    let totalMinutes: Int
    let likedCount: Int
    let playlistCount: Int
    let followedArtistsCount: Int
    let savedAlbumsCount: Int
    let streakDays: Int
    let mostActiveHour: Int
    let hourlyCounts: [Int]
    let topTracks: [Track]
    let topArtists: [TopArtistStat]

    enum CodingKeys: String, CodingKey {
        case totalPlays, totalMinutes, likedCount, playlistCount
        case followedArtistsCount, savedAlbumsCount, streakDays
        case mostActiveHour, hourlyCounts, topTracks, topArtists
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        totalPlays = (try? c.decodeIfPresent(Int.self, forKey: .totalPlays)) ?? 0
        totalMinutes = (try? c.decodeIfPresent(Int.self, forKey: .totalMinutes)) ?? 0
        likedCount = (try? c.decodeIfPresent(Int.self, forKey: .likedCount)) ?? 0
        playlistCount = (try? c.decodeIfPresent(Int.self, forKey: .playlistCount)) ?? 0
        followedArtistsCount = (try? c.decodeIfPresent(Int.self, forKey: .followedArtistsCount)) ?? 0
        savedAlbumsCount = (try? c.decodeIfPresent(Int.self, forKey: .savedAlbumsCount)) ?? 0
        streakDays = (try? c.decodeIfPresent(Int.self, forKey: .streakDays)) ?? 0
        mostActiveHour = (try? c.decodeIfPresent(Int.self, forKey: .mostActiveHour)) ?? 0
        hourlyCounts = (try? c.decodeIfPresent([Int].self, forKey: .hourlyCounts)) ?? Array(repeating: 0, count: 24)
        if let wrapped = try? c.decodeIfPresent([TopTrackStat].self, forKey: .topTracks) {
            topTracks = wrapped.map(\.track)
        } else {
            topTracks = (try? c.decodeIfPresent([Track].self, forKey: .topTracks)) ?? []
        }
        topArtists = (try? c.decodeIfPresent([TopArtistStat].self, forKey: .topArtists)) ?? []
    }
}
