"""Render terminal-style PNG screenshots from captured command output."""
import sys
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "C:/Windows/Fonts/consola.ttf"
BG = (30, 30, 30)
TITLEBAR = (45, 45, 48)
TITLE_FG = (200, 200, 200)
PROMPT = (87, 200, 77)        # green
CMD_FG = (220, 220, 220)
OUT_FG = (230, 230, 230)
DIM = (140, 140, 140)


def wrap_line(line, max_chars):
    if len(line) <= max_chars:
        return [line]
    out = []
    while len(line) > max_chars:
        out.append(line[:max_chars])
        line = line[max_chars:]
    out.append(line)
    return out


def render(title, command, output, out_path, width=1400, max_lines=None):
    title_font = ImageFont.truetype(FONT_PATH, 16)
    header_font = ImageFont.truetype(FONT_PATH, 17)
    body_font = ImageFont.truetype(FONT_PATH, 14)

    char_w = body_font.getlength("M")
    pad_x = 18
    usable = width - 2 * pad_x
    max_chars = int(usable // char_w)

    # Wrap output
    out_lines = []
    for raw in output.splitlines() or [""]:
        # expand tabs
        raw = raw.replace("\t", "    ")
        out_lines.extend(wrap_line(raw, max_chars))

    if max_lines and len(out_lines) > max_lines:
        out_lines = out_lines[:max_lines] + [f"... (truncated, {len(out_lines)-max_lines} more lines)"]

    cmd_lines = wrap_line(command, max_chars - 2)

    # Heights
    title_h = 32
    line_h = 18
    header_h = 26
    top_pad = 12
    bottom_pad = 16
    body_h = top_pad + header_h * len(cmd_lines) + 10 + line_h * len(out_lines) + bottom_pad
    height = title_h + body_h

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    # Title bar
    draw.rectangle([0, 0, width, title_h], fill=TITLEBAR)
    # Traffic lights
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        cx = 14 + i * 18
        draw.ellipse([cx, 10, cx + 12, 22], fill=c)
    draw.text((80, 8), title, font=title_font, fill=TITLE_FG)

    # Prompt + command
    y = title_h + top_pad
    prompt_text = "PS> "
    for i, cl in enumerate(cmd_lines):
        if i == 0:
            draw.text((pad_x, y), prompt_text, font=header_font, fill=PROMPT)
            pw = header_font.getlength(prompt_text)
            draw.text((pad_x + pw, y), cl, font=header_font, fill=CMD_FG)
        else:
            draw.text((pad_x + header_font.getlength(prompt_text), y), cl, font=header_font, fill=CMD_FG)
        y += header_h
    y += 6

    # Output
    for ol in out_lines:
        draw.text((pad_x, y), ol, font=body_font, fill=OUT_FG)
        y += line_h

    img.save(out_path, optimize=True)
    print(f"wrote {out_path} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    # Expect JSON spec on stdin: {title, command, output_file or output, out}
    spec = json.loads(sys.stdin.read())
    output = spec.get("output")
    if output is None and spec.get("output_file"):
        output = Path(spec["output_file"]).read_text(encoding="utf-8", errors="replace")
    render(spec["title"], spec["command"], output or "", spec["out"],
           width=spec.get("width", 1400), max_lines=spec.get("max_lines"))
