# In-app update check on a clean Windows machine:
# install the last published release, start it, then run the same update helper the app uses
# with the installer just built, and confirm the new version is installed and GrepLens reopened.
$ErrorActionPreference = "Continue"
$new = (Get-ChildItem src-tauri/target/release/bundle/nsis/*-setup.exe | Select-Object -First 1).FullName
New-Item -ItemType Directory -Force prev | Out-Null
gh release download --repo $env:GITHUB_REPOSITORY --pattern "*-setup.exe" --dir prev --clobber
$old = (Get-ChildItem prev/*-setup.exe | Select-Object -First 1).FullName
"Previous release installer: $old"
Start-Process $old -ArgumentList "/S" -Wait
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\GrepLens"
$before = (Get-ItemProperty $key -ErrorAction SilentlyContinue).DisplayVersion
$uninstall = (Get-ItemProperty $key).UninstallString.Trim('"')
$dir = Split-Path $uninstall -Parent
"Installed $before in $dir"
Start-Process "$dir\GrepLens.exe"
Start-Sleep 25
"Running before the update:"; Get-Process | Where-Object { $_.Path -like "$dir\*" } | Select-Object ProcessName, Id | Format-Table | Out-String
python backend/tests/update_check.py "$dir\greplens-backend.exe" "$new"
for ($i = 0; $i -lt 36; $i++) {
  Start-Sleep 5
  $after = (Get-ItemProperty $key -ErrorAction SilentlyContinue).DisplayVersion
  if ($after -ne $before -and (Get-Process | Where-Object { $_.Path -eq "$dir\GrepLens.exe" })) { break }
}
"Version before: $before  after: $after"
"Running after the update:"; Get-Process | Where-Object { $_.Path -like "$dir\*" } | Select-Object ProcessName, Id | Format-Table | Out-String
Get-ChildItem "$env:LOCALAPPDATA\GrepLens\logs" -ErrorAction SilentlyContinue | ForEach-Object { "--- $($_.Name)"; Get-Content $_.FullName -Tail 20 }
"Installer processes still running:"; Get-Process | Where-Object { $_.ProcessName -like "*setup*" } | Select-Object ProcessName, Id, Path | Format-Table | Out-String
if ($after -eq $before) {
  "Trying the installer directly in update mode:"
  $p = Start-Process $new -ArgumentList "/UPDATE", "/P", "/R" -PassThru
  if (-not $p.WaitForExit(180000)) { "installer still waiting after 3 minutes (a dialog?)"; $p.Kill() }
  else { "exit code $($p.ExitCode); version now $((Get-ItemProperty $key).DisplayVersion)" }
  throw "The update did not install."
}
"UPDATE CHECK PASSED"
