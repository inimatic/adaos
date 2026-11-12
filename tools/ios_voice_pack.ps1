param(
    [string]$Catalog = (Join-Path $PSScriptRoot '../jailbreak/smoke/Resources/onboarding-prompts.json'),
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '../jailbreak/smoke/Resources/Voice')
)
$ErrorActionPreference = 'Stop'
$catalogPath = (Resolve-Path -LiteralPath $Catalog).Path
$pack = Get-Content -LiteralPath $catalogPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($pack.schema -ne 'adaos.ios.voice-pack.v1') { throw 'Unsupported prompt catalog' }
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
[System.IO.Directory]::CreateDirectory($outputPath) | Out-Null
$voice = New-Object -ComObject SAPI.SpVoice
$voices = @($voice.GetVoices())
$canonical = [System.Text.Encoding]::UTF8.GetBytes(([System.IO.File]::ReadAllText($catalogPath) -replace "`r`n", "`n"))
$catalogDigest = [System.Security.Cryptography.SHA256]::Create().ComputeHash($canonical)
$catalogHash = ([System.BitConverter]::ToString($catalogDigest)).Replace('-', '').ToLowerInvariant()
$receipt = [ordered]@{ schema = 'adaos.ios.voice-assets.v1'; catalog_sha256 = $catalogHash; files = [ordered]@{} }
foreach ($locale in @('ru', 'en')) {
    $language = if ($locale -eq 'ru') { '419' } else { '409' }
    $selected = @($voices | Where-Object { $_.GetAttribute('Language') -eq $language })
    if ($selected.Count -eq 0) { throw "Missing local synthesis voice for $locale" }
    $voice.Voice = $selected[0]
    $voice.Rate = -1
    foreach ($prompt in $pack.locales.$locale.PSObject.Properties) {
        $name = $prompt.Value.audio
        if ($name -cnotmatch '^(ru|en)_[a-z_]+\.wav$') { throw 'Unsafe asset name' }
        $destination = Join-Path $outputPath $name
        $stream = New-Object -ComObject SAPI.SpFileStream
        $format = New-Object -ComObject SAPI.SpAudioFormat
        $format.Type = 18 # SAFT16kHz16BitMono; format is also checked before packaging.
        $stream.Format = $format
        try {
            $stream.Open($destination, 3, $false)
            $voice.AudioOutputStream = $stream
            $voice.Speak($prompt.Value.text, 0) | Out-Null
        } finally { $stream.Close() }
        $receipt.files[$name] = [ordered]@{ sha256 = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant(); voice = $selected[0].GetDescription() }
    }
}
$json = $receipt | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText((Join-Path $outputPath 'assets.json'), $json + [Environment]::NewLine, (New-Object System.Text.UTF8Encoding($false)))
Write-Output "Prepared $($receipt.files.Count) local prompt recordings."
