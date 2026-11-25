import Combine
import Foundation

@MainActor
final class LibraryStore: ObservableObject {
    private static let regionDefaultsKey = "sonify.region"

    @Published private(set) var likedIds: Set<String> = []
    @Published private(set) var likedTracks: [Track] = []
    @Published private(set) var playlists: [Playlist] = []
    @Published private(set) var history: [HistoryItem] = []
    @Published var region: String
    @Published private(set) var settings: AppSettings = AppSettings()
    @Published private(set) var isSyncing: Bool = false
    @Published var toastMessage: String?

    let api: APIClient

    init(api: APIClient) {
        self.api = api
        self.region = UserDefaults.standard.string(forKey: Self.regionDefaultsKey) ?? "global"
    }

    func isLiked(_ trackId: String) -> Bool {
        likedIds.contains(trackId)
    }

    func showToast(_ message: String) {
        toastMessage = message
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_600_000_000)
            if self?.toastMessage == message {
                self?.toastMessage = nil
            }
        }
    }

    func hydrateAll() async {
        isSyncing = true
        defer { isSyncing = false }

        async let likesTask = try? api.getLikes()
        async let playlistsTask = try? api.listPlaylists()
        async let historyTask = try? api.getHistory(limit: 50)
        async let settingsTask = try? api.getSettings()

        if let likes = await likesTask {
            likedTracks = likes.tracks
            likedIds = Set(likes.likedIds)
        }
        if let pls = await playlistsTask {
            playlists = pls.playlists
        }
        if let hist = await historyTask {
            history = hist.items
        }
        if let remoteSettings = await settingsTask {
            settings = remoteSettings
            if remoteSettings.region == "global" || remoteSettings.region == "vn" {
                region = remoteSettings.region
                UserDefaults.standard.set(remoteSettings.region, forKey: Self.regionDefaultsKey)
            }
        }
    }

    func setRegion(_ newRegion: String) async {
        guard newRegion == "global" || newRegion == "vn" else { return }
        region = newRegion
        UserDefaults.standard.set(newRegion, forKey: Self.regionDefaultsKey)
        settings.region = newRegion
        _ = try? await api.patchSettings(settings)
    }

    func toggleLike(_ track: Track) async {
        let wasLiked = likedIds.contains(track.id)
        if wasLiked {
            likedIds.remove(track.id)
            likedTracks.removeAll { $0.id == track.id }
            do {
                try await api.unlikeTrack(id: track.id)
                showToast("Removed from Liked Songs")
            } catch {
                likedIds.insert(track.id)
                showToast(error.localizedDescription)
            }
        } else {
            likedIds.insert(track.id)
            var likedCopy = track
            likedCopy.liked = true
            likedTracks.insert(likedCopy, at: 0)
            do {
                try await api.likeTrack(track)
                showToast("Saved to Liked Songs")
            } catch {
                likedIds.remove(track.id)
                likedTracks.removeAll { $0.id == track.id }
                showToast(error.localizedDescription)
            }
        }
    }

    func refreshPlaylists() async {
        if let res = try? await api.listPlaylists() {
            playlists = res.playlists
        }
    }

    func createPlaylist(name: String, description: String = "") async -> Playlist? {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        do {
            let res = try await api.createPlaylist(name: trimmed, description: description)
            playlists.insert(res.playlist, at: 0)
            showToast("Created \"\(res.playlist.name)\"")
            return res.playlist
        } catch {
            showToast(error.localizedDescription)
            return nil
        }
    }

    func updatePlaylist(id: String, name: String, description: String) async -> Playlist? {
        do {
            let res = try await api.updatePlaylist(id: id, name: name, description: description)
            if let idx = playlists.firstIndex(where: { $0.id == id }) {
                playlists[idx] = res.playlist
            }
            showToast("Playlist updated")
            return res.playlist
        } catch {
            showToast(error.localizedDescription)
            return nil
        }
    }

    func deletePlaylist(id: String) async {
        do {
            try await api.deletePlaylist(id: id)
            playlists.removeAll { $0.id == id }
            showToast("Playlist deleted")
        } catch {
            showToast(error.localizedDescription)
        }
    }

    func addTrack(_ track: Track, toPlaylistId playlistId: String) async {
        do {
            let res = try await api.addTrackToPlaylist(playlistId: playlistId, track: track)
            await refreshPlaylists()
            if res.alreadyInPlaylist {
                showToast("Already in playlist")
            } else {
                showToast("Added to playlist")
            }
        } catch {
            showToast(error.localizedDescription)
        }
    }

    func removeTrack(trackId: String, fromPlaylistId playlistId: String) async {
        do {
            try await api.removeTrackFromPlaylist(playlistId: playlistId, trackId: trackId)
            await refreshPlaylists()
            showToast("Removed from playlist")
        } catch {
            showToast(error.localizedDescription)
        }
    }

    func recordHistory(track: Track) async {
        _ = try? await api.recordHistory(track: track)
        if let hist = try? await api.getHistory(limit: 50) {
            history = hist.items
        }
    }

    func clearHistory() async {
        do {
            try await api.clearHistory()
            history = []
            showToast("Listening history cleared")
        } catch {
            showToast(error.localizedDescription)
        }
    }
}
