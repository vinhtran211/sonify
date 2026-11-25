import AVKit
import SwiftUI
import UIKit

struct AirPlayButton: UIViewRepresentable {
    var tintColor: UIColor = .white
    var activeTintColor: UIColor = UIColor(red: 29.0 / 255.0, green: 185.0 / 255.0, blue: 84.0 / 255.0, alpha: 1.0)

    func makeUIView(context: Context) -> AVRoutePickerView {
        let picker = AVRoutePickerView()
        picker.tintColor = tintColor
        picker.activeTintColor = activeTintColor
        picker.prioritizesVideoDevices = false
        picker.backgroundColor = .clear
        return picker
    }

    func updateUIView(_ uiView: AVRoutePickerView, context: Context) {
        uiView.tintColor = tintColor
        uiView.activeTintColor = activeTintColor
    }
}
