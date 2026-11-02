# Microphone driver: plan (not implemented)

**Why it needs a driver.** Windows has no API for an ordinary program to create a microphone other apps can choose; the device
must be provided by an audio driver, and Windows only loads drivers signed by Microsoft (or test-signed, see below).

**Why it isn't in this build.** A driver runs inside the Windows kernel: a bug blue-screens the machine. It was not responsible to hand
you kernel code that had never been compiled or run, so the order is: prove the camera pipeline first, then the driver.

**What is already done (so the driver is small):** the app converts phone audio to 48 kHz mono PCM16 and writes it to a ring buffer in
shared memory (`Local\AndroCamAudio`, layout in `host/androcam_host/shm.py`). The driver only has to read it.

**Plan**
1. Start from Microsoft's MIT-licensed *SimpleAudioSample* (Windows-driver-samples), reduced to ONE capture endpoint named "AndroCam Microphone".
2. Its capture stream reads `AudioShm`: frames between `read_pos` and `write_pos`; if `connected == 0` or the app is closed, it returns silence.
3. CI builds it with the Windows Driver Kit and signs it with a **test certificate**.
4. First run happens only in a virtual machine (snapshot first). No installer ships it to a real PC until that works.
5. Signing reality for end users:
   * Test-signed drivers load only with Windows "test signing" on, which requires **Secure Boot off** in firmware, a reboot, and shows a
     "Test Mode" watermark. Acceptable for developers, not for everyone.
   * Normal users need Microsoft attestation signing: an EV code-signing certificate (paid, annual) and a Partner Center account.
   If you obtain one, step 3 becomes a signing step and the Test Mode requirement disappears.
