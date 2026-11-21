"""
Build video/ETL_Code_Walkthrough.mp4: a narrated walkthrough of etl_pipeline.py.

    python video/make_video.py

Slides are rendered with Pillow + Pygments (code is read live from etl_pipeline.py),
narration is synthesised with the Windows built-in voice (System.Speech), and
ffmpeg (from the imageio-ffmpeg package) assembles the MP4.
Requires: pip install pillow pygments imageio-ffmpeg
"""
from __future__ import annotations

import ast
import io
import json
import subprocess
import sys
import wave
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont
from pygments import highlight
from pygments.formatters import ImageFormatter
from pygments.lexers import PythonLexer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = HERE / "build"
OUT_MP4 = HERE / "ETL_Code_Walkthrough.mp4"
SCRIPT_MD = HERE / "narration_script.md"
PIPELINE = ROOT / "etl_pipeline.py"
FIGS = ROOT / "report" / "figures"

VOICE = "Microsoft Zira Desktop"
W, H = 1920, 1080
BG = (13, 17, 23)
PANEL = (22, 27, 34)
FG = (230, 237, 243)
MUTED = (139, 148, 158)
ACCENT = (88, 166, 255)
GREEN = (63, 185, 80)
YELLOW = (210, 153, 34)
FONTS = Path("C:/Windows/Fonts")


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


F_TITLE = font("segoeuib.ttf", 54)
F_SECTION = font("segoeuib.ttf", 26)
F_BODY = font("segoeui.ttf", 36)
F_SMALL = font("segoeui.ttf", 24)
F_MONO = font("consola.ttf", 23)


# --------------------------------------------------------------------------- #
# Reading code from etl_pipeline.py
# --------------------------------------------------------------------------- #
SOURCE = PIPELINE.read_text(encoding="utf-8")
LINES = SOURCE.splitlines()
TREE = ast.parse(SOURCE)


def _is_log_only(stmt: ast.stmt) -> bool:
    """True for `log.x(...)` calls and for if/for blocks containing only those."""
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        f = stmt.value.func
        return isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "log"
    if isinstance(stmt, (ast.If, ast.For)) and not stmt.orelse:
        return all(_is_log_only(s) for s in stmt.body)
    return False


def function_source(name: str, drop_docstring=True, drop_logging=True) -> tuple[str, int, int]:
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    skip: set[int] = set()
    body = node.body
    if drop_docstring and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        skip.update(range(body[0].lineno, body[0].end_lineno + 1))
    if drop_logging:
        for sub in ast.walk(node):
            for field in ("body", "orelse"):
                for s in getattr(sub, field, []) or []:
                    if isinstance(s, ast.stmt) and _is_log_only(s):
                        skip.update(range(s.lineno, s.end_lineno + 1))
    start = node.lineno - len(node.decorator_list)
    kept = [LINES[i - 1] for i in range(start, node.end_lineno + 1) if i not in skip]
    # collapse double blank lines left behind
    out: list[str] = []
    for line in kept:
        if line.strip() == "" and out and out[-1].strip() == "":
            continue
        out.append(line)
    return "\n".join(out).rstrip(), start, node.end_lineno


def constants_source(names: list[str]) -> str:
    parts = []
    for n in TREE.body:
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets):
            start = n.lineno
            # include a comment line directly above
            if LINES[start - 2].strip().startswith("#"):
                start -= 1
            parts.append("\n".join(LINES[start - 1:n.end_lineno]))
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Slide rendering
# --------------------------------------------------------------------------- #
def base_slide(section: str, title: str, idx: int, total: int) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=ACCENT)
    if section:
        d.text((70, 42), section.upper(), font=F_SECTION, fill=ACCENT)
    d.text((70, 76), title, font=F_TITLE, fill=FG)
    d.text((70, H - 50), "Smart Factory ETL Pipeline", font=F_SMALL, fill=MUTED)
    d.text((W - 70, H - 50), f"{idx} / {total}", font=F_SMALL, fill=MUTED, anchor="ra")
    return img


def paste_fit(img: Image.Image, content: Image.Image, box: tuple[int, int, int, int], card=None):
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    scale = min(bw / content.width, bh / content.height, 1.0)
    c = content.resize((int(content.width * scale), int(content.height * scale)), Image.LANCZOS)
    px, py = x0 + (bw - c.width) // 2, y0 + (bh - c.height) // 2
    if card:
        ImageDraw.Draw(img).rounded_rectangle([px - 20, py - 20, px + c.width + 20, py + c.height + 20],
                                              radius=18, fill=card)
    img.paste(c, (px, py))


