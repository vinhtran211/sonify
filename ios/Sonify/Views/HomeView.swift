import SwiftUI

struct HomeView: View {
    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService

    @State private var homeData: HomeResponse?
    @State private var isLoading = false
    @State private var errorText: String?
    @State private var showSettings = false
    @State private var playlistTargetTrack: Track?

    var body: some View {
        NavigationStack {
            ZStack {
                SonifyTheme.bgSurface.ignoresSafeArea()

                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 24) {
                        headerBar

                        if let homeData, homeData.mode == "offline" {
                            HStack(spacing: 8) {
                                Image(systemName: "wifi.slash")
                                    .foregroundColor(SonifyTheme.warning)
                                Text("Offline Showcase Mode — showing local demo catalog")
                                    .font(.caption)
                                    .foregroundColor(SonifyTheme.warning)
                                Spacer()
                            }
                            .padding(10)
                            .background(SonifyTheme.warning.opacity(0.14))
                            .cornerRadius(8)
                            .padding(.horizontal)
                        }

                        if isLoading && homeData == nil {
                            loadingSkeleton
                        } else if let errorText, homeData == nil {
                            errorState(errorText)
                        } else if let homeData {
                            // Quick picks 2-column Spotify grid from first tracks section
                            if let firstTracks = homeData.sections.first(where: { !$0.tracks.isEmpty })?.tracks,
                               !firstTracks.isEmpty {
                                quickPicksGrid(tracks: Array(firstTracks.prefix(6)), context: firstTracks)
                            }

                            // Made For You personalized mixes carousel
                            if !library.madeForYouMixes.isEmpty {
                                madeForYouSection(library.madeForYouMixes)
                            }

                            ForEach(homeData.sections.filter { !$0.isEmpty }) { section in
                                sectionView(section)
                            }
                        }

                        // Reserve space for floating MiniPlayerBar
                        Color.clear.frame(height: 88)
                    }
                    .padding(.top, 8)
                }
                .refreshable {
                    await loadHome(refresh: true)
                    await library.loadMadeForYou(force: true)
                }
            }
            .navigationBarHidden(true)
            .sheet(isPresented: $showSettings) {
                SettingsView()
            }
            .sheet(item: $playlistTargetTrack) { track in
                AddToPlaylistSheet(track: track)
            }
            .task {
                if homeData == nil {
                    await loadHome(refresh: false)
                }
                await library.loadMadeForYou(force: false)
            }
            .onChange(of: library.region) { _ in
                Task {
                    await loadHome(refresh: false)
                    await library.loadMadeForYou(force: true)
                }
            }
            .onChange(of: api.baseURLString) { _ in
                Task {
                    await loadHome(refresh: true)
                    await library.loadMadeForYou(force: true)
                }
            }
        }
    }

    private var headerBar: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(SonifyTheme.greetingForCurrentHour())
                    .font(.title2.weight(.bold))
                    .foregroundColor(SonifyTheme.textPrimary)
                HStack(spacing: 6) {
                    Circle()
                        .fill(SonifyTheme.accentGreen)
                        .frame(width: 6, height: 6)
                    Text("100% Ad-Free")
                        .font(.caption2.weight(.semibold))
                        .foregroundColor(SonifyTheme.accentGreen)
                }
            }

            Spacer()

            // Global / Vietnam region switcher pill
            HStack(spacing: 2) {
                regionChip(label: "Global", code: "global")
                regionChip(label: "VN", code: "vn")
            }
            .padding(3)
            .background(SonifyTheme.bgHighlight)
            .clipShape(Capsule())

            NavigationLink {
                StatsView()
            } label: {
                Image(systemName: "chart.bar.fill")
                    .font(.system(size: 16))
                    .foregroundColor(SonifyTheme.accentGreen)
                    .frame(width: 36, height: 36)
                    .background(SonifyTheme.bgHighlight)
                    .clipShape(Circle())
            }
            .buttonStyle(.plain)

            Button {
                showSettings = true
            } label: {
                Image(systemName: "gearshape.fill")
                    .font(.system(size: 18))
                    .foregroundColor(SonifyTheme.textPrimary)
                    .frame(width: 36, height: 36)
                    .background(SonifyTheme.bgHighlight)
                    .clipShape(Circle())
            }
        }
        .padding(.horizontal)
    }

    private func regionChip(label: String, code: String) -> some View {
        let active = library.region == code
        return Button {
            Task { await library.setRegion(code) }
        } label: {
            Text(label)
                .font(.caption.weight(.bold))
                .foregroundColor(active ? .black : SonifyTheme.textPrimary)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(active ? SonifyTheme.accentGreen : Color.clear)
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
    }

    private func madeForYouSection(_ mixes: [MadeForYouMix]) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Made For You")
                    .font(.title3.weight(.bold))
                    .foregroundColor(SonifyTheme.textPrimary)
                Text("Daily Mixes, Discover Weekly & Release Radar")
                    .font(.caption)
                    .foregroundColor(SonifyTheme.textSecondary)
            }
            .padding(.horizontal)

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 14) {
                    ForEach(mixes) { mix in
                        NavigationLink {
                            MixDetailView(mix: mix)
                        } label: {
                            VStack(alignment: .leading, spacing: 8) {
                                ZStack(alignment: .bottomLeading) {
                                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                                        .fill(
                                            LinearGradient(
                                                colors: [Color(hexString: mix.color), SonifyTheme.bgElevated],
                                                startPoint: .topLeading,
                                                endPoint: .bottomTrailing
                                            )
                                        )
                                        .frame(width: 148, height: 148)
                                    if !mix.cover.isEmpty {
                                        ArtworkView(urlString: mix.cover, size: 148, cornerRadius: 10)
                                            .opacity(0.82)
                                    }
                                    Text(mix.title)
                                        .font(.caption.weight(.heavy))
                                        .foregroundColor(.white)
                                        .padding(.horizontal, 8)
                                        .padding(.vertical, 4)
                                        .background(Color.black.opacity(0.65))
                                        .cornerRadius(6)
                                        .padding(8)
                                }
                                Text(mix.title)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundColor(SonifyTheme.textPrimary)
                                    .lineLimit(1)
                                Text(mix.subtitle.isEmpty ? "\(mix.tracks.count) songs" : mix.subtitle)
                                    .font(.caption)
                                    .foregroundColor(SonifyTheme.textSecondary)
                                    .lineLimit(1)
                            }
                            .frame(width: 148)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal)
            }
        }
    }

    private func quickPicksGrid(tracks: [Track], context: [Track]) -> some View {
        LazyVGrid(
            columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)],
            spacing: 8
        ) {
            ForEach(tracks) { track in
                Button {
                    player.play(track: track, context: context)
                } label: {
                    HStack(spacing: 8) {
                        ArtworkView(urlString: track.bestCoverURL, size: 52, cornerRadius: 4)
                        Text(track.title)
                            .font(.caption.weight(.bold))
                            .foregroundColor(
                                player.currentTrack?.id == track.id
                                    ? SonifyTheme.accentGreen
                                    : SonifyTheme.textPrimary
                            )
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                        Spacer(minLength: 4)
                    }
                    .background(SonifyTheme.bgHighlight.opacity(0.85))
                    .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal)
    }

    @ViewBuilder
    private func sectionView(_ section: HomeSection) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(section.title)
                    .font(.title3.weight(.bold))
                    .foregroundColor(SonifyTheme.textPrimary)
                if let sub = section.subtitle, !sub.isEmpty {
                    Text(sub)
                        .font(.caption)
                        .foregroundColor(SonifyTheme.textSecondary)
                }
            }
            .padding(.horizontal)

            ScrollView(.horizontal, showsIndicators: false) {
                LazyHStack(alignment: .top, spacing: 14) {
                    if !section.tracks.isEmpty {
                        ForEach(section.tracks) { track in
                            trackCard(track, context: section.tracks)
                        }
                    } else if !section.albums.isEmpty {
                        ForEach(section.albums) { album in
                            NavigationLink {
                                AlbumDetailView(albumId: album.id, initialAlbum: album)
                            } label: {
                                albumCard(album)
                            }
                            .buttonStyle(.plain)
                        }
                    } else if !section.artists.isEmpty {
                        ForEach(section.artists) { artist in
                            NavigationLink {
                                ArtistDetailView(artistId: artist.id, initialArtist: artist)
                            } label: {
                                artistCard(artist)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .padding(.horizontal)
            }
        }
    }

    private func trackCard(_ track: Track, context: [Track]) -> some View {
        Button {
            player.play(track: track, context: context)
        } label: {
            VStack(alignment: .leading, spacing: 8) {
                ZStack(alignment: .bottomTrailing) {
                    ArtworkView(urlString: track.bestCoverURL, size: 144, cornerRadius: 8)
                    Image(systemName: player.currentTrack?.id == track.id && player.isPlaying ? "pause.circle.fill" : "play.circle.fill")
                        .font(.system(size: 36))
                        .foregroundColor(SonifyTheme.accentGreen)
                        .background(Circle().fill(Color.black.opacity(0.65)).padding(4))
                        .padding(6)
                }
                Text(track.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundColor(
                        player.currentTrack?.id == track.id ? SonifyTheme.accentGreen : SonifyTheme.textPrimary
                    )
                    .lineLimit(1)
                Text(track.artist)
                    .font(.caption)
                    .foregroundColor(SonifyTheme.textSecondary)
                    .lineLimit(1)
            }
            .frame(width: 144)
        }
        .buttonStyle(.plain)
        .contextMenu {
            Button {
                player.enqueueNext(track)
            } label: {
                Label("Add to Queue", systemImage: "text.badge.plus")
            }
            Button {
                player.startRadio(for: track)
            } label: {
                Label("Go to Song Radio", systemImage: "dot.radiowaves.left.and.right")
            }
            Button {
                Task { await library.toggleLike(track) }
            } label: {
                Label(
                    library.isLiked(track.id) ? "Remove from Liked Songs" : "Save to Liked Songs",
                    systemImage: library.isLiked(track.id) ? "heart.slash" : "heart"
                )
            }
            Button {
                playlistTargetTrack = track
            } label: {
                Label("Add to Playlist", systemImage: "plus.circle")
            }
        }
    }

    private func albumCard(_ album: Album) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ArtworkView(urlString: album.bestCoverURL, size: 144, cornerRadius: 8, symbolName: "square.stack")
            Text(album.title)
                .font(.subheadline.weight(.semibold))
                .foregroundColor(SonifyTheme.textPrimary)
                .lineLimit(1)
            Text(album.artist)
                .font(.caption)
                .foregroundColor(SonifyTheme.textSecondary)
                .lineLimit(1)
        }
        .frame(width: 144)
    }

    private func artistCard(_ artist: Artist) -> some View {
        VStack(spacing: 8) {
            ArtworkView(
                urlString: artist.bestImageURL,
                size: 132,
                isCircular: true,
                symbolName: "person.fill"
            )
            Text(artist.name)
                .font(.subheadline.weight(.semibold))
                .foregroundColor(SonifyTheme.textPrimary)
                .lineLimit(1)
            Text("Artist")
                .font(.caption2)
                .foregroundColor(SonifyTheme.textSecondary)
        }
        .frame(width: 132)
    }

    private var loadingSkeleton: some View {
        VStack(spacing: 16) {
            ProgressView()
                .tint(SonifyTheme.accentGreen)
                .scaleEffect(1.2)
            Text("Loading live catalog...")
                .font(.subheadline)
                .foregroundColor(SonifyTheme.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 90)
    }

    private func errorState(_ message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "antenna.radiowaves.left.and.right.slash")
                .font(.largeTitle)
                .foregroundColor(SonifyTheme.textSubdued)
            Text("Could not reach Sonify server")
                .font(.headline)
                .foregroundColor(SonifyTheme.textPrimary)
            Text(message)
                .font(.caption)
                .foregroundColor(SonifyTheme.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
            HStack(spacing: 12) {
                Button("Retry") {
                    Task { await loadHome(refresh: true) }
                }
                .buttonStyle(.borderedProminent)
                .tint(SonifyTheme.accentGreen)
                .foregroundColor(.black)

                Button("Server Settings") {
                    showSettings = true
                }
                .buttonStyle(.bordered)
                .foregroundColor(SonifyTheme.textPrimary)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 80)
    }

    private func loadHome(refresh: Bool) async {
        isLoading = true
        errorText = nil
        do {
            homeData = try await api.getHome(region: library.region, refresh: refresh)
        } catch is CancellationError {
            return
        } catch {
            errorText = error.localizedDescription
        }
        isLoading = false
    }
}
