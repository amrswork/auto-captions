# Auto Captions (made by Amr) - instagram.com/amrswork
# Transcribes timeline audio (or reads an SRT) and places Text+ captions.
# Powered by Whisper (OpenAI, MIT) via whisper.cpp (MIT).
import glob
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile

VERSION = "1.7"
APP = os.path.join(os.environ.get("LOCALAPPDATA", ""), "AmrCaptions")
MODEL = os.path.join(APP, "ggml-large-v3-turbo.bin")
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
GPU_RELEASE = "https://api.github.com/repos/jerryshell/whisper.cpp-windows-vulkan-bin/releases/latest"  # Vulkan: AMD/NVIDIA/Intel
CPU_ZIP = "https://github.com/ggml-org/whisper.cpp/releases/latest/download/whisper-bin-x64.zip"  # official fallback
LOGO = os.path.join(APP, "logo.png")
DRB = os.path.join(APP, "Auto Captions.drb")  # the bundled animations, imported into each project once
BIN_NAME = "Auto Captions"
OLD_BIN_NAMES = ("Amr Captions",)  # projects made with the first versions
# plain Text+ defaults in Resolve 21: the templates keep only their animation
DEFAULT_LOOK = {"Font": "Open Sans", "Style": "Semibold", "Size": 0.09, "CharacterSpacing": 1.0}
INSTAGRAM = "https://instagram.com/amrswork"
DISCORD = "https://discord.gg/CFcSEg6nCq"
LANGS = {"English": "en", "Arabic": "ar"}
OUT_STYLES = ["Reverse", "Fade", "None"]
UPLOAD_ICON = os.path.join(APP, "upload.png")
INSTA_ICON = os.path.join(APP, "instagram.png")
DISCORD_ICON = os.path.join(APP, "discord.png")
PRESETS = os.path.join(APP, "presets.json")  # kept outside Resolve, so presets work in every project


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# ---------- pure helpers (tested in test_amr_captions.py) ----------

def parse_srt(text):
    """Return [(start_sec, end_sec, text)]."""
    def sec(h, m, s, ms):
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0
    out = []
    for block in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"\s*(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", line)
            if m:
                words = " ".join(l.strip() for l in lines[i + 1:]).strip()
                if words:
                    out.append((sec(*m.groups()[:4]), sec(*m.groups()[4:]), words))
                break
    return out


def words_from_json(data):
    """whisper.cpp full JSON (-ojf with --dtw) -> [(start_sec, end_sec, word)].
    Word text comes from the segment text (always valid UTF-8, Arabic too);
    each word's start comes from the DTW time of its first token."""
    words = []
    for seg in data["transcription"]:
        texts = seg["text"].split()
        starts = []
        for tok in seg.get("tokens", []):
            t = tok.get("text", "")
            if t.startswith("[_") or t.startswith("<|") or not t.strip():
                continue
            if t.startswith(" ") or not starts:
                dtw = tok.get("t_dtw", -1)
                starts.append(dtw / 100.0 if dtw is not None and dtw >= 0 else tok["offsets"]["from"] / 1000.0)
        seg_end = seg["offsets"]["to"] / 1000.0
        if len(starts) != len(texts):  # tokens didn't line up with words: spread the segment evenly
            a = seg["offsets"]["from"] / 1000.0
            starts = [a + (seg_end - a) * i / max(len(texts), 1) for i in range(len(texts))]
        for i, w in enumerate(texts):
            words.append((starts[i], starts[i + 1] if i + 1 < len(texts) else seg_end, w))
    return words


def group_words(words, max_words, gap=0.6):
    """Join word-level entries into captions: max N words, break on pauses and sentence ends."""
    caps, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w[0] - cur[-1][1] > gap):
            caps.append(cur)
            cur = []
        cur.append(w)
        if w[2][-1:] in ".?!؟":  # ؟ = Arabic question mark
            caps.append(cur)
            cur = []
    if cur:
        caps.append(cur)
    return [(c[0][0], c[-1][1], " ".join(x[2] for x in c)) for c in caps]


