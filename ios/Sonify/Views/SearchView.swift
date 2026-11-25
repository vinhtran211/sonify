import SwiftUI

struct SearchView: View {
    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var player: AudioPlayerService

    @State private var queryText = ""
    @State private var selectedFilter = "all"
    @State private var response: SearchResponse?
    @State private var isSearching = false
    @State private var errorMessage: String?
    @State private var searchTask: Task<Void, Never>?
    @State private var playlistTargetTrack: Track?

    private let filters: [(String, String)] = [
        ("all", "All"),
        ("tracks", "Songs"),
        ("artists", "Artists"),
        ("albums", "Albums"),
    ]

    private let moodTiles: [(String, String, Color)] = [
        ("V-Pop Hits", "Sơn Tùng M-TP", Color(hex: 0xE8115B)),
        ("Pop Essentials", "Taylor Swift", Color(hex: 0x1E3264)),
        ("R&B & Chill", "The Weeknd", Color(hex: 0x8D67AB)),
        ("K-Pop", "BLACKPINK", Color(hex: 0xE13300)),
        ("Indie & Synth", "Tame Impala", Color(hex: 0x148A08)),
        ("Vietnam Rap", "HIEUTHUHAI", Color(hex: 0xBC5900)),
        ("Ballad", "Bruno Mars", Color(hex: 0x503750)),
        ("Electronic", "Daft Punk", Color(hex: 0x0D73EC)),
    ]

