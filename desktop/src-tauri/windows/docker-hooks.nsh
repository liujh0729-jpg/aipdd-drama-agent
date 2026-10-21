; =============================================================================
; Docker Desktop optional component for the NSIS installer.
; UTF-8 WITH BOM: makensis only decodes a BOM-marked file as UTF-8, and the
; Chinese LangStrings below require it. Do not save this file as ANSI.
;
; The file is included by installer.nsi near the top, BEFORE product defines
; and Var declarations, so top level may only contain !define / !macro.
; Anything that needs ${PRODUCTNAME}, $PassiveMode, ... runs from a macro
; inserted at the proper place in installer.nsi.
; =============================================================================

!define DOCKER_TERMS_URL "https://www.docker.com/legal/docker-subscription-service-agreement/"

!macro DOCKER_LANGSTRINGS
  LangString DOCKER_PAGE_TITLE ${LANG_SIMPCHINESE} "可选组件"
  LangString DOCKER_PAGE_TITLE ${LANG_ENGLISH} "Optional components"
  LangString DOCKER_PAGE_SUBTITLE ${LANG_SIMPCHINESE} "选择是否安装 Docker Desktop"
  LangString DOCKER_PAGE_SUBTITLE ${LANG_ENGLISH} "Choose whether to install Docker Desktop"
  LangString DOCKER_INTRO ${LANG_SIMPCHINESE} "Docker Desktop 用于运行容器化服务。跳过安装不影响 ${PRODUCTNAME} 的主体功能。"
  LangString DOCKER_INTRO ${LANG_ENGLISH} "Docker Desktop runs containerized services. Skipping it does not affect the core features of ${PRODUCTNAME}."
  LangString DOCKER_CHECKBOX ${LANG_SIMPCHINESE} "安装 Docker Desktop（约 650 MB）"
  LangString DOCKER_CHECKBOX ${LANG_ENGLISH} "Install Docker Desktop (about 650 MB)"
  LangString DOCKER_DETAIL ${LANG_SIMPCHINESE} "需要管理员权限，安装完成后需要重启电脑。安装包从 Docker 官方地址下载，运行前会校验 Docker Inc. 数字签名。"
  LangString DOCKER_DETAIL ${LANG_ENGLISH} "Requires administrator rights and a reboot after installation. The package is downloaded from Docker's official server and its Docker Inc. signature is verified before running."
  LangString DOCKER_DETECTED_LABEL ${LANG_SIMPCHINESE} "已检测到 Docker Desktop，将跳过安装"
  LangString DOCKER_DETECTED_LABEL ${LANG_ENGLISH} "Docker Desktop detected - installation will be skipped"
  LangString DOCKER_DETECTED_DETAIL ${LANG_SIMPCHINESE} "当前系统已安装 Docker Desktop，无需重复安装。如需升级，请稍后手动运行 Docker Desktop 安装程序。"
  LangString DOCKER_DETECTED_DETAIL ${LANG_ENGLISH} "Docker Desktop is already installed. To upgrade, run the Docker Desktop installer manually later."
  LangString DOCKER_TERMS ${LANG_SIMPCHINESE} "查看 Docker 订阅服务协议"
  LangString DOCKER_TERMS ${LANG_ENGLISH} "Docker Subscription Service Agreement"
  LangString DOCKER_SCRIPT_MISSING ${LANG_SIMPCHINESE} "未找到 Docker 安装脚本，已跳过 Docker Desktop。"
  LangString DOCKER_SCRIPT_MISSING ${LANG_ENGLISH} "Docker install script not found; skipping Docker Desktop."
  LangString DOCKER_DOWNLOADING ${LANG_SIMPCHINESE} "正在下载并安装 Docker Desktop，请在 UAC 窗口中允许提权..."
  LangString DOCKER_DOWNLOADING ${LANG_ENGLISH} "Downloading and installing Docker Desktop. Approve the UAC prompt to continue..."
  LangString DOCKER_SKIP_DETECTED ${LANG_SIMPCHINESE} "已检测到 Docker Desktop，跳过安装。"
  LangString DOCKER_SKIP_DETECTED ${LANG_ENGLISH} "Docker Desktop already installed; skipping."
  LangString DOCKER_SUCCESS ${LANG_SIMPCHINESE} "Docker Desktop 安装完成。"
  LangString DOCKER_SUCCESS ${LANG_ENGLISH} "Docker Desktop installation finished."
  LangString DOCKER_SUCCESS_MSG ${LANG_SIMPCHINESE} "Docker Desktop 已安装。建议重启电脑后再使用。"
  LangString DOCKER_SUCCESS_MSG ${LANG_ENGLISH} "Docker Desktop was installed. Restart your computer before using it."
  LangString DOCKER_SUCCESS_REBOOT_MSG ${LANG_SIMPCHINESE} "Docker Desktop 已安装，需要重启电脑后生效。"
  LangString DOCKER_SUCCESS_REBOOT_MSG ${LANG_ENGLISH} "Docker Desktop was installed and requires a reboot to take effect."
  LangString DOCKER_CANCELLED ${LANG_SIMPCHINESE} "Docker Desktop 安装已取消。"
  LangString DOCKER_CANCELLED ${LANG_ENGLISH} "Docker Desktop installation was cancelled."
  LangString DOCKER_CANCELLED_MSG ${LANG_SIMPCHINESE} "已取消 Docker Desktop 安装（未获得管理员权限）。主程序安装不受影响。"
  LangString DOCKER_CANCELLED_MSG ${LANG_ENGLISH} "Docker Desktop installation was cancelled (administrator rights were not granted). The main application installation is unaffected."
  LangString DOCKER_FAILED ${LANG_SIMPCHINESE} "Docker Desktop 安装失败，退出码："
  LangString DOCKER_FAILED ${LANG_ENGLISH} "Docker Desktop installation failed, exit code: "
  LangString DOCKER_FAILED_MSG ${LANG_SIMPCHINESE} "Docker Desktop 安装失败。主程序已正常安装，可稍后手动安装 Docker Desktop，详情见安装日志。"
  LangString DOCKER_FAILED_MSG ${LANG_ENGLISH} "Docker Desktop could not be installed. The main application was installed successfully; you can install Docker Desktop manually later. See the installer log for details."
