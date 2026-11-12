param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$taskWorkspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskWorkspace '.venv\Scripts\python.exe'
$taskPythonWindowless = Join-Path $taskWorkspace '.venv\Scripts\pythonw.exe'
if (Test-Path -LiteralPath $taskPythonWindowless -PathType Leaf) {
    $taskPython = $taskPythonWindowless
}
$taskScript = Join-Path $taskWorkspace 'scripts\maintain_workspace_storage.py'
$taskDigest = [Security.Cryptography.SHA256]::Create()
try {
    $taskSuffix = [BitConverter]::ToString($taskDigest.ComputeHash([Text.Encoding]::UTF8.GetBytes($taskWorkspace.ToLowerInvariant()))).Replace('-', '').Substring(0, 12)
} finally {
    $taskDigest.Dispose()
}
$taskName = "AdaOS-WorkspaceStorage-$taskSuffix"
if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    return
}
if (-not (Test-Path -LiteralPath $taskPython -PathType Leaf)) {
    throw "Repository Python environment missing: $taskPython"
}
$taskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$taskArguments = '"' + $taskScript + '" --workspace-root "' + $taskWorkspace + '" --apply'
$taskAction = New-ScheduledTaskAction -Execute $taskPython -Argument $taskArguments -WorkingDirectory $taskWorkspace
$taskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(10) -RepetitionInterval (New-TimeSpan -Hours 6)
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$taskPrincipal = New-ScheduledTaskPrincipal -UserId $taskUser -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $taskTrigger -Settings $taskSettings -Principal $taskPrincipal -Description 'Bounded cleanup of completed managed AdaOS scratch. Protects active tasks, unknown data, worktrees with changes, runtime and recovery.' -Force | Select-Object TaskName, State
