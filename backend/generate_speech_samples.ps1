$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech

$projectRoot = Split-Path -Parent $PSScriptRoot
$audioDirectory = Join-Path $projectRoot 'demo_audio'
New-Item -ItemType Directory -Force -Path $audioDirectory | Out-Null

# Every output from this script is Windows text-to-speech. These files exercise
# the pipeline and demo scenarios; they are not genuine or cloned human voices.
$samples = @(
    @{
        Filename = 'tts-customer-inquiry.wav'
        Rate = -1
        Text = @"
Hello, good afternoon. I am calling regarding my recurring deposit account with your branch.
I noticed that the quarterly interest credit was scheduled for yesterday, but I have not received the SMS confirmation yet.
Could you please confirm whether my registered contact details are current?
Thank you for your assistance.
"@
    },
    @{
        Filename = 'tts-cfo-fraud-scenario.wav'
        Rate = 1
        Text = @"
Listen carefully. This is the group finance officer.
I am in an emergency board meeting and an acquisition escrow closes in thirty minutes.
I need you to authorize an immediate priority transfer to the vendor clearing account.
Do not delay with the standard callback procedure. Process the transaction right away.
"@
    },
    @{
        Filename = 'tts-bank-scam-scenario.wav'
        Rate = 2
        Text = @"
Attention customer. This is an automated security advisory.
A suspicious debit was initiated on your credit card.
To block the transaction, press one immediately or provide your one-time password.
Failure to respond will result in card deactivation.
"@
    }
)

$voiceProbe = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $availableVoices = @(
        $voiceProbe.GetInstalledVoices() |
            Where-Object { $_.Enabled } |
            ForEach-Object { $_.VoiceInfo.Name }
    )
}
finally {
    $voiceProbe.Dispose()
}
if (-not $availableVoices) {
    throw 'No enabled Windows speech-synthesis voice is installed.'
}

foreach ($sample in $samples) {
    $synthesizer = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $synthesizer.SelectVoice($availableVoices[0])
        $synthesizer.Rate = $sample.Rate
        $outputPath = Join-Path $audioDirectory $sample.Filename
        $synthesizer.SetOutputToWaveFile($outputPath)
        $synthesizer.Speak($sample.Text)
        Write-Host "Generated $($sample.Filename) (synthetic TTS test sample)"
    }
    finally {
        $synthesizer.Dispose()
    }
}
