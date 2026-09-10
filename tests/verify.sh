#!/usr/bin/env bash
# Real-CLI regression checks for the astra-pixel-art skill.
#
#   bash tests/verify.sh
#
# Not a test framework: every check runs the real command and fails loudly
# with a non-zero exit code.  It deliberately uses the system python3 with
# no virtualenv and no installed packages, because that is the point of the
# design.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PC="$ROOT/skills/astra-pixel-art/scripts/pixelart.py"
WORK="$(mktemp -d /tmp/astra-verify-XXXXXX)"
FAILED=0

# Some checks import the engine in-process, which would otherwise drop a
# __pycache__ into the source tree.  The product's own guard (the CLI sets
# sys.dont_write_bytecode) is tested separately, with this switched back off.
export PYTHONDONTWRITEBYTECODE=1

# A run that stops early must not look like a run that passed: a syntax error
# halfway down the file used to print "all checks passed" and exit 0, because
# the trap ran on the way out and the count was still zero.  The sentinel at
# the end of the file is what makes that impossible.
FINISHED=0
cleanup() {
  if [ "$FINISHED" != "1" ]; then
    echo "the suite stopped before the end (syntax error, or an early exit): treat this run as failed ($WORK)"
    FAILED=$((FAILED + 1))
  fi
  if [ "$FAILED" = "0" ]; then
    echo "all checks passed ($WORK)"
    exit 0
  fi
  echo "$FAILED check(s) failed ($WORK)"
  exit 1
}
trap cleanup EXIT

check() { # check <description> <actual> <expected>
  if [ "$2" = "$3" ]; then
    echo "  ok   $1"
  else
    echo "  FAIL $1: got=$2 want=$3"
    FAILED=$((FAILED + 1))
  fi
}

pc() { python3 "$PC" --workspace "$WORK" "$@"; }
steps() { pc log --summary | grep -c '^ *[0-9]\+\.'; }

echo "[1] the engine needs nothing installed"
check "no numpy / Pillow / jupyter imports" \
  "$(grep -rE '^(import|from) +(numpy|PIL|jupyter|ipykernel)' \
      "$ROOT/skills/astra-pixel-art/scripts/astra_pixelart" | wc -l)" "0"
check "runs from an unrelated cwd on a bare python3" \
  "$(cd /tmp && python3 "$PC" --workspace "$WORK" help >/dev/null 2>&1 && echo yes || echo no)" "yes"

echo "[2] the cheat sheet documents the API"
pc help > "$WORK/help.txt"
check "help shows new_canvas" "$(grep -c 'new_canvas(' "$WORK/help.txt")" "1"
check "help shows the export contract" "$(grep -c 'save(name, canvas=None, scales=(1,4), indexed=False)' "$WORK/help.txt")" "1"

echo "[3] steps accumulate and variables survive between CLI invocations"
pc exec > "$WORK/step1.txt" <<'PY'
sky = new_canvas(32, 24, "#1b2a4a")
sky.color("#f4f1de", name="moon")
sky.circle(20, 9, 5, "moon")
PY
check "first step ok" "$(grep -c 'step 1 ok' "$WORK/step1.txt")" "1"
pc exec > "$WORK/step2.txt" <<'PY'
print("moon index", sky.color("moon"))
rain = new_canvas(32, 24)
rain.dither(0, 18, 32, 6, ["#16213d", "#243a63"], pattern="checker")
final = flatten([sky, rain], name="night")
print(save("night"))
PY
check "the earlier canvas is still there" "$(grep -c 'moon index 2' "$WORK/step2.txt")" "1"
check "log holds 2 steps" "$(steps)" "2"

echo "[4] a failing step changes nothing"
pc exec > "$WORK/bad.txt" 2>&1 <<'PY'
final.rect(0, 0, 999, 999, "#ff0000")
raise RuntimeError("boom")
PY
check "failing step exits non-zero" "$?" "1"
check "failure says it was not saved" "$(grep -c 'not saved' "$WORK/bad.txt")" "1"
check "log still holds 2 steps" "$(steps)" "2"
pc exec > "$WORK/check.txt" <<'PY'
sky_index = sky.color("#1b2a4a")
print("pixel(0,0) is still the sky colour:", final.get(0, 0) == sky_index)
PY
check "the half-finished red rect left no trace" \
  "$(grep -c 'still the sky colour: True' "$WORK/check.txt")" "1"

echo "[5] exports are 1x and 4x"
check "1x PNG written" "$([ -f "$WORK/exports/night.png" ] && echo yes || echo no)" "yes"
check "4x PNG written" "$([ -f "$WORK/exports/night@4x.png" ] && echo yes || echo no)" "yes"
check "reloadable state written" "$([ -f "$WORK/exports/night.pixelart.json" ] && echo yes || echo no)" "yes"

echo "[6] the PNGs really contain the drawing"
check "pixel probe (Pillow when present, stdlib otherwise)" \
  "$(python3 "$ROOT/tests/png_probe.py" "$WORK")" "PIXELS ok"

echo "[7] text observation carries a legend"
pc exec > "$WORK/text.txt" <<'PY'
print(final.matrix_text((14, 3, 25, 15)))
PY
check "legend names the registered colour, with its index" "$(grep -c 'moon (#f4f1deff, id 2)' "$WORK/text.txt")" "1"
check "the view is bounded to the region" "$(grep -c 'region (14,3)-(25,15)' "$WORK/text.txt")" "1"

echo "[8] undo drops the newest step"
pc exec >/dev/null <<'PY'
marker = "this step will be undone"
PY
check "log has 5 steps before undo" "$(steps)" "5"
pc undo 1 > "$WORK/undo.txt"
check "undo reports 4 left" "$(grep -c '4 step(s) left' "$WORK/undo.txt")" "1"
check "log has 4 steps after undo" "$(steps)" "4"

echo "[9] notebook export is one cell per step"
pc notebook --out "$WORK/program.ipynb" >/dev/null
check "notebook has 4 cells" \
  "$(python3 -c "import json;print(len(json.load(open('$WORK/program.ipynb'))['cells']))")" "4"

echo "[10] a fresh workspace starts clean, reset clears an old one"
FRESH="$(mktemp -d /tmp/astra-fresh-XXXXXX)"
check "fresh workspace has no steps" \
  "$(python3 "$PC" --workspace "$FRESH" log | grep -c 'no steps yet')" "1"
check "using a canvas before creating one explains itself" \
  "$(python3 "$PC" --workspace "$FRESH" exec 2>&1 <<'PY' | grep -c 'CanvasError: no canvas yet'
print(current())
PY
)" "1"
check "reset empties the log" \
  "$(pc reset --yes >/dev/null; pc log | grep -c 'no steps yet')" "1"
check "reset removes exports" "$(ls "$WORK/exports" 2>/dev/null | wc -l)" "0"
rm -rf "$FRESH"

echo "[11] the documented examples and API names are real"
SKILLDIR="$ROOT/skills/astra-pixel-art"
check "SKILL.md links to a file that exists" \
  "$([ -f "$SKILLDIR/references/api.md" ] && echo yes || echo no)" "yes"
check "frontmatter name equals the directory name" \
  "$(awk '/^name:/{print $2; exit}' "$SKILLDIR/SKILL.md")" "$(basename "$SKILLDIR")"
check "frontmatter stays inside the six spec fields" \
  "$(awk '/^---$/{n++; next} n==1 && /^[a-z-]+:/{print $1}' "$SKILLDIR/SKILL.md" \
     | grep -vcE '^(name|description|license|compatibility|metadata|allowed-tools):$')" "0"
# the deliverable's location is the one fact whose absence makes an agent
# tell the user something untrue, so the entry point has to state it
check "SKILL.md says where the exported files land" \
  "$(grep -c 'pixelart/exports' "$SKILLDIR/SKILL.md")" "1"
check "SKILL.md stays lean (spec allows 500; an agent reads all of it)" \
  "$([ "$(wc -l < "$SKILLDIR/SKILL.md")" -le 120 ] && echo yes || echo no)" "yes"
check "every function the docs *call* really exists" \
  "$(python3 - "$SKILLDIR" <<'PY'
import re, sys, pathlib, tempfile
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart.canvas import Canvas
from astra_pixelart.session import Session

session = Session(tempfile.mkdtemp(prefix="astra-doc-"))
available = set(session._api()) | {n for n in dir(Canvas) if not n.startswith("_")}
doc = (root / "SKILL.md").read_text(encoding="utf-8") + \
      (root / "references" / "api.md").read_text(encoding="utf-8")

# every call the docs show, minus the ones belonging to other libraries the
# docs deliberately demonstrate (the optional Pillow import snippet)
import builtins
# methods belonging to libraries the docs deliberately demonstrate (Pillow)
elsewhere = {
    "Image", "img", "small", "resize", "getpixel", "convert", "quantize",
    "fromarray", "frombytes", "open", "to_rgba", "to_indexed",
}
called = set(re.findall(r"\b([A-Za-z_][A-Za-z_0-9]*)\(", doc)) - elsewhere
missing = sorted(
    n for n in called if n not in available and n not in dir(builtins)
)
print("ok" if not missing else "documented but absent: " + ", ".join(missing))
PY
)" "ok"

echo "[12] the installable unit stands on its own"
check "ships its own LICENSE (MIT requires it to travel with copies)" \
  "$([ -f "$SKILLDIR/LICENSE" ] && echo yes || echo no)" "yes"
check "names the project the bundled font came from" \
  "$([ "$(grep -c 'PixelBench' "$SKILLDIR/THIRD_PARTY_NOTICES.md")" -ge 1 ] && echo yes || echo no)" "yes"
