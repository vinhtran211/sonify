import SwiftUI

enum SonifyTheme {
    static let bgBase = Color(hex: 0x000000)
    static let bgSurface = Color(hex: 0x121212)
    static let bgElevated = Color(hex: 0x181818)
    static let bgHighlight = Color(hex: 0x282828)
    static let accentGreen = Color(hex: 0x1DB954)
    static let accentGreenBright = Color(hex: 0x1ED760)
    static let textPrimary = Color(hex: 0xFFFFFF)
    static let textSecondary = Color(hex: 0xB3B3B3)
    static let textSubdued = Color(hex: 0x727272)
    static let danger = Color(hex: 0xF15E6C)
    static let warning = Color(hex: 0xFFA42B)
    static let borderSubtle = Color.white.opacity(0.08)

    static let miniPlayerHeight: CGFloat = 64

    static func formatDuration(_ seconds: Double) -> String {
        guard seconds.isFinite && seconds > 0 else { return "0:00" }
        let total = Int(seconds.rounded(.down))
        let hrs = total / 3600
        let mins = (total % 3600) / 60
        let secs = total % 60
        if hrs > 0 {
            return String(format: "%d:%02d:%02d", hrs, mins, secs)
        }
        return String(format: "%d:%02d", mins, secs)
    }

    static func greetingForCurrentHour(date: Date = Date()) -> String {
        let hour = Calendar.current.component(.hour, from: date)
        switch hour {
        case 5..<12: return "Good morning"
        case 12..<18: return "Good afternoon"
        default: return "Good evening"
        }
    }
}

extension Color {
    init(hex: UInt32, opacity: Double = 1.0) {
        let r = Double((hex >> 16) & 0xFF) / 255.0
        let g = Double((hex >> 8) & 0xFF) / 255.0
        let b = Double(hex & 0xFF) / 255.0
        self.init(.sRGB, red: r, green: g, blue: b, opacity: opacity)
    }

    init(hexString: String, fallback: UInt32 = 0x1E3264) {
        let cleaned = hexString.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var val: UInt64 = 0
        if cleaned.count == 6, Scanner(string: cleaned).scanHexInt64(&val) {
            self.init(hex: UInt32(val))
        } else {
            self.init(hex: fallback)
        }
    }
}
