<p align="center"><img src="logo.png" width="110"></p>

<h1 align="center">Auto Captions</h1>
<p align="center"><b>Animated Text+ captions for DaVinci Resolve Studio, free and offline.</b><br>
Made by <b>Amr</b> · v1.7 | <a href="https://instagram.com/amrswork">Instagram</a> · <a href="https://discord.gg/CFcSEg6nCq">Discord</a></p>

---

## What it does
- Transcribes your timeline audio **on your own PC** with Whisper (no uploads, no limits, no account)
- Or imports an **SRT** file
- Places every caption as its own **Text+** on a new track, timed to your voice
- Uses **your own animations**: any Text+ in a bin called `Auto Captions`
- Animate in / out, font, style, colour, size, position, letter spacing, and presets that save every setting
- English and Arabic

## Install (Windows)
1. Download **AutoCaptions-Setup.exe** from [Releases](../../releases)
2. Run it. If Windows says "unknown publisher", click **More info > Run anyway**
3. Restart DaVinci Resolve
4. Open **Workspace > Scripts > Auto Captions**
5. First time only: click **Download Whisper** (1.6 GB)

Requires **DaVinci Resolve Studio** (scripts with windows don't run in the free version).

## Is it safe?
- All the code is here: `Auto Captions.py` is plain text you can read
- The installer is built from `installer.nsi` in this repo
- VirusTotal scan: (https://www.virustotal.com/gui/file/0fa1da1940d395b15853e70da5cf84adcd9fba000c4908173388454cf4bf87c2?nocache=1)

## Credits
- Made by **Amr**
- Speech recognition: [Whisper](https://github.com/openai/whisper) by OpenAI (MIT) via [whisper.cpp](https://github.com/ggml-org/whisper.cpp) (MIT)

MIT License