check "the font file itself points at the notice it needs" \
  "$(grep -c 'THIRD_PARTY_NOTICES.md' "$SKILLDIR/scripts/astra_pixelart/font5x7.py")" "1"
check "no maintainer docs inside the shipped directory" \
  "$(ls "$SKILLDIR" | grep -cE '^(README|AGENTS|CLAUDE|CHANGELOG)')" "0"
check "no tests inside the shipped directory" \
  "$([ -d "$SKILLDIR/tests" ] && echo yes || echo no)" "no"
check "the skill still runs after the move (clean copy, no repo)" \
  "$(D="$(mktemp -d /tmp/astra-standalone-XXXXXX)"; cp -r "$SKILLDIR" "$D/s"; \
     python3 "$D/s/scripts/pixelart.py" --workspace "$D/ws" exec >/dev/null 2>&1 <<'PY'
c = new_canvas(8, 8, "#20304a")
print(save("standalone"))
PY
     [ -f "$D/ws/exports/standalone.png" ] && [ -f "$D/ws/exports/standalone@4x.png" ] && echo yes || echo no; rm -rf "$D")" "yes"
# The host owns an installed skill directory: it is replaced on update, and it
# may be mounted read-only.  Bytecode dropped next to the source would be
# litter in someone else's tree, so tar the whole directory (mtimes included)
# and compare it before and after a real run.
check "running it leaves the shipped directory byte-for-byte untouched" \
  "$(D="$(mktemp -d /tmp/astra-pristine-XXXXXX)"; cp -r "$SKILLDIR" "$D/s"; \
     before="$(tar -cf - -C "$D/s" . | md5sum)"; \
     env -u PYTHONDONTWRITEBYTECODE python3 "$D/s/scripts/pixelart.py" \
       --workspace "$D/ws" exec >/dev/null 2>&1 <<'PY'
c = new_canvas(8, 8, "#20304a")
save("pristine")
PY
     after="$(tar -cf - -C "$D/s" . | md5sum)"; \
     [ "$before" = "$after" ] && [ -f "$D/ws/exports/pristine.png" ] && echo yes || echo no; \
     rm -rf "$D")" "yes"
check "so no __pycache__ appears, even with the interpreter left to decide" \
  "$(D="$(mktemp -d /tmp/astra-pycache-XXXXXX)"; cp -r "$SKILLDIR" "$D/s"; \
     find "$D/s" -name '__pycache__' -type d -prune -exec rm -rf {} +; \
     env -u PYTHONDONTWRITEBYTECODE python3 "$D/s/scripts/pixelart.py" \
       --workspace "$D/ws" help >/dev/null 2>&1; \
     find "$D/s" \( -name '__pycache__' -o -name '*.pyc' \) | wc -l; rm -rf "$D")" "0"

echo "[13] the cheat sheet is the whole surface (SKILL.md defers to it)"
check "every API name and CLI verb appears in \`help\`" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, tempfile, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
import pixelart
from astra_pixelart.canvas import Canvas
from astra_pixelart.session import Session

sheet = pixelart.CHEATSHEET
# what a step can call, plus the Canvas methods that are meant for authors
api = set(Session(tempfile.mkdtemp(prefix="astra-help-"))._api()) - {"Canvas", "CanvasError"}
# engine plumbing an author never calls directly (serialisation + interop)
methods = {n for n in dir(Canvas) if not n.startswith("_")} - {
    "to_rgba", "to_indexed", "to_dict", "from_dict", "from_image",
}
# the real verb names, straight out of argparse
verbs = set(pixelart.build_parser()._subparsers._group_actions[0].choices)
# an API entry must be *called* in the sheet (prose containing the word is not
# documentation); a CLI verb is checked in its own section, without parens
commands = sheet.split("COMMANDS", 1)[-1]
missing = sorted(
    [n for n in (api | methods) if f"{n}(" not in sheet]
    + [v for v in verbs if v not in commands]
)
print("ok" if not missing else "undocumented: " + ", ".join(missing))
PY
)" "ok"
# the sheet is the short version of the reference: it must stay smaller than
# references/api.md, which is the thing it tells the agent to read next, and
# small enough to be one sitting (the absolute cap is a backstop, not a target)
check "\`help\` stays smaller than the reference it defers to" \
  "$([ "$(python3 "$PC" --workspace "$WORK" help | wc -c)" -lt "$(wc -c < "$SKILLDIR/references/api.md")" ] && echo yes || echo no)" "yes"
check "\`help\` stays a single-sitting read (< 12 KB)" \
  "$([ "$(python3 "$PC" --workspace "$WORK" help | wc -c)" -lt 12288 ] && echo yes || echo no)" "yes"
# structure, not just presence: `view` is documented under LOOKING AT THE
# RESULT, and `save` under OUTPUT. Both drifted into CANVASES once, where a
# reader looking for "how do I look at it" would not find them — and the
# presence-only check above could not see it.
check "\`view\` is documented in the looking section" \
  "$(python3 -c "
import sys; sys.path.insert(0, '$SKILLDIR/scripts')
import pixelart
s = pixelart.CHEATSHEET
print('ok' if s.index('LOOKING AT THE RESULT') < s.index('view(canvas=None') < s.index('OUTPUT —') else 'misplaced')")" "ok"
check "\`save\` is documented in the output section" \
  "$(python3 -c "
import sys; sys.path.insert(0, '$SKILLDIR/scripts')
import pixelart
s = pixelart.CHEATSHEET
print('ok' if s.index('OUTPUT —') < s.index('save(name, canvas=None') < s.index('STEPPING BACK') else 'misplaced')")" "ok"
check "\`help\` explains behaviour, not just names (defaults, errors, clipping)" \
  "$([ "$(python3 "$PC" --workspace "$WORK" help | grep -cE 'default|raises|clipped|overwrites|transparent')" -ge 8 ] && echo yes || echo no)" "yes"

echo "[14] the error messages name the fix, not just the fault"
check "an unregistered colour name says how to register it" \
  "$(pc exec 2>&1 <<'PY' | grep -c 'register it first'
canvas = new_canvas(4, 4)
canvas.set(0, 0, "ink")
PY
)" "1"
check "a string passed where a palette belongs points at background=" \
  "$(pc exec 2>&1 <<'PY' | grep -c 'pass background='
from astra_pixelart import Canvas
Canvas(4, 4, "#fff")
PY
)" "1"

echo "[15] alpha blends when layers are composited"
pc exec > "$WORK/alpha.txt" <<'PY'
bg = new_canvas(8, 8, "#0000ff")
half = new_canvas(8, 8)
half.rect(0, 0, 4, 4, (255, 0, 0, 128))
flat = flatten([bg, half], name="blend")
blended = flat.pal.rgba(flat.get(1, 1))
print("blended      ", blended)
print("is a blend   ", blended[0] > 100 and blended[2] > 100 and blended[3] == 255)
print("half of layer", flat.pal.rgba(flat.get(6, 6)) == (0, 0, 255, 255))
PY
check "50% red over blue is a blend, not a cover" "$(grep -c 'is a blend    True' "$WORK/alpha.txt")" "1"
check "the untouched area still shows the layer below" \
  "$(grep -c 'half of layer True' "$WORK/alpha.txt")" "1"

pc exec > "$WORK/alpha2.txt" <<'PY'
blue = new_canvas(4, 4, "#0000ff")
opaque = new_canvas(4, 4)
opaque.rect(0, 0, 2, 2, "#ff0000")
transparent = new_canvas(4, 4)
print("opaque wins  ", flatten([blue, opaque]).pal.rgba(flatten([blue, opaque]).get(0, 0)) == (255, 0, 0, 255))
print("clear passes ", flatten([blue, transparent]).pal.rgba(flatten([blue, transparent]).get(0, 0)) == (0, 0, 255, 255))
d = new_canvas(4, 4, background="#20304a")
d.set(1, 1, (255, 0, 0, 128))
print("draw replaces", d.pal.rgba(d.get(1, 1)) == (255, 0, 0, 128))
print("alpha 0 is invisible", new_canvas(4, 4, background=(255, 0, 0, 0)).stats()["visible_pixels"] == 0)
PY
check "an opaque pixel still hides the layer below" "$(grep -c 'opaque wins   True' "$WORK/alpha2.txt")" "1"
check "a transparent pixel still lets it through" "$(grep -c 'clear passes  True' "$WORK/alpha2.txt")" "1"
check "drawing stores alpha instead of blending it" "$(grep -c 'draw replaces True' "$WORK/alpha2.txt")" "1"
check "a fully transparent colour paints nothing" "$(grep -c 'alpha 0 is invisible True' "$WORK/alpha2.txt")" "1"

check "the text view reports partial alpha" \
  "$(pc exec <<'PY' | grep -c '50% alpha'
c = new_canvas(4, 4, background="#20304a")
c.rect(0, 0, 2, 2, (255, 0, 0, 128))
print(c.matrix_text())
PY
)" "1"
check "the exported PNG carries the blend" \
  "$(pc exec >/dev/null <<'PY'
bg = new_canvas(4, 4, "#0000ff")
top = new_canvas(4, 4)
top.rect(0, 0, 4, 4, (255, 0, 0, 128))
save("alpha_check", flatten([bg, top], name="alpha_flat"))
PY
python3 - "$WORK/exports/alpha_check.png" <<'PY'
import struct, sys, zlib
d = open(sys.argv[1], "rb").read()
pos, idat = 8, b""
while pos < len(d):
    (ln,) = struct.unpack(">I", d[pos:pos+4]); tag = d[pos+4:pos+8]
    body = d[pos+8:pos+8+ln]; pos += 12 + ln
    if tag == b"IDAT": idat += body
    if tag == b"IEND": break
