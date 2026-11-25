import AVFoundation
import Combine
import Foundation
import MediaPlayer
import UIKit

/// Pure value-type queue state machine for deterministic playback navigation and unit testing.
struct PlaybackQueueEngine: Equatable {
    private(set) var queue: [Track] = []
    private(set) var currentIndex: Int = -1
    private(set) var upNext: [Track] = []
    var shuffle: Bool = false
    var repeatMode: RepeatMode = .off

    var currentTrack: Track? {
        guard currentIndex >= 0 && currentIndex < queue.count else { return nil }
        return queue[currentIndex]
    }

    var upcomingTracks: [Track] {
        var list = upNext
        if currentIndex >= 0 && currentIndex + 1 < queue.count {
            list.append(contentsOf: queue[(currentIndex + 1)...])
        }
        return list
    }

    mutating func play(track: Track, context: [Track]? = nil) -> Track {
        if let context, !context.isEmpty {
            queue = context
            if let idx = queue.firstIndex(where: { $0.id == track.id }) {
                currentIndex = idx
            } else {
                queue.insert(track, at: 0)
                currentIndex = 0
            }
        } else if let existing = queue.firstIndex(where: { $0.id == track.id }) {
            currentIndex = existing
        } else {
            queue.append(track)
            currentIndex = queue.count - 1
        }
        return queue[currentIndex]
    }

    mutating func enqueueNext(_ track: Track) {
        upNext.append(track)
    }

    mutating func removeUpNext(at offsets: IndexSet) {
        for idx in offsets.sorted(by: >) where idx < upNext.count {
            upNext.remove(at: idx)
        }
    }

    mutating func moveUpNext(from source: IndexSet, to destination: Int) {
        let items = source.sorted().map { upNext[$0] }
        for idx in source.sorted(by: >) {
            upNext.remove(at: idx)
        }
        let adjustedDest = min(destination - source.filter { $0 < destination }.count, upNext.count)
        upNext.insert(contentsOf: items, at: max(0, adjustedDest))
    }

    mutating func nextTrack(fromAutoEnd: Bool, randomIndex: ((Int) -> Int)? = nil) -> Track? {
        if fromAutoEnd && repeatMode == .one, let cur = currentTrack {
            return cur
        }

        if !upNext.isEmpty {
            let nextItem = upNext.removeFirst()
            let insertPos = min(max(currentIndex + 1, 0), queue.count)
            queue.insert(nextItem, at: insertPos)
            currentIndex = insertPos
            return nextItem
        }

        guard !queue.isEmpty else { return nil }

        if shuffle && queue.count > 1 {
            let picker = randomIndex ?? { Int.random(in: 0..<$0) }
            var candidate = picker(queue.count)
            if candidate == currentIndex {
                candidate = (candidate + 1) % queue.count
            }
            currentIndex = candidate
            return queue[currentIndex]
        }

        if currentIndex + 1 < queue.count {
            currentIndex += 1
            return queue[currentIndex]
        }

        if repeatMode == .all {
            currentIndex = 0
            return queue[0]
        }

        return nil
    }

    mutating func previousTrack(currentTime: Double) -> (track: Track?, restartCurrent: Bool) {
        if currentTime > 3.0, let cur = currentTrack {
            return (cur, true)
        }
        guard !queue.isEmpty else { return (nil, false) }
        if currentIndex > 0 {
            currentIndex -= 1
            return (queue[currentIndex], false)
        }
        if repeatMode == .all {
            currentIndex = queue.count - 1
            return (queue[currentIndex], false)
        }
        return (currentTrack, true)
    }
}

@MainActor
final class AudioPlayerService: ObservableObject {
    @Published private(set) var currentTrack: Track?
    @Published private(set) var streamInfo: StreamInfoResponse?
    @Published private(set) var isPlaying: Bool = false
    @Published private(set) var isLoading: Bool = false
    @Published private(set) var currentTime: Double = 0
    @Published private(set) var duration: Double = 0
    @Published private(set) var errorMessage: String?
    @Published private(set) var queueEngine = PlaybackQueueEngine()
    @Published var showNowPlayingSheet: Bool = false

