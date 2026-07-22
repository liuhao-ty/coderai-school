!macro NSIS_HOOK_PREUNINSTALL
  ; Remove WebView caches and logs. Authentication credentials are deleted by
  ; Windows Credential Manager entries and the explicit cmdkey targets below.
  RMDir /r "$APPDATA\cn.coderai.school"
  RMDir /r "$LOCALAPPDATA\cn.coderai.school"
  nsExec::ExecToLog 'cmdkey.exe /delete:cn.coderai.school:coderai_teacher_token'
  nsExec::ExecToLog 'cmdkey.exe /delete:cn.coderai.school:coderai_teacher_refresh_token'
  nsExec::ExecToLog 'cmdkey.exe /delete:cn.coderai.school:coderai_student_token'
!macroend
