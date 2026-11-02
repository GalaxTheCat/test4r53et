package app.androcam

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.Size
import androidx.camera.core.Camera
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.Executors
import org.json.JSONArray
import org.json.JSONObject

/**
 * Foreground service: connects to the PC, authenticates with the pairing code, then streams camera frames
 * (JPEG) and microphone audio (PCM16) until stopped. The PC can send commands (flash, camera, quality, mic).
 */
class StreamService : LifecycleService() {

    companion object {
        const val ACTION_START = "app.androcam.START"
        const val ACTION_STOP = "app.androcam.STOP"
        const val EXTRA_HOST = "host"
        const val EXTRA_PORT = "port"
        const val EXTRA_CODE = "code"
        private const val NOTIF_ID = 1
        @Volatile var state: String = "Idle"
        @Volatile var running: Boolean = false
    }

    private class AuthException(msg: String) : Exception(msg)

    private val main = Handler(Looper.getMainLooper())
    private val analysisExecutor = Executors.newSingleThreadExecutor()
    private var worker: Thread? = null
    @Volatile private var socket: Socket? = null
    @Volatile private var out: java.io.OutputStream? = null
    private val sendLock = Any()

    private var provider: ProcessCameraProvider? = null
    private var camera: Camera? = null
    @Volatile private var facing = "back"
    @Volatile private var targetW = 1280
    @Volatile private var targetH = 720
    @Volatile private var jpegQuality = 80
    @Volatile private var targetFps = 30
    @Volatile private var torchOn = false
    @Volatile private var micOn = true
    @Volatile private var lastFrame = 0L
    private var t0 = 0L

    private var audio: AudioRecord? = null
    @Volatile private var audioRunning = false

