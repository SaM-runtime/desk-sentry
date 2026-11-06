[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'Programs\DeskSentry'),
    [switch]$Force,
    [switch]$DryRun,
    [switch]$NoStart,
    [switch]$SkipRegistry
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$projectRoot = [IO.Path]::GetFullPath((Split-Path $PSScriptRoot -Parent))
$logPath = Join-Path $env:LOCALAPPDATA 'DeskSentry\deploy.log'
$mutex = $null
$ownsMutex = $false
$exitCode = 0

function Write-Step([string]$Step, [string]$Message) {
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz'), $Step, $Message
    Write-Output $line
    # DryRun is read-only, including the log and its rotation.
    if (-not $DryRun) {
        try {
            [IO.Directory]::CreateDirectory((Split-Path $logPath -Parent)) | Out-Null
            if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 1MB) {
                Move-Item -LiteralPath $logPath -Destination "$logPath.1" -Force
            }
            Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
        }
        catch { Write-Warning "deploy.log: $($_.Exception.Message)" }
    }
}

function Get-Head {
    $hash = & git -C $projectRoot rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or "$hash" -notmatch '^[a-f0-9]{40,64}$') { throw 'Cannot resolve git HEAD' }
    return "$hash"
}

function Get-TrackedChanges {
    $changes = & git -C $projectRoot status --porcelain --untracked-files=no -- src/ packaging/ pyproject.toml models/ licenses/
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect tracked files' }
    return $changes
}

function Test-SamePath([string]$Left, [string]$Right) {
    return [string]::Equals($Left, $Right, [StringComparison]::OrdinalIgnoreCase)
}

function Test-Within([string]$Path, [string]$Parent) {
    return (Test-SamePath $Path $Parent) -or $Path.StartsWith($Parent.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
}

function Assert-PlainDirectory([string]$Path) {
    # Reject junctions in the target's ancestry before copying or recursively deleting.
    $cursor = $Path
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Expected a plain directory: $cursor"
            }
        }
        $cursor = Split-Path $cursor -Parent
    }
}

function Remove-InstallDirectory([string]$Path) {
    # Only these two absolute, validated targets can be removed, never a computed parent.
    if (-not ((Test-SamePath $Path $InstallDir) -or (Test-SamePath $Path $previousDir))) {
        throw "Refusing recursive removal: $Path"
    }
    Assert-PlainDirectory $Path
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force }
}

function Stop-InstalledProcesses {
    $allowedPaths = @($installedPythonw, $installedLegacyExe, $projectLegacyExe)
    $candidates = @(
        Get-CimInstance -ClassName Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'DeskSentry.exe'" |
            Where-Object {
                $_.ExecutablePath -and $allowedPaths -icontains $_.ExecutablePath -and
                ((Test-SamePath $_.ExecutablePath $installedLegacyExe) -or
                    (Test-SamePath $_.ExecutablePath $projectLegacyExe) -or
                    $_.CommandLine -match '(?i)(?:^|\s)-m\s+desk_sentry(?:\s|$)')
            }
    )
    $targets = @()
    foreach ($candidate in $candidates) {
        if (-not $candidate.ExecutablePath -or $allowedPaths -inotcontains $candidate.ExecutablePath) { continue }
        $process = Get-Process -Id $candidate.ProcessId -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        # Recheck the live process path; a PID can be reused after the CIM snapshot.
        if ($allowedPaths -inotcontains $process.Path) { continue }
        $targets += $process
    }
    if (-not $targets.Count) {
        Write-Step 'stop' 'complete (no matching executable paths)'
        return
    }
    $quitExe = if (Test-Path -LiteralPath $installedPythonw -PathType Leaf) {
        $installedPythonw
    }
    elseif (Test-Path -LiteralPath $installedLegacyExe -PathType Leaf) {
        $installedLegacyExe
    }
    else {
        $projectLegacyExe
    }
    if (Test-Path -LiteralPath $quitExe -PathType Leaf) {
        $quitArguments = if (Test-SamePath $quitExe $installedPythonw) { '-m desk_sentry --quit' } else { '--quit' }
        Write-Step 'stop' "notify graceful quit: $quitExe $quitArguments"
        Start-Process -FilePath $quitExe -ArgumentList $quitArguments -WorkingDirectory $InstallDir -WindowStyle Hidden
    }
    foreach ($target in $targets) {
        $process = Get-Process -Id $target.Id -ErrorAction SilentlyContinue
        if (-not $process -or $allowedPaths -inotcontains $process.Path) { continue }
        Write-Step 'stop' "wait up to 10s for PID $($process.Id): $($process.Path)"
        if (-not $process.WaitForExit(10000)) {
            $live = Get-Process -Id $target.Id -ErrorAction SilentlyContinue
            if ($live -and $allowedPaths -icontains $live.Path) {
                Write-Step 'stop' "timeout 10s; CloseMainWindow fallback PID $($live.Id): $($live.Path)"
                $null = $live.CloseMainWindow()
                if (-not $live.WaitForExit(1000)) {
                    $live = Get-Process -Id $target.Id -ErrorAction SilentlyContinue
                    if ($live -and $allowedPaths -icontains $live.Path) {
                        Write-Step 'stop' "force fallback PID $($live.Id): $($live.Path)"
                        Stop-Process -InputObject $live -Force
                        if (-not $live.WaitForExit(10000)) { throw "Process did not exit: $($live.Id)" }
                    }
                }
            }
        }
    }
    Write-Step 'stop' 'complete (exact executable paths only)'
}

