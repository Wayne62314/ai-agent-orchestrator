LangString AiaoAutostartPrompt ${LANG_ENGLISH} \
  "Start AI Agent Orchestrator automatically when you sign in to Windows?"
LangString AiaoAutostartPrompt ${LANG_SIMPCHINESE} \
  "登录 Windows 后自动启动 AI Agent Orchestrator？"

!macro NSIS_HOOK_POSTINSTALL
  ClearErrors
  ${GetOptions} $CMDLINE "/AUTOSTART" $R0
  ${IfNot} ${Errors}
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCTNAME}" '$\"$INSTDIR\${MAINBINARYNAME}.exe$\"'
  ${ElseIf} $UpdateMode = 0
    ${If} $PassiveMode = 0
      ${IfNot} ${Silent}
        MessageBox MB_ICONQUESTION|MB_YESNO|MB_DEFBUTTON2 \
          "$(AiaoAutostartPrompt)" IDNO aiao_no_autostart
        WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
          "${PRODUCTNAME}" '$\"$INSTDIR\${MAINBINARYNAME}.exe$\"'
        Goto aiao_autostart_done

        aiao_no_autostart:
          DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
            "${PRODUCTNAME}"

        aiao_autostart_done:
      ${EndIf}
    ${EndIf}
  ${EndIf}
!macroend