    var body: some View {
        NavigationStack {
            ZStack {
                SonifyTheme.bgSurface.ignoresSafeArea()

                VStack(spacing: 10) {
                    searchBar

                    if !queryText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        filterChips
                    }

                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 18) {
                            if queryText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                browseMoodGrid
                            } else if isSearching && response == nil {
                                ProgressView()
                                    .tint(SonifyTheme.accentGreen)
                                    .frame(maxWidth: .infinity)
                                    .padding(.top, 60)
                            } else if let errorMessage {
                                Text(errorMessage)
                                    .font(.subheadline)
                                    .foregroundColor(SonifyTheme.danger)
                                    .padding()
                            } else if let response {
                                resultsContent(response)
                            }

                            Color.clear.frame(height: 88)
                        }
                        .padding(.horizontal)
                        .padding(.top, 6)
                    }
                }
            }
            .navigationTitle("Search")
            .navigationBarTitleDisplayMode(.large)
            .sheet(item: $playlistTargetTrack) { track in
                AddToPlaylistSheet(track: track)
            }
        }
    }

    private var searchBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .foregroundColor(SonifyTheme.textSecondary)
            TextField("Songs, artists, or albums", text: $queryText)
                .textFieldStyle(.plain)
                .foregroundColor(SonifyTheme.textPrimary)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .onSubmit {
                    triggerSearch(immediate: true)
                }
            if !queryText.isEmpty {
                Button {
                    queryText = ""
                    response = nil
                    errorMessage = nil
                    searchTask?.cancel()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(SonifyTheme.textSecondary)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(SonifyTheme.bgHighlight)
        .cornerRadius(10)
        .padding(.horizontal)
        .onChange(of: queryText) { _ in
            triggerSearch(immediate: false)
        }
    }

    private var filterChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(filters, id: \.0) { code, label in
                    let active = selectedFilter == code
                    Button {
                        selectedFilter = code
                        triggerSearch(immediate: true)
                    } label: {
                        Text(label)
                            .font(.caption.weight(.semibold))
                            .foregroundColor(active ? .black : SonifyTheme.textPrimary)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 7)
                            .background(active ? SonifyTheme.accentGreen : SonifyTheme.bgHighlight)
                            .clipShape(Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal)
        }
    }

    private var browseMoodGrid: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Browse All")
                .font(.headline)
                .foregroundColor(SonifyTheme.textPrimary)

            LazyVGrid(
                columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)],
                spacing: 12
            ) {
                ForEach(moodTiles, id: \.0) { title, sampleQuery, color in
                    Button {
                        queryText = sampleQuery
                        triggerSearch(immediate: true)
                    } label: {
                        ZStack(alignment: .topLeading) {
                            RoundedRectangle(cornerRadius: 10, style: .continuous)
                                .fill(
                                    LinearGradient(
                                        colors: [color, color.opacity(0.65)],
                                        startPoint: .topLeading,
                                        endPoint: .bottomTrailing
                                    )
                                )
                                .frame(height: 86)
                            Text(title)
                                .font(.headline.weight(.bold))
                                .foregroundColor(.white)
                                .padding(12)
                        }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    @ViewBuilder
    private func resultsContent(_ res: SearchResponse) -> some View {
        if res.tracks.isEmpty && res.artists.isEmpty && res.albums.isEmpty {
            VStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .font(.largeTitle)
                    .foregroundColor(SonifyTheme.textSubdued)
                Text("No results for \"\(res.query)\"")
                    .font(.headline)
                    .foregroundColor(SonifyTheme.textPrimary)
                Text("Try checking spelling or searching another artist.")
                    .font(.caption)
                    .foregroundColor(SonifyTheme.textSecondary)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 60)
        } else {
            if selectedFilter == "all", let top = res.topResult {
                topResultCard(top, trackContext: res.tracks)
            }

            if (selectedFilter == "all" || selectedFilter == "tracks") && !res.tracks.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Songs")
                        .font(.headline)
                        .foregroundColor(SonifyTheme.textPrimary)
                    ForEach(res.tracks) { track in
                        TrackRowView(
                            track: track,
                            onTap: { player.play(track: track, context: res.tracks) },
                            onAddToPlaylist: { playlistTargetTrack = $0 }
                        )
                    }
                }
            }

            if (selectedFilter == "all" || selectedFilter == "artists") && !res.artists.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Artists")
                        .font(.headline)
                        .foregroundColor(SonifyTheme.textPrimary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        LazyHStack(spacing: 14) {
                            ForEach(res.artists) { artist in
                                NavigationLink {
                                    ArtistDetailView(artistId: artist.id, initialArtist: artist)
                                } label: {
                                    VStack(spacing: 6) {
                                        ArtworkView(urlString: artist.bestImageURL, size: 112, isCircular: true, symbolName: "person.fill")
                                        Text(artist.name)
                                            .font(.caption.weight(.semibold))
                                            .foregroundColor(SonifyTheme.textPrimary)
                                            .lineLimit(1)
                                    }
                                    .frame(width: 112)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
            }

            if (selectedFilter == "all" || selectedFilter == "albums") && !res.albums.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Albums")
                        .font(.headline)
                        .foregroundColor(SonifyTheme.textPrimary)
                    ScrollView(.horizontal, showsIndicators: false) {
                        LazyHStack(spacing: 14) {
                            ForEach(res.albums) { album in
                                NavigationLink {
                                    AlbumDetailView(albumId: album.id, initialAlbum: album)
                                } label: {
                                    VStack(alignment: .leading, spacing: 6) {
                                        ArtworkView(urlString: album.bestCoverURL, size: 124, cornerRadius: 8, symbolName: "square.stack")
                                        Text(album.title)
                                            .font(.caption.weight(.semibold))
                                            .foregroundColor(SonifyTheme.textPrimary)
                                            .lineLimit(1)
                                        Text(album.artist)
                                            .font(.caption2)
                                            .foregroundColor(SonifyTheme.textSecondary)
                                            .lineLimit(1)
                                    }
                                    .frame(width: 124)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func topResultCard(_ top: TopResult, trackContext: [Track]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Top Result")
                .font(.headline)
                .foregroundColor(SonifyTheme.textPrimary)

            switch top {
            case .track(let track):
                Button {
                    player.play(track: track, context: trackContext)
                } label: {
                    HStack(spacing: 14) {
                        ArtworkView(urlString: track.bestCoverURL, size: 72, cornerRadius: 8)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(track.title)
                                .font(.headline.weight(.bold))
                                .foregroundColor(SonifyTheme.textPrimary)
                                .lineLimit(2)
                            Text("Song • \(track.artist)")
                                .font(.caption)
                                .foregroundColor(SonifyTheme.textSecondary)
                        }
                        Spacer()
                        Image(systemName: "play.circle.fill")
                            .font(.system(size: 40))
                            .foregroundColor(SonifyTheme.accentGreen)
                    }
                    .padding(14)
                    .background(SonifyTheme.bgElevated)
                    .cornerRadius(12)
                }
                .buttonStyle(.plain)

            case .artist(let artist):
                NavigationLink {
                    ArtistDetailView(artistId: artist.id, initialArtist: artist)
                } label: {
                    HStack(spacing: 14) {
                        ArtworkView(urlString: artist.bestImageURL, size: 72, isCircular: true, symbolName: "person.fill")
                        VStack(alignment: .leading, spacing: 4) {
                            Text(artist.name)
                                .font(.headline.weight(.bold))
                                .foregroundColor(SonifyTheme.textPrimary)
                            Text("Artist")
                                .font(.caption)
                                .foregroundColor(SonifyTheme.textSecondary)
                        }
                        Spacer()
                        Image(systemName: "chevron.right")
                            .foregroundColor(SonifyTheme.textSecondary)
                    }
                    .padding(14)
                    .background(SonifyTheme.bgElevated)
                    .cornerRadius(12)
                }
                .buttonStyle(.plain)

            case .album(let album):
                NavigationLink {
                    AlbumDetailView(albumId: album.id, initialAlbum: album)
                } label: {
                    HStack(spacing: 14) {
                        ArtworkView(urlString: album.bestCoverURL, size: 72, cornerRadius: 8)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(album.title)
                                .font(.headline.weight(.bold))
                                .foregroundColor(SonifyTheme.textPrimary)
                            Text("Album • \(album.artist)")
                                .font(.caption)
                                .foregroundColor(SonifyTheme.textSecondary)
                        }
                        Spacer()
                        Image(systemName: "chevron.right")
                            .foregroundColor(SonifyTheme.textSecondary)
                    }
                    .padding(14)
                    .background(SonifyTheme.bgElevated)
                    .cornerRadius(12)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func triggerSearch(immediate: Bool) {
        searchTask?.cancel()
        let q = queryText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else {
            response = nil
            isSearching = false
            return
        }

        let currentFilter = selectedFilter
        searchTask = Task {
            if !immediate {
                try? await Task.sleep(nanoseconds: 300_000_000)
            }
            guard !Task.isCancelled else { return }
            isSearching = true
            errorMessage = nil
            do {
                let res = try await api.search(query: q, type: currentFilter, limit: 20)
                guard !Task.isCancelled else { return }
                response = res
            } catch is CancellationError {
                return
            } catch {
                errorMessage = error.localizedDescription
            }
            isSearching = false
        }
    }
}
