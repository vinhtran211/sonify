import SwiftUI

struct LyricsView: View {
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var lyricsStore: LyricsStore

    private var activeIndex: Int? {
        LyricsSyncEngine.activeLineIndex(in: lyricsStore.resolvedLines, at: player.currentTime)
    }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(Color.black.opacity(0.32))

            if lyricsStore.isLoading {
                VStack(spacing: 12) {
                    ProgressView()
                        .tint(SonifyTheme.accentGreen)
                    Text("Fetching synced lyrics...")
                        .font(.subheadline)
                        .foregroundColor(SonifyTheme.textSecondary)
                }
            } else if let data = lyricsStore.lyrics {
                if data.instrumental || data.status == "instrumental" {
                    emptyLyricsState(
                        icon: "pianokeys",
                        title: "Instrumental Track",
                        subtitle: "Enjoy the music — no vocals detected."
                    )
                } else if !lyricsStore.resolvedLines.isEmpty {
                    syncedLyricsScroll
                } else if !data.plainLyrics.isEmpty {
                    ScrollView {
                        Text(data.plainLyrics)
                            .font(.title3.weight(.semibold))
                            .foregroundColor(SonifyTheme.textPrimary.opacity(0.9))
                            .lineSpacing(8)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(20)
                    }
                } else {
                    emptyLyricsState(
                        icon: "music.mic",
                        title: "Lyrics Unavailable",
                        subtitle: "We couldn't find lyrics for this track yet."
                    )
                }
            } else {
                emptyLyricsState(
                    icon: "music.mic",
                    title: "No Lyrics Loaded",
                    subtitle: "Start playing a song to see real-time synced lyrics."
                )
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var syncedLyricsScroll: some View {
        ScrollViewReader { proxy in
            ScrollView(showsIndicators: false) {
                LazyVStack(alignment: .leading, spacing: 18) {
                    ForEach(Array(lyricsStore.resolvedLines.enumerated()), id: \.offset) { idx, line in
                        let isActive = idx == activeIndex
                        let isPast = (activeIndex ?? -1) > idx

                        Button {
                            player.seek(to: line.time)
                        } label: {
                            Text(line.text)
                                .font(isActive ? .title2.weight(.bold) : .title3.weight(.semibold))
                                .foregroundColor(
                                    isActive
                                        ? SonifyTheme.textPrimary
                                        : (isPast ? SonifyTheme.textPrimary.opacity(0.45) : SonifyTheme.textPrimary.opacity(0.62))
                                )
                                .multilineTextAlignment(.leading)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .scaleEffect(isActive ? 1.02 : 1.0, anchor: .leading)
                                .animation(.easeOut(duration: 0.18), value: isActive)
                        }
                        .buttonStyle(.plain)
                        .id(idx)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 28)
            }
            .onChange(of: activeIndex) { newIdx in
                if let newIdx {
                    withAnimation(.easeInOut(duration: 0.3)) {
                        proxy.scrollTo(newIdx, anchor: .center)
                    }
                }
            }
        }
    }

    private func emptyLyricsState(icon: String, title: String, subtitle: String) -> some View {
        VStack(spacing: 10) {
            Image(systemName: icon)
                .font(.largeTitle)
                .foregroundColor(SonifyTheme.textSecondary)
            Text(title)
                .font(.headline)
                .foregroundColor(SonifyTheme.textPrimary)
            Text(subtitle)
                .font(.caption)
                .foregroundColor(SonifyTheme.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
            if let track = player.currentTrack {
                Button("Retry Lyrics") {
                    lyricsStore.loadLyrics(for: track, forceRefresh: true)
                }
                .font(.caption.weight(.bold))
                .foregroundColor(SonifyTheme.accentGreen)
                .padding(.top, 4)
            }
        }
        .padding()
    }
}
