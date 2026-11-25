import SwiftUI

struct NowPlayingView: View {
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var lyricsStore: LyricsStore
    @Environment(\.dismiss) private var dismiss

    @State private var isDraggingSlider = false
    @State private var dragPosition: Double = 0
    @State private var showLyrics = false
    @State private var showQueueSheet = false
    @State private var showAddToPlaylist = false

    private var displayedTime: Double {
        isDraggingSlider ? dragPosition : player.currentTime
    }

    var body: some View {
        ZStack {
            // Dynamic dark Spotify gradient backdrop
            LinearGradient(
                colors: [Color(hex: 0x1E3A2A), SonifyTheme.bgSurface, SonifyTheme.bgBase],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()

            if let track = player.currentTrack {
                VStack(spacing: 18) {
                    topBar(track: track)

                    if showLyrics {
                        LyricsView()
                            .transition(.opacity)
                    } else {
                        Spacer(minLength: 4)
                        ArtworkView(urlString: track.bestCoverURL, size: 310, cornerRadius: 12)
                            .shadow(color: .black.opacity(0.6), radius: 24, y: 12)
                        Spacer(minLength: 4)
                    }

                    trackMetadataRow(track: track)
                    seekSliderSection
                    transportControls
                    bottomUtilityRow(track: track)
                }
                .padding(.horizontal, 24)
                .padding(.vertical, 12)
                .sheet(isPresented: $showQueueSheet) {
                    QueueSheetView()
                }
                .sheet(isPresented: $showAddToPlaylist) {
                    AddToPlaylistSheet(track: track)
                }
            } else {
                VStack(spacing: 12) {
                    Text("Nothing Playing")
                        .font(.headline)
                        .foregroundColor(SonifyTheme.textPrimary)
                    Button("Close") { dismiss() }
                        .foregroundColor(SonifyTheme.accentGreen)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func topBar(track: Track) -> some View {
        HStack {
            Button {
                dismiss()
            } label: {
                Image(systemName: "chevron.down")
                    .font(.title3.weight(.semibold))
                    .foregroundColor(SonifyTheme.textPrimary)
                    .frame(width: 36, height: 36)
            }

            Spacer()

            VStack(spacing: 2) {
                Text("NOW PLAYING")
                    .font(.system(size: 10, weight: .bold))
                    .tracking(1.2)
                    .foregroundColor(SonifyTheme.textSecondary)
                HStack(spacing: 6) {
                    if let info = player.streamInfo {
                        Text(info.qualityBadgeLabel)
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(info.isPreview ? .black : SonifyTheme.accentGreen)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .background(info.isPreview ? SonifyTheme.warning : SonifyTheme.accentGreen.opacity(0.18))
                            .clipShape(Capsule())
                    }
                    Text("Ad-Free")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(SonifyTheme.textSecondary)
                }
            }

            Spacer()

            Button {
                showAddToPlaylist = true
            } label: {
                Image(systemName: "plus.circle")
                    .font(.title3)
                    .foregroundColor(SonifyTheme.textPrimary)
                    .frame(width: 36, height: 36)
            }
        }
    }

    private func trackMetadataRow(track: Track) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(track.title)
                    .font(.title3.weight(.bold))
                    .foregroundColor(SonifyTheme.textPrimary)
                    .lineLimit(1)
                Text(track.artist)
                    .font(.subheadline)
                    .foregroundColor(SonifyTheme.textSecondary)
                    .lineLimit(1)
            }

            Spacer()

            Button {
                Task { await library.toggleLike(track) }
            } label: {
                Image(systemName: library.isLiked(track.id) ? "heart.fill" : "heart")
                    .font(.title2)
                    .foregroundColor(library.isLiked(track.id) ? SonifyTheme.accentGreen : SonifyTheme.textPrimary)
            }
        }
    }

    private var seekSliderSection: some View {
        VStack(spacing: 4) {
            let maxDur = max(player.duration, 1)
            Slider(
                value: Binding(
                    get: { min(displayedTime, maxDur) },
                    set: { newVal in
                        dragPosition = newVal
                    }
                ),
                in: 0...maxDur,
                onEditingChanged: { editing in
                    isDraggingSlider = editing
                    if !editing {
                        player.seek(to: dragPosition)
                    }
                }
            )
            .tint(SonifyTheme.accentGreen)

            HStack {
                Text(SonifyTheme.formatDuration(displayedTime))
                    .font(.caption2.monospacedDigit())
                    .foregroundColor(SonifyTheme.textSecondary)
                Spacer()
                Text(SonifyTheme.formatDuration(player.duration))
                    .font(.caption2.monospacedDigit())
                    .foregroundColor(SonifyTheme.textSecondary)
            }
        }
    }

    private var transportControls: some View {
        HStack {
            Button {
                player.toggleShuffle()
            } label: {
                Image(systemName: "shuffle")
                    .font(.title3)
                    .foregroundColor(player.queueEngine.shuffle ? SonifyTheme.accentGreen : SonifyTheme.textSecondary)
            }

            Spacer()

            Button {
                player.previousTrack()
            } label: {
                Image(systemName: "backward.end.fill")
                    .font(.title)
                    .foregroundColor(SonifyTheme.textPrimary)
            }

            Spacer()

            Button {
                player.togglePlayPause()
            } label: {
                ZStack {
                    Circle()
                        .fill(Color.white)
                        .frame(width: 66, height: 66)
                    if player.isLoading {
                        ProgressView()
                            .tint(.black)
                    } else {
                        Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                            .font(.system(size: 28, weight: .bold))
                            .foregroundColor(.black)
                            .offset(x: player.isPlaying ? 0 : 2)
                    }
                }
            }

            Spacer()

            Button {
                player.nextTrack(fromAutoEnd: false)
            } label: {
                Image(systemName: "forward.end.fill")
                    .font(.title)
                    .foregroundColor(SonifyTheme.textPrimary)
            }

            Spacer()

            Button {
                player.cycleRepeat()
            } label: {
                let mode = player.queueEngine.repeatMode
                Image(systemName: mode == .one ? "repeat.1" : "repeat")
                    .font(.title3)
                    .foregroundColor(mode == .off ? SonifyTheme.textSecondary : SonifyTheme.accentGreen)
            }
        }
        .padding(.vertical, 4)
    }

    private func bottomUtilityRow(track: Track) -> some View {
        HStack {
            AirPlayButton()
                .frame(width: 32, height: 32)

            Spacer()

            Button {
                withAnimation(.easeInOut(duration: 0.2)) {
                    showLyrics.toggle()
                }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "quote.bubble.fill")
                    Text("Lyrics")
                        .font(.caption.weight(.bold))
                }
                .foregroundColor(showLyrics ? .black : SonifyTheme.textPrimary)
                .padding(.horizontal, 14)
                .padding(.vertical, 8)
                .background(showLyrics ? SonifyTheme.accentGreen : SonifyTheme.bgHighlight)
                .clipShape(Capsule())
            }

            Spacer()

            Button {
                showQueueSheet = true
            } label: {
                Image(systemName: "list.bullet")
                    .font(.title3)
                    .foregroundColor(SonifyTheme.textPrimary)
                    .frame(width: 32, height: 32)
            }
        }
    }
}
