import SwiftUI

@main
struct SonifyApp: App {
    @StateObject private var apiClient: APIClient
    @StateObject private var offlineDownloads: OfflineDownloadManager
    @StateObject private var libraryStore: LibraryStore
    @StateObject private var lyricsStore: LyricsStore
    @StateObject private var playerService: AudioPlayerService

    init() {
        let client = APIClient()
        let downloads = OfflineDownloadManager()
        let library = LibraryStore(api: client)
        let lyrics = LyricsStore(api: client, offlineDownloads: downloads)
        let player = AudioPlayerService(api: client)
        player.attachStores(library: library, lyrics: lyrics, offlineDownloads: downloads)

        _apiClient = StateObject(wrappedValue: client)
        _offlineDownloads = StateObject(wrappedValue: downloads)
        _libraryStore = StateObject(wrappedValue: library)
        _lyricsStore = StateObject(wrappedValue: lyrics)
        _playerService = StateObject(wrappedValue: player)
    }

    var body: some Scene {
        WindowGroup {
            MainTabView()
                .environmentObject(apiClient)
                .environmentObject(offlineDownloads)
                .environmentObject(libraryStore)
                .environmentObject(lyricsStore)
                .environmentObject(playerService)
                .preferredColorScheme(.dark)
                .tint(SonifyTheme.accentGreen)
                .task {
                    if !offlineDownloads.offlineModeEnabled {
                        await libraryStore.hydrateAll()
                    }
                }
        }
    }
}