    override fun onBind(intent: Intent): IBinder? = super.onBind(intent)

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        if (intent?.action == ACTION_STOP) {
            shutdown()
            stopSelf()
            return START_NOT_STICKY
        }
        if (intent?.action == ACTION_START && !running) {
            val host = intent.getStringExtra(EXTRA_HOST) ?: return START_NOT_STICKY
            val port = intent.getIntExtra(EXTRA_PORT, Proto.PORT)
            val code = intent.getStringExtra(EXTRA_CODE) ?: ""
            val types = ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA or ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            ServiceCompat.startForeground(this, NOTIF_ID, buildNotification(), types)
            running = true
            t0 = SystemClock.elapsedRealtime()
            worker = Thread { runLoop(host, port, code) }.also { it.isDaemon = true; it.start() }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        shutdown()
        analysisExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun buildNotification(): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(NotificationChannel("stream", "Streaming", NotificationManager.IMPORTANCE_LOW))
        }
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val b = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, "stream") else Notification.Builder(this)
        return b.setContentTitle("AndroCam is streaming")
            .setContentText("Your phone is working as a webcam")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentIntent(open)
            .setOngoing(true)
            .build()
    }

    // ------------------------------------------------------------------ connection loop
    private fun runLoop(host: String, port: Int, code: String) {
        while (running) {
            try {
                session(host, port, code)
            } catch (e: AuthException) {
                state = e.message ?: "Pairing failed"
                running = false
                break
            } catch (e: Exception) {
                state = "Reconnecting… (${e.message ?: e.javaClass.simpleName})"
            }
            stopCapture()
            if (!running) break
            try { Thread.sleep(2000) } catch (_: InterruptedException) { break }
        }
        stopCapture()
        main.post { stopSelf() }
    }

    private fun session(host: String, port: Int, code: String) {
        state = "Connecting to $host…"
        val sock = Socket()
        socket = sock
        try {
            sock.connect(InetSocketAddress(host, port), 4000)
            sock.tcpNoDelay = true
            sock.soTimeout = 8000                       // handshake timeout
            val inp = DataInputStream(BufferedInputStream(sock.getInputStream()))
            out = sock.getOutputStream()

            val ch = Proto.readPacket(inp)
            if (ch.type == Proto.T_AUTH_FAIL) throw AuthException(reason(ch))
            if (ch.type != Proto.T_CHALLENGE) throw IOException("unexpected packet ${ch.type}")
            send(Proto.packet(Proto.T_AUTH, Proto.hmac(code, ch.payload)))
            val r = Proto.readPacket(inp)
            if (r.type == Proto.T_AUTH_FAIL) throw AuthException(reason(r))
            if (r.type != Proto.T_AUTH_OK) throw IOException("unexpected packet ${r.type}")
            val pcName = try { JSONObject(String(r.payload)).optString("pc", host) } catch (_: Exception) { host }

            sock.soTimeout = 0
            send(Proto.json(Proto.T_HELLO, hello()))
            state = "Streaming to $pcName"
            startCapture()
            startPinger()

            while (running) {
                val p = Proto.readPacket(inp)
                when (p.type) {
                    Proto.T_CONTROL -> handleControl(JSONObject(String(p.payload)))
                    Proto.T_PING -> send(Proto.packet(Proto.T_PONG))
                }
            }
        } finally {
            out = null
            try { sock.close() } catch (_: Exception) {}
            socket = null
        }
    }

    private fun reason(p: Proto.Packet): String =
        try { JSONObject(String(p.payload)).optString("reason", "Pairing failed") } catch (_: Exception) { "Pairing failed" }

    /** Writes a packet; if the network is gone, closes the socket so the session loop ends and reconnects. */
    private fun send(bytes: ByteArray) {
        val o = out ?: return
        try {
            synchronized(sendLock) { o.write(bytes); o.flush() }
        } catch (e: IOException) {
            try { socket?.close() } catch (_: Exception) {}
            throw e
        }
    }

    private fun startPinger() {
        Thread {
            while (running && out != null) {
                try { Thread.sleep(4000); send(Proto.packet(Proto.T_PING)) } catch (_: Exception) { break }
            }
        }.also { it.isDaemon = true; it.start() }
    }

    private fun hello(): JSONObject {
        val cm = getSystemService(CAMERA_SERVICE) as CameraManager
        val cams = JSONArray()
        val seen = HashSet<String>()
        for (id in cm.cameraIdList) {
            val f = when (cm.getCameraCharacteristics(id).get(CameraCharacteristics.LENS_FACING)) {
                CameraCharacteristics.LENS_FACING_BACK -> "back"
                CameraCharacteristics.LENS_FACING_FRONT -> "front"
                else -> null
            }
            if (f != null && seen.add(f)) cams.put(f)
        }
        return JSONObject().put("name", Build.MODEL).put("os", "android").put("ver", 1).put("cameras", cams)
    }

    private fun sendStatus() {
        val st = JSONObject().put("torch", torchOn).put("facing", facing).put("w", targetW).put("h", targetH)
            .put("mic", micOn).put("torch_ok", camera?.cameraInfo?.hasFlashUnit() == true)
        try { send(Proto.json(Proto.T_STATUS, st)) } catch (_: Exception) {}
    }

    // ------------------------------------------------------------------ commands from the PC
    private fun handleControl(c: JSONObject) {
        when (c.optString("cmd")) {
            "torch" -> { torchOn = c.optBoolean("on"); main.post { applyTorch(); sendStatus() } }
            "mic" -> { micOn = c.optBoolean("on"); sendStatus() }
            "camera" -> {
                val f = c.optString("facing")
                if (f == "front" || f == "back") { facing = f; torchOn = false; main.post { bindCamera() } }
            }
            "quality" -> {
                val w = c.optInt("w", targetW); val h = c.optInt("h", targetH)
                jpegQuality = c.optInt("jpeg", jpegQuality).coerceIn(30, 100)
                targetFps = c.optInt("fps", targetFps).coerceIn(5, 60)
                if (w != targetW || h != targetH) { targetW = w; targetH = h; main.post { bindCamera() } } else sendStatus()
            }
        }
    }

    // ------------------------------------------------------------------ capture
    private fun startCapture() {
        main.post { bindCamera(); startAudio() }
    }

    private fun stopCapture() {
        audioRunning = false
        try { audio?.stop(); audio?.release() } catch (_: Exception) {}
        audio = null
        main.post {
            try { provider?.unbindAll() } catch (_: Exception) {}
            camera = null
        }
    }

    private fun bindCamera() {
        if (!running) return
        val future = ProcessCameraProvider.getInstance(this)
        future.addListener({
            try {
                val p = future.get()
                provider = p
                p.unbindAll()
                val selector = if (facing == "front") CameraSelector.DEFAULT_FRONT_CAMERA else CameraSelector.DEFAULT_BACK_CAMERA
                val rs = ResolutionSelector.Builder()
                    .setResolutionStrategy(ResolutionStrategy(Size(targetW, targetH), ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER))
                    .build()
                val analysis = ImageAnalysis.Builder()
                    .setResolutionSelector(rs)
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()
                analysis.setAnalyzer(analysisExecutor) { image ->
                    try { onFrame(image) } catch (_: Exception) {} finally { image.close() }
                }
                camera = p.bindToLifecycle(this, selector, analysis)
                applyTorch()
                sendStatus()
            } catch (e: Exception) {
                state = "Camera error: ${e.message}"
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun applyTorch() {
        val cam = camera ?: return
        if (cam.cameraInfo.hasFlashUnit()) cam.cameraControl.enableTorch(torchOn) else torchOn = false
    }

    private fun onFrame(image: ImageProxy) {
        if (out == null) return
        val now = SystemClock.elapsedRealtime()
        if (now - lastFrame < 1000L / targetFps - 4) return        // frame-rate cap: skip before the costly conversion
        lastFrame = now
        val jpeg = toJpeg(image, jpegQuality)
        val facingByte = if (facing == "front") 1 else 0
        try {
            send(Proto.video(image.width, image.height, image.imageInfo.rotationDegrees, false, facingByte, (now - t0).toInt(), jpeg))
        } catch (_: IOException) {
        }
    }

    /** YUV_420_888 -> NV21 -> JPEG, honouring row/pixel strides (the classic Android pitfall). */
    private fun toJpeg(image: ImageProxy, quality: Int): ByteArray {
        val w = image.width
        val h = image.height
        val yp = image.planes[0]
        val up = image.planes[1]
        val vp = image.planes[2]
        val nv21 = ByteArray(w * h * 3 / 2)
        val yBuf = yp.buffer
        var pos = 0
        if (yp.rowStride == w) {
            yBuf.position(0)
            yBuf.get(nv21, 0, w * h)
            pos = w * h
        } else {
            for (r in 0 until h) {
                yBuf.position(r * yp.rowStride)
                yBuf.get(nv21, pos, w)
                pos += w
            }
        }
        val uBuf = up.buffer
        val vBuf = vp.buffer
        val rowStride = vp.rowStride
        val pixStride = vp.pixelStride
        for (r in 0 until h / 2) {
            for (c in 0 until w / 2) {
                val idx = r * rowStride + c * pixStride
                nv21[pos++] = vBuf.get(idx)
                nv21[pos++] = uBuf.get(idx)
            }
        }
        val bos = ByteArrayOutputStream(w * h / 4)
        YuvImage(nv21, ImageFormat.NV21, w, h, null).compressToJpeg(Rect(0, 0, w, h), quality, bos)
        return bos.toByteArray()
    }

    // ------------------------------------------------------------------ microphone
    private fun startAudio() {
        if (audioRunning || checkSelfPermission(android.Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) return
        try {
            val rate = 48000
            val min = AudioRecord.getMinBufferSize(rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val rec = AudioRecord(MediaRecorder.AudioSource.MIC, rate, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(min, 19200))
            audio = rec
            rec.startRecording()
            audioRunning = true
            Thread {
                val buf = ByteArray(1920)                      // 20 ms of mono 48 kHz PCM16
                while (audioRunning && running) {
                    var n = 0
                    while (n < buf.size && audioRunning) {
                        val r = rec.read(buf, n, buf.size - n)
                        if (r <= 0) break
                        n += r
                    }
                    if (n == buf.size && micOn && out != null) {
                        try {
                            send(Proto.audio(rate, 1, (SystemClock.elapsedRealtime() - t0).toInt(), buf.copyOf()))
                        } catch (_: IOException) {
                            break
                        }
                    }
                }
            }.also { it.isDaemon = true; it.start() }
        } catch (e: Exception) {
            state = "Microphone error: ${e.message}"
        }
    }

    private fun shutdown() {
        running = false
        try { socket?.close() } catch (_: Exception) {}
        stopCapture()
        state = "Idle"
    }
}