def to_frames(caps, fps, start_frame):
    """[(record_frame, duration)]: each caption lasts until the next one starts (no gaps)."""
    out = []
    for i, (s, e, _) in enumerate(caps):
        a = int(round(s * fps))
        b = int(round(caps[i + 1][0] * fps)) if i + 1 < len(caps) else int(round(e * fps))
        out.append((start_frame + a, max(1, b - a)))
    return out


def build_keys(orig, in_f, out_f, end, out_style, is_opacity, use_in=True):
    """Rebuild one spline: the template's in-curve scaled to in_f frames (or held at its
    final value when use_in is off), plus an out animation that finishes at `end`."""
    L0 = max(orig) or 1.0
    if not use_in:
        in_f = 0
    if in_f + out_f > end:  # clip too short: shrink both to fit
        k = end / float(in_f + out_f)
        in_f, out_f = in_f * k, out_f * k
    si, so = in_f / L0, out_f / L0

    def handle(h, f):
        return {1: h[1] * f, 2: h[2]}

    keys = {}
    if not use_in:
        keys[0.0] = {1: orig[L0][1]}
    else:
        for t, k in orig.items():
            nk = {1: k[1]}
            if "LH" in k:
                nk["LH"] = handle(k["LH"], si)
            if "RH" in k:
                nk["RH"] = handle(k["RH"], si)
            keys[t * si] = nk
    if out_style == "Reverse":
        for t, k in orig.items():
            nk = {1: k[1]}  # mirrored: handles swap sides
            if "RH" in k:
                nk["LH"] = {1: end - k["RH"][1] * so, 2: k["RH"][2]}
            if "LH" in k:
                nk["RH"] = {1: end - k["LH"][1] * so, 2: k["LH"][2]}
            keys[end - t * so] = nk
    elif out_style == "Fade" and is_opacity:
        last = orig[L0][1]
        keys[end - out_f] = {1: last}
        keys[end] = {1: 0.0}
    return keys


# ---------- one-time Whisper download ----------

def whisper_exe():
    hits = glob.glob(os.path.join(APP, "whisper", "**", "whisper-cli.exe"), recursive=True)
    return hits[0] if hits else None


def model_ok():
    return os.path.exists(MODEL) and os.path.getsize(MODEL) > 1500 * 1024 * 1024


def fetch(url, dest, progress, label):
    req = urllib.request.Request(url, headers={"User-Agent": "AutoCaptions/" + VERSION})
    with urllib.request.urlopen(req) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            progress["text"] = "Downloading %s... %d / %d MB" % (label, done >> 20, total >> 20)


def setup_whisper(progress):
    os.makedirs(APP, exist_ok=True)
    if not whisper_exe():
        try:
            req = urllib.request.Request(GPU_RELEASE, headers={"User-Agent": "AutoCaptions/" + VERSION})
            assets = json.load(urllib.request.urlopen(req))["assets"]
            url = next(a["browser_download_url"] for a in assets if a["name"].endswith(".zip"))
        except Exception:
            url = CPU_ZIP
        z = os.path.join(APP, "whisper.zip")
        fetch(url, z, progress, "Whisper")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(os.path.join(APP, "whisper"))
        os.remove(z)
    if not model_ok():
        fetch(MODEL_URL, MODEL + ".part", progress, "AI model")
        os.replace(MODEL + ".part", MODEL)


# ---------- Resolve side ----------

