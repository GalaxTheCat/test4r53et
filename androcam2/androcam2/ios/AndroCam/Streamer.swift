import AVFoundation
import Foundation
import UIKit

/// Connects to the PC, authenticates with the pairing code, then streams JPEG frames + PCM16 microphone audio.
/// The PC can send commands: flash, camera switch, quality, mic on/off.
final class Streamer: NSObject, ObservableObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    @Published var status = "Idle"
    @Published var running = false

    private var input: InputStream?
    private var output: OutputStream?
    private let sendLock = NSLock()
    private var stopFlag = false

    private let session = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let videoQueue = DispatchQueue(label: "androcam.video")
    private var device: AVCaptureDevice?
    private var front = false
    private var torchOn = false
    private var micOn = true
    private var targetH = 720
    private var fps = 30
    private var lastFrame = Date.distantPast
    private var t0 = Date()
    private let engine = AVAudioEngine()

    // MARK: public
    func start(host: String, code: String) {
        guard !running else { return }
        running = true
        stopFlag = false
        set("Requesting permissions…")
        AVCaptureDevice.requestAccess(for: .video) { camOK in
            AVAudioSession.sharedInstance().requestRecordPermission { micOK in
                guard camOK, micOK else {
                    self.set("AndroCam needs camera and microphone access (Settings › AndroCam).")
                    DispatchQueue.main.async { self.running = false }
                    return
                }
                Thread { self.runLoop(host: host, code: code) }.start()
            }
        }
    }

    func stop() {
        stopFlag = true
        closeStreams()
    }

    // MARK: connection loop
    private func runLoop(host: String, code: String) {
        while !stopFlag {
            do {
                try sessionRun(host: host, code: code)
            } catch let e as AuthError {
                set(e.message)
                break
            } catch {
                set("Reconnecting… (\(error.localizedDescription))")
            }
            stopCapture()
            closeStreams()
            if stopFlag { break }
            Thread.sleep(forTimeInterval: 2)
        }
        stopCapture()
        DispatchQueue.main.async { self.running = false; UIApplication.shared.isIdleTimerDisabled = false }
        if stopFlag { set("Idle") }
    }

    private struct AuthError: Error { let message: String }
    private struct NetError: Error, LocalizedError { let text: String; var errorDescription: String? { text } }

    private func sessionRun(host: String, code: String) throws {
        set("Connecting to \(host)…")
        var readRef: Unmanaged<CFReadStream>?
        var writeRef: Unmanaged<CFWriteStream>?
        CFStreamCreatePairWithSocketToHost(nil, host as CFString, UInt32(Proto.port), &readRef, &writeRef)
        guard let r = readRef?.takeRetainedValue(), let w = writeRef?.takeRetainedValue() else { throw NetError(text: "cannot create socket") }
        let inp = r as InputStream, out = w as OutputStream
        inp.open(); out.open()
        input = inp; output = out

        let challenge = try readPacket(inp)
        if challenge.type == Proto.tAuthFail { throw AuthError(message: reason(challenge.payload)) }
        guard challenge.type == Proto.tChallenge else { throw NetError(text: "unexpected reply") }
        try send(Proto.packet(Proto.tAuth, Proto.hmac(code: code, nonce: challenge.payload)))
        let ok = try readPacket(inp)
        if ok.type == Proto.tAuthFail { throw AuthError(message: reason(ok.payload)) }
        guard ok.type == Proto.tAuthOK else { throw NetError(text: "unexpected reply") }
        let pc = ((try? JSONSerialization.jsonObject(with: ok.payload)) as? [String: Any])?["pc"] as? String ?? host

        try send(Proto.json(Proto.tHello, ["name": UIDevice.current.name, "os": "ios", "ver": 1, "cameras": ["back", "front"]]))
        set("Streaming to \(pc)")
        t0 = Date()
        DispatchQueue.main.async { UIApplication.shared.isIdleTimerDisabled = true }
        startCapture()
        startAudio()
        startPinger()

        while !stopFlag {
            let p = try readPacket(inp)
            if p.type == Proto.tControl, let c = (try? JSONSerialization.jsonObject(with: p.payload)) as? [String: Any] { handle(c) }
            else if p.type == Proto.tPing { try send(Proto.packet(Proto.tPong)) }
        }
    }

    private func reason(_ d: Data) -> String {
        ((try? JSONSerialization.jsonObject(with: d)) as? [String: Any])?["reason"] as? String ?? "Pairing failed"
    }

    // MARK: byte I/O (blocking, on background threads)
    private func readExactly(_ s: InputStream, _ n: Int) throws -> Data {
        var data = Data(count: n)
        var got = 0
        while got < n {
            let r = data.withUnsafeMutableBytes { buf -> Int in
                s.read(buf.bindMemory(to: UInt8.self).baseAddress!.advanced(by: got), maxLength: n - got)
            }
            if r <= 0 { throw NetError(text: "connection closed") }
            got += r
        }
        return data
    }

    private func readPacket(_ s: InputStream) throws -> (type: UInt8, payload: Data) {
        let h = try readExactly(s, 8)
        guard h[0] == 0x41, h[1] == 0x43 else { throw NetError(text: "bad magic") }
        let len = Int(h[4]) << 24 | Int(h[5]) << 16 | Int(h[6]) << 8 | Int(h[7])
        guard len >= 0, len <= Proto.maxPayload else { throw NetError(text: "bad length") }
        return (h[2], len == 0 ? Data() : try readExactly(s, len))
    }

    private func send(_ d: Data) throws {
        guard let out = output else { throw NetError(text: "not connected") }
        sendLock.lock(); defer { sendLock.unlock() }
        var sent = 0
        while sent < d.count {
            let r = d.withUnsafeBytes { buf -> Int in
                out.write(buf.bindMemory(to: UInt8.self).baseAddress!.advanced(by: sent), maxLength: d.count - sent)
            }
            if r <= 0 { closeStreams(); throw NetError(text: "send failed") }
            sent += r
        }
    }

    private func closeStreams() {
        input?.close(); output?.close()
        input = nil; output = nil
    }

    private func startPinger() {
        Thread {
            while !self.stopFlag && self.output != nil {
                Thread.sleep(forTimeInterval: 4)
                try? self.send(Proto.packet(Proto.tPing))
            }
        }.start()
    }

    private func set(_ s: String) { DispatchQueue.main.async { self.status = s } }

    // MARK: commands from the PC
    private func handle(_ c: [String: Any]) {
        switch c["cmd"] as? String {
        case "torch": torchOn = (c["on"] as? Bool) ?? false; applyTorch(); sendStatus()
        case "mic": micOn = (c["on"] as? Bool) ?? true; sendStatus()
        case "camera":
            if let f = c["facing"] as? String { front = (f == "front"); torchOn = false; DispatchQueue.main.async { self.configureSession() } }
        case "quality":
            if let h = c["h"] as? Int { targetH = h }
            if let f = c["fps"] as? Int { fps = max(5, min(60, f)) }
            DispatchQueue.main.async { self.configureSession() }
        default: break
        }
    }

    private func sendStatus() {
        let hasTorch = device?.hasTorch ?? false
        try? send(Proto.json(Proto.tStatus, ["torch": torchOn, "facing": front ? "front" : "back", "mic": micOn, "torch_ok": hasTorch, "h": targetH]))
    }

    // MARK: camera
    private func startCapture() {
        DispatchQueue.main.async {
            self.configureSession()
            self.videoQueue.async { if !self.session.isRunning { self.session.startRunning() } }
        }
    }

    private func stopCapture() {
        if engine.isRunning { engine.inputNode.removeTap(onBus: 0); engine.stop() }
        videoQueue.async { if self.session.isRunning { self.session.stopRunning() } }
    }

    private func configureSession() {
        session.beginConfiguration()
        for i in session.inputs { session.removeInput(i) }
        session.sessionPreset = targetH >= 1080 ? .hd1920x1080 : (targetH >= 720 ? .hd1280x720 : .vga640x480)
        let pos: AVCaptureDevice.Position = front ? .front : .back
        if let dev = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: pos),
           let inp = try? AVCaptureDeviceInput(device: dev), session.canAddInput(inp) {
            session.addInput(inp)
            device = dev
            if (try? dev.lockForConfiguration()) != nil {
                dev.activeVideoMinFrameDuration = CMTime(value: 1, timescale: CMTimeScale(fps))
                dev.activeVideoMaxFrameDuration = CMTime(value: 1, timescale: CMTimeScale(fps))
                dev.unlockForConfiguration()
            }
        }
        if !session.outputs.contains(videoOutput) {
            videoOutput.videoSettings = [AVVideoCodecKey: AVVideoCodecType.jpeg]       // frames arrive already JPEG-encoded
            videoOutput.alwaysDiscardsLateVideoFrames = true
            videoOutput.setSampleBufferDelegate(self, queue: videoQueue)
            if session.canAddOutput(videoOutput) { session.addOutput(videoOutput) }
        }
        if let conn = videoOutput.connection(with: .video) {
            if #available(iOS 17.0, *) { conn.videoRotationAngle = 90 }               // portrait
            else if conn.isVideoOrientationSupported { conn.videoOrientation = .portrait }
        }
        session.commitConfiguration()
        applyTorch()
        sendStatus()
    }

    private func applyTorch() {
        guard let d = device, d.hasTorch, (try? d.lockForConfiguration()) != nil else { torchOn = false; return }
        d.torchMode = torchOn ? .on : .off
        d.unlockForConfiguration()
    }

    // MARK: AVCaptureVideoDataOutputSampleBufferDelegate
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard self.output != nil, let block = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }
        let now = Date()
        if now.timeIntervalSince(lastFrame) < 1.0 / Double(fps) - 0.004 { return }
        lastFrame = now
        var length = 0
        var ptr: UnsafeMutablePointer<Int8>?
        guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length, dataPointerOut: &ptr) == kCMBlockBufferNoErr,
              let p = ptr, length > 0, let fmt = CMSampleBufferGetFormatDescription(sampleBuffer) else { return }
        let dims = CMVideoFormatDescriptionGetDimensions(fmt)
        let jpeg = Data(bytes: p, count: length)
        let ts = UInt32(truncatingIfNeeded: Int(now.timeIntervalSince(t0) * 1000))
        // The connection is set to portrait, so buffers are already upright: rotation 0.
        try? send(Proto.video(w: Int(dims.width), h: Int(dims.height), rotation: 0, mirror: false, facing: front ? 1 : 0, ts: ts, jpeg: jpeg))
    }

    // MARK: microphone
    private func startAudio() {
        do {
            let s = AVAudioSession.sharedInstance()
            try s.setCategory(.playAndRecord, mode: .videoRecording, options: [.mixWithOthers, .defaultToSpeaker])
            try s.setActive(true)
            let inFormat = engine.inputNode.outputFormat(forBus: 0)
            guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 48000, channels: 1, interleaved: true),
                  let converter = AVAudioConverter(from: inFormat, to: outFormat) else { return }
            engine.inputNode.installTap(onBus: 0, bufferSize: 2048, format: inFormat) { [weak self] buf, _ in
                guard let self = self, self.micOn, self.output != nil else { return }
                let cap = AVAudioFrameCount(Double(buf.frameLength) * 48000.0 / inFormat.sampleRate) + 32
                guard let out = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: cap) else { return }
                var fed = false
                var err: NSError?
                converter.convert(to: out, error: &err) { _, status in
                    if fed { status.pointee = .noDataNow; return nil }
                    fed = true
                    status.pointee = .haveData
                    return buf
                }
                guard err == nil, out.frameLength > 0, let ch = out.int16ChannelData else { return }
                let pcm = Data(bytes: ch[0], count: Int(out.frameLength) * 2)
                let ts = UInt32(truncatingIfNeeded: Int(Date().timeIntervalSince(self.t0) * 1000))
                try? self.send(Proto.audio(rate: 48000, channels: 1, ts: ts, pcm: pcm))
            }
            try engine.start()
        } catch {
            set("Microphone error: \(error.localizedDescription)")
        }
    }
}
