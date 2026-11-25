import Foundation

struct Track: Codable, Identifiable, Equatable, Hashable {
    let id: String
    let source: String?
    let title: String
    let artist: String
    let artistId: String?
    let album: String?
    let albumId: String?
    let duration: Int
    let coverSmall: String?
    let coverLarge: String?
    let explicit: Bool
    let releaseDate: String?
    let previewUrl: String?
    let streamUrl: String?
    var liked: Bool
    let likedAt: Int?
    let position: Int?
    let addedAt: Int?

    enum CodingKeys: String, CodingKey {
        case id, source, title, artist, artistId, album, albumId, duration
        case coverSmall, coverLarge, explicit, releaseDate, previewUrl, streamUrl
        case liked, likedAt, position, addedAt
    }

    init(
        id: String,
        source: String? = nil,
        title: String,
        artist: String,
        artistId: String? = nil,
        album: String? = nil,
        albumId: String? = nil,
        duration: Int = 0,
        coverSmall: String? = nil,
        coverLarge: String? = nil,
        explicit: Bool = false,
        releaseDate: String? = nil,
        previewUrl: String? = nil,
        streamUrl: String? = nil,
        liked: Bool = false,
        likedAt: Int? = nil,
        position: Int? = nil,
        addedAt: Int? = nil
    ) {
        self.id = id
        self.source = source
        self.title = title
        self.artist = artist
        self.artistId = artistId
        self.album = album
        self.albumId = albumId
        self.duration = duration
        self.coverSmall = coverSmall
        self.coverLarge = coverLarge
        self.explicit = explicit
        self.releaseDate = releaseDate
        self.previewUrl = previewUrl
        self.streamUrl = streamUrl
        self.liked = liked
        self.likedAt = likedAt
        self.position = position
        self.addedAt = addedAt
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        source = try c.decodeIfPresent(String.self, forKey: .source)
        title = try c.decode(String.self, forKey: .title)
        artist = try c.decode(String.self, forKey: .artist)
        artistId = try c.decodeIfPresent(String.self, forKey: .artistId)
        album = try c.decodeIfPresent(String.self, forKey: .album)
        albumId = try c.decodeIfPresent(String.self, forKey: .albumId)
        if let intDur = try? c.decodeIfPresent(Int.self, forKey: .duration) {
            duration = intDur
        } else if let dblDur = try? c.decodeIfPresent(Double.self, forKey: .duration) {
            duration = Int(dblDur.rounded())
        } else {
            duration = 0
        }
        coverSmall = try c.decodeIfPresent(String.self, forKey: .coverSmall)
        coverLarge = try c.decodeIfPresent(String.self, forKey: .coverLarge)
        explicit = (try? c.decodeIfPresent(Bool.self, forKey: .explicit)) ?? false
        releaseDate = try c.decodeIfPresent(String.self, forKey: .releaseDate)
        previewUrl = try c.decodeIfPresent(String.self, forKey: .previewUrl)
        streamUrl = try c.decodeIfPresent(String.self, forKey: .streamUrl)
        liked = (try? c.decodeIfPresent(Bool.self, forKey: .liked)) ?? false
        likedAt = try c.decodeIfPresent(Int.self, forKey: .likedAt)
        position = try c.decodeIfPresent(Int.self, forKey: .position)
        addedAt = try c.decodeIfPresent(Int.self, forKey: .addedAt)
    }

    var bestCoverURL: String? {
        if let large = coverLarge, !large.isEmpty { return large }
        if let small = coverSmall, !small.isEmpty { return small }
        return nil
    }
}

struct Artist: Codable, Identifiable, Equatable, Hashable {
    let id: String
    let source: String?
    let name: String
    let picture: String?
    let pictureLarge: String?
    let fans: Int?

    enum CodingKeys: String, CodingKey {
        case id, source, name, picture, pictureLarge, fans
    }

    init(
        id: String,
        source: String? = nil,
        name: String,
        picture: String? = nil,
        pictureLarge: String? = nil,
        fans: Int? = nil
    ) {
        self.id = id
        self.source = source
        self.name = name
        self.picture = picture
        self.pictureLarge = pictureLarge
        self.fans = fans
    }

    var bestImageURL: String? {
        if let large = pictureLarge, !large.isEmpty { return large }
        if let pic = picture, !pic.isEmpty { return pic }
        return nil
    }
}

struct Album: Codable, Identifiable, Equatable, Hashable {
    let id: String
    let source: String?
    let title: String
    let artist: String
    let artistId: String?
    let coverSmall: String?
    let coverLarge: String?
    let releaseDate: String?
    let trackCount: Int?

    enum CodingKeys: String, CodingKey {
        case id, source, title, artist, artistId, coverSmall, coverLarge, releaseDate, trackCount
    }

    init(
        id: String,
        source: String? = nil,
        title: String,
        artist: String,
        artistId: String? = nil,
        coverSmall: String? = nil,
        coverLarge: String? = nil,
        releaseDate: String? = nil,
        trackCount: Int? = nil
    ) {
        self.id = id
        self.source = source
        self.title = title
        self.artist = artist
        self.artistId = artistId
        self.coverSmall = coverSmall
        self.coverLarge = coverLarge
        self.releaseDate = releaseDate
        self.trackCount = trackCount
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        source = try c.decodeIfPresent(String.self, forKey: .source)
        title = try c.decode(String.self, forKey: .title)
        artist = (try? c.decodeIfPresent(String.self, forKey: .artist)) ?? "Unknown Artist"
        artistId = try c.decodeIfPresent(String.self, forKey: .artistId)
        coverSmall = try c.decodeIfPresent(String.self, forKey: .coverSmall)
        coverLarge = try c.decodeIfPresent(String.self, forKey: .coverLarge)
        releaseDate = try c.decodeIfPresent(String.self, forKey: .releaseDate)
        trackCount = try c.decodeIfPresent(Int.self, forKey: .trackCount)
    }

    var bestCoverURL: String? {
        if let large = coverLarge, !large.isEmpty { return large }
        if let small = coverSmall, !small.isEmpty { return small }
        return nil
    }
}

struct HistoryItem: Codable, Identifiable, Equatable {
    let playedAt: Int
    let track: Track

    var id: String { "\(track.id)-\(playedAt)" }
}
