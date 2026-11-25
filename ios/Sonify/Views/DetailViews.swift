import SwiftUI

// MARK: - Artist Detail View

struct ArtistDetailView: View {
    let artistId: String
    var initialArtist: Artist?

    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var player: AudioPlayerService

    @State private var detail: ArtistDetailResponse?
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var playlistTargetTrack: Track?

    var body: some View {
        ZStack {
            SonifyTheme.bgSurface.ignoresSafeArea()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 20) {
                    let artist = detail?.artist ?? initialArtist
                    if let artist {
                        VStack(spacing: 12) {
                            ArtworkView(urlString: artist.bestImageURL, size: 170, isCircular: true, symbolName: "person.fill")
                                .shadow(color: .black.opacity(0.5), radius: 14, y: 6)
                            Text(artist.name)
                                .font(.title.weight(.bold))
                                .foregroundColor(SonifyTheme.textPrimary)
                                .multilineTextAlignment(.center)
                            if let tracks = detail?.topTracks, let first = tracks.first {
                                Button {
                                    player.play(track: first, context: tracks)
                                } label: {
                                    Label("Play Top Tracks", systemImage: "play.fill")
                                        .font(.subheadline.weight(.bold))
                                        .foregroundColor(.black)
                                        .padding(.horizontal, 28)
                                        .padding(.vertical, 12)
                                        .background(SonifyTheme.accentGreen)
                                        .clipShape(Capsule())
                                }
                            }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.top, 12)
                    }

                    if isLoading && detail == nil {
                        ProgressView()
                            .tint(SonifyTheme.accentGreen)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    } else if let errorMessage {
                        Text(errorMessage)
                            .font(.caption)
                            .foregroundColor(SonifyTheme.danger)
                            .padding(.horizontal)
                    } else if let detail {
                        if !detail.topTracks.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Popular")
                                    .font(.headline)
                                    .foregroundColor(SonifyTheme.textPrimary)
                                ForEach(Array(detail.topTracks.enumerated()), id: \.element.id) { idx, track in
                                    TrackRowView(
                                        track: track,
                                        indexNumber: idx + 1,
                                        onTap: { player.play(track: track, context: detail.topTracks) },
                                        onAddToPlaylist: { playlistTargetTrack = $0 }
                                    )
                                }
                            }
                            .padding(.horizontal)
                        }

                        if !detail.albums.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("Albums")
                                    .font(.headline)
                                    .foregroundColor(SonifyTheme.textPrimary)
                                    .padding(.horizontal)
                                ScrollView(.horizontal, showsIndicators: false) {
                                    LazyHStack(spacing: 14) {
                                        ForEach(detail.albums) { album in
                                            NavigationLink {
                                                AlbumDetailView(albumId: album.id, initialAlbum: album)
                                            } label: {
                                                VStack(alignment: .leading, spacing: 6) {
                                                    ArtworkView(urlString: album.bestCoverURL, size: 132, cornerRadius: 8)
                                                    Text(album.title)
                                                        .font(.caption.weight(.semibold))
                                                        .foregroundColor(SonifyTheme.textPrimary)
                                                        .lineLimit(1)
                                                    if let rel = album.releaseDate, !rel.isEmpty {
                                                        Text(String(rel.prefix(4)))
                                                            .font(.caption2)
                                                            .foregroundColor(SonifyTheme.textSecondary)
                                                    }
                                                }
                                                .frame(width: 132)
                                            }
                                            .buttonStyle(.plain)
                                        }
                                    }
                                    .padding(.horizontal)
                                }
                            }
                        }
                    }

                    Color.clear.frame(height: 88)
                }
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $playlistTargetTrack) { track in
            AddToPlaylistSheet(track: track)
        }
        .task {
            isLoading = true
            do {
                detail = try await api.getArtist(id: artistId)
            } catch {
                errorMessage = error.localizedDescription
            }
            isLoading = false
        }
    }
}

// MARK: - Album Detail View

struct AlbumDetailView: View {
    let albumId: String
    var initialAlbum: Album?

    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var player: AudioPlayerService

    @State private var detail: AlbumDetailResponse?
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var playlistTargetTrack: Track?

