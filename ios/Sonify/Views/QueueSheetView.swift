import SwiftUI

struct QueueSheetView: View {
    @EnvironmentObject private var player: AudioPlayerService
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ZStack {
                SonifyTheme.bgSurface.ignoresSafeArea()

                List {
                    if let current = player.currentTrack {
                        Section("Now Playing") {
                            HStack(spacing: 12) {
                                ArtworkView(urlString: current.bestCoverURL, size: 48, cornerRadius: 6)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(current.title)
                                        .font(.subheadline.weight(.bold))
                                        .foregroundColor(SonifyTheme.accentGreen)
                                        .lineLimit(1)
                                    Text(current.artist)
                                        .font(.caption)
                                        .foregroundColor(SonifyTheme.textSecondary)
                                        .lineLimit(1)
                                }
                                Spacer()
                                Image(systemName: player.isPlaying ? "waveform" : "pause.fill")
                                    .foregroundColor(SonifyTheme.accentGreen)
                            }
                            .listRowBackground(SonifyTheme.bgElevated)
                        }
                    }

                    if !player.queueEngine.upNext.isEmpty {
                        Section("Next In Queue") {
                            ForEach(player.queueEngine.upNext) { track in
                                Button {
                                    player.play(track: track)
                                } label: {
                                    HStack(spacing: 12) {
                                        ArtworkView(urlString: track.bestCoverURL, size: 44, cornerRadius: 6)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(track.title)
                                                .font(.subheadline.weight(.semibold))
                                                .foregroundColor(SonifyTheme.textPrimary)
                                                .lineLimit(1)
                                            Text(track.artist)
                                                .font(.caption)
                                                .foregroundColor(SonifyTheme.textSecondary)
                                                .lineLimit(1)
                                        }
                                    }
                                }
                                .listRowBackground(SonifyTheme.bgSurface)
                            }
                            .onDelete { offsets in
                                player.removeUpNext(at: offsets)
                            }
                            .onMove { source, destination in
                                player.moveUpNext(from: source, to: destination)
                            }
                        }
                    }

                    let contextRemaining = Array(player.queueEngine.upcomingTracks.dropFirst(player.queueEngine.upNext.count))
                    if !contextRemaining.isEmpty {
                        Section("Up Next") {
                            ForEach(contextRemaining) { track in
                                Button {
                                    player.play(track: track, context: player.queueEngine.queue)
                                } label: {
                                    HStack(spacing: 12) {
                                        ArtworkView(urlString: track.bestCoverURL, size: 44, cornerRadius: 6)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(track.title)
                                                .font(.subheadline.weight(.semibold))
                                                .foregroundColor(SonifyTheme.textPrimary)
                                                .lineLimit(1)
                                            Text(track.artist)
                                                .font(.caption)
                                                .foregroundColor(SonifyTheme.textSecondary)
                                                .lineLimit(1)
                                        }
                                    }
                                }
                                .listRowBackground(SonifyTheme.bgSurface)
                            }
                        }
                    }
                }
                .listStyle(.insetGrouped)
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("Queue")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    if !player.queueEngine.upNext.isEmpty {
                        EditButton()
                    }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .foregroundColor(SonifyTheme.accentGreen)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}