raw = zlib.decompress(idat)
r, g, b, a = raw[1], raw[2], raw[3], raw[4]   # RGBA of the first pixel
print("blended into the file:", (r, g, b, a) == (128, 0, 127, 255))
PY
)" "blended into the file: True"

echo "[16] output format: RGBA by default, indexed on request"
pc exec > "$WORK/out.txt" <<'PY'
c = new_canvas(64, 64, "#1b2a4a")
c.color("#f4f1de", name="moon")
c.dither(0, 32, 64, 32, ["#16213d", "#243a63"], pattern="bayer4")
c.circle(30, 22, 10, "moon")
print(save("both_rgba", c))
print(save("both_indexed", c, indexed=True))
PY
check "indexed PNG is smaller at 64x64" \
  "$([ "$(stat -c%s "$WORK/exports/both_indexed.png")" -lt "$(stat -c%s "$WORK/exports/both_rgba.png")" ] && echo yes || echo no)" "yes"
check "the default is truecolour (colour type 6)" \
  "$(python3 -c "
d = open('$WORK/exports/both_rgba.png','rb').read(); print(d[25])")" "6"
check "indexed=True writes colour type 3" \
  "$(python3 -c "
d = open('$WORK/exports/both_indexed.png','rb').read(); print(d[25])")" "3"
check "indexed carries a palette chunk" \
  "$(python3 -c "
d = open('$WORK/exports/both_indexed.png','rb').read(); print(b'PLTE' in d)")" "True"
check "the indexed PNG decodes back to the same pixels" \
  "$(pc exec <<'PY' | grep -c 'round trip: True'
a = load("exports/both_rgba.png")
b = load("exports/both_indexed.png")
print("round trip:", (a.w, a.h) == (b.w, b.h) and a.to_rgba()[2] == b.to_rgba()[2])
PY
)" "1"
check "too many colours for indexing fails with the fix" \
  "$(pc exec 2>&1 <<'PY' | grep -c 'save without indexed=True'
c = new_canvas(64, 64)
for i in range(300):
    c.set(i % 64, i // 64, (i % 256, (i * 7) % 256, (i * 13) % 256))
save("toomany", c, indexed=True)
PY
)" "1"
check "a failed step leaves no orphaned preview behind" \
  "$(D="$(mktemp -d /tmp/astra-prev-XXXXXX)"; python3 "$PC" --workspace "$D" exec >/dev/null 2>&1 <<'PY'
c = new_canvas(8, 8, "#20304a")
view()
PY
python3 "$PC" --workspace "$D" exec >/dev/null 2>&1 <<'PY'
view()
raise RuntimeError("boom")
PY
python3 "$PC" --workspace "$D" exec >/dev/null 2>&1 <<'PY'
view()
PY
ls "$D/previews" | wc -l; rm -rf "$D")" "1"
check "the preview name does not move between replays" \
  "$(D="$(mktemp -d /tmp/astra-prev2-XXXXXX)"; python3 "$PC" --workspace "$D" exec >/dev/null 2>&1 <<'PY'
c = new_canvas(8, 8, "#20304a")
print(view())
PY
python3 "$PC" --workspace "$D" exec >/dev/null 2>&1 <<'PY'
print(view())
PY
ls "$D/previews" | tr '\n' ' '; rm -rf "$D")" "c1@vision.png "

echo "[17] view() prepares the drawing for a vision model"
pc exec > "$WORK/vision.txt" <<'PY'
c = new_canvas(64, 64, "#20304a")
c.rect(0, 0, 32, 32, "#f4f1de")
for label, path in (
    ("vision", view()),                       # the tested default
    ("plain", view(scale=8, margin=0)),       # faithful render, no margin
    ("nomargin", view(margin=0)),             # same 512 size, no margin
    ("bottomright", view(margin=4, edges="br")),
):
    img = load(path)
    print(label, img.w, img.h, "corner", img.get(0, 0) == 0, "far", img.get(img.w - 1, img.h - 1) != 0)
PY
check "the default preview is 512 on the long edge" "$(grep -c 'vision 512 512' "$WORK/vision.txt")" "1"
check "it carries a transparent margin (top-left corner is empty)" \
  "$(grep -c 'vision 512 512 corner True' "$WORK/vision.txt")" "1"
check "the drawing still reaches the far corner" \
  "$(grep -c 'vision 512 512 corner True far True' "$WORK/vision.txt")" "1"
check "scale=N renders plainly, with no margin" \
  "$(grep -c 'plain 512 512 corner False' "$WORK/vision.txt")" "1"
check "margin=0 keeps the 512 size but drops the margin" \
  "$(grep -c 'nomargin 512 512 corner False far True' "$WORK/vision.txt")" "1"
check "edges=br puts the margin on the other side instead" \
  "$(grep -c 'bottomright 512 512 corner False far False' "$WORK/vision.txt")" "1"
check "the step report explains the margin, without needing print()" \
  "$(pc exec 2>&1 <<'PY' | grep -c 'do not shift anything to compensate'
c2 = new_canvas(64, 64, "#20304a")
view(c2)
PY
)" "1"
check "save() never pads: the deliverable is the canvas size" \
  "$(pc exec >/dev/null <<'PY'
c3 = new_canvas(40, 24, "#20304a")
save("unpadded", c3)
PY
python3 -c "
import struct
d = open('$WORK/exports/unpadded.png','rb').read()
print(struct.unpack('>II', d[16:24]))")" "(40, 24)"
check "non-square keeps its aspect, long edge at 512" \
  "$(pc exec <<'PY' | sed -n 's/^  SIZE //p'
wide = new_canvas(96, 32, "#20304a")
img = load(view(wide))
print("SIZE", img.w, img.h)
PY
)" "512 191"
check "a 512 canvas is left alone (nothing to upscale away)" \
  "$(pc exec <<'PY' | sed -n 's/^  SIZE //p'
big = new_canvas(512, 512, "#20304a")
img = load(view(big))
print("SIZE", img.w, img.h, img.get(0, 0) != 0)
PY
)" "512 512 True"

echo "[18] the two doors into a preview agree, byte for byte"
DOOR_A="$(mktemp -d /tmp/astra-doorA-XXXXXX)"
DOOR_B="$(mktemp -d /tmp/astra-doorB-XXXXXX)"
python3 "$PC" --workspace "$DOOR_A" exec >/dev/null <<'PY'
c = new_canvas(64, 64, "#20304a")
c.rect(0, 0, 32, 32, "#f4f1de")
view()
view(scale=8, margin=0)
PY
python3 "$PC" --workspace "$DOOR_B" exec >/dev/null <<'PY'
c = new_canvas(64, 64, "#20304a")
c.rect(0, 0, 32, 32, "#f4f1de")
PY
python3 "$PC" --workspace "$DOOR_B" view >/dev/null
python3 "$PC" --workspace "$DOOR_B" view --scale 8 --margin 0 >/dev/null
check "both doors write the same two previews" \
  "$(ls "$DOOR_A/previews" | tr '\n' ' ')" "$(ls "$DOOR_B/previews" | tr '\n' ' ')"
check "the vision preview is identical through either door" \
  "$(cmp -s "$DOOR_A/previews/c1@vision.png" "$DOOR_B/previews/c1@vision.png" && echo same || echo differs)" "same"
check "the plain render is identical through either door" \
  "$(cmp -s "$DOOR_A/previews/c1@8x-m0-tl.png" "$DOOR_B/previews/c1@8x-m0-tl.png" && echo same || echo differs)" "same"
check "the CLI says what the margin is, like the step report does" \
  "$(python3 "$PC" --workspace "$DOOR_B" view | grep -c 'do not shift anything to compensate')" "1"
rm -rf "$DOOR_A" "$DOOR_B"

echo "[19] view() takes a size, a frame, or a scale — and they do not collide"
# its own workspace: canvas names and the preview directory are per-session
MODES="$(mktemp -d /tmp/astra-modes-XXXXXX)"
python3 "$PC" --workspace "$MODES" exec > "$MODES/out.txt" <<'PY'
c = new_canvas(64, 64, "#20304a")
c.rect(0, 0, 32, 32, "#f4f1de")
for label, path in (
    ("default", view()),
    ("factor", view(scale=4)),
    ("smaller", view(size=128)),
    ("frame", view(size=(200, 300))),
    ("nomargin", view(margin=0)),
):
    img = load(path)
    print("MODE", label, img.w, img.h, path.rsplit("/", 1)[-1])
PY
check "the default is padding then 512" \
  "$(grep -c 'MODE default 512 512 c1@vision.png' "$MODES/out.txt")" "1"
check "scale=4 is exactly 4x of the padded canvas" \
  "$(grep -c 'MODE factor 272 272 c1@4x-m4-tl.png' "$MODES/out.txt")" "1"
check "a smaller target is honoured" \
  "$(grep -c 'MODE smaller 128 128 c1@long128-m4-tl.png' "$MODES/out.txt")" "1"
check "an exact frame is delivered into, not distorted" \
  "$(grep -c 'MODE frame 200 300 c1@200x300-m4-tl.png' "$MODES/out.txt")" "1"
check "every variant gets its own file, so none is overwritten" \
  "$(ls "$MODES/previews" | wc -l)" "5"
check "the factor render explains its margin as well" \
  "$(grep -c 'rendered at 4x' "$MODES/out.txt")" "1"
# four of the five calls added a margin and must each explain it; the
# margin=0 one must stay silent, because it changed nothing
check "every padded render explains itself; the unpadded one stays quiet" \
  "$(grep -c '^note:' "$MODES/out.txt")" "4"
check "passing both scale and size is refused, with the reason" \
  "$(python3 "$PC" --workspace "$MODES" exec 2>&1 <<'PY' | grep -c 'not both'
