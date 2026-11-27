Unicode True
!include "LogicLib.nsh"
!include "x64.nsh"

!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\SyncPlayU"

Name "SyncPlayU ${DISPLAY_VERSION}"
OutFile "${INSTALLER}"
InstallDir "$LOCALAPPDATA\Programs\SyncPlayU"
RequestExecutionLevel user
Icon "${PAYLOAD}\resources\syncplayu.ico"
UninstallIcon "${PAYLOAD}\resources\syncplayu.ico"
SetCompressor /SOLID lzma
VIProductVersion "${FILE_VERSION}"
VIAddVersionKey "ProductName" "SyncPlayU"
VIAddVersionKey "FileDescription" "SyncPlayU Windows x64 Setup"
VIAddVersionKey "CompanyName" "NightMachinery"
VIAddVersionKey "FileVersion" "${FILE_VERSION}"
VIAddVersionKey "ProductVersion" "${DISPLAY_VERSION}"

LicenseData "${PAYLOAD}\LICENSE"
Page license
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Function .onInit
  ${If} ${RunningX64}
    SetRegView 64
  ${Else}
    MessageBox MB_ICONSTOP "SyncPlayU requires a 64-bit version of Windows." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
FunctionEnd

Section "SyncPlayU"
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r /x "syncplay.ini" "${PAYLOAD}\*"
  SetOverwrite off
  File "${PAYLOAD}\syncplay.ini"
  SetOverwrite on

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortCut "$SMPROGRAMS\SyncPlayU.lnk" "$INSTDIR\SyncPlayU.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "SyncPlayU"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "NightMachinery"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${DISPLAY_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\SyncPlayU.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  SetOutPath "$TEMP"
  !include "${REMOVE_FILES}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\SyncPlayU.lnk"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd
