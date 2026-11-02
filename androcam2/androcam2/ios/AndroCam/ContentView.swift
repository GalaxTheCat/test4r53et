import SwiftUI

struct ContentView: View {
    @StateObject private var streamer = Streamer()
    @AppStorage("host") private var host = ""
    @AppStorage("code") private var code = ""

    var body: some View {
        ZStack {
            Color(red: 0.04, green: 0.05, blue: 0.07).ignoresSafeArea()
            VStack(alignment: .leading, spacing: 14) {
                Text("AndroCam").font(.system(size: 34, weight: .bold)).foregroundColor(.white)
                Text("Use this iPhone as a webcam and microphone for your PC.").foregroundColor(.gray)
                Text(streamer.status)
                    .font(.headline)
                    .foregroundColor(streamer.status.hasPrefix("Streaming") ? .green : .orange)
                    .padding(.vertical, 6)

                Text("1. PC address (shown in the AndroCam app on your PC)").font(.footnote).foregroundColor(.gray)
                TextField("e.g. 192.168.1.4", text: $host)
                    .textInputAutocapitalization(.never).autocorrectionDisabled(true).keyboardType(.numbersAndPunctuation)
                    .padding(12).background(Color(red: 0.09, green: 0.1, blue: 0.14)).cornerRadius(10).foregroundColor(.white)

                Text("2. Pairing code").font(.footnote).foregroundColor(.gray)
                TextField("6-digit code", text: $code)
                    .keyboardType(.numberPad)
                    .padding(12).background(Color(red: 0.09, green: 0.1, blue: 0.14)).cornerRadius(10).foregroundColor(.white)

                Button(action: toggle) {
                    Text(streamer.running ? "Stop" : "Start").frame(maxWidth: .infinity).padding(14)
                        .background(Color(red: 0.36, green: 0.55, blue: 1.0)).foregroundColor(.white).cornerRadius(12)
                }.padding(.top, 8)

                Text("Keep this app open while streaming: iOS pauses the camera when an app goes to the background.")
                    .font(.footnote).foregroundColor(.gray)
                Spacer()
            }.padding(24)
        }
    }

    private func toggle() {
        if streamer.running { streamer.stop(); return }
        let h = host.trimmingCharacters(in: .whitespaces), c = code.trimmingCharacters(in: .whitespaces)
        guard !h.isEmpty, c.count == 6 else { streamer.status = "Enter the PC address and the 6-digit code."; return }
        streamer.start(host: h, code: c)
    }
}
