import SwiftUI

@main
struct SonifyApp: App {
    @StateObject private var apiClient: APIClient
    @StateObject private var libraryStore: LibraryStore
    @StateObject private var lyricsStore: LyricsStore
    @StateObject private var playerService: AudioPlayerService

    init() {
        let client = APIClient()
        let library = LibraryStore(api: client)
        let lyrics = LyricsStore(api: client)
        let player = AudioPlayerService(api: client)
        player.attachStores(library: library, lyrics: lyrics)

        _apiClient = StateObject(wrappedValue: client)
        _libraryStore = StateObject(wrappedValue: library)
        _lyricsStore = StateObject(wrappedValue: lyrics)
        _playerService = StateObject(wrappedValue: player)
    }

    var body: some Scene {
        WindowGroup {
            MainTabView()
                .environmentObject(apiClient)
                .environmentObject(libraryStore)
                .environmentObject(lyricsStore)
                .environmentObject(playerService)
                .preferredColorScheme(.dark)
                .tint(SonifyTheme.accentGreen)
                .task {
                    await libraryStore.hydrateAll()
                }
        }
    }
}