    var body: some View {
        ZStack {
            SonifyTheme.bgSurface.ignoresSafeArea()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    let album = detail?.album ?? initialAlbum
                    if let album {
                        VStack(spacing: 12) {
                            ArtworkView(urlString: album.bestCoverURL, size: 210, cornerRadius: 10)
                                .shadow(color: .black.opacity(0.55), radius: 16, y: 8)
                            Text(album.title)
                                .font(.title2.weight(.bold))
                                .foregroundColor(SonifyTheme.textPrimary)
                                .multilineTextAlignment(.center)
                            Text(album.artist)
                                .font(.subheadline)
                                .foregroundColor(SonifyTheme.textSecondary)
                            if let tracks = detail?.tracks, let first = tracks.first {
                                Button {
                                    player.play(track: first, context: tracks)
                                } label: {
                                    Label("Play Album", systemImage: "play.fill")
                                        .font(.subheadline.weight(.bold))
                                        .foregroundColor(.black)
                                        .padding(.horizontal, 32)
                                        .padding(.vertical, 12)
                                        .background(SonifyTheme.accentGreen)
                                        .clipShape(Capsule())
                                }
                            }
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.horizontal)
                        .padding(.top, 10)
                    }

                    if isLoading && detail == nil {
                        ProgressView()
                            .tint(SonifyTheme.accentGreen)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    } else if let errorMessage {
                        Text(errorMessage)
                            .font(.caption)
                            .foregroundColor(SonifyTheme.danger)
                            .padding(.horizontal)
                    } else if let tracks = detail?.tracks {
                        VStack(spacing: 2) {
                            ForEach(Array(tracks.enumerated()), id: \.element.id) { idx, track in
                                TrackRowView(
                                    track: track,
                                    indexNumber: idx + 1,
                                    showArtwork: false,
                                    onTap: { player.play(track: track, context: tracks) },
                                    onAddToPlaylist: { playlistTargetTrack = $0 }
                                )
                            }
                        }
                        .padding(.horizontal)
                    }

                    Color.clear.frame(height: 88)
                }
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $playlistTargetTrack) { track in
            AddToPlaylistSheet(track: track)
        }
        .task {
            isLoading = true
            do {
                detail = try await api.getAlbum(id: albumId)
            } catch {
                errorMessage = error.localizedDescription
            }
            isLoading = false
        }
    }
}

// MARK: - Playlist Detail View

struct PlaylistDetailView: View {
    let playlistId: String
    var initialPlaylist: Playlist?

    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @Environment(\.dismiss) private var dismiss

    @State private var playlist: Playlist?
    @State private var tracks: [Track] = []
    @State private var isLoading = false
    @State private var showEditSheet = false
    @State private var editName = ""
    @State private var editDesc = ""