Push-Location $projectRoot
try {
    $InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\', '/')
    if ($InstallDir -eq [IO.Path]::GetPathRoot($InstallDir).TrimEnd('\', '/')) {
        throw 'InstallDir must not be a drive or share root'
    }
    $previousDir = "$InstallDir.prev"
    $stagingDir = Join-Path $projectRoot '.tmp\deploy\dist\DeskSentry'
    $installedPythonw = Join-Path $InstallDir 'python\pythonw.exe'
    $installedLegacyExe = Join-Path $InstallDir 'DeskSentry.exe'
    $projectLegacyExe = Join-Path $projectRoot 'dist\DeskSentry\DeskSentry.exe'
    if ((Test-Within $projectRoot $InstallDir) -or (Test-Within $projectRoot $previousDir)) {
        throw 'InstallDir must not contain the project'
    }
    foreach ($protected in @('src', 'packaging', 'models', 'licenses', 'tools', 'tests', '.venv', '.git', '.agents', ('.' + 'codex'), ('.' + 'console'), ('.' + 'task'), 'dist', '.tmp\deploy\dist', '.tmp\deploy\metadata')) {
        $protectedPath = Join-Path $projectRoot $protected
        if ((Test-Within $InstallDir $protectedPath) -or (Test-Within $protectedPath $InstallDir) -or
            (Test-Within $previousDir $protectedPath) -or (Test-Within $protectedPath $previousDir)) {
            throw "InstallDir overlaps protected path: $protectedPath"
        }
    }
    Assert-PlainDirectory $InstallDir
    Assert-PlainDirectory $previousDir
    if (-not $DryRun) {
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $mutex = New-Object Threading.Mutex($false, "Local\DeskSentry.Deploy.$sid")
        try { $ownsMutex = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
        if (-not $ownsMutex) {
            Write-Step 'lock' 'another deploy is running; skip'
            exit 0
        }
    }
    $head = Get-Head
    $dirty = @(Get-TrackedChanges)
    if ($dirty.Count -and -not $Force) {
        Write-Step 'preflight' 'dirty，跳過 (tracked build inputs have uncommitted changes)'
    }
    else {
        Write-Step 'preflight' "HEAD=$head; Force=$Force; InstallDir=$InstallDir"
        foreach ($model in @('object_detection_nanodet_2022nov.onnx', 'face_detection_yunet_2026may.onnx', 'face_recognition_sface_2021dec.onnx')) {
            $modelPath = Join-Path $projectRoot "models\$model"
            if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf)) { throw "Missing required ONNX model: $modelPath" }
        }
        $versionPath = Join-Path $InstallDir 'VERSION.txt'
        $installedHash = ''
        if (Test-Path -LiteralPath $versionPath -PathType Leaf) {
            $versionLines = @(Get-Content -LiteralPath $versionPath -Encoding UTF8)
            if ($versionLines.Count -ge 2 -and $versionLines[1] -match '^[a-f0-9]{40,64}$') {
                $installedHash = $versionLines[1]
            }
            elseif ($versionLines.Count -ge 1 -and $versionLines[0] -match '^[a-f0-9]{40,64}$') {
                # TASK-08 legacy format: full hash was the first line.
                $installedHash = $versionLines[0]
            }
        }
        if (-not $Force -and $installedHash -eq $head -and (Test-Path -LiteralPath $installedPythonw -PathType Leaf)) {
            Write-Step 'version' 'same HEAD，跳過 (already installed)'
        }
        else {
            $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
            $metadataDir = Join-Path $projectRoot '.tmp\deploy\metadata'
            Write-Step 'build' "$python packaging\build_release.py bundle --output $stagingDir --metadata-dir $metadataDir"
            if (-not $DryRun) {
                if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Missing build Python: $python" }
                # bundle publishes by atomically renaming into a path that must not exist.
                Assert-PlainDirectory $stagingDir
                if (Test-Path -LiteralPath $stagingDir) {
                    Remove-Item -LiteralPath $stagingDir -Recurse -Force
                }
                # Native build output is retained in deploy.log; judge its exit code.
                $ErrorActionPreference = 'Continue'
                try {
                    & $python packaging\build_release.py bundle --output $stagingDir --metadata-dir $metadataDir 2>&1 |
                        ForEach-Object { Write-Step 'build-output' "$_" }
                    $buildExitCode = $LASTEXITCODE
                }
                finally { $ErrorActionPreference = 'Stop' }
                if ($buildExitCode -ne 0) { throw "Build failed, exit $buildExitCode; installed version untouched" }
                if (-not (Test-Path -LiteralPath (Join-Path $stagingDir 'python\pythonw.exe') -PathType Leaf)) { throw 'Bundle produced no python\pythonw.exe' }
                $buildInfoPath = Join-Path $stagingDir 'app\desk_sentry\_build_info.json'
                if (-not (Test-Path -LiteralPath $buildInfoPath -PathType Leaf)) { throw 'Build produced no _build_info.json' }
                try { $stagedBuildInfo = Get-Content -LiteralPath $buildInfoPath -Encoding UTF8 -Raw | ConvertFrom-Json }
                catch { throw "Invalid staged _build_info.json: $($_.Exception.Message)" }
                $buildDisplayVersion = "$($stagedBuildInfo.display_version)"
                if (-not $buildDisplayVersion) { throw 'Staged _build_info.json has no display_version' }
                if ((Get-Head) -ne $head -or (-not $Force -and @(Get-TrackedChanges).Count)) {
                    throw 'Build inputs changed during build; installed version untouched; rerun deploy'
                }
                Write-Step 'build' 'success; installed version still untouched'
            }
            Write-Step 'stop' 'run -m desk_sentry --quit, then CloseMainWindow/force fallback only for exact Desk Sentry paths'
            if (-not $DryRun) { Stop-InstalledProcesses }
            Write-Step 'swap' "$InstallDir -> $previousDir; copy $stagingDir; write VERSION.txt"
            if (-not $DryRun) {
                $movedPrevious = $false
                $copyStarted = $false
                try {
                    Assert-PlainDirectory $InstallDir
                    if (Test-Path -LiteralPath $InstallDir) {
                        Remove-InstallDirectory $previousDir
                        Move-Item -LiteralPath $InstallDir -Destination $previousDir
                        $movedPrevious = $true
                    }
                    $copyStarted = $true
                    [IO.Directory]::CreateDirectory((Split-Path $InstallDir -Parent)) | Out-Null
                    Copy-Item -LiteralPath $stagingDir -Destination $InstallDir -Recurse -Force
                    if ($movedPrevious) {
                        # Inno owns these files; every successful swap carries them
                        # forward before a later deployment can remove the older .prev.
                        $uninstallFiles = @(Get-ChildItem -LiteralPath $previousDir -File -Force |
                            Where-Object { $_.Name -like 'unins*.exe' -or $_.Name -like 'unins*.dat' -or $_.Name -like 'unins*.msg' })
                        foreach ($file in $uninstallFiles) {
                            Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $InstallDir $file.Name) -Force
                        }
                        Write-Step 'swap' "preserved $($uninstallFiles.Count) Inno uninstall files"
                    }
                    Set-Content -LiteralPath $versionPath -Encoding UTF8 -Value @($buildDisplayVersion, $head, (Get-Date -Format 'o'))
                    Write-Step 'swap' 'success; VERSION.txt written; .prev retained'
                }
                catch {
                    if ($copyStarted) { Remove-InstallDirectory $InstallDir }
                    if ($movedPrevious) { Move-Item -LiteralPath $previousDir -Destination $InstallDir }
                    Write-Step 'rollback' 'swap failed; previous installation restored (if present)'
                    throw
                }
            }
            $command = '"{0}" -m desk_sentry --minimized' -f $installedPythonw
            if ($SkipRegistry) { Write-Step 'registry' 'skip (-SkipRegistry)' }
            else {
                Write-Step 'registry' "HKCU Run DeskSentry = $command"
                if (-not $DryRun) {
                    $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
                    New-Item -Path $runKey -Force | Out-Null
                    New-ItemProperty -LiteralPath $runKey -Name DeskSentry -Value $command -PropertyType String -Force | Out-Null
                }
            }
            if ($NoStart) { Write-Step 'start' 'skip (-NoStart)' }
            else {
                Write-Step 'start' $command
                if (-not $DryRun) { Start-Process -FilePath $installedPythonw -ArgumentList '-m desk_sentry --minimized' -WorkingDirectory $InstallDir -WindowStyle Hidden }
            }
            if ($DryRun) { Write-Step 'complete' "plan only for $head (no changes)" }
            else { Write-Step 'complete' "deployed $head" }
        }
    }
}
catch {
    Write-Step 'error' $_.Exception.Message
    $exitCode = 1
}
finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    if ($mutex) { $mutex.Dispose() }
    Pop-Location
}
exit $exitCode
