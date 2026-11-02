import Foundation
import CryptoKit

/// AndroCam wire protocol v1. Mirrors host/androcam_host/protocol.py. All integers are big-endian.
enum Proto {
    static let port: UInt16 = 47801
    static let tHello: UInt8 = 1, tChallenge: UInt8 = 2, tAuth: UInt8 = 3, tAuthOK: UInt8 = 4, tAuthFail: UInt8 = 5
    static let tVideo: UInt8 = 6, tAudio: UInt8 = 7, tControl: UInt8 = 8, tStatus: UInt8 = 9, tPing: UInt8 = 10, tPong: UInt8 = 11
    static let maxPayload = 16 * 1024 * 1024

    static func be16(_ v: UInt16) -> Data { Data([UInt8(v >> 8), UInt8(v & 0xFF)]) }
    static func be32(_ v: UInt32) -> Data { Data([UInt8(v >> 24), UInt8((v >> 16) & 0xFF), UInt8((v >> 8) & 0xFF), UInt8(v & 0xFF)]) }

    static func packet(_ type: UInt8, _ payload: Data = Data()) -> Data {
        var d = Data([0x41, 0x43, type, 0])            // 'A' 'C', type, flags
        d.append(be32(UInt32(payload.count)))
        d.append(payload)
        return d
    }

    static func json(_ type: UInt8, _ obj: [String: Any]) -> Data {
        guard let body = try? JSONSerialization.data(withJSONObject: obj) else { return packet(type) }
        return packet(type, body)
    }

    /// VIDEO: width u16, height u16, rotation u16 (clockwise degrees), mirror u8, facing u8, ts_ms u32, JPEG bytes.
    static func video(w: Int, h: Int, rotation: Int, mirror: Bool, facing: UInt8, ts: UInt32, jpeg: Data) -> Data {
        var p = Data()
        p.append(be16(UInt16(truncatingIfNeeded: w)))
        p.append(be16(UInt16(truncatingIfNeeded: h)))
        p.append(be16(UInt16(truncatingIfNeeded: rotation)))
        p.append(mirror ? 1 : 0)
        p.append(facing)
        p.append(be32(ts))
        p.append(jpeg)
        return packet(tVideo, p)
    }

    /// AUDIO: sample_rate u32, channels u8, bits u8, reserved u16, ts_ms u32, PCM16 little-endian.
    static func audio(rate: UInt32, channels: UInt8, ts: UInt32, pcm: Data) -> Data {
        var p = Data()
        p.append(be32(rate))
        p.append(channels)
        p.append(16)
        p.append(be16(0))
        p.append(be32(ts))
        p.append(pcm)
        return packet(tAudio, p)
    }

    static func hmac(code: String, nonce: Data) -> Data {
        let key = SymmetricKey(data: Data(code.utf8))
        return Data(HMAC<SHA256>.authenticationCode(for: nonce, using: key))
    }
}