    private let api: APIClient
    private weak var libraryStore: LibraryStore?
    private weak var lyricsStore: LyricsStore?

    private let player: AVPlayer = {
        let p = AVPlayer()
        p.automaticallyWaitsToMinimizeStalling = true
        return p
    }()

    private var playbackSeq: Int = 0
    private var loadTask: Task<Void, Never>?
    private var artworkTask: Task<Void, Never>?
    private var prefetchTask: Task<Void, Never>?
    private var prefetchedInfo: [String: (info: StreamInfoResponse, fetchedAt: Date)] = [:]
    private var prefetchedOrder: [String] = []
    private var currentRoute: PlaybackRoute?
    private var didFallbackToProxy: Bool = false
    private var timeObserverToken: Any?
    private var itemStatusObserver: NSKeyValueObservation?
    private var timeControlObserver: NSKeyValueObservation?
    private var didPlayToEndObserver: NSObjectProtocol?
    private var interruptionObserver: NSObjectProtocol?
    private var routeChangeObserver: NSObjectProtocol?
    private var wasPlayingBeforeInterruption: Bool = false
    private var cachedArtwork: MPMediaItemArtwork?

    private static let prefetchTTL: TimeInterval = 600.0
    private static let maxPrefetchedEntries: Int = 12

    init(api: APIClient) {
        self.api = api
        configureAudioSession()
        setupRemoteCommandCenter()
        setupObservers()
    }

    func attachStores(library: LibraryStore, lyrics: LyricsStore) {
        self.libraryStore = library
        self.lyricsStore = lyrics
    }

    deinit {
        if let token = timeObserverToken {
            player.removeTimeObserver(token)
        }
        if let obs = didPlayToEndObserver {
            NotificationCenter.default.removeObserver(obs)
        }
        if let obs = interruptionObserver {
            NotificationCenter.default.removeObserver(obs)
        }
        if let obs = routeChangeObserver {
            NotificationCenter.default.removeObserver(obs)
        }
    }

    // MARK: - Public Playback API

    func play(track: Track, context: [Track]? = nil) {
        let active = queueEngine.play(track: track, context: context)
        startPlayback(for: active)
    }

    func enqueueNext(_ track: Track) {
        queueEngine.enqueueNext(track)
        libraryStore?.showToast("Added \"\(track.title)\" to Queue")
    }

    func removeUpNext(at offsets: IndexSet) {
        queueEngine.removeUpNext(at: offsets)
    }

    func moveUpNext(from source: IndexSet, to destination: Int) {
        queueEngine.moveUpNext(from: source, to: destination)
    }

    func togglePlayPause() {
        if isPlaying {
            pause()
        } else {
            resume()
        }
    }

    func pause() {
        player.pause()
        isPlaying = false
        updateNowPlayingInfo()
    }

    func resume() {
        guard currentTrack != nil else { return }
        activateAudioSession()
        player.play()
        isPlaying = true
        updateNowPlayingInfo()
    }

    func nextTrack(fromAutoEnd: Bool = false) {
        if fromAutoEnd && queueEngine.repeatMode == .one, currentTrack != nil {
            seek(to: 0)
            resume()
            return
        }
        if let next = queueEngine.nextTrack(fromAutoEnd: fromAutoEnd) {
            startPlayback(for: next)
        } else {
            pause()
        }
    }

    func previousTrack() {
        let outcome = queueEngine.previousTrack(currentTime: currentTime)
        if outcome.restartCurrent {
            seek(to: 0)
            resume()
        } else if let prev = outcome.track {
            startPlayback(for: prev)
        }
    }

