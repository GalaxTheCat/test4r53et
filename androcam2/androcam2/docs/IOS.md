# iPhone app

Apple only lets you install apps you signed yourself, so there is no way around needing a Mac and an Apple ID.

**Build:** the CI job `ios` produces `AndroCam-unsigned.ipa`. It is deliberately unsigned.
**Install (pick one):**
1. On a Mac with Xcode: `brew install xcodegen && cd ios && xcodegen`, open `AndroCam.xcodeproj`, choose your Apple ID team under
   *Signing & Capabilities*, plug in the iPhone, press Run. A free Apple ID works; the app must be re-installed every 7 days.
2. Re-sign the unsigned .ipa with a sideloading tool of your choice.

**Use:** open AndroCam on the iPhone, type the PC address and the 6-digit code from the PC app, tap Start.
Keep the app in the foreground (iOS pauses the camera in the background). The first connection triggers iOS's "Local Network" prompt: allow it.