c2 = new_canvas(8, 8, "#20304a")
view(scale=2, size=64)
PY
)" "1"
check "shrinking works: nearest neighbour drops pixels, it does not blur" \
  "$(python3 "$PC" --workspace "$MODES" exec <<'PY' | sed -n 's/^  SHRUNK //p'
big = new_canvas(512, 512, "#20304a")
big.rect(0, 0, 256, 256, "#f4f1de")
img = load(view(size=128, margin=0))
print("SHRUNK", img.w, img.h, img.get(0, 0) != 0)
PY
)" "128 128 True"
rm -rf "$MODES"

echo "[20] inspecting a preview does not add it to the drawing"
INSPECT="$(mktemp -d /tmp/astra-inspect-XXXXXX)"
check "loading with add=False leaves canvases() alone" \
  "$(python3 "$PC" --workspace "$INSPECT" exec <<'PY' | sed -n 's/^  BEFORE_AFTER //p'
c = new_canvas(32, 32, "#20304a")
before = len(canvases())
img = load(view(), add=False)
after = len(canvases())
print("BEFORE_AFTER", before, after, img.w, img.h)
PY
)" "1 1 512 512"
check "loading with the default still makes a layer" \
  "$(python3 "$PC" --workspace "$INSPECT" exec <<'PY' | sed -n 's/^  GREW //p'
before = len(canvases())
load(view())
print("GREW", len(canvases()) - before)
PY
)" "1"
check "an inspected file is not written into the saved state" \
  "$(python3 "$PC" --workspace "$INSPECT" exec >/dev/null <<'PY'
save("plain")
PY
python3 -c "
import json
d = json.load(open('$INSPECT/exports/plain.pixelart.json'))
names = [c['name'] for c in d['canvases']]
print('CANVASES', sorted(names))")" "CANVASES ['c1', 'c1@vision']"
rm -rf "$INSPECT"

echo "[21] the documented methods behave as their one-liners say"
# Own workspace: canvases and snapshots are per-session state.
#
# These compare *colours* (pal.rgba), never palette indices. Indices are
# relative to a palette, so two canvases with different palettes can agree on
# an index while showing different colours — which is exactly how a broken
# copy() stayed invisible: it gave the clone its own empty palette, and the
# assertion compared indices.
API="$(mktemp -d /tmp/astra-api-XXXXXX)"
python3 "$PC" --workspace "$API" exec <<'PY' > "$API/out.txt"
a = new_canvas(4, 4, "#20304a")
b = a.copy(name="clone")
b.set(0, 0, "#ff0000")
print("COPY", b.pal.rgba(b.get(0, 0)) != a.pal.rgba(a.get(0, 0)),
      b.w, b.h, a.pal is b.pal)
a.color("#f4f1de", name="moon")
print("PAL", a.copy().pal is a.pal, a.copy().color("moon") == a.color("moon"))
before = len(canvases())
load(view(a), add=False)
print("INSPECT", len(canvases()) - before)
PY
check "copy() duplicates pixels without touching the original" \
  "$(sed -n 's/^  COPY //p' "$API/out.txt")" "True 4 4 True"
check "a clone shares the drawing's palette object" \
  "$(sed -n 's/^  PAL //p' "$API/out.txt")" "True True"
check "inspecting a preview adds no canvas" \
  "$(sed -n 's/^  INSPECT //p' "$API/out.txt")" "0"
check "restore refuses an unknown snapshot, by name" \
  "$(python3 "$PC" --workspace "$API" exec 2>&1 <<'PY' | grep -c 'CanvasError: no snapshot named'
c = new_canvas(4, 4, "#20304a")
c.restore("never-saved")
PY
)" "1"
check "snapshot/restore round-trips one canvas" \
  "$(python3 "$PC" --workspace "$API" exec <<'PY' | sed -n 's/^  ROUND //p'
c = new_canvas(8, 8, "#20304a")
c.snapshot("before")
c.rect(0, 0, 8, 8, "#ff0000")
c.restore("before")
print("ROUND", c.pal.rgba(c.get(3, 3)) != (255, 0, 0, 255))
PY
)" "True"
rm -rf "$API"

echo "[22] drawing state survives a save and a reload"
RT="$(mktemp -d /tmp/astra-roundtrip-XXXXXX)"
python3 "$PC" --workspace "$RT" exec >/dev/null <<'PY'
c = new_canvas(8, 8, "#1b2a4a")
c.color("#f4f1de", name="moon")
c.circle(4, 4, 2, "moon")
c.set(0, 0, (255, 0, 0, 128))
save("rt", c)
PY
check "a saved state reloads with the same size, pixels, colours and palette" \
  "$(python3 "$PC" --workspace "$RT" exec <<'PY' | sed -n 's/^  RT //p'
c = canvas("c1")
r = load("exports/rt.pixelart.json", add=False)
same_colours = all(r.pal.rgba(r.px[i]) == c.pal.rgba(c.px[i]) for i in range(len(c.px)))
print("RT", (r.w, r.h) == (c.w, c.h), r.px == c.px, same_colours, r.pal is c.pal)
PY
)" "True True True True"
# A corrupt file has to be read by a session whose log does not save over it:
# replaying a step that called save() would rewrite the file we just broke.
BAD="$(mktemp -d /tmp/astra-badstate-XXXXXX)"
mkdir -p "$BAD/exports"
python3 -c "
import json
d = {'format': 'astra-pixel-art-v1',
     'palette': {'colors': [[0, 0, 0, 0], [27, 42, 74, 255]], 'names': {'transparent': 0}},
     'canvas': {'name': 'broken', 'width': 2, 'height': 2, 'pixels': [0, 1, 1, 7]},
     'canvases': []}
open('$BAD/exports/broken.pixelart.json', 'w').write(json.dumps(d))"
check "a state file whose pixels outrun its palette is refused" \
  "$(python3 "$PC" --workspace "$BAD" exec 2>&1 <<'PY' | grep -c 'do not match'
load("exports/broken.pixelart.json", add=False)
PY
)" "1"
check "and a state file whose pixel count is wrong is refused" \
  "$(python3 -c "
import json
d = json.load(open('$BAD/exports/broken.pixelart.json'))
d['canvas']['pixels'] = [0, 1, 1]
json.dump(d, open('$BAD/exports/short.pixelart.json', 'w'))"
python3 "$PC" --workspace "$BAD" exec 2>&1 <<'PY' | grep -c 'CanvasError: pixel count'
load("exports/short.pixelart.json", add=False)
PY
)" "1"
rm -rf "$BAD"
rm -rf "$RT"

echo "[23] the documented keyword and the stable legend"
KW="$(mktemp -d /tmp/astra-kw-XXXXXX)"
check "save(canvas=...) and view(canvas=...) accept the documented keyword" \
  "$(python3 "$PC" --workspace "$KW" exec <<'PY' | sed -n 's/^  KW //p'
a = new_canvas(8, 8, "#20304a")
b = new_canvas(8, 8, "#7ad1a1")
try:
    p1 = save("chosen", canvas=b)["png"].rsplit("/", 1)[-1]
    p2 = view(canvas=b).rsplit("/", 1)[-1]
    print("KW", p1, p2)
except TypeError as e:
    print("KW TypeError:", e)
PY
)" "chosen.png c2@vision.png"
check "flatten makes its result the current canvas" \
  "$(python3 "$PC" --workspace "$KW" exec <<'PY' | sed -n 's/^  CUR //p'
x = new_canvas(4, 4, "#20304a")
y = new_canvas(4, 4, "#7ad1a1")
print("CUR", flatten([x, y], name="both") is current())
PY
)" "True"
check "a legend letter means the same colour in every view of a drawing" \
  "$(python3 "$PC" --workspace "$KW" exec <<'PY' | sed -n 's/^  LET //p'
c = new_canvas(8, 4, "#20304a")
c.color("#f4f1de", name="fur")
c.color("#e0455a", name="collar")
c.rect(0, 0, 4, 4, "fur")
c.rect(4, 0, 4, 4, "collar")
full = c.matrix_text()
region = c.matrix_text((4, 0, 7, 3))
letter_full = [l.split()[0] for l in full.splitlines() if "collar" in l][0]
letter_region = [l.split()[0] for l in region.splitlines() if "collar" in l][0]
# the letter itself depends on the palette index, which depends on the
# session's history, so assert stability rather than a particular letter
print("LET", letter_full == letter_region, len(letter_full) == 1)
PY
)" "True True"
rm -rf "$KW"

echo "[24] where an export lands, and how bad arguments read"
SAFE="$(mktemp -d /tmp/astra-safe-XXXXXX)"
# NB: the step must be self-contained — a step that reads an environment
# variable works the first time and then fails on every replay, which is
# exactly what the skill's own "keep steps deterministic" rule warns about.
python3 "$PC" --workspace "$SAFE" exec <<PY > "$SAFE/out.txt"
c = new_canvas(8, 8, "#20304a")
print("PLAIN", save("cat")["png"])
print("SUB", save("scenes/cat")["png"])
print("EXT", save("cat.png")["png"])
print("ABS", save("$SAFE/absolute/cat")["png"])
print("ESC", save("../elsewhere/cat")["png"])
PY
check "a plain name lands in <workspace>/exports" \
  "$(sed -n 's|^  PLAIN .*\(/exports/cat\.png\)$|\1|p' "$SAFE/out.txt")" "/exports/cat.png"
check "a relative directory stays under exports" \
  "$([ -f "$SAFE/exports/scenes/cat.png" ] && [ -f "$SAFE/exports/scenes/cat@4x.png" ] && echo yes || echo no)" "yes"