    func seek(to seconds: Double) {
        let clamped = max(0, min(seconds, max(duration, seconds)))
        let cmTime = CMTime(seconds: clamped, preferredTimescale: 600)
        player.seek(to: cmTime, toleranceBefore: .zero, toleranceAfter: .zero) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.currentTime = clamped
                self.updateNowPlayingInfo()
            }
        }
    }

    func toggleShuffle() {
        queueEngine.shuffle.toggle()
        libraryStore?.showToast(queueEngine.shuffle ? "Shuffle On" : "Shuffle Off")
    }

    func cycleRepeat() {
        queueEngine.repeatMode = queueEngine.repeatMode.next
        switch queueEngine.repeatMode {
        case .off: libraryStore?.showToast("Repeat Off")
        case .all: libraryStore?.showToast("Repeat All")
        case .one: libraryStore?.showToast("Repeat One")
        }
    }

    // MARK: - Internal Stream Resolution & AVPlayer Setup

    private func cachedPrefetchInfo(for trackId: String) -> StreamInfoResponse? {
        guard let entry = prefetchedInfo[trackId] else { return nil }
        if Date().timeIntervalSince(entry.fetchedAt) > Self.prefetchTTL {
            prefetchedInfo.removeValue(forKey: trackId)
            prefetchedOrder.removeAll { $0 == trackId }
            return nil
        }
        return entry.info
    }

    private func storePrefetchInfo(_ info: StreamInfoResponse, for trackId: String) {
        guard !info.isPreview else { return }
        prefetchedInfo[trackId] = (info: info, fetchedAt: Date())
        prefetchedOrder.removeAll { $0 == trackId }
        prefetchedOrder.append(trackId)
        while prefetchedOrder.count > Self.maxPrefetchedEntries {
            let oldest = prefetchedOrder.removeFirst()
            prefetchedInfo.removeValue(forKey: oldest)
        }
    }

    private func scheduleNextTrackPrefetch(seq: Int) {
        prefetchTask?.cancel()
        guard let nextTrack = queueEngine.upcomingTracks.first else { return }
        let nextId = nextTrack.id
        if cachedPrefetchInfo(for: nextId) != nil { return }

        prefetchTask = Task(priority: .utility) { [weak self] in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            guard !Task.isCancelled, let self, seq == self.playbackSeq else { return }
            if let info = try? await self.api.getStreamInfo(trackId: nextId) {
                guard !Task.isCancelled else { return }
                self.storePrefetchInfo(info, for: nextId)
            }
        }
    }

    private func makePlayerItem(for route: PlaybackRoute, contentType: String) -> AVPlayerItem {
        var assetOptions: [String: Any] = [:]
        // Never override MIME on native HLS .m3u8 playlists so AVPlayer parses the manifest natively
        if !route.isHLS {
            if #available(iOS 17.0, *) {
                assetOptions[AVURLAssetOverrideMIMETypeKey] = StreamInfoResponse.normalizedMIME(contentType)
            }
        }
        let asset = AVURLAsset(url: route.url, options: assetOptions)
        let item = AVPlayerItem(asset: asset)
        item.preferredForwardBufferDuration = 2.0
        return item
    }

    private func startPlayback(for track: Track) {
        playbackSeq += 1
        let seq = playbackSeq

        loadTask?.cancel()
        artworkTask?.cancel()
        prefetchTask?.cancel()
        itemStatusObserver?.invalidate()
        itemStatusObserver = nil

        player.pause()
        player.replaceCurrentItem(with: nil)

        currentTrack = track
        streamInfo = nil
        currentRoute = nil
        didFallbackToProxy = false
        currentTime = 0
        duration = Double(max(track.duration, 0))
        isPlaying = false
        isLoading = true
        errorMessage = nil
        cachedArtwork = nil

        updateNowPlayingInfo()
        loadNowPlayingArtwork(for: track)
        lyricsStore?.loadLyrics(for: track)

        loadTask = Task { [weak self] in
            guard let self else { return }
            do {
                let info: StreamInfoResponse
                if let cached = self.cachedPrefetchInfo(for: track.id) {
                    info = cached
                } else {
                    let fetched = try await self.api.getStreamInfo(trackId: track.id)
                    self.storePrefetchInfo(fetched, for: track.id)
                    info = fetched
                }
                guard !Task.isCancelled, seq == self.playbackSeq else { return }

                let proxyURL = self.api.streamURL(for: info)
                guard let route = info.preferredPlaybackRoute(proxyURL: proxyURL) else {
                    self.isLoading = false
                    self.errorMessage = "Invalid audio stream URL"
                    return
                }

                self.streamInfo = info
                self.currentRoute = route
                self.activateAudioSession()

                let item = self.makePlayerItem(for: route, contentType: info.contentType)
                self.observePlayerItem(item, seq: seq)
                self.player.replaceCurrentItem(with: item)
                self.player.play()
                self.updateNowPlayingInfo()

                Task { [weak self] in
                    await self?.libraryStore?.recordHistory(track: track)
                }
            } catch is CancellationError {
                return
            } catch {
                guard seq == self.playbackSeq else { return }
                self.isLoading = false
                self.isPlaying = false
                self.errorMessage = error.localizedDescription
                self.libraryStore?.showToast(error.localizedDescription)
            }
        }
    }

    private func observePlayerItem(_ item: AVPlayerItem, seq: Int) {
        itemStatusObserver = item.observe(\.status, options: [.new]) { [weak self] observedItem, _ in
            Task { @MainActor [weak self] in
                guard let self, seq == self.playbackSeq else { return }
                switch observedItem.status {
                case .readyToPlay:
                    let itemDur = observedItem.duration.seconds
                    if itemDur.isFinite && itemDur > 0 {
                        self.duration = itemDur
                    }
                    self.isLoading = false
                    self.updateNowPlayingInfo()
                    self.scheduleNextTrackPrefetch(seq: seq)
                case .failed:
                    // Automatic 1-shot fallback to backend proxy if direct edge / HLS URL failed
                    if !self.didFallbackToProxy,
                       let route = self.currentRoute,
                       !route.isProxy,
                       let info = self.streamInfo,
                       let proxyURL = self.api.streamURL(for: info) {
                        self.didFallbackToProxy = true
                        let proxyRoute = PlaybackRoute.proxy(proxyURL)
                        self.currentRoute = proxyRoute
                        self.itemStatusObserver?.invalidate()
                        self.itemStatusObserver = nil
                        let fallbackItem = self.makePlayerItem(for: proxyRoute, contentType: info.contentType)
                        self.observePlayerItem(fallbackItem, seq: seq)
                        self.player.replaceCurrentItem(with: fallbackItem)
                        self.player.play()
                        return
                    }
                    self.isLoading = false
                    self.isPlaying = false
                    let msg = observedItem.error?.localizedDescription ?? "Unable to play this track"
                    self.errorMessage = msg
                    self.libraryStore?.showToast(msg)
                    self.updateNowPlayingInfo()
                default:
                    break
                }
            }
        }
    }

    private func configureAudioSession() {
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default, options: [])
        } catch {
            // Audio session configuration can fail on headless simulator; handled gracefully
        }
    }

    private func activateAudioSession() {
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default, options: [])
            try session.setActive(true)
        } catch {
            // Ignore simulator audio activation errors
        }
    }

    private func setupObservers() {
        let interval = CMTime(seconds: 0.25, preferredTimescale: 600)
        timeObserverToken = player.addPeriodicTimeObserver(forInterval: interval, queue: .main) { [weak self] cmTime in
            Task { @MainActor [weak self] in
                guard let self else { return }
                let secs = cmTime.seconds
                if secs.isFinite && secs >= 0 {
                    self.currentTime = secs
                }
                if let itemDur = self.player.currentItem?.duration.seconds, itemDur.isFinite && itemDur > 0 {
                    self.duration = itemDur
                }
            }
        }

        timeControlObserver = player.observe(\.timeControlStatus, options: [.new]) { [weak self] p, _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                switch p.timeControlStatus {
                case .playing:
                    self.isPlaying = true
                    self.isLoading = false
                case .waitingToPlayAtSpecifiedRate:
                    self.isLoading = true
                case .paused:
                    self.isPlaying = false
                @unknown default:
                    break
                }
                self.updateNowPlayingInfo()
            }
        }

        didPlayToEndObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: nil,
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor [weak self] in
                guard let self,
                      let finishedItem = notification.object as? AVPlayerItem,
                      finishedItem == self.player.currentItem else { return }
                self.nextTrack(fromAutoEnd: true)
            }
        }

        interruptionObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor [weak self] in
                guard let self,
                      let userInfo = notification.userInfo,
                      let typeRaw = userInfo[AVAudioSessionInterruptionTypeKey] as? UInt,
                      let type = AVAudioSession.InterruptionType(rawValue: typeRaw) else { return }
                switch type {
                case .began:
                    self.wasPlayingBeforeInterruption = self.isPlaying
                    self.pause()
                case .ended:
                    let optionsRaw = userInfo[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
                    let options = AVAudioSession.InterruptionOptions(rawValue: optionsRaw)
                    if options.contains(.shouldResume) && self.wasPlayingBeforeInterruption {
                        self.resume()
                    }
                    self.wasPlayingBeforeInterruption = false
                @unknown default:
                    break
                }
            }
        }

        routeChangeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] notification in
            Task { @MainActor [weak self] in
                guard let self,
                      let userInfo = notification.userInfo,
                      let reasonRaw = userInfo[AVAudioSessionRouteChangeReasonKey] as? UInt,
                      let reason = AVAudioSession.RouteChangeReason(rawValue: reasonRaw) else { return }
                if reason == .oldDeviceUnavailable {
                    self.pause()
                }
            }
        }
    }

    // MARK: - Lock Screen & Control Center

    private func setupRemoteCommandCenter() {
        let center = MPRemoteCommandCenter.shared()
        center.playCommand.isEnabled = true
        center.pauseCommand.isEnabled = true
        center.togglePlayPauseCommand.isEnabled = true
        center.nextTrackCommand.isEnabled = true
        center.previousTrackCommand.isEnabled = true
        center.changePlaybackPositionCommand.isEnabled = true

        center.playCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.resume() }
            return .success
        }
        center.pauseCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.pause() }
            return .success
        }
        center.togglePlayPauseCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.togglePlayPause() }
            return .success
        }
        center.nextTrackCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.nextTrack(fromAutoEnd: false) }
            return .success
        }
        center.previousTrackCommand.addTarget { [weak self] _ in
            Task { @MainActor [weak self] in self?.previousTrack() }
            return .success
        }
        center.changePlaybackPositionCommand.addTarget { [weak self] event in
            guard let posEvent = event as? MPChangePlaybackPositionCommandEvent else {
                return .commandFailed
            }
            Task { @MainActor [weak self] in
                self?.seek(to: posEvent.positionTime)
            }
            return .success
        }
    }

    private func updateNowPlayingInfo() {
        guard let track = currentTrack else {
            MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
            return
        }
        var info: [String: Any] = [
            MPMediaItemPropertyTitle: track.title,
            MPMediaItemPropertyArtist: track.artist,
            MPMediaItemPropertyAlbumTitle: track.album ?? "Sonify",
            MPMediaItemPropertyPlaybackDuration: max(duration, Double(track.duration)),
            MPNowPlayingInfoPropertyElapsedPlaybackTime: currentTime,
            MPNowPlayingInfoPropertyPlaybackRate: isPlaying ? 1.0 : 0.0,
        ]
        if let cachedArtwork {
            info[MPMediaItemPropertyArtwork] = cachedArtwork
        }
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    private func loadNowPlayingArtwork(for track: Track) {
        guard let coverStr = track.bestCoverURL,
              coverStr.hasPrefix("http://") || coverStr.hasPrefix("https://"),
              let url = URL(string: coverStr) else {
            return
        }
        let targetId = track.id
        artworkTask = Task { [weak self] in
            guard let (data, _) = try? await URLSession.shared.data(from: url),
                  let image = UIImage(data: data) else { return }
            let art = MPMediaItemArtwork(boundsSize: image.size) { _ in image }
            await MainActor.run { [weak self] in
                guard let self, self.currentTrack?.id == targetId else { return }
                self.cachedArtwork = art
                self.updateNowPlayingInfo()
            }
        }
    }
}
