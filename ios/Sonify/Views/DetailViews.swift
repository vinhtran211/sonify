import SwiftUI

// MARK: - Artist Detail View

struct ArtistDetailView: View {
    let artistId: String
    var initialArtist: Artist?

    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var library: LibraryStore
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

                            HStack(spacing: 10) {
                                if let tracks = detail?.topTracks, let first = tracks.first {
                                    Button {
                                        player.play(track: first, context: tracks)
                                    } label: {
                                        Label("Play", systemImage: "play.fill")
                                            .font(.subheadline.weight(.bold))
                                            .foregroundColor(.black)
                                            .padding(.horizontal, 22)
                                            .padding(.vertical, 10)
                                            .background(SonifyTheme.accentGreen)
                                            .clipShape(Capsule())
                                    }
                                }

                                let following = library.isFollowing(artist.id)
                                Button {
                                    Task { await library.toggleFollow(artist: artist) }
                                } label: {
                                    Text(following ? "Following" : "Follow")
                                        .font(.subheadline.weight(.bold))
                                        .foregroundColor(following ? SonifyTheme.accentGreen : SonifyTheme.textPrimary)
                                        .padding(.horizontal, 18)
                                        .padding(.vertical, 10)
                                        .background(SonifyTheme.bgHighlight)
                                        .overlay(
                                            Capsule().stroke(following ? SonifyTheme.accentGreen : Color.white.opacity(0.25), lineWidth: 1)
                                        )
                                        .clipShape(Capsule())
                                }

                                Button {
                                    player.startArtistRadio(artistId: artist.id, name: artist.name)
                                } label: {
                                    Image(systemName: "dot.radiowaves.left.and.right")
                                        .font(.subheadline.weight(.bold))
                                        .foregroundColor(SonifyTheme.textPrimary)
                                        .frame(width: 40, height: 40)
                                        .background(SonifyTheme.bgHighlight)
                                        .clipShape(Circle())
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
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var offlineDownloads: OfflineDownloadManager

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

                            HStack(spacing: 12) {
                                if let tracks = detail?.tracks, let first = tracks.first {
                                    Button {
                                        player.play(track: first, context: tracks)
                                    } label: {
                                        Label("Play Album", systemImage: "play.fill")
                                            .font(.subheadline.weight(.bold))
                                            .foregroundColor(.black)
                                            .padding(.horizontal, 26)
                                            .padding(.vertical, 11)
                                            .background(SonifyTheme.accentGreen)
                                            .clipShape(Capsule())
                                    }
                                }

                                let saved = library.isAlbumSaved(album.id)
                                Button {
                                    Task { await library.toggleSave(album: album) }
                                } label: {
                                    Label(saved ? "Saved" : "Save", systemImage: saved ? "heart.fill" : "heart")
                                        .font(.subheadline.weight(.bold))
                                        .foregroundColor(saved ? SonifyTheme.accentGreen : SonifyTheme.textPrimary)
                                        .padding(.horizontal, 18)
                                        .padding(.vertical, 11)
                                        .background(SonifyTheme.bgHighlight)
                                        .clipShape(Capsule())
                                }

                                if let tracks = detail?.tracks, !tracks.isEmpty {
                                    let allDownloaded = tracks.allSatisfy { offlineDownloads.isDownloaded($0.id) }
                                    Button {
                                        let qual = library.settings.audioQuality
                                        library.showToast("Downloading \(tracks.count) tracks...")
                                        Task {
                                            let count = await offlineDownloads.downloadTracks(tracks, api: api, quality: qual)
                                            library.showToast("Downloaded \(count) songs for offline playback")
                                        }
                                    } label: {
                                        Image(systemName: allDownloaded ? "arrow.down.circle.fill" : "arrow.down.circle")
                                            .font(.title3)
                                            .foregroundColor(allDownloaded ? SonifyTheme.accentGreen : SonifyTheme.textPrimary)
                                            .frame(width: 42, height: 42)
                                            .background(SonifyTheme.bgHighlight)
                                            .clipShape(Circle())
                                    }
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

// MARK: - Playlist Detail View (with Duplicate & Recommendations)

struct PlaylistDetailView: View {
    let playlistId: String
    var initialPlaylist: Playlist?

    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var offlineDownloads: OfflineDownloadManager
    @Environment(\.dismiss) private var dismiss

    @State private var playlist: Playlist?
    @State private var tracks: [Track] = []
    @State private var recommendations: [Track] = []
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
                        HStack(spacing: 12) {
                            Button {
                                player.play(track: first, context: tracks)
                            } label: {
                                Label("Play", systemImage: "play.fill")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundColor(.black)
                                    .padding(.horizontal, 32)
                                    .padding(.vertical, 12)
                                    .background(SonifyTheme.accentGreen)
                                    .clipShape(Capsule())
                            }

                            let allDownloaded = tracks.allSatisfy { offlineDownloads.isDownloaded($0.id) }
                            Button {
                                let qual = library.settings.audioQuality
                                library.showToast("Downloading \(tracks.count) tracks...")
                                Task {
                                    let count = await offlineDownloads.downloadTracks(tracks, api: api, quality: qual)
                                    library.showToast("Downloaded \(count) songs for offline playback")
                                }
                            } label: {
                                Label(allDownloaded ? "Downloaded" : "Download", systemImage: allDownloaded ? "arrow.down.circle.fill" : "arrow.down.circle")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundColor(allDownloaded ? SonifyTheme.accentGreen : SonifyTheme.textPrimary)
                                    .padding(.horizontal, 16)
                                    .padding(.vertical, 12)
                                    .background(SonifyTheme.bgHighlight)
                                    .clipShape(Capsule())
                            }
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
                    Text("No songs yet. Tap ••• on any song or add from Recommended below.")
                        .font(.subheadline)
                        .foregroundColor(SonifyTheme.textSecondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 24)
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

                if !recommendations.isEmpty {
                    Section {
                        ForEach(recommendations) { rec in
                            HStack(spacing: 12) {
                                ArtworkView(urlString: rec.bestCoverURL, size: 44, cornerRadius: 6)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(rec.title)
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundColor(SonifyTheme.textPrimary)
                                        .lineLimit(1)
                                    Text(rec.artist)
                                        .font(.caption)
                                        .foregroundColor(SonifyTheme.textSecondary)
                                        .lineLimit(1)
                                }
                                Spacer()
                                Button {
                                    Task {
                                        await library.addTrack(rec, toPlaylistId: playlistId)
                                        await reload()
                                    }
                                } label: {
                                    Text("+ Add")
                                        .font(.caption.weight(.bold))
                                        .foregroundColor(SonifyTheme.textPrimary)
                                        .padding(.horizontal, 12)
                                        .padding(.vertical, 6)
                                        .background(SonifyTheme.bgHighlight)
                                        .clipShape(Capsule())
                                }
                                .buttonStyle(.plain)
                            }
                            .listRowBackground(SonifyTheme.bgSurface)
                        }
                    } header: {
                        Text("Recommended Songs")
                            .foregroundColor(SonifyTheme.textSecondary)
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
                    Button {
                        Task { _ = await library.duplicatePlaylist(id: playlistId) }
                    } label: {
                        Label("Duplicate Playlist", systemImage: "doc.on.doc")
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
        if let recRes = try? await api.getPlaylistRecommendations(id: playlistId, limit: 8) {
            recommendations = recRes.tracks
        }
        isLoading = false
    }
}

// MARK: - Made For You Mix Detail View

struct MixDetailView: View {
    let mix: MadeForYouMix

    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var offlineDownloads: OfflineDownloadManager
    @State private var playlistTargetTrack: Track?

    var body: some View {
        ZStack {
            SonifyTheme.bgSurface.ignoresSafeArea()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    VStack(spacing: 12) {
                        ZStack {
                            RoundedRectangle(cornerRadius: 14, style: .continuous)
                                .fill(
                                    LinearGradient(
                                        colors: [Color(hexString: mix.color), SonifyTheme.bgElevated],
                                        startPoint: .topLeading,
                                        endPoint: .bottomTrailing
                                    )
                                )
                                .frame(width: 200, height: 200)
                            if !mix.cover.isEmpty {
                                ArtworkView(urlString: mix.cover, size: 200, cornerRadius: 14)
                                    .opacity(0.88)
                            }
                        }
                        Text(mix.title)
                            .font(.title2.weight(.bold))
                            .foregroundColor(SonifyTheme.textPrimary)
                        if !mix.subtitle.isEmpty {
                            Text(mix.subtitle)
                                .font(.caption)
                                .foregroundColor(SonifyTheme.textSecondary)
                                .multilineTextAlignment(.center)
                        }

                        HStack(spacing: 12) {
                            if let first = mix.tracks.first {
                                Button {
                                    player.play(track: first, context: mix.tracks)
                                } label: {
                                    Label("Play Mix", systemImage: "play.fill")
                                        .font(.subheadline.weight(.bold))
                                        .foregroundColor(.black)
                                        .padding(.horizontal, 26)
                                        .padding(.vertical, 11)
                                        .background(SonifyTheme.accentGreen)
                                        .clipShape(Capsule())
                                }
                            }

                            Button {
                                Task {
                                    if let created = await library.createPlaylist(name: mix.title, description: mix.subtitle) {
                                        for tr in mix.tracks {
                                            await library.addTrack(tr, toPlaylistId: created.id)
                                        }
                                        library.showToast("Saved \(mix.title) as Playlist")
                                    }
                                }
                            } label: {
                                Label("Save Playlist", systemImage: "plus")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundColor(SonifyTheme.textPrimary)
                                    .padding(.horizontal, 16)
                                    .padding(.vertical, 11)
                                    .background(SonifyTheme.bgHighlight)
                                    .clipShape(Capsule())
                            }

                            if !mix.tracks.isEmpty {
                                let allDownloaded = mix.tracks.allSatisfy { offlineDownloads.isDownloaded($0.id) }
                                Button {
                                    let qual = library.settings.audioQuality
                                    library.showToast("Downloading \(mix.tracks.count) tracks...")
                                    Task {
                                        let count = await offlineDownloads.downloadTracks(mix.tracks, api: api, quality: qual)
                                        library.showToast("Downloaded \(count) songs for offline playback")
                                    }
                                } label: {
                                    Image(systemName: allDownloaded ? "arrow.down.circle.fill" : "arrow.down.circle")
                                        .font(.title3)
                                        .foregroundColor(allDownloaded ? SonifyTheme.accentGreen : SonifyTheme.textPrimary)
                                        .frame(width: 42, height: 42)
                                        .background(SonifyTheme.bgHighlight)
                                        .clipShape(Circle())
                                }
                            }
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 12)

                    VStack(spacing: 4) {
                        ForEach(Array(mix.tracks.enumerated()), id: \.element.id) { idx, track in
                            TrackRowView(
                                track: track,
                                indexNumber: idx + 1,
                                onTap: { player.play(track: track, context: mix.tracks) },
                                onAddToPlaylist: { playlistTargetTrack = $0 }
                            )
                        }
                    }
                    .padding(.horizontal)

                    Color.clear.frame(height: 88)
                }
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $playlistTargetTrack) { track in
            AddToPlaylistSheet(track: track)
        }
    }
}

// MARK: - Genre & Mood Hub View

struct GenreHubView: View {
    let slug: String
    let title: String
    let accentColor: Color

    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService

    @State private var hub: GenreHubResponse?
    @State private var isLoading = false
    @State private var playlistTargetTrack: Track?

    var body: some View {
        ZStack {
            SonifyTheme.bgSurface.ignoresSafeArea()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("GENRE & MOOD HUB")
                            .font(.caption2.weight(.bold))
                            .foregroundColor(.white.opacity(0.8))
                        Text(hub?.genre.name ?? title)
                            .font(.largeTitle.weight(.heavy))
                            .foregroundColor(.white)
                        if let tracks = hub?.tracks, let first = tracks.first {
                            Button {
                                player.play(track: first, context: tracks)
                            } label: {
                                Label("Play All", systemImage: "play.fill")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundColor(.black)
                                    .padding(.horizontal, 24)
                                    .padding(.vertical, 10)
                                    .background(SonifyTheme.accentGreen)
                                    .clipShape(Capsule())
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(20)
                    .background(
                        LinearGradient(
                            colors: [accentColor, SonifyTheme.bgElevated],
                            startPoint: .topLeading,
                            endPoint: .bottomTrailing
                        )
                    )
                    .cornerRadius(14)
                    .padding(.horizontal)

                    if isLoading && hub == nil {
                        ProgressView()
                            .tint(SonifyTheme.accentGreen)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    } else if let hub {
                        if !hub.tracks.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Popular Tracks")
                                    .font(.headline)
                                    .foregroundColor(SonifyTheme.textPrimary)
                                ForEach(Array(hub.tracks.enumerated()), id: \.element.id) { idx, track in
                                    TrackRowView(
                                        track: track,
                                        indexNumber: idx + 1,
                                        onTap: { player.play(track: track, context: hub.tracks) },
                                        onAddToPlaylist: { playlistTargetTrack = $0 }
                                    )
                                }
                            }
                            .padding(.horizontal)
                        }

                        if !hub.albums.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("Albums")
                                    .font(.headline)
                                    .foregroundColor(SonifyTheme.textPrimary)
                                    .padding(.horizontal)
                                ScrollView(.horizontal, showsIndicators: false) {
                                    LazyHStack(spacing: 14) {
                                        ForEach(hub.albums) { album in
                                            NavigationLink {
                                                AlbumDetailView(albumId: album.id, initialAlbum: album)
                                            } label: {
                                                VStack(alignment: .leading, spacing: 6) {
                                                    ArtworkView(urlString: album.bestCoverURL, size: 128, cornerRadius: 8)
                                                    Text(album.title)
                                                        .font(.caption.weight(.semibold))
                                                        .foregroundColor(SonifyTheme.textPrimary)
                                                        .lineLimit(1)
                                                }
                                                .frame(width: 128)
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
                .padding(.top, 8)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .sheet(item: $playlistTargetTrack) { track in
            AddToPlaylistSheet(track: track)
        }
        .task {
            isLoading = true
            hub = try? await api.getGenreHub(slug: slug, region: library.region)
            isLoading = false
        }
    }
}

// MARK: - Sound Capsule Stats View

struct StatsView: View {
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService

    @State private var playlistTargetTrack: Track?

    var body: some View {
        ZStack {
            SonifyTheme.bgSurface.ignoresSafeArea()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 20) {
                    if let stats = library.stats {
                        LazyVGrid(
                            columns: [GridItem(.flexible(), spacing: 12), GridItem(.flexible(), spacing: 12)],
                            spacing: 12
                        ) {
                            kpiCard(value: "\(stats.totalMinutes)", label: "Minutes Listened")
                            kpiCard(value: "\(stats.totalPlays)", label: "Total Plays")
                            kpiCard(value: "\(stats.streakDays)d", label: "Listening Streak")
                            kpiCard(value: "\(stats.likedCount)", label: "Liked Songs")
                        }
                        .padding(.horizontal)

                        // 24-hour distribution bar chart
                        VStack(alignment: .leading, spacing: 10) {
                            Text("Listening by Hour")
                                .font(.headline)
                                .foregroundColor(SonifyTheme.textPrimary)
                            let maxVal = max(stats.hourlyCounts.max() ?? 1, 1)
                            HStack(alignment: .bottom, spacing: 3) {
                                ForEach(0..<min(24, stats.hourlyCounts.count), id: \.self) { hr in
                                    let count = stats.hourlyCounts[hr]
                                    let frac = CGFloat(count) / CGFloat(maxVal)
                                    RoundedRectangle(cornerRadius: 2)
                                        .fill(count > 0 ? SonifyTheme.accentGreen : SonifyTheme.bgHighlight)
                                        .frame(maxWidth: .infinity)
                                        .frame(height: max(6, frac * 96))
                                }
                            }
                            .frame(height: 100, alignment: .bottom)
                            .padding(12)
                            .background(SonifyTheme.bgElevated)
                            .cornerRadius(12)
                        }
                        .padding(.horizontal)

                        if !stats.topArtists.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                Text("Top Artists")
                                    .font(.headline)
                                    .foregroundColor(SonifyTheme.textPrimary)
                                ForEach(Array(stats.topArtists.prefix(6).enumerated()), id: \.element.id) { idx, ar in
                                    HStack(spacing: 12) {
                                        Text("#\(idx + 1)")
                                            .font(.subheadline.weight(.heavy))
                                            .foregroundColor(SonifyTheme.accentGreen)
                                            .frame(width: 28)
                                        ArtworkView(urlString: ar.coverSmall, size: 42, isCircular: true, symbolName: "person.fill")
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(ar.artist)
                                                .font(.subheadline.weight(.semibold))
                                                .foregroundColor(SonifyTheme.textPrimary)
                                            Text("\(ar.playCount) plays")
                                                .font(.caption)
                                                .foregroundColor(SonifyTheme.textSecondary)
                                        }
                                        Spacer()
                                    }
                                    .padding(10)
                                    .background(SonifyTheme.bgElevated)
                                    .cornerRadius(10)
                                }
                            }
                            .padding(.horizontal)
                        }

                        if !stats.topTracks.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                Text("Top Tracks")
                                    .font(.headline)
                                    .foregroundColor(SonifyTheme.textPrimary)
                                ForEach(Array(stats.topTracks.enumerated()), id: \.element.id) { idx, track in
                                    TrackRowView(
                                        track: track,
                                        indexNumber: idx + 1,
                                        onTap: { player.play(track: track, context: stats.topTracks) },
                                        onAddToPlaylist: { playlistTargetTrack = $0 }
                                    )
                                }
                            }
                            .padding(.horizontal)
                        }
                    } else {
                        ProgressView()
                            .tint(SonifyTheme.accentGreen)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 60)
                    }

                    Color.clear.frame(height: 88)
                }
                .padding(.top, 12)
            }
        }
        .navigationTitle("Sound Capsule")
        .navigationBarTitleDisplayMode(.large)
        .sheet(item: $playlistTargetTrack) { track in
            AddToPlaylistSheet(track: track)
        }
        .task {
            await library.loadStats()
        }
    }

    private func kpiCard(value: String, label: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value)
                .font(.title.weight(.heavy))
                .foregroundColor(SonifyTheme.accentGreen)
            Text(label)
                .font(.caption)
                .foregroundColor(SonifyTheme.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(SonifyTheme.bgElevated)
        .cornerRadius(12)
    }
}

// MARK: - Liked Songs View

struct LikedSongsView: View {
    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var offlineDownloads: OfflineDownloadManager
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
                            HStack(spacing: 10) {
                                Button {
                                    player.play(track: first, context: filteredTracks)
                                } label: {
                                    Label("Play All", systemImage: "play.fill")
                                        .font(.subheadline.weight(.bold))
                                        .foregroundColor(.black)
                                        .padding(.horizontal, 26)
                                        .padding(.vertical, 11)
                                        .background(SonifyTheme.accentGreen)
                                        .clipShape(Capsule())
                                }

                                let allDownloaded = filteredTracks.allSatisfy { offlineDownloads.isDownloaded($0.id) }
                                Button {
                                    let qual = library.settings.audioQuality
                                    library.showToast("Downloading \(filteredTracks.count) liked songs...")
                                    Task {
                                        let count = await offlineDownloads.downloadTracks(filteredTracks, api: api, quality: qual)
                                        library.showToast("Downloaded \(count) songs for offline playback")
                                    }
                                } label: {
                                    Label(allDownloaded ? "Downloaded" : "Download", systemImage: allDownloaded ? "arrow.down.circle.fill" : "arrow.down.circle")
                                        .font(.subheadline.weight(.bold))
                                        .foregroundColor(allDownloaded ? SonifyTheme.accentGreen : SonifyTheme.textPrimary)
                                        .padding(.horizontal, 16)
                                        .padding(.vertical, 11)
                                        .background(SonifyTheme.bgHighlight)
                                        .clipShape(Capsule())
                                }
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


// MARK: - Offline Downloads View (100% Airplane-Mode Playback)

struct DownloadsView: View {
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var offlineDownloads: OfflineDownloadManager

    @State private var filterText = ""
    @State private var showClearConfirm = false
    @State private var playlistTargetTrack: Track?

    private var filteredRecords: [DownloadedTrackRecord] {
        let q = filterText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !q.isEmpty else { return offlineDownloads.downloadedTracks }
        return offlineDownloads.downloadedTracks.filter {
            $0.track.title.lowercased().contains(q) || $0.track.artist.lowercased().contains(q)
        }
    }

    private var filteredTracks: [Track] {
        filteredRecords.map(\.track)
    }

    var body: some View {
        ZStack {
            SonifyTheme.bgSurface.ignoresSafeArea()
            List {
                // Header Banner
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 12) {
                        ZStack {
                            LinearGradient(
                                colors: [Color(hex: 0x006450), SonifyTheme.accentGreen],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                            Image(systemName: "arrow.down.circle.fill")
                                .font(.system(size: 30))
                                .foregroundColor(.white)
                        }
                        .frame(width: 64, height: 64)
                        .cornerRadius(12)

                        VStack(alignment: .leading, spacing: 4) {
                            Text("Offline Downloads")
                                .font(.title2.weight(.bold))
                                .foregroundColor(SonifyTheme.textPrimary)
                            Text("\(offlineDownloads.downloadedTracks.count) songs • \(offlineDownloads.formattedTotalSize)")
                                .font(.caption)
                                .foregroundColor(SonifyTheme.textSecondary)
                            Text("Plays 100% offline in Airplane Mode")
                                .font(.caption2.weight(.semibold))
                                .foregroundColor(SonifyTheme.accentGreen)
                        }
                    }

                    if let first = filteredTracks.first {
                        HStack(spacing: 10) {
                            Button {
                                player.play(track: first, context: filteredTracks)
                            } label: {
                                Label("Play All", systemImage: "play.fill")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundColor(.black)
                                    .padding(.horizontal, 24)
                                    .padding(.vertical, 10)
                                    .background(SonifyTheme.accentGreen)
                                    .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)

                            Button {
                                let shuffled = filteredTracks.shuffled()
                                if let firstShuffled = shuffled.first {
                                    player.play(track: firstShuffled, context: shuffled)
                                }
                            } label: {
                                Label("Shuffle", systemImage: "shuffle")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundColor(SonifyTheme.textPrimary)
                                    .padding(.horizontal, 18)
                                    .padding(.vertical, 10)
                                    .background(SonifyTheme.bgHighlight)
                                    .clipShape(Capsule())
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    TextField("Filter downloaded songs", text: $filterText)
                        .padding(10)
                        .background(SonifyTheme.bgHighlight)
                        .foregroundColor(SonifyTheme.textPrimary)
                        .cornerRadius(8)
                }
                .padding(.vertical, 6)
                .listRowBackground(SonifyTheme.bgSurface)
                .listRowSeparator(.hidden)

                // Active Downloads Progress Section
                if !offlineDownloads.activeDownloads.isEmpty {
                    Section {
                        ForEach(Array(offlineDownloads.activeDownloads.keys.sorted()), id: \.self) { tid in
                            let prog = offlineDownloads.activeDownloads[tid] ?? 0
                            let activeTrack = offlineDownloads.activeTracksById[tid]
                            VStack(alignment: .leading, spacing: 6) {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(activeTrack?.title ?? tid)
                                            .font(.subheadline.weight(.semibold))
                                            .foregroundColor(SonifyTheme.textPrimary)
                                            .lineLimit(1)
                                        Text(activeTrack?.artist ?? "Downloading...")
                                            .font(.caption)
                                            .foregroundColor(SonifyTheme.textSecondary)
                                            .lineLimit(1)
                                    }
                                    Spacer()
                                    Text("\(Int((prog * 100).rounded()))%")
                                        .font(.caption.monospacedDigit().weight(.bold))
                                        .foregroundColor(SonifyTheme.accentGreen)
                                    Button {
                                        offlineDownloads.cancelDownload(trackId: tid)
                                    } label: {
                                        Image(systemName: "xmark.circle.fill")
                                            .foregroundColor(SonifyTheme.textSecondary)
                                    }
                                    .buttonStyle(.plain)
                                }
                                ProgressView(value: prog)
                                    .tint(SonifyTheme.accentGreen)
                            }
                            .padding(.vertical, 4)
                            .listRowBackground(SonifyTheme.bgSurface)
                        }
                    } header: {
                        Text("Downloading Now")
                            .foregroundColor(SonifyTheme.accentGreen)
                    }
                }

                // Downloaded Tracks List
                if filteredRecords.isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: "arrow.down.circle")
                            .font(.system(size: 40))
                            .foregroundColor(SonifyTheme.textSubdued)
                        Text(offlineDownloads.downloadedTracks.isEmpty ? "No offline downloads yet" : "No matching downloads")
                            .font(.headline)
                            .foregroundColor(SonifyTheme.textPrimary)
                        Text("Tap ••• on any song or the Download button on a playlist, album, or Liked Songs to save audio, artwork, and synced lyrics for Airplane Mode.")
                            .font(.caption)
                            .foregroundColor(SonifyTheme.textSecondary)
                            .multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 32)
                    .listRowBackground(SonifyTheme.bgSurface)
                    .listRowSeparator(.hidden)
                } else {
                    ForEach(filteredRecords) { record in
                        TrackRowView(
                            track: record.track,
                            onTap: { player.play(track: record.track, context: filteredTracks) },
                            onAddToPlaylist: { playlistTargetTrack = $0 }
                        )
                        .listRowBackground(SonifyTheme.bgSurface)
                    }
                    .onDelete { offsets in
                        let ids = offsets.compactMap { $0 < filteredRecords.count ? filteredRecords[$0].id : nil }
                        for id in ids {
                            offlineDownloads.removeDownload(trackId: id)
                        }
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
        .navigationTitle("Downloads")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if !offlineDownloads.downloadedTracks.isEmpty {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Clear All") {
                        showClearConfirm = true
                    }
                    .foregroundColor(SonifyTheme.danger)
                }
            }
        }
        .confirmationDialog(
            "Delete all offline downloads?",
            isPresented: $showClearConfirm,
            titleVisibility: .visible
        ) {
            Button("Clear \(offlineDownloads.downloadedTracks.count) Songs (\(offlineDownloads.formattedTotalSize))", role: .destructive) {
                offlineDownloads.clearAllDownloads()
                library.showToast("Cleared all offline downloads")
            }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(item: $playlistTargetTrack) { track in
            AddToPlaylistSheet(track: track)
        }
    }
}
