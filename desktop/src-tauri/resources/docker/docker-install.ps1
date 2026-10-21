param(
    [string]$InstallerUrl = 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe',
    [string]$DownloadPath = '',
    [int]$MaxAttempts = 3
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($DownloadPath)) {
    $DownloadPath = Join-Path $env:TEMP 'AIPDD-DockerDesktop-Installer.exe'
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Write-Step {
    param([string]$Message)
    Write-Host ('[docker] ' + $Message)
}

function Get-RemoteLength {
    param([string]$Url)
    try {
        $request = [Net.HttpWebRequest]::Create($Url)
        $request.Method = 'HEAD'
        $request.UserAgent = 'AIPDD-DramaClaw-Installer'
        $request.Timeout = 30000
        $response = $request.GetResponse()
        try {
            return [int64]$response.ContentLength
        } finally {
            $response.Close()
        }
    } catch {
        Write-Step ('HEAD request failed: ' + $_.Exception.Message)
        return [int64]0
    }
}

$total = Get-RemoteLength -Url $InstallerUrl
if ($total -gt 0) {
    Write-Step ('Download size: {0:N0} MB' -f ($total / 1MB))
}

$attempt = 0
$downloaded = $false
while (-not $downloaded) {
    $attempt++
    if ($attempt -gt $MaxAttempts) {
        throw ('Download failed after ' + $MaxAttempts + ' attempts')
    }

    $existing = [int64]0
    if (Test-Path -LiteralPath $DownloadPath) {
        $existing = (Get-Item -LiteralPath $DownloadPath).Length
    }
    if ($total -gt 0 -and $existing -eq $total) {
        Write-Step 'Partial file is already complete; reusing it.'
        $downloaded = $true
        break
    }
    if ($total -gt 0 -and $existing -gt $total) {
        Remove-Item -LiteralPath $DownloadPath -Force
        $existing = [int64]0
    }

    try {
        $request = [Net.HttpWebRequest]::Create($InstallerUrl)
        $request.UserAgent = 'AIPDD-DramaClaw-Installer'
        $request.Timeout = 60000
        $request.ReadWriteTimeout = 300000
        if ($existing -gt 0) {
            $request.AddRange($existing)
        }
        $response = $request.GetResponse()
        try {
            $responseLength = [int64]$response.ContentLength
            $resume = ($existing -gt 0) -and ([int]$response.StatusCode -eq 206)
            if (-not $resume) {
                $existing = [int64]0
            }
            $resumedFrom = $existing
            if ($responseLength -gt 0) {
                $totalBytes = $resumedFrom + $responseLength
            } else {
                $totalBytes = $total
            }
            if ($resume) {
                Write-Step ('Resuming at {0:N0} MB' -f ($resumedFrom / 1MB))
            } else {
                Write-Step 'Starting download from the beginning.'
            }

            if ($resume) {
                $mode = [IO.FileMode]::Append
            } else {
                $mode = [IO.FileMode]::Create
            }
            $file = [IO.File]::Open($DownloadPath, $mode, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $stream = $response.GetResponseStream()
                $buffer = New-Object byte[] 1048576
                $lastReport = Get-Date
                $received = $resumedFrom
                while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $file.Write($buffer, 0, $read)
                    $received += $read
                    $now = Get-Date
                    if (($now - $lastReport).TotalSeconds -ge 5) {
                        if ($totalBytes -gt 0) {
                            $percent = [int](($received * 100) / $totalBytes)
                            Write-Step ('Downloaded {0:N0} / {1:N0} MB ({2}%)' -f ($received / 1MB), ($totalBytes / 1MB), $percent)
                        } else {
                            Write-Step ('Downloaded {0:N0} MB' -f ($received / 1MB))
                        }
                        $lastReport = $now
                    }
                }
            } finally {
                $file.Close()
                $response.Close()
            }

            $final = (Get-Item -LiteralPath $DownloadPath).Length
            if ($total -gt 0 -and $final -lt $total) {
                throw ('Incomplete download: {0} of {1} bytes' -f $final, $total)
            }
            Write-Step ('Download complete: {0:N0} MB' -f ($final / 1MB))
            $downloaded = $true
        } catch {
            if ($response) {
                $response.Close()
            }
            throw
        }
    } catch {
        Write-Step ('Download attempt {0} failed: {1}' -f $attempt, $_.Exception.Message)
        if ($attempt -ge $MaxAttempts) {
            throw
        }
        Start-Sleep -Seconds (5 * $attempt)
    }
}

Write-Step 'Verifying Authenticode signature...'
$signature = Get-AuthenticodeSignature -LiteralPath $DownloadPath
if ($signature.Status -ne 'Valid') {
    throw ('Signature status is {0}; refusing to run the installer.' -f $signature.Status)
}
$signer = $signature.SignerCertificate
if ($null -eq $signer -or $signer.Subject -notmatch 'Docker Inc') {
    $subject = '<none>'
    if ($null -ne $signer) {
        $subject = $signer.Subject
    }
    throw ('Unexpected signer: ' + $subject)
}
Write-Step ('Signature OK: ' + $signer.Subject)

$versionInfo = (Get-Item -LiteralPath $DownloadPath).VersionInfo
$fileVersion = $versionInfo.FileVersion
if ($fileVersion -match '^\s*(\d+)\.') {
    $major = [int]$Matches[1]
    if ($major -lt 4) {
        throw ('Unexpected Docker Desktop version: ' + $fileVersion)
    }
    Write-Step ('Docker Desktop installer version: ' + $fileVersion)
} else {
    Write-Step ("WARNING: unable to parse installer version '" + $fileVersion + "'; continuing with a valid signature.")
}

$installArgs = @('install', '--quiet', '--accept-license')
Write-Step 'Starting Docker Desktop installer (administrator approval required)...'
try {
    $process = Start-Process -FilePath $DownloadPath -ArgumentList $installArgs -Verb RunAs -Wait -PassThru
    $exitCode = $process.ExitCode
} catch {
    $exception = $_.Exception
    if (($exception -is [System.ComponentModel.Win32Exception] -and $exception.NativeErrorCode -eq 1223) -or ($exception.Message -match 'cancel')) {
        Write-Step 'Elevation was cancelled by the user.'
        exit 1223
    }
    throw
}

Write-Step ('Docker Desktop installer exit code: ' + $exitCode)
if ($exitCode -eq 0 -or $exitCode -eq 3010 -or $exitCode -eq 1641) {
    Remove-Item -LiteralPath $DownloadPath -Force -ErrorAction SilentlyContinue
}
exit $exitCode