check "a name that already ends in .png is not doubled" \
  "$([ -f "$SAFE/exports/cat.png" ] && [ ! -f "$SAFE/exports/cat.png.png" ] && echo yes || echo no)" "yes"
check "an absolute path is honoured, directories and all" \
  "$([ -f "$SAFE/absolute/cat.png" ] && [ -f "$SAFE/absolute/cat.pixelart.json" ] && echo yes || echo no)" "yes"
check "and a .. that leaves exports is honoured too" \
  "$([ -f "$SAFE/elsewhere/cat@4x.png" ] && echo yes || echo no)" "yes"
check "a name that is only a directory is refused" \
  "$(python3 "$PC" --workspace "$SAFE" exec 2>&1 <<'PY' | grep -c 'CanvasError: export name'
c = new_canvas(4, 4, "#20304a")
save("art/")
PY
)" "1"
check "a canvas name is an identifier, so a separator in one is refused" \
  "$(python3 "$PC" --workspace "$SAFE" exec 2>&1 <<'PY' | grep -c 'cannot be used as a file name'
new_canvas(4, 4, name="../oops")
PY
)" "1"
check "a bad --size is a message, not a traceback" \
  "$(python3 "$PC" --workspace "$SAFE" view --size 0 2>&1 | grep -c 'Traceback')" "0"
check "a bad --margin is a message too" \
  "$(python3 "$PC" --workspace "$SAFE" view --margin -3 2>&1 | grep -c 'Traceback')" "0"
check "and a bad --edges" \
  "$(python3 "$PC" --workspace "$SAFE" view --edges q 2>&1 | grep -c 'Traceback')" "0"
check "and a scale below 1, through the CLI or through save()" \
  "$(python3 "$PC" --workspace "$SAFE" view --scale 0 2>&1 | grep -c 'Traceback')" "0"
check "save(scales=(0,)) is refused with a message, not a stack" \
  "$(python3 "$PC" --workspace "$SAFE" exec 2>&1 <<'PY' | grep -c 'CanvasError: scale must be 1 or more'
c = new_canvas(4, 4, "#20304a")
save("x", c, scales=(0, 1))
PY
)" "1"
rm -rf "$SAFE"

echo "[25] path() draws curves, arcs and multi-part outlines"
pc exec > "$WORK/pathstep.txt" <<'PY'
heart = new_canvas(64, 48, name="heart")
heart.path("M32 14 C32 6 20 6 20 16 C20 26 32 32 32 42 C32 32 44 26 44 16 C44 6 32 6 32 14 Z",
           "#e0455a")
s = heart.stats()
print("filled      ", s["visible_pixels"] > 450)
print("bbox        ", s["bbox"] == [20, 9, 44, 41])
print("cleft empty ", heart.get(32, 9) == 0)
print("lobes filled", heart.get(26, 10) != 0 and heart.get(38, 10) != 0)
print("body filled ", heart.get(32, 24) != 0)

# a second subpath is a hole, not a cover: the subpaths fill as one region
ring = new_canvas(24, 24)
ring.path("M2 2 H22 V22 H2 Z M8 8 H16 V16 H8 Z", "#7ec8e3")
print("ring outside", ring.get(4, 12) != 0)
print("ring hole   ", ring.get(12, 12) == 0)
print("ring inside ", ring.get(8, 8) != 0)

# fill=False strokes open, and only Z closes it
open_t = new_canvas(24, 14)
open_t.path("M2 10 L10 2 L18 10", "#fff", fill=False)
closed_t = new_canvas(24, 14)
closed_t.path("M2 10 L10 2 L18 10 Z", "#fff", fill=False)
print("open no chord", open_t.get(10, 10) == 0)
print("Z draws chord", closed_t.get(10, 10) != 0)
print("no interior  ", open_t.get(10, 8) == 0 and closed_t.get(10, 8) == 0)

# fill=True closes an open subpath implicitly, the way SVG fills do
implicit = new_canvas(24, 14)
implicit.path("M2 10 L10 2 L18 10", "#fff")
print("fill closes  ", implicit.get(10, 6) != 0)

# the arc flags pick opposite bulges between the same two endpoints
sweep1 = new_canvas(24, 24)
sweep1.path("M2 12 A10 10 0 0 1 12 2", "#fff", fill=False)
sweep0 = new_canvas(24, 24)
sweep0.path("M2 12 A10 10 0 0 0 12 2", "#fff", fill=False)
print("arc ends     ", sweep1.get(2, 12) != 0 and sweep1.get(12, 2) != 0
                       and sweep0.get(2, 12) != 0 and sweep0.get(12, 2) != 0)
print("sweep 1 side ", sweep1.get(4, 6) != 0 and sweep1.get(11, 6) == 0)
print("sweep 0 side ", sweep0.get(11, 6) != 0 and sweep0.get(4, 6) == 0)

# relative commands, implicit repetition, unseparated numbers and erasing
rel = new_canvas(24, 24)
rel.path("m4 4 h16 v16 h-16 z", "#fff")
abs_ = new_canvas(24, 24)
abs_.path("M4 4 L20 4 L20 20 L4 20 Z", "#fff")
print("rel == abs   ", rel.px == abs_.px)
twice = new_canvas(24, 24)
twice.path("M2 2 L8 2 14 2 20 2", "#fff", fill=False)
print("repeat coords", twice.get(8, 2) != 0 and twice.get(14, 2) != 0 and twice.get(20, 2) != 0)
tight = new_canvas(24, 24)
tight.path("M4 12 l16-8", "#fff", fill=False)
print("no separators", tight.get(20, 4) != 0 and tight.get(12, 8) != 0)
hole = new_canvas(16, 16, "#224466")
hole.path("M4 4 H12 V12 H4 Z", None)
print("erases       ", hole.get(8, 8) == 0)

# a single closed subpath fills exactly the way poly() fills it
as_path = new_canvas(24, 24)
as_path.path("M2 2 L22 2 L22 22 L2 22 Z", "#0f0")
as_poly = new_canvas(24, 24)
as_poly.poly([(2, 2), (22, 2), (22, 22), (2, 22)], "#0f0")
print("poly agrees  ", as_path.px == as_poly.px)

# and it chains like every other drawing call
chained = new_canvas(24, 24)
print("chains       ", chained.path("M2 2 L20 2", "#fff", fill=False) is chained)
PY
check "a cubic heart fills, with the cleft the curves leave" \
  "$(grep -c 'cleft empty  True' "$WORK/pathstep.txt")" "1"
check "and the lobes and body are solid" \
  "$(grep -c 'lobes filled True' "$WORK/pathstep.txt")" "1"
check "and it lands on the pixels the curves describe" \
  "$(grep -c 'bbox         True' "$WORK/pathstep.txt")" "1"
check "a second subpath cuts a hole instead of painting over the first" \
  "$(grep -cE 'ring (outside|hole|inside) +True' "$WORK/pathstep.txt")" "3"
check "fill=False strokes open and only Z closes" \
  "$(grep -cE 'open no chord|Z draws chord' "$WORK/pathstep.txt")" "2"
check "fill=True closes an open subpath implicitly, like SVG" \
  "$(grep -c 'fill closes   True' "$WORK/pathstep.txt")" "1"
check "fill=False never fills the interior" \
  "$(grep -c 'no interior   True' "$WORK/pathstep.txt")" "1"
check "both arc endpoints are drawn" \
  "$(grep -c 'arc ends      True' "$WORK/pathstep.txt")" "1"
check "the sweep flag chooses between the two candidate arcs" \
  "$(grep -cE 'sweep [01] side +True' "$WORK/pathstep.txt")" "2"
check "relative commands match their absolute spelling, pixel for pixel" \
  "$(grep -c 'rel == abs    True' "$WORK/pathstep.txt")" "1"
check "extra coordinates repeat the command" \
  "$(grep -c 'repeat coords True' "$WORK/pathstep.txt")" "1"
check "numbers may run together with no separator (M1-2, .5.5)" \
  "$(grep -c 'no separators True' "$WORK/pathstep.txt")" "1"
check "path() erases when the colour is None" \
  "$(grep -c 'erases        True' "$WORK/pathstep.txt")" "1"
check "one closed subpath fills exactly the way poly() fills it" \
  "$(grep -c 'poly agrees   True' "$WORK/pathstep.txt")" "1"
check "path() returns the canvas, so it chains" \
  "$(grep -c 'chains        True' "$WORK/pathstep.txt")" "1"

# The engine-side promises: the subdivision tolerance, and the parser corners
# that a regex tokeniser gets wrong.
check "flattened curves stay inside the 0.25 px tolerance" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, math, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart import pathdata

tol = pathdata.TOLERANCE + 1e-6

def seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    d2 = dx * dx + dy * dy
    if d2 == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d2))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))

def worst_gap(nodes, truth, n=400):
    return max(min(seg_dist(truth(i / n), a, b) for a, b in zip(nodes, nodes[1:]))
               for i in range(n + 1))

def bez(p0, c1, c2, p3, t):
    u = 1 - t
    return (u**3*p0[0] + 3*u*u*t*c1[0] + 3*u*t*t*c2[0] + t**3*p3[0],
            u**3*p0[1] + 3*u*u*t*c1[1] + 3*u*t*t*c2[1] + t**3*p3[1])

def quad(p0, c, p3, t):
    u = 1 - t
    return (u*u*p0[0] + 2*u*t*c[0] + t*t*p3[0], u*u*p0[1] + 2*u*t*c[1] + t*t*p3[1])