def render_code(code: str) -> Image.Image:
    fmt = ImageFormatter(style="github-dark", font_name="Consolas", font_size=26, line_numbers=False,
                         image_pad=28, line_pad=6)
    png = highlight(code, PythonLexer(), fmt)
    return Image.open(io.BytesIO(png)).convert("RGB")


def slide_title(s, idx, total):
    img = base_slide("", "", idx, total)
    d = ImageDraw.Draw(img)
    d.text((W // 2, 360), s["heading"], font=font("segoeuib.ttf", 84), fill=FG, anchor="mm")
    d.text((W // 2, 460), s["sub"], font=font("segoeui.ttf", 42), fill=ACCENT, anchor="mm")
    y = 600
    for b in s.get("bullets", []):
        d.text((W // 2, y), b, font=F_BODY, fill=MUTED, anchor="mm")
        y += 62
    return img


def slide_code(s, idx, total):
    img = base_slide(s["section"], s["title"], idx, total)
    d = ImageDraw.Draw(img)
    codes = []
    labels = []
    for name in s["functions"]:
        src, a, b = function_source(name, **s.get("opts", {}))
        codes.append(src)
        labels.append(f"{name}()  lines {a}-{b}")
    code = "\n\n".join(codes) if "code" not in s else s["code"]
    if "code" in s:
        labels = [s.get("label", "")]
    d.text((70, 150), "etl_pipeline.py  ·  " + "  ·  ".join(labels), font=F_SMALL, fill=MUTED)
    paste_fit(img, render_code(code), (70, 200, W - 70, H - 80))
    return img


def slide_image(s, idx, total):
    img = base_slide(s["section"], s["title"], idx, total)
    files = s["images"]
    top, bottom = 170, H - 80
    if len(files) == 1:
        paste_fit(img, Image.open(files[0]).convert("RGB"), (90, top + 20, W - 90, bottom - 20),
                  card=s.get("card"))
    else:
        ims = [Image.open(f).convert("RGB") for f in files]
        gap = 30
        avail = bottom - top - gap * (len(ims) - 1)
        total_h = sum(i.height for i in ims)
        y = top
        for im in ims:
            h = int(avail * im.height / total_h)
            paste_fit(img, im, (90, y, W - 90, y + h))
            y += h + gap
    return img


def slide_terminal(s, idx, total):
    img = base_slide(s["section"], s["title"], idx, total)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = 70, 170, W - 70, H - 80
    d.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=(10, 12, 16), outline=(48, 54, 61), width=2)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([x0 + 24 + i * 30, y0 + 18, x0 + 40 + i * 30, y0 + 34], fill=c)
    y = y0 + 56
    d.text((x0 + 28, y), "> ", font=F_MONO, fill=GREEN)
    d.text((x0 + 52, y), s["command"], font=F_MONO, fill=FG)
    y += 38
    line_h = min(31, (y1 - y - 20) // max(1, len(s["lines"])))
    for line in s["lines"]:
        color = YELLOW if "WARNING" in line else (ACCENT if "VERIFY" in line else
                                                   (GREEN if "finished" in line else FG))
        if " | " in line:
            ts, rest = line.split(" | ", 1)
            d.text((x0 + 28, y), ts + " | ", font=F_MONO, fill=MUTED)
            d.text((x0 + 28 + F_MONO.getlength(ts + " | "), y), rest, font=F_MONO, fill=color)
        else:
            d.text((x0 + 28, y), line, font=F_MONO, fill=color)
        y += line_h
    return img


RENDER = {"title": slide_title, "code": slide_code, "image": slide_image, "terminal": slide_terminal}


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #
def run_pipeline_for_demo() -> list[str]:
    """Run the real pipeline and capture its log for the demo slide."""
    res = subprocess.run([sys.executable, "-I", "etl_pipeline.py", "--db", "smart_factory.db"],
                         cwd=ROOT, capture_output=True, text=True)
    if res.returncode != 0:
        raise SystemExit("Pipeline run failed (close smart_factory.db in DB Browser?):\n" + res.stderr)
    return [l for l in res.stderr.splitlines() if l.strip() and "=====" not in l]


def scenes(demo_log: list[str]) -> list[dict]:
    return [
        dict(kind="title", heading="Smart Factory ETL Pipeline", sub="Code structure and end-to-end demo",
             bullets=["Python  ·  pandas  ·  SQLite", "etl_pipeline.py  ->  smart_factory.db"],
             say="Hello. This video walks through the Smart Factory E T L pipeline. It is a Python script "
                 "that extracts machine telemetry and quality control logs from two C S V files, cleans and "
                 "joins them, and loads the result into a sequel-lite database called smart factory dot D B. "
                 "I will explain the code structure first, and then run the pipeline end to end."),
        dict(kind="image", section="Overview", title="Architecture and data flow",
             images=[FIGS / "architecture.png"], card=(255, 255, 255),
             say="Here is the data flow. On the left are the two sources: the telemetry file, encoded in U T F 8, "
                 "and the inspection log, encoded in I S O 8859 1. Each one has its own extract function. "
                 "The transform stage cleans the sensor data, standardizes both schemas, joins them, and builds "
                 "hourly metrics. The load stage writes three tables: sensor readings, quality checks, and "
                 "hourly summary."),
        dict(kind="code", section="Code structure", title="Configuration in one place", functions=[],
             code=constants_source(["LOOKBACK_DAYS", "ERROR_CODES", "KEY_COLS", "QC_COLUMN_MAP",
                                    "OPERATING_BOUNDS"]),
             label="module-level constants",
             say="The script is organised as one function per step, and all the rules live at the top as "
                 "constants: the seven day lookback window, the error codes, the composite join key, the "
                 "mapping of the Q C column names, and the valid operating bounds for temperature, pressure, "
                 "and vibration. Changing a rule means editing one constant, not searching through the code."),
        dict(kind="code", section="Phase 1  ·  Extract", title="Extract functions",
             functions=["extract_sensor_data", "extract_qc_data"],
             say="Phase one is extract. Extract sensor data reads the telemetry file, finds the latest timestamp, "
                 "and keeps only the rows from the last seven days. That leaves 6,971 of the 10,000 rows. "
                 "Extract Q C data reads the inspection log with the I S O 8859 1 encoding, and keeps only "
                 "completed inspections, which gives 1,463 rows. Both rely on a shared helper with try and "
                 "except blocks, so a missing file, a wrong encoding, or a malformed C S V raises a clear "
                 "E T L error instead of crashing."),
        dict(kind="code", section="Phase 2  ·  Transform", title="Standardizing the schemas",
             functions=["standardize_sensor_schema", "standardize_qc_schema"],
             say="Next, the schemas are standardized. Column names become lower case with underscores, so "
                 "Line I D becomes line underscore I D. The Q C columns are renamed to match the telemetry: "
                 "inspection time becomes timestamp, line code becomes line I D, and equipment tag becomes "
                 "machine I D. All timestamps are converted to real date time objects, and the text None in "
                 "the defect type becomes a true null value."),
        dict(kind="code", section="Phase 2  ·  Transform", title="Cleaning the sensor data",
             functions=["clean_sensor_data"],
             say="Cleaning is the core of the transform. First, the error codes, minus 999 and minus 1, become "
                 "missing values. Then any value outside the operating bounds is also marked as missing. "
                 "The rows are sorted by time within each machine, and a grouped forward fill replaces each "
                 "invalid value with the previous valid reading from the same machine. Finally, the data "
                 "quality column marks the row as estimated if anything was filled, and good otherwise. "
                 "In this data set, 684 rows were estimated."),
        dict(kind="code", section="Phase 2  ·  Transform", title="Record ID and the left join",
             functions=["add_record_id", "join_sensor_quality"],
             say="Each sensor row gets a record I D: a hash of its timestamp, line, machine, and its row number "
                 "in the source file. The row number matters, because the raw file repeats some readings, and "
                 "without it the primary key would collide. The join is a left join on the composite key of "
                 "timestamp, line, and machine. A few inspections appear twice, so before joining, I keep one "
                 "per key, with fail taking priority over pass. The join is validated as many to one, so the "
                 "number of sensor rows can never change. The quality status is then pass, fail, or not checked."),
        dict(kind="code", section="Phase 2  ·  Transform", title="Hourly aggregation",
             functions=["aggregate_hourly"],
             say="The hourly aggregation groups the joined data by hour, line, and machine. It computes the "
                 "average, minimum, and maximum of temperature, pressure, and vibration. It counts the checks "
                 "and the failures, and calculates the defect rate as failures divided by checks, times 100. "
                 "Hours without any inspection get an empty defect rate, instead of a misleading zero."),
        dict(kind="code", section="Phase 3  ·  Load", title="Loading into SQLite",
             functions=["load_to_sqlite"],
             say="Phase three is load. The table schema is written in sequel, with primary keys, auto increment "
                 "I Ds, and check constraints. All three tables are written inside a single transaction, so if "
                 "any step fails, nothing is saved. The tables are dropped and rebuilt on every run, which makes "
                 "the pipeline safe to run again."),
        dict(kind="code", section="Orchestration", title="run_pipeline() and main()",
             functions=["run_pipeline", "main"],
             say="Run pipeline ties everything together in order: extract, transform, and load, followed by a "
                 "verification step that prints the row counts. The main function reads the command line "
                 "options for the data folder and the database path, and returns an exit code of one if "
                 "anything fails."),
        dict(kind="terminal", section="Demo", title="End-to-end execution", command="python etl_pipeline.py",
             lines=demo_log,
             say="Now the end to end run. The log shows every stage: 6,971 sensor rows and 1,463 completed "
                 "inspections extracted, 477 error codes and 209 out of range values cleaned, the result of the "
                 "join, and 3,801 hourly rows. All three tables load successfully, and the verification step "
                 "lists the machines with the highest defect rates."),
        dict(kind="image", section="Demo", title="Querying smart_factory.db",
             images=[FIGS / "q1_counts.png", FIGS / "q5_analysis.png"],
             say="Querying the database confirms the result. Sensor readings has 6,971 rows, quality checks has "
                 "1,463, and hourly summary has 3,801. Grouping by production line, line 2 has the highest defect "
                 "rate, at 20.7 percent, and line 1 has the lowest, at 15.8 percent."),
        dict(kind="title", heading="Summary", sub="Modular, validated, re-runnable",
             bullets=["Extract: 7-day window, ISO-8859-1, completed inspections only",
                      "Transform: error codes + bounds -> per-machine forward fill",
                      "Left join on (timestamp, line_id, machine_id), validated many-to-one",
                      "Load: 3 tables in one SQLite transaction"],
             say="To summarize: the pipeline has modular extract, transform, and load functions, careful "
                 "handling of error codes and duplicates, a validated left join, and a transactional load into "
                 "sequel-lite. Thank you for watching."),
    ]


# --------------------------------------------------------------------------- #
# Narration + assembly
# --------------------------------------------------------------------------- #
TTS_PS1 = r"""
param([string]$JsonPath, [string]$Voice)
Add-Type -AssemblyName System.Speech
$items = Get-Content -Raw -Encoding UTF8 $JsonPath | ConvertFrom-Json
foreach ($it in $items) {
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.SelectVoice($Voice)
    $s.Rate = 0
    $s.SetOutputToWaveFile($it.wav)
    $s.Speak($it.text)
    $s.Dispose()
}
"""


def synthesize(texts: list[str], wavs: list[Path]) -> None:
    ps1 = BUILD / "tts.ps1"
    ps1.write_text(TTS_PS1, encoding="utf-8")
    job = BUILD / "tts.json"
    job.write_text(json.dumps([{"text": t, "wav": str(w)} for t, w in zip(texts, wavs)]), encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
                    "-JsonPath", str(job), "-Voice", VOICE], check=True)


def wav_seconds(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    print("running pipeline for the demo slide ...")
    sc = scenes(run_pipeline_for_demo())
    total = len(sc)

    print("rendering slides ...")
    pngs, wavs = [], []
    for i, s in enumerate(sc, 1):
        png = BUILD / f"slide_{i:02d}.png"
        RENDER[s["kind"]](s, i, total).save(png)
        pngs.append(png)
        wavs.append(BUILD / f"voice_{i:02d}.wav")

    print("synthesizing narration ...")
    synthesize([s["say"] for s in sc], wavs)

    print("encoding segments ...")
    segs = []
    lead, tail = 0.5, 0.9
    for i, (png, wav) in enumerate(zip(pngs, wavs), 1):
        dur = lead + wav_seconds(wav) + tail
        seg = BUILD / f"seg_{i:02d}.mp4"
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30", "-i", str(png),
                        "-i", str(wav), "-filter_complex",
                        f"[1:a]adelay={int(lead*1000)}:all=1,apad,aformat=sample_rates=48000:channel_layouts=stereo[a];"
                        f"[0:v]fade=t=in:st=0:d=0.3,fade=t=out:st={dur-0.3:.2f}:d=0.3,format=yuv420p[v]",
                        "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", "-c:v", "libx264", "-preset", "medium",
                        "-tune", "stillimage", "-crf", "20", "-r", "30", "-c:a", "aac", "-b:a", "160k",
                        str(seg)], check=True)
        segs.append((seg, dur))

    concat = BUILD / "concat.txt"
    concat.write_text("".join(f"file '{p.as_posix()}'\n" for p, _ in segs), encoding="utf-8")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat),
                    "-c", "copy", "-movflags", "+faststart", str(OUT_MP4)], check=True)

    # narration script for recording your own voice-over
    t = 0.0
    md = ["# Video narration script", "",
          "Narration used in `ETL_Code_Walkthrough.mp4`, with the time each slide starts.", ""]
    for i, (s, (_, dur)) in enumerate(zip(sc, segs), 1):
        title = s.get("title") or s.get("heading")
        md += [f"## {i}. {title}  ({int(t // 60)}:{int(t % 60):02d})", "", s["say"], ""]
        t += dur
    SCRIPT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"done: {OUT_MP4}  ({int(t // 60)}:{int(t % 60):02d})")


if __name__ == "__main__":
    main()
