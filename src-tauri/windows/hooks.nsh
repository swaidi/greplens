; GrepLens installer hooks.
; Before files are copied (or removed), stop the GrepLens back end and the AI engine that run from
; the install folder, so no file is locked ("Can't write ... ggml-*.dll"). Other programs are not touched.

!macro GREPLENS_STOP_PROCESSES
  nsExec::Exec `powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process -Name llama-server,greplens-backend -ErrorAction SilentlyContinue | Where-Object { $$_.Path -like '$INSTDIR\*' } | Stop-Process -Force"`
  Pop $0
  Sleep 1000
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro GREPLENS_STOP_PROCESSES
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro GREPLENS_STOP_PROCESSES
!macroend
