import SwiftUI

struct LibraryView: View {
    @EnvironmentObject private var library: LibraryStore

    @State private var showCreateAlert = false
    @State private var newName = ""
    @State private var newDescription = ""

    var body: some View {
        NavigationStack {
            ZStack {
                SonifyTheme.bgSurface.ignoresSafeArea()

                List {
                    // Liked Songs Pin
                    NavigationLink {
                        LikedSongsView()
                    } label: {
                        HStack(spacing: 12) {
                            ZStack {
                                LinearGradient(
                                    colors: [Color(hex: 0x450AF5), Color(hex: 0xC4EFD9)],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                                Image(systemName: "heart.fill")
                                    .font(.title3)
                                    .foregroundColor(.white)
                            }
                            .frame(width: 54, height: 54)
                            .cornerRadius(8)

                            VStack(alignment: .leading, spacing: 3) {
                                Text("Liked Songs")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundColor(SonifyTheme.textPrimary)
                                Text("Playlist • \(library.likedTracks.count) songs")
                                    .font(.caption)
                                    .foregroundColor(SonifyTheme.textSecondary)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .listRowBackground(SonifyTheme.bgSurface)

                    // Recently Played Pin
                    NavigationLink {
                        HistoryView()
                    } label: {
                        HStack(spacing: 12) {
                            ZStack {
                                RoundedRectangle(cornerRadius: 8)
                                    .fill(SonifyTheme.bgHighlight)
                                Image(systemName: "clock.arrow.circlepath")
                                    .font(.title3)
                                    .foregroundColor(SonifyTheme.accentGreen)
                            }
                            .frame(width: 54, height: 54)

                            VStack(alignment: .leading, spacing: 3) {
                                Text("Recently Played")
                                    .font(.subheadline.weight(.bold))
                                    .foregroundColor(SonifyTheme.textPrimary)
                                Text("History • \(library.history.count) plays")
                                    .font(.caption)
                                    .foregroundColor(SonifyTheme.textSecondary)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                    .listRowBackground(SonifyTheme.bgSurface)

                    // Custom Playlists
                    Section {
                        if library.playlists.isEmpty {
                            Text("Tap + to create your first custom playlist.")
                                .font(.caption)
                                .foregroundColor(SonifyTheme.textSecondary)
                                .listRowBackground(SonifyTheme.bgSurface)
                        } else {
                            ForEach(library.playlists) { playlist in
                                NavigationLink {
                                    PlaylistDetailView(playlistId: playlist.id, initialPlaylist: playlist)
                                } label: {
                                    HStack(spacing: 12) {
                                        PlaylistMosaicArtworkView(covers: playlist.covers, size: 54, cornerRadius: 8)
                                        VStack(alignment: .leading, spacing: 3) {
                                            Text(playlist.name)
                                                .font(.subheadline.weight(.semibold))
                                                .foregroundColor(SonifyTheme.textPrimary)
                                                .lineLimit(1)
                                            Text("Playlist • \(playlist.trackCount) songs")
                                                .font(.caption)
                                                .foregroundColor(SonifyTheme.textSecondary)
                                        }
                                    }
                                    .padding(.vertical, 4)
                                }
                                .listRowBackground(SonifyTheme.bgSurface)
                            }
                            .onDelete { offsets in
                                for idx in offsets where idx < library.playlists.count {
                                    let id = library.playlists[idx].id
                                    Task { await library.deletePlaylist(id: id) }
                                }
                            }
                        }
                    } header: {
                        Text("Playlists")
                            .foregroundColor(SonifyTheme.textSecondary)
                    }

                    Color.clear
                        .frame(height: 80)
                        .listRowBackground(Color.clear)
                        .listRowSeparator(.hidden)
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .refreshable {
                    await library.hydrateAll()
                }
            }
            .navigationTitle("Your Library")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        newName = ""
                        newDescription = ""
                        showCreateAlert = true
                    } label: {
                        Image(systemName: "plus")
                            .font(.headline.weight(.bold))
                            .foregroundColor(SonifyTheme.textPrimary)
                    }
                }
            }
            .sheet(isPresented: $showCreateAlert) {
                createPlaylistSheet
            }
        }
    }

    private var createPlaylistSheet: some View {
        NavigationStack {
            ZStack {
                SonifyTheme.bgSurface.ignoresSafeArea()
                VStack(spacing: 16) {
                    TextField("Playlist name", text: $newName)
                        .padding(12)
                        .background(SonifyTheme.bgHighlight)
                        .foregroundColor(SonifyTheme.textPrimary)
                        .cornerRadius(8)

                    TextField("Description (optional)", text: $newDescription)
                        .padding(12)
                        .background(SonifyTheme.bgHighlight)
                        .foregroundColor(SonifyTheme.textPrimary)
                        .cornerRadius(8)

                    Button {
                        let name = newName
                        let desc = newDescription
                        showCreateAlert = false
                        Task { _ = await library.createPlaylist(name: name, description: desc) }
                    } label: {
                        Text("Create Playlist")
                            .font(.headline)
                            .foregroundColor(.black)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 14)
                            .background(SonifyTheme.accentGreen)
                            .clipShape(Capsule())
                    }
                    .disabled(newName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                    Spacer()
                }
                .padding()
            }
            .navigationTitle("New Playlist")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { showCreateAlert = false }
                        .foregroundColor(SonifyTheme.textPrimary)
                }
            }
        }
        .presentationDetents([.medium])
        .preferredColorScheme(.dark)
    }
}
