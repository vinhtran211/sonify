import SwiftUI
import UIKit

enum AppTab: Hashable {
    case home
    case search
    case library
}

struct MainTabView: View {
    @State private var selectedTab: AppTab = .home
    @EnvironmentObject private var player: AudioPlayerService
    @EnvironmentObject private var library: LibraryStore

    init() {
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = UIColor(red: 0.05, green: 0.05, blue: 0.05, alpha: 0.96)
        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            TabView(selection: $selectedTab) {
                HomeView()
                    .tabItem {
                        Label("Home", systemImage: selectedTab == .home ? "house.fill" : "house")
                    }
                    .tag(AppTab.home)

                SearchView()
                    .tabItem {
                        Label("Search", systemImage: "magnifyingglass")
                    }
                    .tag(AppTab.search)

                LibraryView()
                    .tabItem {
                        Label("Your Library", systemImage: selectedTab == .library ? "books.vertical.fill" : "books.vertical")
                    }
                    .tag(AppTab.library)
            }
            .accentColor(SonifyTheme.accentGreen)

            VStack(spacing: 8) {
                if let toast = library.toastMessage {
                    Text(toast)
                        .font(.caption.weight(.semibold))
                        .foregroundColor(.black)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 9)
                        .background(SonifyTheme.accentGreen)
                        .clipShape(Capsule())
                        .shadow(color: .black.opacity(0.4), radius: 6, y: 2)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }

                if player.currentTrack != nil {
                    MiniPlayerBar()
                }
            }
            .padding(.bottom, 56)
            .animation(.easeInOut(duration: 0.2), value: library.toastMessage)
            .animation(.easeInOut(duration: 0.2), value: player.currentTrack?.id)
        }
        .background(SonifyTheme.bgBase.ignoresSafeArea())
        .fullScreenCover(isPresented: $player.showNowPlayingSheet) {
            NowPlayingView()
        }
    }
}