    var body: some View {
        ZStack {
            SonifyTheme.bgSurface.ignoresSafeArea()
            List {
                VStack(spacing: 12) {
                    let activePlaylist = playlist ?? initialPlaylist
                    PlaylistMosaicArtworkView(covers: activePlaylist?.covers ?? [], size: 190, cornerRadius: 10)
                        .shadow(color: .black.opacity(0.5), radius: 14, y: 6)
                    Text(activePlaylist?.name ?? "Playlist")
                        .font(.title2.weight(.bold))
                        .foregroundColor(SonifyTheme.textPrimary)
                        .multilineTextAlignment(.center)
                    if let desc = activePlaylist?.description, !desc.isEmpty {
                        Text(desc)
                            .font(.caption)
                            .foregroundColor(SonifyTheme.textSecondary)
                            .multilineTextAlignment(.center)
                    }
                    Text("\(tracks.count) songs")
                        .font(.caption)
                        .foregroundColor(SonifyTheme.textSubdued)

                    if let first = tracks.first {
                        Button {
                            player.play(track: first, context: tracks)
                        } label: {
                            Label("Play", systemImage: "play.fill")
                                .font(.subheadline.weight(.bold))
                                .foregroundColor(.black)
                                .padding(.horizontal, 36)
                                .padding(.vertical, 12)
                                .background(SonifyTheme.accentGreen)
                                .clipShape(Capsule())
                        }
                    }
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .listRowBackground(SonifyTheme.bgSurface)
                .listRowSeparator(.hidden)

                if isLoading && tracks.isEmpty {
                    ProgressView()
                        .tint(SonifyTheme.accentGreen)
                        .frame(maxWidth: .infinity)
                        .listRowBackground(SonifyTheme.bgSurface)
                } else if tracks.isEmpty {
                    Text("No songs yet. Tap ••• on any song to add it here.")
                        .font(.subheadline)
                        .foregroundColor(SonifyTheme.textSecondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 32)
                        .listRowBackground(SonifyTheme.bgSurface)
                        .listRowSeparator(.hidden)
                } else {
                    ForEach(tracks) { track in
                        TrackRowView(
                            track: track,
                            onTap: { player.play(track: track, context: tracks) }
                        )
                        .listRowBackground(SonifyTheme.bgSurface)
                    }
                    .onDelete { offsets in
                        for idx in offsets.sorted(by: >) where idx < tracks.count {
                            let tid = tracks[idx].id
                            tracks.remove(at: idx)
                            Task { await library.removeTrack(trackId: tid, fromPlaylistId: playlistId) }
                        }
                    }
                    .onMove { source, destination in
                        tracks.move(fromOffsets: source, toOffset: destination)
                        let orderedIds = tracks.map(\.id)
                        Task { _ = try? await api.reorderPlaylistTracks(playlistId: playlistId, trackIds: orderedIds) }
                    }
                }

                Color.clear
                    .frame(height: 88)
                    .listRowBackground(Color.clear)
                    .listRowSeparator(.hidden)
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .navigationBarTrailing) {
                EditButton()
                Menu {
                    Button {
                        let p = playlist ?? initialPlaylist
                        editName = p?.name ?? ""
                        editDesc = p?.description ?? ""
                        showEditSheet = true
                    } label: {
                        Label("Edit Details", systemImage: "pencil")
                    }
                    Button(role: .destructive) {
                        Task {
                            await library.deletePlaylist(id: playlistId)
                            dismiss()
                        }
                    } label: {
                        Label("Delete Playlist", systemImage: "trash")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .sheet(isPresented: $showEditSheet) {
            NavigationStack {
                ZStack {
                    SonifyTheme.bgSurface.ignoresSafeArea()
                    VStack(spacing: 14) {
                        TextField("Playlist name", text: $editName)
                            .padding(12)
                            .background(SonifyTheme.bgHighlight)
                            .foregroundColor(SonifyTheme.textPrimary)
                            .cornerRadius(8)
                        TextField("Description", text: $editDesc)
                            .padding(12)
                            .background(SonifyTheme.bgHighlight)
                            .foregroundColor(SonifyTheme.textPrimary)
                            .cornerRadius(8)
                        Button("Save Changes") {
                            let n = editName
                            let d = editDesc
                            showEditSheet = false
                            Task {
                                if let updated = await library.updatePlaylist(id: playlistId, name: n, description: d) {
                                    playlist = updated
                                }
                            }
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(SonifyTheme.accentGreen)
                        .foregroundColor(.black)
                        .disabled(editName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        Spacer()
                    }
                    .padding()
                }
                .navigationTitle("Edit Playlist")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Cancel") { showEditSheet = false }
                    }
                }
            }
            .presentationDetents([.medium])
            .preferredColorScheme(.dark)
        }
        .task {
            await reload()
        }
    }

    private func reload() async {
        isLoading = true
        if let res = try? await api.getPlaylist(id: playlistId) {
            playlist = res.playlist
            tracks = res.tracks
        }
        isLoading = false
    }
}

// MARK: - Liked Songs View

struct LikedSongsView: View {
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @State private var filterText = ""
    @State private var playlistTargetTrack: Track?

    private var filteredTracks: [Track] {
        let q = filterText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return library.likedTracks }
        return library.likedTracks.filter {
            $0.title.lowercased().contains(q) || $0.artist.lowercased().contains(q)
        }
    }

    var body: some View {
        ZStack {
            SonifyTheme.bgSurface.ignoresSafeArea()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Liked Songs")
                            .font(.largeTitle.weight(.bold))
                            .foregroundColor(SonifyTheme.textPrimary)
                        Text("\(library.likedTracks.count) songs")
                            .font(.subheadline)
                            .foregroundColor(SonifyTheme.textSecondary)

                        if let first = filteredTracks.first {
                            Button {
                                player.play(track: first, context: filteredTracks)
                            } label: {
                                Label("Play All", systemImage: "play.fill")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundColor(.black)
                                    .padding(.horizontal, 28)
                                    .padding(.vertical, 11)
                                    .background(SonifyTheme.accentGreen)
                                    .clipShape(Capsule())
                            }
                            .padding(.top, 4)
                        }
                    }

                    TextField("Filter liked songs", text: $filterText)
                        .padding(10)
                        .background(SonifyTheme.bgHighlight)
                        .foregroundColor(SonifyTheme.textPrimary)
                        .cornerRadius(8)

                    if filteredTracks.isEmpty {
                        Text("No liked songs yet. Tap the heart on any song to save it here.")
                            .font(.subheadline)
                            .foregroundColor(SonifyTheme.textSecondary)
                            .padding(.top, 24)
                    } else {
                        ForEach(filteredTracks) { track in
                            TrackRowView(
                                track: track,
                                onTap: { player.play(track: track, context: filteredTracks) },
                                onAddToPlaylist: { playlistTargetTrack = $0 }
                            )
                        }
                    }

                    Color.clear.frame(height: 88)
                }
                .padding(.horizontal)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $playlistTargetTrack) { track in
            AddToPlaylistSheet(track: track)
        }
    }
}

// MARK: - History View

struct HistoryView: View {
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @State private var playlistTargetTrack: Track?

    var body: some View {
        ZStack {
            SonifyTheme.bgSurface.ignoresSafeArea()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 8) {
                    if library.history.isEmpty {
                        Text("Songs you listen to will appear here.")
                            .font(.subheadline)
                            .foregroundColor(SonifyTheme.textSecondary)
                            .padding(.top, 40)
                            .frame(maxWidth: .infinity)
                    } else {
                        let contextTracks = library.history.map(\.track)
                        ForEach(library.history) { item in
                            TrackRowView(
                                track: item.track,
                                onTap: { player.play(track: item.track, context: contextTracks) },
                                onAddToPlaylist: { playlistTargetTrack = $0 }
                            )
                        }
                    }

                    Color.clear.frame(height: 88)
                }
                .padding(.horizontal)
            }
        }
        .navigationTitle("Recently Played")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if !library.history.isEmpty {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Clear") {
                        Task { await library.clearHistory() }
                    }
                    .foregroundColor(SonifyTheme.danger)
                }
            }
        }
        .sheet(item: $playlistTargetTrack) { track in
            AddToPlaylistSheet(track: track)
        }
    }
}
