[Setup]
AppName=Marvin Gym
AppVersion=1.1
DefaultDirName={autopf}\Marvin Gym
DefaultGroupName=Marvin Gym
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=MarvinGym_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "dist\MarvinGym.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Marvin Gym"; Filename: "{app}\MarvinGym.exe"
Name: "{autodesktop}\Marvin Gym"; Filename: "{app}\MarvinGym.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
