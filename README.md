# Astra Pixel Art

An agent skill for drawing pixel art. The agent writes short Python
snippets, the drawing accumulates, and it exports PNG files at **1× and 4×**.

Nothing to install: no virtualenv, no packages, no server process. The engine
is standard-library Python, so it runs in sandboxes that have no network and
no way to install anything.

## Install

```bash
git clone https://github.com/Ori-Replication/astra-pixel-art-skill.git
mkdir -p ~/.agents/skills
cp -r astra-pixel-art-skill/skills/astra-pixel-art ~/.agents/skills/
# Claude Code does not read ~/.agents/skills; give it a link
mkdir -p ~/.claude/skills
ln -sfn ~/.agents/skills/astra-pixel-art ~/.claude/skills/astra-pixel-art
```

`~/.agents/skills/` is read by Codex, Cursor, Gemini CLI, Copilot, OpenCode,
Amp, Windsurf, Zed and DSH. Requires Python 3.9+ and nothing else; Pillow is
optional, only for importing a JPEG or GIF to pixelate.

Then just ask for pixel art — the skill triggers on its own.

## What you get

```bash
$ PA=~/.agents/skills/astra-pixel-art/scripts/pixelart.py
$ python3 "$PA" exec <<'PY'
sky = new_canvas(64, 64, "#1b2a4a")
moon = new_canvas(64, 64)
moon.circle(30, 22, 10, "#f4f1de")
moon.circle(35, 19, 9, None)        # None erases -> crescent
final = flatten([sky, moon], name="night")
save("night")
PY
step 1 ok | canvases: c1 64x64, c2 64x64, night 64x64*
files:
  .../exports/night.png
  .../exports/night@4x.png
  .../exports/night.pixelart.json
```

A PNG at the drawing's real size, a 4× copy to look at, and a state file that
reloads. `view()` renders a preview for a vision model to read.

## Which model

**GPT-6 Astra** is the recommendation. **Gemini 3.8 Flash** is a step down,
and still produces usable pixel art.

## What's inside

- **Canvases are layers.** Draw each element on its own canvas, then
  `flatten([...])` them. Export is `save("name")` — 1× and 4× PNG.
- **Pixels, rectangles, circles, ellipses, polygons, lines, flood fill,
  mirror, shift, dithering, and a built-in 5×7 font.** Curves, arcs, rounded
  corners and multi-part outlines come from one call that takes SVG path
  data — `path("M8 24 C8 8 40 8 40 24 A6 6 0 0 1 34 30 Z", "#e0455a")` —
  with no SVG renderer and nothing to install. `outline()` rims a sprite so
  it reads on any background. Alpha compositing across layers, PNG import,
  and an indexed-PNG export mode. `pixelart help` lists it all.
- **The state is a log, not a process.** Each step is appended to
  `pixelart/session.jsonl` and replayed to rebuild the drawing, so variables
  and canvases survive between commands with no daemon to keep alive: a crash
  loses nothing, and `undo` is durable.
- **A failed step changes nothing.** It is never appended, so it cannot leave
  half a shape behind.

The reasoning behind the design, and what was rejected, is in
[AGENTS.md](AGENTS.md).

## Development

```bash
bash tests/verify.sh
```

The checks run the real CLI with the system `python3`, no virtualenv and
nothing installed. Exports are decoded with an independent reader, so the
encoder never checks itself.

## Licence

MIT — see [LICENSE](LICENSE). The 5×7 bitmap font is the one borrowed part:
third-party MIT code, with the notice in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Everything else — the canvas
engine, the PNG codec, the session log, the preview handling — was written
for this project.