cubic = pathdata.flatten("M0 0 C0 20 20 20 20 0")[0].points
cubic_gap = worst_gap(cubic, lambda t: bez((0, 0), (0, 20), (20, 20), (20, 0), t))
quadratic = pathdata.flatten("M0 0 Q10 20 20 0")[0].points
quad_gap = worst_gap(quadratic, lambda t: quad((0, 0), (10, 20), (20, 0), t))

arc = pathdata.flatten("M10 0 A10 10 0 0 1 0 10")[0].points
fits = [c for c in ((0, 0), (10, 10))
        if all(abs(math.hypot(p[0]-c[0], p[1]-c[1]) - 10) <= tol for p in arc)]

problems = []
if cubic_gap > tol:
    problems.append(f"cubic off by {cubic_gap:.4f}")
if quad_gap > tol:
    problems.append(f"quadratic off by {quad_gap:.4f}")
if len(fits) != 1:
    problems.append(f"arc centres that fit: {len(fits)} (want exactly 1)")
if arc[-1] != (0.0, 10.0):
    problems.append(f"arc does not land on its endpoint: {arc[-1]}")
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"
check "the smooth shorthands S and T equal spelling the control point out" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart import pathdata
pairs = [
    ("M0 0 C0 10 10 10 10 0 S20 -10 20 0", "M0 0 C0 10 10 10 10 0 C10 -10 20 -10 20 0"),
    ("M0 0 Q5 10 10 0 T20 0", "M0 0 Q5 10 10 0 Q15 -10 20 0"),
    # after a non-curve command there is nothing to mirror, so the control
    # point sits on the current point (spec 8.3.6)
    ("M0 0 L5 5 S10 0 15 5", "M0 0 L5 5 C5 5 10 0 15 5"),
]
bad = [a for a, b in pairs
       if pathdata.flatten(a)[0].points != pathdata.flatten(b)[0].points]
print("ok" if not bad else "mismatch: " + "; ".join(bad))
PY
)" "ok"
check "arc flags written without separators still parse as flags" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart import pathdata
loose = pathdata.flatten("M0 10 A10 10 0 0110 0")[0].points
spaced = pathdata.flatten("M0 10 A10 10 0 0 1 10 0")[0].points
print("ok" if loose == spaced and spaced[-1] == (10.0, 0.0) else f"{loose[:2]} vs {spaced[:2]}")
PY
)" "ok"
check "numbers run together exactly as the SVG grammar allows" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart import pathdata
# a sign or a second dot ends the previous number; "1e" is the number 1, then junk
cases = {
    "M1-2": [(1.0, -2.0)],
    "M.5.5": [(0.5, 0.5)],
    "M-.5-.5": [(-0.5, -0.5)],
    "M1e1 2": [(10.0, 2.0)],
    "M1.5e-1 2": [(0.15, 2.0)],
}
bad = [d for d, want in cases.items() if pathdata.flatten(d)[0].points != want]
print("ok" if not bad else "wrong: " + "; ".join(f"{d} -> {pathdata.flatten(d)[0].points}" for d in bad))
PY
)" "ok"
  "$(pc exec 2>&1 <<'PY' | grep -c 'expected a number at character 9'
c = new_canvas(8, 8)
c.path("M0 0 C1 1", "#fff")
PY
)" "1"
check "an unknown command names itself and the commands it knows" \
  "$(pc exec 2>&1 <<'PY' | grep -c "unsupported command 'X'"
c = new_canvas(8, 8)
c.path("M0 0 X1 1", "#fff")
PY
)" "1"
check "a path that does not start with M is refused" \
  "$(pc exec 2>&1 <<'PY' | grep -c 'must start with'
c = new_canvas(8, 8)
c.path("L1 1", "#fff")
PY
)" "1"
check "an empty path is refused with the fix in the message" \
  "$(pc exec 2>&1 <<'PY' | grep -c 'path data is empty'
c = new_canvas(8, 8)
c.path("", "#fff")
PY
)" "1"
check "a non-string is refused rather than iterated" \
  "$(pc exec 2>&1 <<'PY' | grep -c 'must be a string'
c = new_canvas(8, 8)
c.path([(0, 0), (4, 4)], "#fff")
PY
)" "1"
# The fill boundary is a documented wart rather than an accident: a polygon
# fill reaches its right-hand coordinate but stops one row short at the bottom,
# while rect() is exact on both axes. Locked here so the reference cannot drift
# from the engine, and so a future normalisation has to come past this check.
check "the fill boundary behaves the way the reference describes" \
  "$(pc exec <<'PY' | grep -c 'BOUNDARY True'
a = new_canvas(24, 24)
a.path("M6 6 H18 V18 H6 Z", "#fff")
b = new_canvas(24, 24)
b.rect(6, 6, 12, 12, "#fff")
print("BOUNDARY", a.stats()["bbox"] == [6, 6, 18, 17] and b.stats()["bbox"] == [6, 6, 17, 17])
PY
)" "1"
check "and the failed path step is not stored" \
  "$(RT="$(mktemp -d /tmp/astra-pathfail-XXXXXX)"; \
     python3 "$PC" --workspace "$RT" exec >/dev/null 2>&1 <<'PY'
c = new_canvas(8, 8)
save("keepme")
PY
     python3 "$PC" --workspace "$RT" exec >/dev/null 2>&1 <<'PY'
c = new_canvas(8, 8)
c.path("M0 0 C1 1", "#fff")
PY
     python3 "$PC" --workspace "$RT" log --summary | grep -c '^ *[0-9]\+\.'; rm -rf "$RT")" "1"

echo "[27] the shipped unit is API only"
# There is no worked example left to check, by design: the skill states the
# surface and the rules, and the drawing is the model's. The checks that used
# to verify a doc example went with the examples, and what replaces them is
# the policy itself, made checkable — a fenced block or a literal colour is
# the signature of an example creeping back in.
check "the shipped docs carry no examples" \
  "$(python3 - "$SKILLDIR" <<'PY'
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
problems = []
for name in ("SKILL.md", "references/api.md"):
    text = (root / name).read_text(encoding="utf-8")
    if "```" in text:
        problems.append(f"{name}: fenced code block")
    for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b", text):
        problems.append(f"{name}: literal colour {m.group(0)!r}")
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"
check "and neither does the cheat sheet" \
  "$(python3 - "$SKILLDIR" <<'PY'
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
import pixelart
sheet = pixelart.CHEATSHEET
found = sorted(set(re.findall(r"#[0-9a-fA-F]{3,8}\b", sheet) + re.findall(r"exec <<", sheet)))
print("ok" if not found else "found: " + ", ".join(found))
PY
)" "ok"
check "and neither does anything else in the shipped unit" \
  "$(python3 - "$SKILLDIR" <<'PY'
import re, sys, pathlib
root = pathlib.Path(sys.argv[1])
problems = []
for path in sorted(root.rglob("*")):
    if not path.is_file() or path.suffix not in (".md", ".py"):
        continue
    text = path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"#[0-9a-fA-F]{3,8}\b", text):
        problems.append(f"{path.relative_to(root)}: literal colour {m.group(0)!r}")
    if "exec <<" in text:
        problems.append(f"{path.relative_to(root)}: sample invocation")
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"
# the docs still have to describe the surface: no examples is not no content,
# and the name has to be *called* in the reference. Matching the bare word let
# c.copy() sit undocumented while every check passed, because the prose said
# "rather than a copy".
check "the reference still documents every method the engine exposes" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, pathlib, tempfile
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart.canvas import Canvas
from astra_pixelart.session import Session
doc = (root / "references" / "api.md").read_text(encoding="utf-8")
api = set(Session(tempfile.mkdtemp(prefix="astra-api-"))._api()) - {"Canvas", "CanvasError"}
methods = {n for n in dir(Canvas) if not n.startswith("_")} - {
    "to_rgba", "to_indexed", "to_dict", "from_dict", "from_image",
}
missing = sorted(n for n in (api | methods) if f"{n}(" not in doc)
print("ok" if not missing else "absent from the reference: " + ", ".join(missing))
PY
)" "ok"

echo "[28] a drawing can leave as a layered document"
# Two formats, both written with the standard library alone.  These checks
# parse the bytes back by hand — the Aseprite chunks and the OpenRaster zip —
# so the writer is never trusted to check itself.
DOCS="$(mktemp -d /tmp/astra-docs-XXXXXX)"
python3 "$PC" --workspace "$DOCS" exec > "$DOCS/export.txt" <<'PY'
sky = new_canvas(48, 32, "#1b2a4a", name="sky")
moon = new_canvas(48, 32, name="moon")
moon.circle(30, 12, 7, "#f4f1de")
moon.circle(33, 10, 6, None)
ground = new_canvas(48, 32, name="ground")
ground.dither(0, 24, 48, 8, ["#243a63", "#16213d"], pattern="checker")
nothing = new_canvas(48, 32, name="nothing")
print("ASE", export("night"))
print("ORA", export("night", format="ora"))
print("NAMED", export("already.ase"))
print("NESTED", export("deep/down/doc", format="ora"))
PY
check "export() returns the path it wrote" \
  "$(grep -c '^  ASE .*/exports/night\.ase$' "$DOCS/export.txt")" "1"
check "a name that already carries the extension is not doubled" \
  "$(grep -c '^  NAMED .*exports/already\.ase$' "$DOCS/export.txt")" "1"
check "and a nested name still lands under exports/" \
  "$(grep -c '^  NESTED .*exports/deep/down/doc\.ora$' "$DOCS/export.txt")" "1"

check "the Aseprite file is a valid document, layer for layer" \
  "$(python3 - "$DOCS" "$SKILLDIR" <<'PY'
import struct, sys, zlib, pathlib
ws = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart.session import Session

data = (ws / "exports/night.ase").read_bytes()
problems = []

