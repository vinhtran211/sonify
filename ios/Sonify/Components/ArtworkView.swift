import SwiftUI
import UIKit

struct ArtworkView: View {
    let urlString: String?
    var localImageData: Data? = nil
    var size: CGFloat = 56
    var cornerRadius: CGFloat = 6
    var isCircular: Bool = false
    var symbolName: String = "music.note"

    private var remoteURL: URL? {
        guard let str = urlString?.trimmingCharacters(in: .whitespacesAndNewlines),
              !str.isEmpty,
              !str.hasPrefix("/api/artwork/"),
              str.lowercased().hasPrefix("http://") || str.lowercased().hasPrefix("https://") else {
            return nil
        }
        return URL(string: str)
    }

    var body: some View {
        Group {
            if let data = localImageData, let uiImg = UIImage(data: data) {
                Image(uiImage: uiImg)
                    .resizable()
                    .scaledToFill()
            } else if let url = remoteURL {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image
                            .resizable()
                            .scaledToFill()
                    case .failure:
                        placeholder
                    case .empty:
                        placeholder
                            .overlay(
                                ProgressView()
                                    .tint(SonifyTheme.textSecondary)
                                    .scaleEffect(size < 48 ? 0.6 : 0.8)
                            )
                    @unknown default:
                        placeholder
                    }
                }
            } else {
                placeholder
            }
        }
        .frame(width: size, height: size)
        .clipped()
        .clipShape(shape)
    }

    private var shape: AnyShape {
        if isCircular {
            return AnyShape(Circle())
        }
        return AnyShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(
                colors: [SonifyTheme.accentGreen.opacity(0.45), SonifyTheme.bgHighlight],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            Image(systemName: symbolName)
                .font(.system(size: max(14, size * 0.36), weight: .semibold))
                .foregroundColor(SonifyTheme.textPrimary.opacity(0.85))
        }
    }
}

struct PlaylistMosaicArtworkView: View {
    let covers: [String]
    var size: CGFloat = 56
    var cornerRadius: CGFloat = 6

    var body: some View {
        if covers.count >= 4 {
            let half = size / 2
            VStack(spacing: 0) {
                HStack(spacing: 0) {
                    ArtworkView(urlString: covers[0], size: half, cornerRadius: 0)
                    ArtworkView(urlString: covers[1], size: half, cornerRadius: 0)
                }
                HStack(spacing: 0) {
                    ArtworkView(urlString: covers[2], size: half, cornerRadius: 0)
                    ArtworkView(urlString: covers[3], size: half, cornerRadius: 0)
                }
            }
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
        } else {
            ArtworkView(
                urlString: covers.first,
                size: size,
                cornerRadius: cornerRadius,
                symbolName: "music.note.list"
            )
        }
    }
}
