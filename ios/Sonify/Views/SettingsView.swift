import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var api: APIClient
    @EnvironmentObject private var library: LibraryStore
    @EnvironmentObject private var player: AudioPlayerService
    @Environment(\.dismiss) private var dismiss

    @State private var serverInput: String = ""
    @State private var healthResult: HealthResponse?
    @State private var healthError: String?
    @State private var isTestingHealth = false

    var body: some View {
        NavigationStack {
            ZStack {
                SonifyTheme.bgSurface.ignoresSafeArea()
                Form {
                    Section("Playback & Audio") {
                        Toggle("Autoplay Similar Songs", isOn: Binding(
                            get: { library.settings.autoplay },
                            set: { val in Task { await library.updateAudioSettings(autoplay: val) } }
                        ))
                        .tint(SonifyTheme.accentGreen)

                        Picker("Audio Quality", selection: Binding(
                            get: { library.settings.audioQuality },
                            set: { val in Task { await library.updateAudioSettings(audioQuality: val) } }
                        )) {
                            Text("Auto").tag("auto")
                            Text("Lossless FLAC").tag("lossless")
                            Text("Fast").tag("fast")
                        }

                        Picker("Playback Speed", selection: Binding(
                            get: { player.playbackRate },
                            set: { spd in player.setPlaybackRate(spd) }
                        )) {
                            Text("0.5x").tag(Float(0.5))
                            Text("0.75x").tag(Float(0.75))
                            Text("1.0x").tag(Float(1.0))
                            Text("1.25x").tag(Float(1.25))
                            Text("1.5x").tag(Float(1.5))
                            Text("2.0x").tag(Float(2.0))
                        }

                        Stepper(
                            "Crossfade: \(library.settings.crossfade == 0 ? "Off" : "\(library.settings.crossfade)s")",
                            value: Binding(
                                get: { library.settings.crossfade },
                                set: { val in Task { await library.updateAudioSettings(crossfade: val) } }
                            ),
                            in: 0...12
                        )

                        NavigationLink {
                            StatsView()
                        } label: {
                            Label("Sound Capsule Listening Stats", systemImage: "chart.bar.fill")
                                .foregroundColor(SonifyTheme.accentGreen)
                        }
                    }
                    .listRowBackground(SonifyTheme.bgElevated)

                    Section {
                        TextField("https://sonify-music.onrender.com", text: $serverInput)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .foregroundColor(SonifyTheme.textPrimary)

                        HStack {
                            Button("Save Server") {
                                api.updateBaseURL(serverInput)
                                Task {
                                    await testHealth()
                                    await library.hydrateAll()
                                }
                            }
                            .foregroundColor(SonifyTheme.accentGreen)

                            Spacer()

                            Button("Reset to Live Render") {
                                serverInput = APIClient.defaultServerURL
                                api.resetBaseURL()
                                Task {
                                    await testHealth()
                                    await library.hydrateAll()
                                }
                            }
                            .foregroundColor(SonifyTheme.textSecondary)
                        }

                        Button {
                            Task { await testHealth() }
                        } label: {
                            HStack {
                                Text("Test Connection")
                                Spacer()
                                if isTestingHealth {
                                    ProgressView()
                                        .tint(SonifyTheme.accentGreen)
                                }
                            }
                        }

                        if let health = healthResult {
                            VStack(alignment: .leading, spacing: 4) {
                                Label("Connected (v\(health.version))", systemImage: "checkmark.circle.fill")
                                    .foregroundColor(SonifyTheme.accentGreen)
                                    .font(.caption.weight(.bold))
                                Text("Region: \(health.renderRegion ?? "cloud") • Ad-Free: \(health.adFree ? "Yes" : "No") • DB Ready: \(health.dbReady ? "Yes" : "No")")
                                    .font(.caption2)
                                    .foregroundColor(SonifyTheme.textSecondary)
                            }
                        } else if let healthError {
                            Label(healthError, systemImage: "exclamationmark.triangle.fill")
                                .font(.caption)
                                .foregroundColor(SonifyTheme.danger)
                        }
                    } header: {
                        Text("Sonify Backend Server")
                    } footer: {
                        Text("Uses Live Render by default, or point to your local Mac/PC LAN server (e.g. http://192.168.1.10:8080).")
                    }
                    .listRowBackground(SonifyTheme.bgElevated)

                    Section("Catalog Region") {
                        Picker("Region", selection: Binding(
                            get: { library.region },
                            set: { newReg in Task { await library.setRegion(newReg) } }
                        )) {
                            Text("Global").tag("global")
                            Text("Vietnam (V-Pop)").tag("vn")
                        }
                        .pickerStyle(.segmented)
                    }
                    .listRowBackground(SonifyTheme.bgElevated)

                    Section("Data") {
                        Button(role: .destructive) {
                            Task { await library.clearHistory() }
                        } label: {
                            Label("Clear Listening History", systemImage: "trash")
                        }
                    }
                    .listRowBackground(SonifyTheme.bgElevated)

                    Section("About Sonify") {
                        HStack {
                            Text("Streaming")
                            Spacer()
                            Text("100% Ad-Free")
                                .foregroundColor(SonifyTheme.accentGreen)
                        }
                        HStack {
                            Text("Audio Formats")
                            Spacer()
                            Text("FLAC • M4A • MP4 • MP3")
                                .foregroundColor(SonifyTheme.textSecondary)
                        }
                    }
                    .listRowBackground(SonifyTheme.bgElevated)
                }
                .scrollContentBackground(.hidden)
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                        .foregroundColor(SonifyTheme.accentGreen)
                }
            }
            .onAppear {
                serverInput = api.baseURLString
            }
        }
        .preferredColorScheme(.dark)
    }

    private func testHealth() async {
        isTestingHealth = true
        healthError = nil
        do {
            healthResult = try await api.health()
        } catch {
            healthResult = nil
            healthError = error.localizedDescription
        }
        isTestingHealth = false
    }
}