def main():
    try:
        resolve  # injected when run from Workspace > Scripts
    except NameError:
        import DaVinciResolveScript as dvr
        globals()["resolve"] = dvr.scriptapp("Resolve")
    try:
        disp_mod = bmd
    except NameError:
        import DaVinciResolveScript as disp_mod
    fu = resolve.Fusion()
    ui = fu.UIManager
    disp = disp_mod.UIDispatcher(ui)

    def project():
        return resolve.GetProjectManager().GetCurrentProject()

    def find_bin(folder):
        if folder.GetName() in (BIN_NAME,) + OLD_BIN_NAMES:
            return folder
        for sub in folder.GetSubFolderList() or []:
            hit = find_bin(sub)
            if hit:
                return hit
        return None

    def templates():
        mp = project().GetMediaPool()
        b = find_bin(mp.GetRootFolder())
        if not b and os.path.exists(DRB):
            mp.SetCurrentFolder(mp.GetRootFolder())
            mp.ImportFolderFromFile(DRB)
            b = find_bin(mp.GetRootFolder())
        out = {}
        for c in (b.GetClipList() if b else []):
            name = c.GetName()
            out["Normal" if name.lower() == "fusion title" else name[:1].upper() + name[1:]] = c
        return out

    def render_audio(tmp):
        p = project()
        p.LoadRenderPreset("Audio Only")
        p.SetRenderSettings({"SelectAllFrames": True, "TargetDir": tmp, "CustomName": "amr_audio",
                             "ExportVideo": False, "ExportAudio": True})
        job = p.AddRenderJob()
        p.StartRendering([job], False)
        while p.IsRenderingInProgress():
            time.sleep(0.5)
        p.DeleteRenderJob(job)
        files = glob.glob(os.path.join(tmp, "amr_audio*"))
        if not files:
            raise RuntimeError("Audio export failed.")
        return files[0]

    def transcribe(lang, max_words):
        exe = whisper_exe()
        if not exe or not model_ok():
            raise RuntimeError("Click 'Download Whisper' first (one time only).")
        tmp = tempfile.mkdtemp(prefix="amrcap_")
        wav = render_audio(tmp)
        base = os.path.join(tmp, "words")
        common = [exe, "-m", MODEL, "-f", wav, "-l", lang, "-of", base]
        # exact word timing (DTW) first; -nfa because DTW needs flash attention off on newer builds
        for extra in (["--dtw", "large.v3.turbo", "-nfa", "-ojf"], ["--dtw", "large.v3.turbo", "-ojf"]):
            try:
                subprocess.run(common + extra, check=True, creationflags=0x08000000)  # no console window
                with open(base + ".json", "rb") as f:
                    words = words_from_json(json.loads(f.read().decode("utf-8", "replace")))
                if words:
                    return group_words(words, max_words)
            except (subprocess.CalledProcessError, OSError, ValueError, KeyError):
                pass
        # shortcut: very old whisper builds without DTW get rough word timing; reinstall to upgrade
        subprocess.run(common + ["-ml", "1", "-sow", "-osrt"], check=True, creationflags=0x08000000)
        with open(base + ".srt", encoding="utf-8") as f:
            words = [w for w in parse_srt(f.read()) if w[2]]
        return group_words(words, max_words)

    def text_tools(comp):
        return (list((comp.GetToolList(False, "StyledTextFollower") or {}).values()),
                list((comp.GetToolList(False, "TextPlus") or {}).values()))

    def set_text(comp, text):
        followers, plus = text_tools(comp)
        for f in followers:
            f.SetInput("Text", text)
        if not followers:
            for t in plus:
                t.SetInput("StyledText", text)

    def apply_style(item, comp, s):
        """Font, style, colour and letter spacing live on the Text+; size and position on Resolve's Transform."""
        followers, plus = text_tools(comp)
        r, g, b = hex_to_rgb(s["color"])
        for t in plus:
            t.SetInput("Font", s["font"])
            if s["style"]:
                t.SetInput("Style", s["style"])
            t.SetInput("CharacterSpacing", s["spacing"])
            t.SetInput("Red1", r)
            t.SetInput("Green1", g)
            t.SetInput("Blue1", b)
            # Pan/Tilt don't move titles, so move the text: pixels -> Text+ Center (same numbers as the Inspector)
            t.SetInput("Center", {1.0: 0.5 + s["x"] / s["w"], 2.0: 0.5 + s["y"] / s["h"], 3.0: 0.0})  # Fusion points need float keys
        if s["delay"] >= 0:
            for f in followers:
                f.SetInput("Delay", s["delay"])
        for k, v in (("ZoomX", s["zoom"]), ("ZoomY", s["zoom"])):
            item.SetProperty(k, v)

    def apply_timing(comp, length, s):
        followers, _ = text_tools(comp)
        stagger = 0.0
        for f in followers:
            n = len(f.GetInput("Text") or "")
            d = f.GetInput("Delay") or 0.0
            # shortcut: DelayType 0 = per character, others treated as a total; adjust if out timing looks off
            stagger = max(stagger, d * max(n - 1, 0) if not f.GetInput("DelayType") else d)
        end = max(1.0, length - 1 - stagger)
        saved = comp.GetData("amr_in") or {}
        for sp in (comp.GetToolList(False, "BezierSpline") or {}).values():
            name = sp.Name
            if name not in saved:  # first time: remember the template's own in-curve
                saved[name] = sp.GetKeyFrames()
            orig = {float(t): k for t, k in saved[name].items()}
            if len(orig) < 2:
                continue
            out_style = s["out_style"] if s["out_on"] else "None"
            sp.SetKeyFrames(build_keys(orig, s["in"], s["out"], end, out_style, "Opacity" in name, s["in_on"]), True)
        comp.SetData("amr_in", saved)

    def text_comp(item, fresh):
        """The comp holding the Text+. On a fresh caption, drop any other comps (e.g. third-party effects)."""
        names = item.GetFusionCompNameList() or []
        comps = [(n, item.GetFusionCompByIndex(i)) for i, n in enumerate(names, 1)]  # read all before deleting
        found = next((c for n, c in comps if c and c.GetToolList(False, "TextPlus")), None)
        if fresh and found:
            for n, c in comps:
                if c is not found:
                    item.DeleteFusionCompByName(n)
        return found

    def font_list():
        try:
            fonts = {fam: sorted(st) for fam, st in (fu.FontManager.GetFontList() or {}).items()}  # {family: {style: path}}
        except Exception:
            fonts = {}
        fonts.setdefault(DEFAULT_LOOK["Font"], [DEFAULT_LOOK["Style"]])  # Resolve's own default font
        return fonts

    def tk_root():
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        return root


    # ---------- window ----------
    GREY = "color:#9a9aa3;"
    PURPLE = "QPushButton{background:#7c5cff;color:white;border:none;border-radius:8px;font-size:14px;font-weight:bold}QPushButton:hover{background:#8d72ff}"
    PURPLE_OUT = "QPushButton{background:transparent;color:#cbbcff;border:1px solid #7c5cff;border-radius:8px;font-size:13px}QPushButton:hover{background:#2d2340}"

    def lab(text, w=0):
        return ui.Label({"Text": text, "StyleSheet": GREY, "Weight": 0, "MinimumSize": [w, 0]})

    win = disp.AddWindow({"ID": "AmrWin", "WindowTitle": "Auto Captions (made by Amr)  v" + VERSION, "Geometry": [300, 120, 400, 690]}, ui.VGroup({"Spacing": 7}, [
        ui.HGroup({"Weight": 0}, [
            ui.Button({"ID": "Logo", "Flat": True, "Icon": ui.Icon({"File": LOGO}), "IconSize": [48, 48],
                       "MinimumSize": [52, 52], "MaximumSize": [52, 52]}),
            ui.VGroup([ui.Label({"Text": "<span style='font-size:17px'><b>Auto Captions</b></span>"}),
                       ui.Label({"Text": "Made by <b>Amr</b>  \u00b7  v" + VERSION, "StyleSheet": GREY})]),
        ]),
        ui.HGroup({"Weight": 0}, [
            ui.Button({"ID": "Insta", "Text": " Instagram", "Icon": ui.Icon({"File": INSTA_ICON}), "IconSize": [16, 16]}),
            ui.Button({"ID": "Discord", "Text": " Discord", "Icon": ui.Icon({"File": DISCORD_ICON}), "IconSize": [16, 16]}),
        ]),
        ui.Label({"Text": "<b>1. Captions from</b>", "Weight": 0}),
        ui.Button({"ID": "Upload", "Text": "  Add SRT file (optional)", "Weight": 0, "MinimumSize": [0, 54], "MaximumSize": [16777215, 54],
                   "Icon": ui.Icon({"File": UPLOAD_ICON}), "IconSize": [26, 26],
                   "StyleSheet": "QPushButton{border:1px dashed #6b5ab8;border-radius:8px;background:#2a2733}QPushButton:hover{background:#322d40}"}),
        ui.Label({"Text": "No file = we listen to your timeline audio", "StyleSheet": "color:#9a9aa3;font-size:11px;",
                  "Alignment": {"AlignHCenter": True}, "Weight": 0}),
        ui.HGroup({"Weight": 0}, [lab("Language", 70), ui.ComboBox({"ID": "Lang"}), lab("Max words"), ui.SpinBox({"ID": "MaxWords", "Minimum": 1, "Maximum": 12, "Value": 3})]),
        ui.Label({"Text": "<b>2. Animation</b>", "Weight": 0}),
        ui.HGroup({"Weight": 0}, [lab("Animation", 70), ui.ComboBox({"ID": "Anim"})]),
        ui.HGroup({"Weight": 0}, [ui.CheckBox({"ID": "InOn", "Text": "Animate in", "Checked": True}),
                                  ui.CheckBox({"ID": "OutOn", "Text": "Animate out", "Checked": True})]),
        ui.Label({"Text": "<b>3. Look</b>", "Weight": 0}),
        ui.HGroup({"Weight": 0}, [lab("Preset", 70), ui.ComboBox({"ID": "Preset"}),
                                  ui.Button({"ID": "SavePreset", "Text": "Save", "Weight": 0, "MinimumSize": [52, 0]}),
                                  ui.Button({"ID": "DelPreset", "Text": "Delete", "Weight": 0, "MinimumSize": [56, 0]})]),
        ui.Label({"Text": "Presets save all your settings (everything except the SRT file)",
                  "StyleSheet": "color:#7d7d86;font-size:10px;", "Weight": 0}),
        ui.HGroup({"Weight": 0}, [lab("Font", 70), ui.ComboBox({"ID": "Font"})]),
        ui.HGroup({"Weight": 0}, [lab("Style", 70), ui.ComboBox({"ID": "Style"})]),
        ui.HGroup({"Weight": 0}, [lab("Colour", 70), ui.Button({"ID": "Color", "Text": "#FFFFFF",
                                  "StyleSheet": "QPushButton{background:#FFFFFF;color:#000;border-radius:6px}"})]),
        ui.HGroup({"Weight": 0}, [lab("Size", 70), ui.DoubleSpinBox({"ID": "Zoom", "Minimum": 0.01, "Maximum": 20, "Value": 1, "Decimals": 3, "SingleStep": 0.05}),
                                  lab("Spacing"), ui.DoubleSpinBox({"ID": "Spacing", "Minimum": 0, "Maximum": 5, "Value": 1, "Decimals": 3, "SingleStep": 0.01})]),
        ui.HGroup({"Weight": 0}, [lab("Position X", 70), ui.DoubleSpinBox({"ID": "PosX", "Minimum": -10000, "Maximum": 10000, "Value": 0, "Decimals": 1}),
                                  lab("Y"), ui.DoubleSpinBox({"ID": "PosY", "Minimum": -10000, "Maximum": 10000, "Value": 0, "Decimals": 1})]),
        ui.Button({"ID": "More", "Text": "More options...", "Flat": True, "Weight": 0, "StyleSheet": "QPushButton{color:#b9a6ff;text-align:left}"}),
        ui.VGap(0, 1),
        ui.Label({"ID": "Banner", "Text": "", "WordWrap": True, "Weight": 0, "MinimumSize": [0, 40]}),
        ui.HGroup({"Weight": 0}, [
            ui.Button({"ID": "Go", "Text": "Generate captions", "MinimumSize": [0, 42], "StyleSheet": PURPLE}),
            ui.Button({"ID": "Apply", "Text": "Apply changes", "Weight": 0, "MinimumSize": [120, 42], "StyleSheet": PURPLE_OUT}),
        ]),
        ui.Label({"Text": "Auto Captions v" + VERSION + "  \u00b7  Powered by Whisper (OpenAI, MIT) + whisper.cpp", "StyleSheet": "color:#6e6e76;font-size:10px;",
                  "Alignment": {"AlignHCenter": True}, "Weight": 0}),
    ]))
    more = disp.AddWindow({"ID": "MoreWin", "WindowTitle": "More options", "Geometry": [710, 300, 300, 200]}, ui.VGroup({"Spacing": 7}, [
        ui.HGroup([lab("In (frames)", 110), ui.SpinBox({"ID": "In", "Minimum": 1, "Maximum": 300, "Value": 15})]),
        ui.HGroup([lab("Out (frames)", 110), ui.SpinBox({"ID": "Out", "Minimum": 1, "Maximum": 300, "Value": 10})]),
        ui.HGroup([lab("Out style", 110), ui.ComboBox({"ID": "OutStyle"})]),
        ui.HGroup([lab("Letter delay", 110), ui.DoubleSpinBox({"ID": "Delay", "Minimum": -1, "Maximum": 50, "Value": -1, "SingleStep": 0.1, "SpecialValueText": "Default"})]),
        ui.HGroup([lab("Captions track V", 110), ui.SpinBox({"ID": "Track", "Minimum": 1, "Maximum": 99, "Value": 2})]),
        ui.Button({"ID": "MoreDone", "Text": "Done"}),
    ]))
    itm = win.GetItems()
    mitm = more.GetItems()
    itm["Lang"].AddItems(list(LANGS))
    mitm["OutStyle"].AddItems(OUT_STYLES)
    fonts = font_list()
    itm["Font"].AddItems(sorted(fonts))
    itm["Font"].CurrentText = DEFAULT_LOOK["Font"]
    state = {"srt": "", "color": "#FFFFFF"}
    progress = {"text": "", "running": False}

    def banner(msg, kind="ok"):
        icon, css = {"ok": ("✔", "background:#1f3326;color:#9fe1b4;border:1px solid #2e7d4f;"),
                     "err": ("⚠", "background:#4a1c1c;color:#ffb3b3;border:1px solid #d64545;"),
                     "info": ("ℹ", "background:#2a2733;color:#cbbcff;border:1px solid #4a3f7a;")}[kind]
        itm["Banner"].Text = icon + "  " + msg
        itm["Banner"].StyleSheet = css + "border-radius:6px;padding:2px 8px;"

    def ready():
        if not state["srt"] and not (whisper_exe() and model_ok()):
            banner("First time? Click Generate to download Whisper (1.6 GB, one time only).", "info")
        else:
            banner("Ready! Click Generate captions.", "info")

    def fill_styles(want=None):
        styles = fonts.get(itm["Font"].CurrentText) or []
        itm["Style"].Clear()
        itm["Style"].AddItems(styles)
        for pick in (want, DEFAULT_LOOK["Style"], "Regular"):
            if pick in styles:
                itm["Style"].CurrentText = pick
                break

    # ---------- presets ----------
    def load_presets():
        try:
            with open(PRESETS, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def refresh_presets(select=""):
        itm["Preset"].Clear()
        itm["Preset"].AddItems(["(none)"] + sorted(load_presets()))
        if select:
            itm["Preset"].CurrentText = select

    # every setting except the SRT file: (window items, widget ID, property)
    PRESET_FIELDS = [(itm, "Anim", "CurrentText"), (itm, "Lang", "CurrentText"), (itm, "MaxWords", "Value"),
                     (itm, "InOn", "Checked"), (itm, "OutOn", "Checked"), (itm, "Zoom", "Value"),
                     (itm, "Spacing", "Value"), (itm, "PosX", "Value"), (itm, "PosY", "Value"),
                     (mitm, "In", "Value"), (mitm, "Out", "Value"), (mitm, "OutStyle", "CurrentText"),
                     (mitm, "Delay", "Value"), (mitm, "Track", "Value")]

    def preset_values():
        p = {k: getattr(w[k], prop) for w, k, prop in PRESET_FIELDS}
        p.update(font=itm["Font"].CurrentText, style=itm["Style"].CurrentText, color=state["color"])
        return p

    def set_color(hexcol):
        state["color"] = hexcol.upper()
        itm["Color"].Text = state["color"]
        itm["Color"].StyleSheet = "QPushButton{background:%s;color:%s;border-radius:6px}" % (
            hexcol, "#000" if sum(hex_to_rgb(hexcol)) > 1.5 else "#fff")

    def on_preset_pick(ev):
        p = load_presets().get(itm["Preset"].CurrentText)
        if not p:
            return
        if p.get("font") in fonts:
            itm["Font"].CurrentText = p["font"]
        fill_styles(p.get("style"))
        if p.get("color"):
            set_color(p["color"])
        for w, k, prop in PRESET_FIELDS:
            if k in p:  # older presets saved fewer settings
                setattr(w[k], prop, p[k])
        on_in_toggle(None)
        on_out_toggle(None)
        banner("Preset '%s' loaded. Click Apply changes to use it on your captions." % itm["Preset"].CurrentText, "info")

    def on_save_preset(ev):
        root = tk_root()
        from tkinter import simpledialog
        name = simpledialog.askstring("Save preset", "Preset name:", parent=root)
        root.destroy()
        name = (name or "").strip()
        if not name or name == "(none)":
            return
        presets = load_presets()
        presets[name] = preset_values()
        os.makedirs(APP, exist_ok=True)
        with open(PRESETS, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=1)
        refresh_presets(name)
        banner("Preset '%s' saved." % name)

    def on_del_preset(ev):
        name = itm["Preset"].CurrentText
        presets = load_presets()
        if name not in presets:
            banner("Pick a preset first, then click Delete.", "err")
            return
        del presets[name]
        with open(PRESETS, "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=1)
        refresh_presets()
        banner("Preset '%s' deleted." % name)

    # ---------- actions ----------
    def settings():
        tl = project().GetCurrentTimeline()
        w = float(tl.GetSetting("timelineResolutionWidth")) if tl else 1920.0
        h = float(tl.GetSetting("timelineResolutionHeight")) if tl else 1080.0
        return {"w": w, "h": h, "in_on": itm["InOn"].Checked, "out_on": itm["OutOn"].Checked,
                "in": mitm["In"].Value, "out": mitm["Out"].Value, "out_style": mitm["OutStyle"].CurrentText,
                "delay": mitm["Delay"].Value, "font": itm["Font"].CurrentText, "style": itm["Style"].CurrentText,
                "color": state["color"], "spacing": itm["Spacing"].Value,
                "zoom": itm["Zoom"].Value, "x": itm["PosX"].Value, "y": itm["PosY"].Value}

    def style_item(item, s, text=None):
        comp = text_comp(item, text is not None)
        if not comp:
            return False
        if text is not None:
            set_text(comp, text)
            for t in text_tools(comp)[1]:
                t.SetInput("Size", DEFAULT_LOOK["Size"])
        apply_style(item, comp, s)
        apply_timing(comp, item.GetDuration(), s)
        return True

    def start_download():
        if progress["running"]:
            return
        progress["running"] = True

        def work():
            try:
                setup_whisper(progress)
                progress["text"] = "Whisper is ready! Click Generate captions."
            except Exception as e:
                progress["text"] = "ERR Download failed. Check your internet and click Generate again. (%s)" % e
            progress["running"] = False
        threading.Thread(target=work, daemon=True).start()
        banner("Downloading Whisper... keep this window open.", "info")

    def generate():
        s = settings()
        tl = project().GetCurrentTimeline()
        if not tl:
            raise RuntimeError("No timeline is open. Open a timeline first.")
        tpl = templates().get(itm["Anim"].CurrentText)
        if not tpl:
            raise RuntimeError("No animation found. Reinstall Auto Captions.")
        if state["srt"]:
            try:
                with open(state["srt"], encoding="utf-8-sig") as f:
                    caps = parse_srt(f.read())
            except (OSError, UnicodeDecodeError):
                raise RuntimeError("Couldn't read this SRT file. Export it again and retry.")
            if not caps:
                raise RuntimeError("This SRT file has no captions in it.")
        else:
            if progress["running"]:
                raise RuntimeError("Whisper is still downloading. Wait until it's done.")
            if not (whisper_exe() and model_ok()):
                start_download()
                return
            caps = transcribe(LANGS[itm["Lang"].CurrentText], itm["MaxWords"].Value)
            if not caps:
                raise RuntimeError("No speech found. Check the timeline audio isn't muted.")
        fps = float(tl.GetSetting("timelineFrameRate"))
        tl.AddTrack("video")
        track = tl.GetTrackCount("video")
        clips = [{"mediaPoolItem": tpl, "startFrame": 0, "endFrame": d, "trackIndex": track, "recordFrame": r}
                 for r, d in to_frames(caps, fps, tl.GetStartFrame())]
        items = project().GetMediaPool().AppendToTimeline(clips) or []
        if not items:
            raise RuntimeError("Resolve couldn't place the captions. Make sure the timeline isn't locked.")
        for item, cap in zip(items, caps):
            style_item(item, s, cap[2])
        mitm["Track"].Value = track
        banner("Done! %d captions on track V%d." % (len(items), track))

    def apply_all():
        tl = project().GetCurrentTimeline()
        if not tl:
            raise RuntimeError("No timeline is open. Open a timeline first.")
        track = mitm["Track"].Value
        n = sum(style_item(i, settings()) for i in (tl.GetItemListInTrack("video", track) or []))
        if not n:
            raise RuntimeError("No captions on track V%d. Set the right track in 'More options'." % track)
        banner("Updated %d captions on track V%d." % (n, track))

    def safely(fn):
        def run(ev):
            try:
                fn()
            except subprocess.CalledProcessError:
                banner("Listening to the audio failed. Try again, or reinstall Auto Captions.", "err")
            except Exception as e:
                banner(str(e), "err")
        return run

    def on_upload(ev):
        root = tk_root()
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=root, title="Choose an SRT file",
                                          filetypes=[("Subtitle files", "*.srt"), ("All files", "*.*")])
        root.destroy()
        if not path:
            return
        if not path.lower().endswith(".srt"):
            banner("Only .srt files work here. '%s' isn't an SRT file." % os.path.basename(path), "err")
            return
        state["srt"] = path
        itm["Upload"].Text = "  " + os.path.basename(path) + "  (click to change)"
        itm["Lang"].Enabled = itm["MaxWords"].Enabled = False
        banner("SRT file added. Click Generate captions.", "info")

    def on_color(ev):
        root = tk_root()
        from tkinter import colorchooser
        picked = colorchooser.askcolor(color=state["color"], parent=root, title="Caption colour")[1]
        root.destroy()
        if picked:
            set_color(picked)

    def on_tick(ev):
        if progress["text"]:
            t = progress["text"]
            banner(t[4:], "err") if t.startswith("ERR ") else banner(t, "info" if progress["running"] else "ok")
            if not progress["running"]:
                progress["text"] = ""

    def on_out_toggle(ev):
        mitm["OutStyle"].Enabled = mitm["Out"].Enabled = itm["OutOn"].Checked

    def on_in_toggle(ev):
        mitm["In"].Enabled = itm["InOn"].Checked

    win.On.Go.Clicked = safely(generate)
    win.On.Apply.Clicked = safely(apply_all)
    win.On.Upload.Clicked = on_upload
    win.On.Color.Clicked = on_color
    win.On.Preset.CurrentIndexChanged = on_preset_pick
    win.On.SavePreset.Clicked = on_save_preset
    win.On.DelPreset.Clicked = on_del_preset
    win.On.OutOn.Clicked = on_out_toggle
    win.On.InOn.Clicked = on_in_toggle
    win.On.More.Clicked = lambda ev: more.Show()
    more.On.MoreDone.Clicked = lambda ev: more.Hide()
    more.On.MoreWin.Close = lambda ev: more.Hide()
    win.On.Font.CurrentIndexChanged = lambda ev: fill_styles()
    win.On.Insta.Clicked = lambda ev: webbrowser.open(INSTAGRAM)
    win.On.Logo.Clicked = lambda ev: webbrowser.open(INSTAGRAM)
    win.On.Discord.Clicked = lambda ev: webbrowser.open(DISCORD)
    win.On.AmrWin.Close = lambda ev: disp.ExitLoop()

    fill_styles()
    refresh_presets()
    order = ["Normal", "Fade in", "Bounce up"]
    itm["Anim"].AddItems(sorted(templates(), key=lambda n: (order.index(n) if n in order else len(order), n)))
    ready()
    try:  # live download progress; if timers aren't supported the download still works
        timer = ui.Timer({"ID": "Tick", "Interval": 500})
        disp.On.Tick.Timeout = on_tick
        timer.Start()
    except Exception:
        pass
    win.Show()
    disp.RunLoop()
    more.Hide()
    win.Hide()


if __name__ == "__main__" and not os.environ.get("AMR_CAPTIONS_TEST"):
    main()