!macroend

; Inserted right after MUI_PAGE_DIRECTORY in installer.nsi.
!macro DOCKER_PAGE
  Page custom PageDocker PageLeaveDocker
!macroend

; Inserted after the Var declarations in installer.nsi.
!macro DOCKER_FUNCTIONS
  Var DockerCheckBox
  Var DockerTermsButton
  Var DockerDetected
  Var DockerWant

  Function DockerDetected
    StrCpy $DockerDetected 0
    ReadRegStr $0 HKLM "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop" "DisplayVersion"
    ReadRegStr $1 HKLM "SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop" "DisplayVersion"
    ReadRegStr $2 HKCU "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop" "DisplayVersion"
    ${If} $0 != ""
    ${OrIf} $1 != ""
    ${OrIf} $2 != ""
      StrCpy $DockerDetected 1
      Return
    ${EndIf}
    ${If} ${FileExists} "$PROGRAMFILES64\Docker\Docker\Docker Desktop.exe"
    ${OrIf} ${FileExists} "$PROGRAMFILES\Docker\Docker\Docker Desktop.exe"
    ${OrIf} ${FileExists} "$LOCALAPPDATA\Docker\Docker Desktop.exe"
      StrCpy $DockerDetected 1
    ${EndIf}
  FunctionEnd

  Function PageDocker
    ; No component page for silent, passive or /UPDATE runs.
    ${If} $PassiveMode = 1
    ${OrIf} ${Silent}
    ${OrIf} $UpdateMode = 1
      Abort
    ${EndIf}

    Call DockerDetected

    !insertmacro MUI_HEADER_TEXT "$(DOCKER_PAGE_TITLE)" "$(DOCKER_PAGE_SUBTITLE)"
    nsDialogs::Create 1018
    Pop $0
    ${If} $0 == error
      Abort
    ${EndIf}
    ${IfThen} $(^RTL) = 1 ${|} nsDialogs::SetRTL $(^RTL) ${|}

    ${NSD_CreateLabel} 0 0 100% 28u "$(DOCKER_INTRO)"
    Pop $0

    ${If} $DockerDetected = 1
      ${NSD_CreateCheckBox} 0 34u 100% 12u "$(DOCKER_DETECTED_LABEL)"
      Pop $DockerCheckBox
      SendMessage $DockerCheckBox ${BM_SETCHECK} ${BST_CHECKED} 0
      EnableWindow $DockerCheckBox 0
      ${NSD_CreateLabel} 0 52u 100% 34u "$(DOCKER_DETECTED_DETAIL)"
      Pop $0
    ${Else}
      ${NSD_CreateCheckBox} 0 34u 100% 12u "$(DOCKER_CHECKBOX)"
      Pop $DockerCheckBox
      SendMessage $DockerCheckBox ${BM_SETCHECK} ${BST_CHECKED} 0
      ${NSD_CreateLabel} 0 52u 100% 34u "$(DOCKER_DETAIL)"
      Pop $0
    ${EndIf}

    ${NSD_CreateButton} 0 90u 150u 14u "$(DOCKER_TERMS)"
    Pop $DockerTermsButton
    ${NSD_OnClick} $DockerTermsButton OnDockerTermsClick

    nsDialogs::Show
  FunctionEnd

  Function PageLeaveDocker
    ${If} $DockerCheckBox == ""
      StrCpy $DockerWant 0
      Return
    ${EndIf}
    ${NSD_GetState} $DockerCheckBox $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $DockerWant 1
    ${Else}
      StrCpy $DockerWant 0
    ${EndIf}
  FunctionEnd

  Function OnDockerTermsClick
    ExecShell "open" "${DOCKER_TERMS_URL}"
  FunctionEnd

  Function InstallDockerIfRequested
    ${If} $DockerWant != 1
      Return
    ${EndIf}
    ${If} $PassiveMode = 1
    ${OrIf} ${Silent}
    ${OrIf} $UpdateMode = 1
      Return
    ${EndIf}

    Call DockerDetected
    ${If} $DockerDetected = 1
      DetailPrint "$(DOCKER_SKIP_DETECTED)"
      Return
    ${EndIf}

    ${IfNot} ${FileExists} "$INSTDIR\resources\docker\docker-install.ps1"
      DetailPrint "$(DOCKER_SCRIPT_MISSING)"
      Return
    ${EndIf}

    DetailPrint "$(DOCKER_DOWNLOADING)"
    nsExec::ExecToLog 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\resources\docker\docker-install.ps1"'
    Pop $0

    ${If} $0 == "0"
      DetailPrint "$(DOCKER_SUCCESS)"
      MessageBox MB_ICONINFORMATION|MB_OK "$(DOCKER_SUCCESS_MSG)"
    ${ElseIf} $0 == "3010"
      DetailPrint "$(DOCKER_SUCCESS)"
      MessageBox MB_ICONINFORMATION|MB_OK "$(DOCKER_SUCCESS_REBOOT_MSG)"
    ${ElseIf} $0 == "1223"
      DetailPrint "$(DOCKER_CANCELLED)"
      MessageBox MB_ICONEXCLAMATION|MB_OK "$(DOCKER_CANCELLED_MSG)"
    ${Else}
      DetailPrint "$(DOCKER_FAILED) $0"
      MessageBox MB_ICONEXCLAMATION|MB_OK "$(DOCKER_FAILED_MSG)"
    ${EndIf}
  FunctionEnd
!macroend

!macro NSIS_HOOK_POSTINSTALL
  Call InstallDockerIfRequested
!macroend
