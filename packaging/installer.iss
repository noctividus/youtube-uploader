#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef ReleaseDir
  #error ReleaseDir is required
#endif

[Setup]
AppId={{39B1F19A-BDBC-45C8-BA04-0BA69C0F9C70}
AppName=vMix YouTube Uploader
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\vMix YouTube Uploader
DefaultGroupName=vMix YouTube Uploader
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ReleaseDir}
OutputBaseFilename=vMixYouTubeUploader-{#AppVersion}-windows-x64-setup
SetupIconFile=..\vmix_youtube_uploader\assets\app.ico
UninstallDisplayIcon={app}\vMixYouTubeUploader.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
AppMutex=vMixYouTubeUploader.Running
Uninstallable=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start when I sign in to Windows"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\vMix YouTube Uploader"; Filename: "{app}\vMixYouTubeUploader.exe"
Name: "{autodesktop}\vMix YouTube Uploader"; Filename: "{app}\vMixYouTubeUploader.exe"; Tasks: desktopicon
Name: "{userstartup}\vMix YouTube Uploader"; Filename: "{app}\vMixYouTubeUploader.exe"; Tasks: startup

[Run]
Filename: "{app}\vMixYouTubeUploader.exe"; Description: "Launch vMix YouTube Uploader"; Flags: nowait postinstall skipifsilent

; Runtime settings, databases, logs and credentials are outside {app} and are
; deliberately retained on uninstall. Never recursively remove user data.
