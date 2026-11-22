; Auto Captions installer (build: makensis installer.nsi)
Unicode true
!include "MUI2.nsh"

!define NAME "Auto Captions (made by Amr)"
!define VERSION "1.7"
!define SCRIPTS "$APPDATA\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility"
!define UNINST "Software\Microsoft\Windows\CurrentVersion\Uninstall\AmrCaptions"

Name "${NAME}"
OutFile "AutoCaptions-Setup.exe"
InstallDir "$LOCALAPPDATA\AmrCaptions"
RequestExecutionLevel user
BrandingText "Auto Captions v${VERSION} - made by Amr"
VIProductVersion "${VERSION}.0.0"
VIAddVersionKey "ProductName" "${NAME}"
VIAddVersionKey "CompanyName" "Amr"
VIAddVersionKey "FileDescription" "${NAME} for DaVinci Resolve"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Amr - MIT License"

!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "sidebar.bmp"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_BITMAP "header.bmp"
!define MUI_HEADERIMAGE_RIGHT
!define MUI_WELCOMEPAGE_TITLE "Auto Captions v${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "Animated Text+ captions for DaVinci Resolve Studio, made by Amr.$\r$\n$\r$\nFree. Works offline. Transcribes with Whisper on your own PC.$\r$\n$\r$\nInstagram: @amrswork"
!define MUI_FINISHPAGE_TITLE "All done!"
!define MUI_FINISHPAGE_TEXT "Restart DaVinci Resolve, then open:$\r$\nWorkspace > Scripts > Auto Captions$\r$\n$\r$\nFirst time: click 'Download Whisper' inside the tool."
!define MUI_FINISHPAGE_LINK "Join the Discord"
!define MUI_FINISHPAGE_LINK_LOCATION "https://discord.gg/CFcSEg6nCq"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE.txt"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "${SCRIPTS}"
  Delete "${SCRIPTS}\Amr Captions.py" ; old name
  File "Auto Captions.py"
  SetOutPath "$INSTDIR"
  File "logo.png"
  File "upload.png"
  File "instagram.png"
  File "discord.png"
  File "icon.ico"
  Delete "$INSTDIR\Amr Captions.drb"
  File "Auto Captions.drb"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "${UNINST}" "DisplayName" "${NAME}"
  WriteRegStr HKCU "${UNINST}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINST}" "Publisher" "Amr"
  WriteRegStr HKCU "${UNINST}" "DisplayIcon" "$INSTDIR\icon.ico"
  WriteRegStr HKCU "${UNINST}" "UninstallString" '"$INSTDIR\Uninstall.exe"'

  ; Resolve needs Python to run scripts
  EnumRegKey $0 HKCU "Software\Python\PythonCore" 0
  StrCmp $0 "" 0 done
  EnumRegKey $0 HKLM "Software\Python\PythonCore" 0
  StrCmp $0 "" 0 done
  MessageBox MB_YESNO "DaVinci Resolve needs Python to run this tool, and it isn't installed.$\r$\n$\r$\nInstall Python now?" IDNO done
  DetailPrint "Installing Python..."
  ExecWait 'winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements' $1
  StrCmp $1 "0" done
  MessageBox MB_OK "Couldn't install Python automatically. Your browser will open python.org: download it and install it."
  ExecShell "open" "https://www.python.org/downloads/windows/"
  done:
SectionEnd

Section "Uninstall"
  Delete "${SCRIPTS}\Auto Captions.py"
  RMDir /r "$INSTDIR"
  DeleteRegKey HKCU "${UNINST}"
SectionEnd
