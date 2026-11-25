import SwiftUI

struct LibraryView: View {
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var offlineDownloads: OfflineDownloadManager

    @State private var showCreateAlert = false
    @State private var newName = ""
    @State private var newDescription = ""
    @State private var selectedFilter = "all"
    @State private var sortAlphabetical = false
    @State private var searchQuery = ""

    private let filterOptions: [(String, String)] = [
        ("all", "All"),
        ("playlists", "Playlists"),
        ("artists", "Artists"),
        ("albums", "Albums"),
        ("downloads", "Downloads"),
    ]

    private var filteredPlaylists: [Playlist] {
        let q = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let base = q.isEmpty ? library.playlists : library.playlists.filter { $0.name.lowercased().contains(q) }
        return sortAlphabetical ? base.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending } : base
    }

    private var filteredArtists: [FollowedArtist] {
        let q = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let base = q.isEmpty ? library.followedArtists : library.followedArtists.filter { $0.name.lowercased().contains(q) }
        return sortAlphabetical ? base.sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending } : base
    }

    private var filteredAlbums: [SavedAlbum] {
        let q = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let base = q.isEmpty
            ? library.savedAlbums
            : library.savedAlbums.filter { $0.title.lowercased().contains(q) || $0.artist.lowercased().contains(q) }
        return sortAlphabetical ? base.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending } : base
    }

    private var filteredDownloadedTracks: [Track] {
        let q = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let all = offlineDownloads.downloadedTracks.map(\.track)
        let base = q.isEmpty ? all : all.filter { $0.title.lowercased().contains(q) || $0.artist.lowercased().contains(q) }
        return sortAlphabetical ? base.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending } : base
    }

    var body: some View {
        NavigationStack {
            ZStack {
                SonifyTheme.bgSurface.ignoresSafeArea()

                VStack(spacing: 8) {
                    // Filter chips & Sort toggle
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(filterOptions, id: \.0) { code, label in
                                let active = selectedFilter == code
                                Button {
                                    selectedFilter = code
                                } label: {
                                    Text(label)
                                        .font(.caption.weight(.semibold))
                                        .foregroundColor(active ? .black : SonifyTheme.textPrimary)
                                        .padding(.horizontal, 12)
                                        .padding(.vertical, 6)
                                        .background(active ? SonifyTheme.accentGreen : SonifyTheme.bgHighlight)
                                        .clipShape(Capsule())
                                }
                                .buttonStyle(.plain)
                            }

                            Button {
                                sortAlphabetical.toggle()
                            } label: {
                                Label(sortAlphabetical ? "A–Z" : "Recents", systemImage: "arrow.up.arrow.down")
                                    .font(.caption.weight(.semibold))
                                    .foregroundColor(SonifyTheme.textSecondary)
                                    .padding(.horizontal, 10)
                                    .padding(.vertical, 6)
                                    .background(SonifyTheme.bgElevated)
                                    .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                        .padding(.horizontal)
                    }

                    TextField("Filter Your Library", text: $searchQuery)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(SonifyTheme.bgHighlight)
                        .foregroundColor(SonifyTheme.textPrimary)
                        .cornerRadius(8)
                        .padding(.horizontal)

                    List {
                        // Offline Downloads Pin (always accessible in All & Downloads tabs — works in Airplane Mode)
                        if selectedFilter == "all" || selectedFilter == "downloads" {
                            NavigationLink {
                                DownloadsView()
                            } label: {
                                HStack(spacing: 12) {
                                    ZStack {
                                        LinearGradient(
                                            colors: [Color(hex: 0x006450), SonifyTheme.accentGreen],
                                            startPoint: .topLeading,
                                            endPoint: .bottomTrailing
                                        )
                                        Image(systemName: "arrow.down.circle.fill")
                                            .font(.title3)
                                            .foregroundColor(.white)
                                    }
                                    .frame(width: 54, height: 54)
                                    .cornerRadius(8)

                                    VStack(alignment: .leading, spacing: 3) {
                                        Text("Downloads")
                                            .font(.subheadline.weight(.bold))
                                            .foregroundColor(SonifyTheme.textPrimary)
                                        let count = offlineDownloads.downloadedTracks.count
                                        Text("\(count) \(count == 1 ? "song" : "songs") • \(offlineDownloads.formattedTotalSize)")
                                            .font(.caption)
                                            .foregroundColor(SonifyTheme.textSecondary)
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                            .listRowBackground(SonifyTheme.bgSurface)
                        }

                        if selectedFilter == "downloads" {
                            Section {
                                if filteredDownloadedTracks.isEmpty {
                                    Text("No offline downloads yet. Tap ••• on any track or Download All on a playlist.")
                                        .font(.caption)
                                        .foregroundColor(SonifyTheme.textSecondary)
                                        .listRowBackground(SonifyTheme.bgSurface)
                                } else {
                                    ForEach(filteredDownloadedTracks) { track in
                                        TrackRowView(
                                            track: track,
                                            onTap: { player.play(track: track, context: filteredDownloadedTracks) }
                                        )
                                        .listRowBackground(SonifyTheme.bgSurface)
                                    }
                                    .onDelete { offsets in
                                        let ids = offsets.compactMap { $0 < filteredDownloadedTracks.count ? filteredDownloadedTracks[$0].id : nil }
                                        for id in ids {
                                            offlineDownloads.removeDownload(trackId: id)
                                        }
                                    }
                                }
                            } header: {
                                Text("Downloaded Songs")
                                    .foregroundColor(SonifyTheme.textSecondary)
                            }
                        }

                        if selectedFilter == "all" || selectedFilter == "playlists" {
                            // Liked Songs Pin
                            NavigationLink {
                                LikedSongsView()
                            } label: {
                                HStack(spacing: 12) {
                                    ZStack {
                                        LinearGradient(
                                            colors: [Color(hex: 0x450AF5), Color(hex: 0xC4EFD9)],
                                            startPoint: .topLeading,
                                            endPoint: .bottomTrailing
                                        )
                                        Image(systemName: "heart.fill")
                                            .font(.title3)
                                            .foregroundColor(.white)
                                    }
                                    .frame(width: 54, height: 54)
                                    .cornerRadius(8)

                                    VStack(alignment: .leading, spacing: 3) {
                                        Text("Liked Songs")
                                            .font(.subheadline.weight(.bold))
                                            .foregroundColor(SonifyTheme.textPrimary)
                                        Text("Playlist • \(library.likedTracks.count) songs")
                                            .font(.caption)
                                            .foregroundColor(SonifyTheme.textSecondary)
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                            .listRowBackground(SonifyTheme.bgSurface)

                            // Sound Capsule Stats Pin
                            NavigationLink {
                                StatsView()
                            } label: {
                                HStack(spacing: 12) {
                                    ZStack {
                                        LinearGradient(
                                            colors: [SonifyTheme.accentGreen, Color(hex: 0x1E3264)],
                                            startPoint: .topLeading,
                                            endPoint: .bottomTrailing
                                        )
                                        Image(systemName: "chart.bar.fill")
                                            .font(.title3)
                                            .foregroundColor(.white)
                                    }
                                    .frame(width: 54, height: 54)
                                    .cornerRadius(8)

                                    VStack(alignment: .leading, spacing: 3) {
                                        Text("Sound Capsule")
                                            .font(.subheadline.weight(.bold))
                                            .foregroundColor(SonifyTheme.textPrimary)
                                        Text("Your listening stats & top artists")
                                            .font(.caption)
                                            .foregroundColor(SonifyTheme.textSecondary)
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                            .listRowBackground(SonifyTheme.bgSurface)

                            // Recently Played Pin
                            NavigationLink {
                                HistoryView()
                            } label: {
                                HStack(spacing: 12) {
                                    ZStack {
                                        RoundedRectangle(cornerRadius: 8)
                                            .fill(SonifyTheme.bgHighlight)
                                        Image(systemName: "clock.arrow.circlepath")
                                            .font(.title3)
                                            .foregroundColor(SonifyTheme.accentGreen)
                                    }
                                    .frame(width: 54, height: 54)

                                    VStack(alignment: .leading, spacing: 3) {
                                        Text("Recently Played")
                                            .font(.subheadline.weight(.bold))
                                            .foregroundColor(SonifyTheme.textPrimary)
                                        Text("History • \(library.history.count) plays")
                                            .font(.caption)
                                            .foregroundColor(SonifyTheme.textSecondary)
                                    }
                                }
                                .padding(.vertical, 4)
                            }
                            .listRowBackground(SonifyTheme.bgSurface)

                            // Custom Playlists
                            Section {
                                if filteredPlaylists.isEmpty {
                                    Text("Tap + to create your first custom playlist.")
                                        .font(.caption)
                                        .foregroundColor(SonifyTheme.textSecondary)
                                        .listRowBackground(SonifyTheme.bgSurface)
                                } else {
                                    ForEach(filteredPlaylists) { playlist in
                                        NavigationLink {
                                            PlaylistDetailView(playlistId: playlist.id, initialPlaylist: playlist)
                                        } label: {
                                            HStack(spacing: 12) {
                                                PlaylistMosaicArtworkView(covers: playlist.covers, size: 54, cornerRadius: 8)
                                                VStack(alignment: .leading, spacing: 3) {
                                                    Text(playlist.name)
                                                        .font(.subheadline.weight(.semibold))
                                                        .foregroundColor(SonifyTheme.textPrimary)
                                                        .lineLimit(1)
                                                    Text("Playlist • \(playlist.trackCount) songs")
                                                        .font(.caption)
                                                        .foregroundColor(SonifyTheme.textSecondary)
                                                }
                                            }
                                            .padding(.vertical, 4)
                                        }
                                        .listRowBackground(SonifyTheme.bgSurface)
                                    }
                                    .onDelete { offsets in
                                        for idx in offsets where idx < filteredPlaylists.count {
                                            let id = filteredPlaylists[idx].id
                                            Task { await library.deletePlaylist(id: id) }
                                        }
                                    }
                                }
                            } header: {
                                Text("Playlists")
                                    .foregroundColor(SonifyTheme.textSecondary)
                            }
                        }

                        if (selectedFilter == "all" || selectedFilter == "artists") && !filteredArtists.isEmpty {
                            Section {
                                ForEach(filteredArtists) { artist in
                                    NavigationLink {
                                        ArtistDetailView(artistId: artist.id)
                                    } label: {
                                        HStack(spacing: 12) {
                                            ArtworkView(urlString: artist.image, size: 54, isCircular: true, symbolName: "person.fill")
                                            VStack(alignment: .leading, spacing: 3) {
                                                Text(artist.name)
                                                    .font(.subheadline.weight(.semibold))
                                                    .foregroundColor(SonifyTheme.textPrimary)
                                                    .lineLimit(1)
                                                Text("Artist")
                                                    .font(.caption)
                                                    .foregroundColor(SonifyTheme.textSecondary)
                                            }
                                        }
                                        .padding(.vertical, 4)
                                    }
                                    .listRowBackground(SonifyTheme.bgSurface)
                                }
                            } header: {
                                Text("Followed Artists")
                                    .foregroundColor(SonifyTheme.textSecondary)
                            }
                        }

                        if (selectedFilter == "all" || selectedFilter == "albums") && !filteredAlbums.isEmpty {
                            Section {
                                ForEach(filteredAlbums) { album in
                                    NavigationLink {
                                        AlbumDetailView(albumId: album.id)
                                    } label: {
                                        HStack(spacing: 12) {
                                            ArtworkView(urlString: album.cover, size: 54, cornerRadius: 8, symbolName: "square.stack")
                                            VStack(alignment: .leading, spacing: 3) {
                                                Text(album.title)
                                                    .font(.subheadline.weight(.semibold))
                                                    .foregroundColor(SonifyTheme.textPrimary)
                                                    .lineLimit(1)
                                                Text("Album • \(album.artist)")
                                                    .font(.caption)
                                                    .foregroundColor(SonifyTheme.textSecondary)
                                                    .lineLimit(1)
                                            }
                                        }
                                        .padding(.vertical, 4)
                                    }
                                    .listRowBackground(SonifyTheme.bgSurface)
                                }
                            } header: {
                                Text("Saved Albums")
                                    .foregroundColor(SonifyTheme.textSecondary)
                            }
                        }

                        Color.clear
                            .frame(height: 80)
                            .listRowBackground(Color.clear)
                            .listRowSeparator(.hidden)
                    }
                    .listStyle(.plain)
                    .scrollContentBackground(.hidden)
                    .refreshable {
                        if !offlineDownloads.offlineModeEnabled {
                            await library.hydrateAll()
                        }
                    }
                }
            }
            .navigationTitle("Your Library")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        newName = ""
                        newDescription = ""
                        showCreateAlert = true
                    } label: {
                        Image(systemName: "plus")
                            .font(.headline.weight(.bold))
                            .foregroundColor(SonifyTheme.textPrimary)
                    }
                }
            }
            .sheet(isPresented: $showCreateAlert) {
                createPlaylistSheet
            }
        }
    }

    private var createPlaylistSheet: some View {
        NavigationStack {
            ZStack {
                SonifyTheme.bgSurface.ignoresSafeArea()
                VStack(spacing: 16) {
                    TextField("Playlist name", text: $newName)
                        .padding(12)
                        .background(SonifyTheme.bgHighlight)
                        .foregroundColor(SonifyTheme.textPrimary)
                        .cornerRadius(8)

                    TextField("Description (optional)", text: $newDescription)
                        .padding(12)
                        .background(SonifyTheme.bgHighlight)
                        .foregroundColor(SonifyTheme.textPrimary)
                        .cornerRadius(8)

                    Button {
                        let name = newName
                        let desc = newDescription
                        showCreateAlert = false
                        Task { _ = await library.createPlaylist(name: name, description: desc) }
                    } label: {
                        Text("Create Playlist")
                            .font(.headline)
                            .foregroundColor(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(SonifyTheme.accentGreen)
                            .clipShape(Capsule())
                    }
                    .disabled(newName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                    Spacer()
                }
                .padding()
            }
            .navigationTitle("New Playlist")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showCreateAlert = false }
                        .foregroundColor(SonifyTheme.textPrimary)
                }
            }
        }
        .presentationDetents([.medium])
        .preferredColorScheme(.dark)
    }
}
