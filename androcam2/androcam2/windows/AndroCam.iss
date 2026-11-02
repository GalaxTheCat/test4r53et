; Inno Setup 6. Built by CI after PyInstaller. Installing needs admin once; that same elevation registers the virtual camera,
; so the app never has to ask for permission again.
[Setup]
AppName=AndroCam
AppVersion=2.0.0-alpha1
AppPublisher=AndroCam
DefaultDirName={autopf}\AndroCam
DefaultGroupName=AndroCam
OutputDir=..\dist
OutputBaseFilename=AndroCam-Setup
SetupIconFile=androcam.ico
UninstallDisplayIcon={app}\AndroCam.exe
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=yes

[Files]
Source: "..\dist\AndroCam\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\AndroCam"; Filename: "{app}\AndroCam.exe"
Name: "{autodesktop}\AndroCam"; Filename: "{app}\AndroCam.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Run]
; register the 64-bit and 32-bit virtual camera so every video app can see it
Filename: "{sys}\regsvr32.exe"; Parameters: "/s ""{app}\_internal\resources\androcam_vcam_x64.dll"""; Flags: runhidden; StatusMsg: "Registering virtual camera..."
Filename: "{syswow64}\regsvr32.exe"; Parameters: "/s ""{app}\_internal\resources\androcam_vcam_x86.dll"""; Flags: runhidden
; let phones on your home network (Private profile only) reach AndroCam; nothing is opened on public networks
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall add rule name=""AndroCam"" dir=in action=allow program=""{app}\AndroCam.exe"" enable=yes profile=private"; Flags: runhidden
Filename: "{app}\AndroCam.exe"; Description: "Start AndroCam and open the setup guide"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\netsh.exe"; Parameters: "advfirewall firewall delete rule name=""AndroCam"""; Flags: runhidden; RunOnceId: "DelFirewall"
Filename: "{sys}\regsvr32.exe"; Parameters: "/s /u ""{app}\_internal\resources\androcam_vcam_x64.dll"""; Flags: runhidden; RunOnceId: "UnregVcam64"
Filename: "{syswow64}\regsvr32.exe"; Parameters: "/s /u ""{app}\_internal\resources\androcam_vcam_x86.dll"""; Flags: runhidden; RunOnceId: "UnregVcam32"
