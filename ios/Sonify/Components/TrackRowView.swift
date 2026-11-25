import SwiftUI

struct TrackRowView: View {
    let track: Track
    var indexNumber: Int? = nil
    var showArtwork: Bool = true
    var onTap: () -> Void
    var onAddToPlaylist: ((Track) -> Void)? = nil

    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var library: LibraryStore

    private var isCurrent: Bool {
        player.currentTrack?.id == track.id
    }

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 12) {
                if let indexNumber {
                    Text("\(indexNumber)")
                        .font(.subheadline.monospacedDigit())
                        .foregroundColor(isCurrent ? SonifyTheme.accentGreen : SonifyTheme.textSecondary)
                        .frame(width: 24, alignment: .trailing)
                }

                if showArtwork {
                    ZStack {
                        ArtworkView(urlString: track.bestCoverURL, size: 48, cornerRadius: 6)
                        if isCurrent {
                            RoundedRectangle(cornerRadius: 6)
                                .fill(Color.black.opacity(0.45))
                                .frame(width: 48, height: 48)
                            Image(systemName: player.isPlaying ? "waveform" : "pause.fill")
                                .font(.subheadline.weight(.bold))
                                .foregroundColor(SonifyTheme.accentGreen)
                        }
                    }
                }

                VStack(alignment: .leading, spacing: 3) {
                    Text(track.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundColor(isCurrent ? SonifyTheme.accentGreen : SonifyTheme.textPrimary)
                        .lineLimit(1)

                    HStack(spacing: 6) {
                        if track.explicit {
                            Text("E")
                                .font(.system(size: 9, weight: .bold))
                                .foregroundColor(.black)
                                .padding(.horizontal, 4)
                                .padding(.vertical, 1)
                                .background(SonifyTheme.textSecondary)
                                .cornerRadius(2)
                        }
                        Text(track.artist)
                            .font(.caption)
                            .foregroundColor(SonifyTheme.textSecondary)
                            .lineLimit(1)
                    }
                }

                Spacer(minLength: 8)

                if track.duration > 0 {
                    Text(SonifyTheme.formatDuration(Double(track.duration)))
                        .font(.caption.monospacedDigit())
                        .foregroundColor(SonifyTheme.textSubdued)
                }

                Button {
                    Task { await library.toggleLike(track) }
                } label: {
                    Image(systemName: library.isLiked(track.id) ? "heart.fill" : "heart")
                        .font(.subheadline)
                        .foregroundColor(library.isLiked(track.id) ? SonifyTheme.accentGreen : SonifyTheme.textSecondary)
                        .frame(width: 32, height: 32)
                }
                .buttonStyle(.plain)

                Menu {
                    Button {
                        player.enqueueNext(track)
                    } label: {
                        Label("Add to Queue", systemImage: "text.badge.plus")
                    }

                    Button {
                        Task { await library.toggleLike(track) }
                    } label: {
                        Label(
                            library.isLiked(track.id) ? "Remove from Liked Songs" : "Save to Liked Songs",
                            systemImage: library.isLiked(track.id) ? "heart.slash" : "heart"
                        )
                    }

                    if let onAddToPlaylist {
                        Button {
                            onAddToPlaylist(track)
                        } label: {
                            Label("Add to Playlist", systemImage: "plus.circle")
                        }
                    }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.subheadline)
                        .foregroundColor(SonifyTheme.textSecondary)
                        .frame(width: 28, height: 32)
                }
            }
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