size, magic, frames, width, height, depth, flags, speed, _, _ = struct.unpack_from("<IHHHHHIHII", data, 0)
if magic != 0xA5E0:
    problems.append(f"magic is {magic:#x}")
if (frames, width, height, depth) != (1, 48, 32, 32):
    problems.append(f"header says {frames} frame(s) {width}x{height} depth {depth}")
if size != len(data):
    problems.append(f"header size {size} != file size {len(data)}")
if not flags & 1:
    problems.append("layer opacity is not marked valid")

frame_bytes, frame_magic, _, duration, chunk_count = struct.unpack_from("<IHHH2xI", data, 128)
if frame_magic != 0xF1FA:
    problems.append(f"frame magic is {frame_magic:#x}")
if 128 + frame_bytes != len(data):
    problems.append(f"frame says {frame_bytes} bytes, file has {len(data) - 128}")

pos, chunks = 128 + 16, []
for _ in range(chunk_count):
    chunk_size, chunk_type = struct.unpack_from("<IH", data, pos)
    chunks.append((chunk_type, data[pos + 6:pos + chunk_size]))
    pos += chunk_size
if pos != len(data):
    problems.append("chunks do not add up to the file size")

names, cels, palette = [], {}, None
for chunk_type, body in chunks:
    if chunk_type == 0x2004:
        layer_flags, kind, level, _, _, blend, opacity = struct.unpack_from("<HHHHHHB3x", body, 0)
        length = struct.unpack_from("<H", body, 16)[0]
        names.append(body[18:18 + length].decode("utf-8"))
        if not layer_flags & 1:
            problems.append(f"layer {names[-1]!r} is not visible")
        if (kind, level, blend, opacity) != (0, 0, 0, 255):
            problems.append(f"layer {names[-1]!r} type/level/blend/opacity {kind}/{level}/{blend}/{opacity}")
    elif chunk_type == 0x2005:
        index, x, y, opacity, cel_type, _z = struct.unpack_from("<HhhBHh5x", body, 0)
        w, h = struct.unpack_from("<HH", body, 16)
        if cel_type != 2:
            problems.append(f"cel type {cel_type} is not a compressed image")
        cels[index] = (x, y, w, h, zlib.decompress(body[20:]))
    elif chunk_type == 0x2019:
        total = struct.unpack_from("<I", body, 0)[0]
        at, entries = 20, []
        while len(entries) < total:
            entry_flags, r, g, b, a = struct.unpack_from("<HBBBB", body, at)
            at += 6
            name = ""
            if entry_flags & 1:
                length = struct.unpack_from("<H", body, at)[0]
                name = body[at + 2:at + 2 + length].decode("utf-8")
                at += 2 + length
            entries.append((name, (r, g, b, a)))
        palette = entries

session = Session(ws)
session.replay()
canvases = session.canvases
if names != [c.name for c in canvases]:
    problems.append(f"layers {names} != canvases {[c.name for c in canvases]}")
if len(palette) != len(session.palette.to_list()):
    problems.append(f"palette has {len(palette)} entries, drawing has {len(session.palette.to_list())}")

for index, canvas in enumerate(canvases):
    w, h, rgba = canvas.to_rgba()
    cel = cels.get(index)
    if cel is None:
        if canvas.stats()["visible_pixels"]:
            problems.append(f"layer {index} has pixels but no cel")
        continue
    x, y, cw, ch, payload = cel
    expect = bytearray()
    for row in range(y, y + ch):
        start = (row * w + x) * 4
        expect += rgba[start:start + cw * 4]
    if payload != bytes(expect):
        problems.append(f"layer {index} cel pixels differ")
    if len(payload) != cw * ch * 4:
        problems.append(f"layer {index} cel is {len(payload)} bytes for {cw}x{ch}")
if 3 in cels:
    problems.append("the empty layer should have no cel")
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"
check "and writing it twice gives the same bytes" \
  "$(python3 "$PC" --workspace "$DOCS" exec <<'PY' | sed -n 's/^  SAME //p'
first = open(export("twice"), "rb").read()
second = open(export("twice"), "rb").read()
print("SAME", first == second)
PY
)" "True"

check "the OpenRaster file follows the spec and keeps the layer order" \
  "$(python3 - "$DOCS" "$SKILLDIR" <<'PY'
import sys, zipfile, pathlib, xml.etree.ElementTree as ET
ws = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(root / "scripts"))
sys.path.insert(0, str(root.parent.parent / "tests"))
from png_probe import read_png_stdlib
from astra_pixelart.session import Session
from astra_pixelart.canvas import flatten

problems = []
ora = ws / "exports/night.ora"
with zipfile.ZipFile(ora) as archive:
    names = archive.namelist()
    if names[0] != "mimetype":
        problems.append(f"first entry is {names[0]!r}")
    info = archive.getinfo("mimetype")
    if info.compress_type != zipfile.ZIP_STORED:
        problems.append("mimetype is compressed")
    if archive.read("mimetype") != b"image/openraster":
        problems.append("mimetype content is wrong")

    image = ET.fromstring(archive.read("stack.xml"))
    if (image.get("w"), image.get("h")) != ("48", "32"):
        problems.append(f"image says {image.get('w')}x{image.get('h')}")
    session = Session(ws)
    session.replay()
    canvases = session.canvases
    # the first element in a stack is the uppermost one, so the file lists
    # the drawing's layers in reverse
    listed = [layer.get("name") for layer in image.findall(".//layer")]
    if listed != [c.name for c in canvases][::-1]:
        problems.append(f"stack lists {listed}, expected {[c.name for c in canvases][::-1]}")
    for layer in image.findall(".//layer"):
        if layer.get("src") not in names:
            problems.append(f"{layer.get('src')} is missing from the zip")
        if layer.get("visibility") != "visible" or layer.get("opacity") != "1":
            problems.append(f"layer {layer.get('name')!r} is not plainly visible")

    # every layer PNG has to be that canvas, pixel for pixel
    for index, layer in enumerate(image.findall(".//layer")):
        wanted = {c.name: c for c in canvases}[layer.get("name")]
        path = ws / "exports" / "_layer.png"
        path.write_bytes(archive.read(layer.get("src")))
        pw, ph, at = read_png_stdlib(path)
        if (pw, ph) != (48, 32):
            problems.append(f"{layer.get('name')!r} is {pw}x{ph}")
        mismatch = 0
        for y in range(ph):
            for x in range(pw):
                get = wanted.get(x, y)
                if tuple(at(x, y)) != tuple(wanted.pal.rgba(get)):
                    mismatch += 1
        if mismatch:
            problems.append(f"{layer.get('name')!r} differs in {mismatch} pixels")
        path.unlink()

    # the merged image is the whole drawing composited
    merged_path = ws / "exports" / "_merged.png"
    merged_path.write_bytes(archive.read("mergedimage.png"))
    mw, mh, mat = read_png_stdlib(merged_path)
    expected = flatten(canvases)
    if (mw, mh) != (48, 32):
        problems.append(f"merged image is {mw}x{mh}")
    mismatch = sum(1 for y in range(mh) for x in range(mw)
                   if tuple(mat(x, y)) != tuple(expected.pal.rgba(expected.get(x, y))))
    if mismatch:
        problems.append(f"merged image differs in {mismatch} pixels")
    merged_path.unlink()

    thumb_path = ws / "exports" / "_thumb.png"
    thumb_path.write_bytes(archive.read("Thumbnails/thumbnail.png"))
    tw, th, _ = read_png_stdlib(thumb_path)
    if max(tw, th) > 256:
        problems.append(f"thumbnail is {tw}x{th}")
    thumb_path.unlink()
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"

DOCFAIL="$(mktemp -d /tmp/astra-docfail-XXXXXX)"
check "layers of different sizes are refused, naming the canvas" \
  "$(python3 "$PC" --workspace "$DOCFAIL" exec 2>&1 <<'PY' | grep -c 'all layers must be the same size; c2 is 9x9'
a = new_canvas(8, 8)
b = new_canvas(9, 9)
export("bad")
PY
)" "1"
check "an unknown format names the ones that exist" \
  "$(python3 "$PC" --workspace "$DOCFAIL" exec 2>&1 <<'PY' | grep -c 'unknown format.*ase'
c = new_canvas(8, 8)
export("x", format="psd")
PY
)" "1"
check "exporting before there is a canvas says so" \
  "$(python3 "$PC" --workspace "$DOCFAIL" exec 2>&1 <<'PY' | grep -c 'CanvasError: no canvas yet'
export("x")
PY
)" "1"
check "and a failed export leaves nothing behind" \
  "$([ -e "$DOCFAIL/exports/bad.ase" ] && echo yes || echo no)" "no"
rm -rf "$DOCFAIL" "$DOCS"

echo "[29] four fonts, and three of them can write Chinese"
# The CJK tables are bitmaps built offline from OFL releases, so these checks
# are about each table being the font it claims to be (the right cell, the
# right way up, not blank), about the engine's metrics on top of them, and
# about the licence that has to travel with them.
check "all three tables are the documented size and content" \
  "$(python3 - "$SKILLDIR" <<'PY'
import hashlib, sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart import fontfusion

