import SwiftUI

struct TrackRowView: View {
    let track: Track
    var indexNumber: Int? = nil
    var showArtwork: Bool = true
    var onTap: () -> Void
    var onAddToPlaylist: ((Track) -> Void)? = nil

    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var offlineDownloads: OfflineDownloadManager

    private var isCurrent: Bool {
        player.currentTrack?.id == track.id
    }

    private var isDownloaded: Bool {
        offlineDownloads.isDownloaded(track.id)
    }

    private var downloadProgress: Double? {
        offlineDownloads.progress(for: track.id)
    }

    private var trackShareURL: URL? {
        let encoded = APIClient.encodePathSegment(track.id)
        return URL(string: "\(api.baseURLString)/#/track/\(encoded)")
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
                        ArtworkView(
                            urlString: track.bestCoverURL,
                            localImageData: offlineDownloads.localArtworkData(for: track.id),
                            size: 48,
                            cornerRadius: 6
                        )
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

                    HStack(spacing: 5) {
                        if let prog = downloadProgress {
                            ProgressView(value: prog)
                                .progressViewStyle(.circular)
                                .tint(SonifyTheme.accentGreen)
                                .scaleEffect(0.55)
                                .frame(width: 14, height: 14)
                        } else if isDownloaded {
                            Image(systemName: "arrow.down.circle.fill")
                                .font(.system(size: 11))
                                .foregroundColor(SonifyTheme.accentGreen)
                        }
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

                    if downloadProgress != nil {
                        Button(role: .destructive) {
                            offlineDownloads.cancelDownload(trackId: track.id)
                            library.showToast("Cancelled download")
                        } label: {
                            Label("Cancel Download", systemImage: "xmark.circle")
                        }
                    } else if isDownloaded {
                        Button(role: .destructive) {
                            offlineDownloads.removeDownload(trackId: track.id)
                            library.showToast("Removed from Offline Downloads")
                        } label: {
                            Label("Remove Download", systemImage: "trash")
                        }
                    } else {
                        Button {
                            let quality = library.settings.audioQuality
                            library.showToast("Downloading \"\(track.title)\"...")
                            Task {
                                let ok = await offlineDownloads.downloadTrack(track, api: api, quality: quality)
                                library.showToast(ok ? "Saved \"\(track.title)\" for offline" : "Download failed")
                            }
                        } label: {
                            Label("Download for Offline", systemImage: "arrow.down.circle")
                        }
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

                    if let onAddToPlaylist {
                        Button {
                            onAddToPlaylist(track)
                        } label: {
                            Label("Add to Playlist", systemImage: "plus.circle")
                        }
                    }

                    if let shareURL = trackShareURL {
                        ShareLink(item: shareURL, subject: Text(track.title), message: Text("\(track.title) by \(track.artist) on Sonify")) {
                            Label("Share Track", systemImage: "square.and.arrow.up")
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
