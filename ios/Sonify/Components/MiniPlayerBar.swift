import SwiftUI

struct MiniPlayerBar: View {
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var offlineDownloads: OfflineDownloadManager

    var body: some View {
        if let track = player.currentTrack {
            VStack(spacing: 0) {
                HStack(spacing: 10) {
                    ArtworkView(
                        urlString: track.bestCoverURL,
                        localImageData: offlineDownloads.localArtworkData(for: track.id),
                        size: 44,
                        cornerRadius: 6
                    )

                    VStack(alignment: .leading, spacing: 2) {
                        Text(track.title)
                            .font(.subheadline.weight(.semibold))
                            .foregroundColor(SonifyTheme.textPrimary)
                            .lineLimit(1)
                        HStack(spacing: 5) {
                            if player.streamInfo?.isOfflineDownload == true || offlineDownloads.isDownloaded(track.id) {
                                Image(systemName: "arrow.down.circle.fill")
                                    .font(.system(size: 10))
                                    .foregroundColor(SonifyTheme.accentGreen)
                            }
                            Text(track.artist)
                                .font(.caption)
                                .foregroundColor(SonifyTheme.textSecondary)
                                .lineLimit(1)
                            if let info = player.streamInfo, info.isPreview {
                                Text("30s")
                                    .font(.system(size: 9, weight: .bold))
                                    .foregroundColor(.black)
                                    .padding(.horizontal, 4)
                                    .padding(.vertical, 1)
                                    .background(SonifyTheme.warning)
                                    .cornerRadius(3)
                            }
                        }
                    }

                    Spacer(minLength: 4)

                    Button {
                        Task { await library.toggleLike(track) }
                    } label: {
                        Image(systemName: library.isLiked(track.id) ? "heart.fill" : "heart")
                            .font(.system(size: 18))
                            .foregroundColor(library.isLiked(track.id) ? SonifyTheme.accentGreen : SonifyTheme.textSecondary)
                            .frame(width: 34, height: 34)
                    }
                    .buttonStyle(.plain)

                    Button {
                        player.togglePlayPause()
                    } label: {
                        if player.isLoading {
                            ProgressView()
                                .tint(SonifyTheme.textPrimary)
                                .frame(width: 36, height: 36)
                        } else {
                            Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                                .font(.system(size: 20, weight: .bold))
                                .foregroundColor(SonifyTheme.textPrimary)
                                .frame(width: 36, height: 36)
                        }
                    }
                    .buttonStyle(.plain)

                    Button {
                        player.nextTrack(fromAutoEnd: false)
                    } label: {
                        Image(systemName: "forward.fill")
                            .font(.system(size: 16))
                            .foregroundColor(SonifyTheme.textPrimary)
                            .frame(width: 32, height: 34)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 8)

                // Thin bottom playback progress bar
                GeometryReader { geo in
                    let progress = player.duration > 0 ? min(max(player.currentTime / player.duration, 0), 1) : 0
                    ZStack(alignment: .leading) {
                        Rectangle()
                            .fill(Color.white.opacity(0.15))
                        Rectangle()
                            .fill(SonifyTheme.accentGreen)
                            .frame(width: geo.size.width * progress)
                    }
                }
                .frame(height: 2.5)
            }
            .background(SonifyTheme.bgElevated)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .stroke(SonifyTheme.borderSubtle, lineWidth: 1)
            )
            .shadow(color: Color.black.opacity(0.45), radius: 8, y: 4)
            .padding(.horizontal, 8)
            .contentShape(Rectangle())
            .onTapGesture {
                player.showNowPlayingSheet = true
            }
        }
    }
}