# what the reference promises, size by size
expected = {
    "8px":  (8, 8, 7, 27977, 14717, 13),
    "10px": (10, 10, 9, 24832, 10560, 18),
    "12px": (12, 12, 10, 36533, 19214, 23),
}
docstring = fontfusion.__doc__ or ""
problems = []
for size, (cw, ch, ascent, glyphs, hanzi, stride) in expected.items():
    font = fontfusion.get(size)
    if (font.cell_w, font.cell_h, font.ascent) != (cw, ch, ascent):
        problems.append(f"{size}: cell {font.cell_w}x{font.cell_h} ascent {font.ascent}")
    if font.payload + 5 != stride:
        problems.append(f"{size}: {stride} bytes per record, not {font.payload + 5}")
    blob = font._path.read_bytes()
    if len(blob) != glyphs * stride:
        problems.append(f"{size}: {len(blob)} bytes, expected {glyphs * stride}")
    if font.count() != glyphs:
        problems.append(f"{size}: reader counts {font.count()}")
    if hashlib.sha256(blob).hexdigest() not in docstring:
        problems.append(f"{size}: sha256 is not the one in the reader's docstring")
    if sum(1 for cp in font.iter_codepoints() if 0x4E00 <= cp <= 0x9FFF) != hanzi:
        problems.append(f"{size}: hanzi count is not the documented {hanzi}")
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"
check "the sizes really do differ, so the choice is a real one" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart import fontfusion
counts = {s: fontfusion.get(s).count() for s in fontfusion.METRICS}
hanzi = {s: sum(1 for cp in fontfusion.get(s).iter_codepoints() if 0x4E00 <= cp <= 0x9FFF)
         for s in fontfusion.METRICS}
problems = []
if len(set(counts.values())) != 3:
    problems.append(f"two sizes hold the same number of glyphs: {counts}")
if not hanzi["12px"] > hanzi["8px"] > hanzi["10px"]:
    problems.append(f"the hanzi counts are not 12 > 8 > 10: {hanzi}")
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"
check "every size's glyphs are the right way up and centred" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart import fontfusion

def ink(font, cp, row):
    return bin(font.rows(cp)[row]).count("1")

problems = []
for size in fontfusion.METRICS:
    f = fontfusion.get(size)
    t_rows = [r for r, b in enumerate(f.rows(ord("T"))) if b]
    l_rows = [r for r, b in enumerate(f.rows(ord("L"))) if b]
    # a T is widest at its top row and an L at its bottom row; a mirrored
    # table swaps those, a row-shifted one loses the wide row
    if ink(f, ord("T"), t_rows[0]) <= ink(f, ord("T"), t_rows[-1]):
        problems.append(f"{size}: T is not wider at the top")
    if ink(f, ord("L"), l_rows[-1]) <= ink(f, ord("L"), l_rows[0]):
        problems.append(f"{size}: L is not wider at the bottom")
    # 中 has a vertical stem down the middle of its cell.  Reading the BDF
    # rows from the wrong bit shifts every glyph sideways, which is exactly
    # how the first version of the converter was wrong
    want = (f.cell_w - 2) // 2
    stem = [k for k in range(f.cell_w) if f.rows(ord("中"))[1] >> (f.cell_w - 1 - k) & 1]
    if stem != [want]:
        problems.append(f"{size}: the stem of 中 is at {stem}, not [{want}]")
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"
check "and they hold real glyphs, not blanks or blocks" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart import fontfusion

problems = []
for size in fontfusion.METRICS:
    f = fontfusion.get(size)
    for ch in "中文字你好人山水":
        glyph = f.rows(ord(ch))
        lit = sum(bin(bits).count("1") for bits in glyph)
        cells = f.cell_w * f.cell_h
        if not 0.05 * cells <= lit <= 0.75 * cells:
            problems.append(f"{size}: {ch} lights {lit} of {cells} pixels")
        if f.advance(ord(ch)) != f.cell_w:
            problems.append(f"{size}: {ch} advances {f.advance(ord(ch))}")
    if f.advance(ord("A")) != f.cell_w // 2:
        problems.append(f"{size}: A advances {f.advance(ord('A'))}")
    if f.has(0x1F600):
        problems.append(f"{size}: an emoji is present")
    # neither table carries U+3031, for two different reasons: the 12px and
    # 10px releases have it and the converter skips it (it does not fit their
    # cell), while the 8px release does not have it at all
    if f.has(0x3031):
        problems.append(f"{size}: U+3031 is present, but no size carries it")
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"
check "no size is read until something is drawn from it" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / "scripts"))
from astra_pixelart import fontfusion
before = {s: fontfusion.get(s).loaded() for s in fontfusion.METRICS}
fontfusion.get("8px").rows(ord("A"))
after = {s: fontfusion.get(s).loaded() for s in fontfusion.METRICS}
ok = (not any(before.values())) and after["8px"] and not after["10px"] and not after["12px"]
print("ok" if ok else f"before={before} after={after}")
PY
)" "ok"

TEXT="$(mktemp -d /tmp/astra-font-XXXXXX)"
python3 "$PC" --workspace "$TEXT" exec > "$TEXT/out.txt" <<'PY'
def lit_rows(canvas, x0, x1):
    return sorted({y for y in range(canvas.h) for x in range(x0, x1) if canvas.get(x, y)})

def span(canvas):
    cols = [x for x in range(canvas.w) if any(canvas.get(x, y) for y in range(canvas.h))]
    return cols[0], max(cols)

small = new_canvas(64, 16, name="small")
small.text(0, 2, "ABC", "#fff")
print("5x7 rows", lit_rows(small, 0, 20))

for size in ("8px", "10px", "12px"):
    c = new_canvas(64, 18, name=size)
    c.text(0, 2, "中文", "#fff", font=size)
    print(size, "rows", lit_rows(c, 0, 26), "span", span(c))

    def drawn(text):
        canvas = new_canvas(64, 18, name=size + text)
        canvas.text(0, 2, text, "#fff", font=size)
        return span(canvas)

    one_cjk, two_cjk = drawn("中"), drawn("中中")
    one_latin, two_latin = drawn("A"), drawn("AA")
    cell = {"8px": 8, "10px": 10, "12px": 12}[size]
    print(size, "two full-width glyphs advance one cell each",
          two_cjk[1] == one_cjk[1] + cell)
    print(size, "two half-width glyphs advance half a cell each",
          two_latin[1] == one_latin[1] + cell // 2)

auto = new_canvas(64, 18, name="auto")
auto.text(0, 2, "中 A", "#fff")
explicit = new_canvas(64, 18, name="explicit")
explicit.text(0, 2, "中 A", "#fff", font="12px")
print("auto picks 12px for a mixed line", auto.px == explicit.px)

forced = new_canvas(64, 18, name="forced")
forced.text(0, 2, "中", "#fff", font="5x7")
print("5x7 on CJK draws a filled cell", forced.stats()["visible_pixels"] == 5 * 7)

missing = new_canvas(64, 18, name="missing")
missing.text(0, 2, "\u3031", "#fff")
print("a glyph the 12px table skipped draws a filled cell",
      missing.stats()["visible_pixels"] == 12 * 10)
print("and the line keeps its length", span(missing))
PY
check "the 5x7 font still occupies its own seven rows" \
  "$(grep -c '5x7 rows \[2, 3, 4, 5, 6, 7, 8\]' "$TEXT/out.txt")" "1"
check "each CJK size fills its own cell below the first row" \
  "$(grep -c '8px rows \[3, 4, 5, 6, 7, 8, 9\]' "$TEXT/out.txt")" "1"
check "the 10px cell is ten rows" \
  "$(grep -c '10px rows \[3, 4, 5, 6, 7, 8, 9, 10, 11\]' "$TEXT/out.txt")" "1"
check "and the 12px cell twelve" \
  "$(grep -c '12px rows \[3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13\]' "$TEXT/out.txt")" "1"
check "each full-width glyph advances by its cell, measured in pixels" \
  "$(grep -c 'two full-width glyphs advance one cell each True' "$TEXT/out.txt")" "3"
check "and each half-width glyph by half of it" \
  "$(grep -c 'two half-width glyphs advance half a cell each True' "$TEXT/out.txt")" "3"
check "font=None picks the 12px font for a line with CJK in it" \
  "$(grep -c 'auto picks 12px for a mixed line True' "$TEXT/out.txt")" "1"
check "asking for 5x7 on CJK gets the filled-cell placeholder" \
  "$(grep -c '5x7 on CJK draws a filled cell True' "$TEXT/out.txt")" "1"
check "a character the 12px table skipped draws a filled cell" \
  "$(grep -c 'a glyph the 12px table skipped draws a filled cell True' "$TEXT/out.txt")" "1"
check "and the fallback still advances by a cell" \
  "$(grep -c 'and the line keeps its length (0, 11)' "$TEXT/out.txt")" "1"
check "an unknown font names every one it has" \
  "$(python3 "$PC" --workspace "$TEXT" exec 2>&1 <<'PY' | grep -c "unknown font 'big'.*5x7.*8px.*10px.*12px"
c = new_canvas(8, 8, background="#000")
c.text(0, 0, "x", "#fff", font="big")
PY
)" "1"
rm -rf "$TEXT"

check "the font's licence travels with the data" \
  "$(python3 - "$SKILLDIR" <<'PY'
import sys, pathlib
root = pathlib.Path(sys.argv[1])
problems = []
licence = root / "scripts/astra_pixelart/FUSION-PIXEL-OFL-1.1.txt"
if not licence.is_file():
    problems.append("the OFL text is not beside the data")
else:
    text = licence.read_text(encoding="utf-8")
    if "SIL OPEN FONT LICENSE" not in text.upper():
        problems.append("the OFL text does not contain the licence")
    if "TakWolf" not in text:
        problems.append("the OFL text does not name the copyright holder")
notice = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
for wanted in ("Fusion Pixel Font", "OFL-1.1", "TakWolf", "fontfusion8.bin",
               "fontfusion10.bin", "fontfusion12.bin"):
    if wanted not in notice:
        problems.append(f"the notice does not mention {wanted}")
print("ok" if not problems else "; ".join(problems))
PY
)" "ok"

FINISHED=1
