package app.androcam

import java.io.DataInputStream
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec
import org.json.JSONObject

/** AndroCam wire protocol v1. Mirrors host/androcam_host/protocol.py. All integers are big-endian. */
object Proto {
    const val PORT = 47801
    const val DISCOVERY_PORT = 47800
    const val T_HELLO = 1
    const val T_CHALLENGE = 2
    const val T_AUTH = 3
    const val T_AUTH_OK = 4
    const val T_AUTH_FAIL = 5
    const val T_VIDEO = 6
    const val T_AUDIO = 7
    const val T_CONTROL = 8
    const val T_STATUS = 9
    const val T_PING = 10
    const val T_PONG = 11
    const val MAX_PAYLOAD = 16 * 1024 * 1024

    class Packet(val type: Int, val payload: ByteArray)

    fun packet(type: Int, payload: ByteArray = ByteArray(0)): ByteArray {
        val b = ByteBuffer.allocate(8 + payload.size)
        b.put('A'.code.toByte()).put('C'.code.toByte()).put(type.toByte()).put(0.toByte()).putInt(payload.size).put(payload)
        return b.array()
    }

    fun json(type: Int, obj: JSONObject): ByteArray = packet(type, obj.toString().toByteArray(Charsets.UTF_8))

    /** VIDEO payload: width u16, height u16, rotation u16 (clockwise degrees), mirror u8, facing u8, ts_ms u32, then JPEG. */
    fun video(w: Int, h: Int, rotation: Int, mirror: Boolean, facing: Int, tsMs: Int, jpeg: ByteArray): ByteArray {
        val b = ByteBuffer.allocate(8 + 12 + jpeg.size)
        b.put('A'.code.toByte()).put('C'.code.toByte()).put(T_VIDEO.toByte()).put(0.toByte()).putInt(12 + jpeg.size)
        b.putShort(w.toShort()).putShort(h.toShort()).putShort(rotation.toShort())
        b.put((if (mirror) 1 else 0).toByte()).put(facing.toByte()).putInt(tsMs).put(jpeg)
        return b.array()
    }

    /** AUDIO payload: sample_rate u32, channels u8, bits u8, reserved u16, ts_ms u32, then PCM16 little-endian. */
    fun audio(rate: Int, channels: Int, tsMs: Int, pcm: ByteArray): ByteArray {
        val b = ByteBuffer.allocate(8 + 12 + pcm.size)
        b.put('A'.code.toByte()).put('C'.code.toByte()).put(T_AUDIO.toByte()).put(0.toByte()).putInt(12 + pcm.size)
        b.putInt(rate).put(channels.toByte()).put(16.toByte()).putShort(0.toShort()).putInt(tsMs).put(pcm)
        return b.array()
    }

    fun readPacket(inp: DataInputStream): Packet {
        val h = ByteArray(8)
        inp.readFully(h)
        if (h[0] != 'A'.code.toByte() || h[1] != 'C'.code.toByte()) throw IOException("bad magic")
        val len = ByteBuffer.wrap(h, 4, 4).int
        if (len < 0 || len > MAX_PAYLOAD) throw IOException("bad length")
        val p = ByteArray(len)
        inp.readFully(p)
        return Packet(h[2].toInt() and 0xFF, p)
    }

    fun hmac(code: String, nonce: ByteArray): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(code.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return mac.doFinal(nonce)
    }
}

data class Pc(val name: String, val host: String, val port: Int)

/** Finds PCs running AndroCam on the local network (UDP broadcast, answered by the PC app). */
object Discovery {
    fun find(timeoutMs: Int = 2500): List<Pc> {
        val found = LinkedHashMap<String, Pc>()
        try {
            DatagramSocket().use { s ->
                s.broadcast = true
                s.soTimeout = 300
                val msg = "ANDROCAM?".toByteArray()
                val end = System.currentTimeMillis() + timeoutMs
                var lastSend = 0L
                while (System.currentTimeMillis() < end) {
                    val now = System.currentTimeMillis()
                    if (now - lastSend > 700) {
                        try {
                            s.send(DatagramPacket(msg, msg.size, InetAddress.getByName("255.255.255.255"), Proto.DISCOVERY_PORT))
                        } catch (_: Exception) {
                        }
                        lastSend = now
                    }
                    try {
                        val buf = ByteArray(512)
                        val pkt = DatagramPacket(buf, buf.size)
                        s.receive(pkt)
                        val j = JSONObject(String(pkt.data, 0, pkt.length, Charsets.UTF_8))
                        val host = pkt.address.hostAddress
                        if (j.optString("app") == "androcam" && host != null) {
                            found[host] = Pc(j.optString("name", host), host, j.optInt("port", Proto.PORT))
                        }
                    } catch (_: SocketTimeoutException) {
                    } catch (_: Exception) {
                    }
                }
            }
        } catch (_: Exception) {
        }
        return found.values.toList()
    }
}
