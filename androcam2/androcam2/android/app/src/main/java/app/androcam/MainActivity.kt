package app.androcam

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

/** One simple screen: pick your PC, type the pairing code once, press Start. */
class MainActivity : Activity() {

    private lateinit var statusView: TextView
    private lateinit var pcList: LinearLayout
    private lateinit var hostEdit: EditText
    private lateinit var codeEdit: EditText
    private lateinit var startBtn: Button
    private lateinit var searchBtn: Button
    private val ui = Handler(Looper.getMainLooper())
    private val prefs by lazy { getSharedPreferences("androcam", Context.MODE_PRIVATE) }

    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()

    private fun label(text: String, sizeSp: Float, color: Int, bold: Boolean = false) = TextView(this).apply {
        this.text = text
        textSize = sizeSp
        setTextColor(color)
        if (bold) setTypeface(typeface, Typeface.BOLD)
    }

    private fun button(text: String, primary: Boolean) = Button(this).apply {
        this.text = text
        isAllCaps = false
        textSize = 16f
        setTextColor(Color.WHITE)
        setBackgroundColor(if (primary) Color.parseColor("#5B8CFF") else Color.parseColor("#262C3A"))
        setPadding(dp(16), dp(14), dp(16), dp(14))
    }

    private fun field(hint: String, inputType: Int) = EditText(this).apply {
        this.hint = hint
        this.inputType = inputType
        setTextColor(Color.WHITE)
        setHintTextColor(Color.parseColor("#8C95A8"))
        setBackgroundColor(Color.parseColor("#171B24"))
        setPadding(dp(14), dp(14), dp(14), dp(14))
        textSize = 17f
    }

    private fun gap(h: Int) = View(this).apply { layoutParams = LinearLayout.LayoutParams(1, dp(h)) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(40), dp(24), dp(24))
            setBackgroundColor(Color.parseColor("#0B0D12"))
        }
        root.addView(label("AndroCam", 32f, Color.WHITE, true))
        root.addView(label("Use this phone as a webcam and microphone for your PC.", 15f, Color.parseColor("#8C95A8")))
        root.addView(gap(20))

        statusView = label("Idle", 17f, Color.parseColor("#33D17A"), true)
        root.addView(statusView)
        root.addView(gap(16))

        root.addView(label("1. Choose your PC", 14f, Color.parseColor("#8C95A8")))
        pcList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(pcList)
        searchBtn = button("Search for my PC", false).apply { setOnClickListener { search() } }
        root.addView(searchBtn)
        root.addView(gap(8))
        hostEdit = field("or type the PC address, e.g. 192.168.1.4", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        hostEdit.setText(prefs.getString("host", ""))
        root.addView(hostEdit)
        root.addView(gap(16))

        root.addView(label("2. Pairing code (shown in the AndroCam app on your PC)", 14f, Color.parseColor("#8C95A8")))
        codeEdit = field("6-digit code", InputType.TYPE_CLASS_NUMBER)
        codeEdit.setText(prefs.getString("code", ""))
        root.addView(codeEdit)
        root.addView(gap(20))

        startBtn = button("Start", true).apply { setOnClickListener { onStartStop() } }
        root.addView(startBtn)

        val tip = label("Keep this app open or leave it running in the background. USB tethering works too: " +
            "turn it on, then type the PC's address above.", 13f, Color.parseColor("#8C95A8"))
        tip.gravity = Gravity.START
        root.addView(gap(16))
        root.addView(tip)

        setContentView(ScrollView(this).apply { setBackgroundColor(Color.parseColor("#0B0D12")); addView(root) })
        search()
    }

    override fun onResume() {
        super.onResume()
        ui.post(object : Runnable {
            override fun run() {
                statusView.text = StreamService.state
                val on = StreamService.running
                startBtn.text = if (on) "Stop" else "Start"
                statusView.setTextColor(Color.parseColor(if (StreamService.state.startsWith("Streaming")) "#33D17A" else "#FFB020"))
                ui.postDelayed(this, 500)
            }
        })
    }

    override fun onPause() {
        ui.removeCallbacksAndMessages(null)
        super.onPause()
    }

    private fun search() {
        searchBtn.isEnabled = false
        searchBtn.text = "Searching…"
        Thread {
            val found = Discovery.find()
            runOnUiThread {
                pcList.removeAllViews()
                if (found.isEmpty()) {
                    pcList.addView(label("No PC found automatically. Type its address below.", 14f, Color.parseColor("#FFB020")))
                }
                for (pc in found) {
                    pcList.addView(button("${pc.name}  (${pc.host})", false).apply {
                        setOnClickListener { hostEdit.setText(pc.host); Toast.makeText(context, "Selected ${pc.name}", Toast.LENGTH_SHORT).show() }
                    })
                    pcList.addView(gap(6))
                    if (found.size == 1 && hostEdit.text.isNullOrBlank()) hostEdit.setText(pc.host)
                }
                searchBtn.isEnabled = true
                searchBtn.text = "Search again"
            }
        }.start()
    }

    private fun onStartStop() {
        if (StreamService.running) {
            startService(Intent(this, StreamService::class.java).setAction(StreamService.ACTION_STOP))
            return
        }
        val host = hostEdit.text.toString().trim()
        val code = codeEdit.text.toString().trim()
        if (host.isEmpty()) { toast("Choose your PC or type its address first."); return }
        if (code.length != 6) { toast("Enter the 6-digit pairing code from the PC app."); return }
        prefs.edit().putString("host", host).putString("code", code).apply()
        val needed = ArrayList<String>()
        for (p in listOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)) {
            if (checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) needed.add(p)
        }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (needed.isEmpty()) launchService() else requestPermissions(needed.toTypedArray(), 7)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != 7) return
        val cam = checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        val mic = checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        if (cam && mic) launchService() else toast("AndroCam needs the camera and microphone permissions.")
    }

    private fun launchService() {
        val i = Intent(this, StreamService::class.java).setAction(StreamService.ACTION_START)
            .putExtra(StreamService.EXTRA_HOST, hostEdit.text.toString().trim())
            .putExtra(StreamService.EXTRA_PORT, Proto.PORT)
            .putExtra(StreamService.EXTRA_CODE, codeEdit.text.toString().trim())
        StreamService.state = "Starting…"
        startForegroundService(i)
    }

    private fun toast(msg: String) = Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
}
