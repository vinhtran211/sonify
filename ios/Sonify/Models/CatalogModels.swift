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
