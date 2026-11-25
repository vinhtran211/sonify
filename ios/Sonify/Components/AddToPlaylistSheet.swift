import SwiftUI

struct AddToPlaylistSheet: View {
    let track: Track
    @EnvironmentObject private var library: LibraryStore
    @Environment(\.dismiss) private var dismiss

    @State private var showCreateField = false
    @State private var newPlaylistName = ""
    @State private var isCreating = false

    var body: some View {
        NavigationStack {
            ZStack {
                SonifyTheme.bgSurface.ignoresSafeArea()
                VStack(spacing: 16) {
                    HStack(spacing: 12) {
                        ArtworkView(urlString: track.bestCoverURL, size: 48, cornerRadius: 6)
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
                        Spacer()
                    }
                    .padding(.horizontal)

                    if showCreateField {
                        HStack(spacing: 10) {
                            TextField("Playlist name", text: $newPlaylistName)
                                .textFieldStyle(.plain)
                                .padding(10)
                                .background(SonifyTheme.bgHighlight)
                                .foregroundColor(SonifyTheme.textPrimary)
                                .cornerRadius(8)
                            Button("Create") {
                                createAndAdd()
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(SonifyTheme.accentGreen)
                            .disabled(newPlaylistName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isCreating)
                        }
                        .padding(.horizontal)
                    } else {
                        Button {
                            showCreateField = true
                        } label: {
                            Label("New Playlist", systemImage: "plus.circle.fill")
                                .font(.subheadline.weight(.semibold))
                                .foregroundColor(.black)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 12)
                                .background(SonifyTheme.accentGreen)
                                .cornerRadius(24)
                        }
                        .padding(.horizontal)
                    }

                    if library.playlists.isEmpty {
                        Spacer()
                        VStack(spacing: 8) {
                            Image(systemName: "music.note.list")
                                .font(.largeTitle)
                                .foregroundColor(SonifyTheme.textSubdued)
                            Text("No playlists yet")
                                .font(.headline)
                                .foregroundColor(SonifyTheme.textPrimary)
                            Text("Create one above to save this song.")
                                .font(.caption)
                                .foregroundColor(SonifyTheme.textSecondary)
                        }
                        Spacer()
                    } else {
                        List {
                            ForEach(library.playlists) { playlist in
                                Button {
                                    Task {
                                        await library.addTrack(track, toPlaylistId: playlist.id)
                                        dismiss()
                                    }
                                } label: {
                                    HStack(spacing: 12) {
                                        PlaylistMosaicArtworkView(covers: playlist.covers, size: 46, cornerRadius: 6)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(playlist.name)
                                                .font(.subheadline.weight(.semibold))
                                                .foregroundColor(SonifyTheme.textPrimary)
                                            Text("\(playlist.trackCount) songs")
                                                .font(.caption)
                                                .foregroundColor(SonifyTheme.textSecondary)
                                        }
                                        Spacer()
                                        Image(systemName: "plus")
                                            .foregroundColor(SonifyTheme.textSecondary)
                                    }
                                }
                                .listRowBackground(SonifyTheme.bgElevated)
                            }
                        }
                        .listStyle(.plain)
                        .scrollContentBackground(.hidden)
                    }
                }
                .padding(.top, 12)
            }
            .navigationTitle("Add to Playlist")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .foregroundColor(SonifyTheme.textPrimary)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func createAndAdd() {
        let name = newPlaylistName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        isCreating = true
        Task {
            if let created = await library.createPlaylist(name: name) {
                await library.addTrack(track, toPlaylistId: created.id)
                dismiss()
            }
            isCreating = false
        }
    }
}
