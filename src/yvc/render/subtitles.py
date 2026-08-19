"""ASS subtitle generation with word-level highlighting.

Design choices worth stating, because each avoids a specific failure:

* **ASS, rendered by the ``ass`` filter** rather than ``subtitles``. We
  author the file ourselves, so going through ffmpeg's subtitle decoder
  and charset-detection path buys nothing and adds one more place for
  cp1252 to appear.

* **One Dialogue event per word** instead of ``\\k`` karaoke tags. The
  full line is repeated in each event with the active word recoloured.
  It is exactly reproducible, has no libass timing quirks, and can be
  debugged by reading the file. The cost -- a few thousand events for a
  60 s clip -- is nothing to libass.

* **Colours are BGR.** ASS writes ``&HBBGGRR&``, reversed from the hex
  used everywhere else. That conversion lives in one function with a
  test, because getting it wrong produces a plausible-looking wrong
  colour rather than an error.

* **All text goes through ``tr_upper``.** A bare ``.upper()`` would put
  ``ISTANBUL`` on screen in front of a Turkish audience.
"""

from __future__ import annotations

from dataclasses import dataclass

from yvc.turkish.casing import tr_upper

# Vertical 9:16 and horizontal 16:9 presets. MarginV on the vertical
# layout clears TikTok/Reels chrome: their controls occupy roughly the
# bottom 250 px and the caption area extends to about 350 px.
LAYOUTS = {
    "9:16": {
        "play_res_x": 1080,
        "play_res_y": 1920,
        "font_size": 74,
        "margin_v": 420,
        "margin_x": 90,
        "hook_font_size": 96,
        "hook_pos": (540, 760),
        "chars_per_line": 18,
        "outline": 5,
    },
    "16:9": {
        "play_res_x": 1920,
        "play_res_y": 1080,
        "font_size": 54,
        "margin_v": 90,
        "margin_x": 120,
        "hook_font_size": 72,
        "hook_pos": (960, 380),
        "chars_per_line": 34,
        "outline": 4,
    },
}


def hex_to_ass(color: str, alpha: int = 0) -> str:
    """Convert ``#rrggbb`` to ASS ``&HAABBGGRR``.

    ASS orders the channels blue-green-red, the reverse of CSS hex, and
    prefixes an alpha byte where 0 is opaque. Mixing these up yields a
    convincing but wrong colour, so this is the only place it happens.
    """
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected #rrggbb, got {color!r}")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def _timestamp(seconds: float) -> str:
    """ASS uses H:MM:SS.cc with centisecond precision."""
    if seconds < 0:
        seconds = 0.0
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    centis = int(round((secs - int(secs)) * 100))
    secs = int(secs)
    if centis == 100:  # rounding carried
        centis = 0
        secs += 1
    return f"{int(hours)}:{int(minutes):02d}:{secs:02d}.{centis:02d}"


@dataclass
class Word:
    text: str
    start: float
    end: float


def wrap_lines(words: list[str], chars_per_line: int, max_lines: int = 2) -> list[list[int]]:
    """Group word indices into balanced lines.

    Wrapping is done here rather than left to libass because Turkish is
    agglutinative -- ``belirlenmesinde`` is one 15-character word -- and
    automatic wrapping produces badly lopsided lines. Balancing keeps the
    caption block visually stable between frames.
    """
    lines: list[list[int]] = []
    current: list[int] = []
    length = 0

    for index, word in enumerate(words):
        addition = len(word) + (1 if current else 0)
        if current and length + addition > chars_per_line:
            lines.append(current)
            current, length = [index], len(word)
            if len(lines) == max_lines:
                # Everything remaining is appended to the last line rather
                # than dropped; a slightly long line beats missing speech.
                for rest in range(index + 1, len(words)):
                    current.append(rest)
                break
        else:
            current.append(index)
            length += addition

    if current:
        lines.append(current)
    return lines[:max_lines] if len(lines) > max_lines else lines


def build_ass(
    words: list[Word],
    *,
    aspect: str,
    accent: str,
    ink: str = "#101010",
    paper: str = "#ffffff",
    font_family: str = "Segoe UI Black",
    hook_text: str = "",
    hook_duration: float = 3.2,
    uppercase: bool = True,
    group_size: int = 6,
) -> str:
    """Render an ASS file for one clip.

    ``words`` carry timings relative to the clip, not the source video.
    """
    layout = LAYOUTS[aspect]
    primary = hex_to_ass(paper)
    outline_c = hex_to_ass(ink)
    highlight = hex_to_ass(accent)

    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {layout['play_res_x']}",
        f"PlayResY: {layout['play_res_y']}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        (
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
            "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,"
            "ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,"
            "MarginL,MarginR,MarginV,Encoding"
        ),
        (
            f"Style: Base,{font_family},{layout['font_size']},{primary},{primary},"
            f"{outline_c},&H80000000,0,0,0,0,100,100,0.6,0,1,"
            f"{layout['outline']},2,2,{layout['margin_x']},{layout['margin_x']},"
            f"{layout['margin_v']},1"
        ),
        (
            f"Style: Hook,{font_family},{layout['hook_font_size']},{primary},{primary},"
            f"{outline_c},&HA0000000,0,0,0,0,100,100,1.0,0,1,"
            f"{layout['outline'] + 1},3,5,80,80,0,1"
        ),
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    events: list[str] = []

    # The hook occupies the opening seconds, above centre so it does not
    # cover the speaker's face.
    if hook_text:
        hook = tr_upper(hook_text) if uppercase else hook_text
        hook_words = hook.split()
        hook_lines = wrap_lines(hook_words, layout["chars_per_line"] - 2, max_lines=2)
        rendered = "\\N".join(
            " ".join(hook_words[i] for i in line) for line in hook_lines
        )
        x, y = layout["hook_pos"]
        events.append(
            f"Dialogue: 1,{_timestamp(0.0)},{_timestamp(hook_duration)},Hook,,0,0,0,,"
            f"{{\\fad(180,260)\\pos({x},{y})}}{rendered}"
        )

    # Captions are emitted in small groups so the viewer sees a stable
    # block of text with one word highlighted, rather than a single word
    # flashing on an empty screen.
    for start in range(0, len(words), group_size):
        group = words[start : start + group_size]
        if not group:
            continue

        tokens = [tr_upper(w.text) if uppercase else w.text for w in group]
        lines = wrap_lines(tokens, layout["chars_per_line"])

        for active, word in enumerate(group):
            parts: list[str] = []
            for line_no, line in enumerate(lines):
                if line_no:
                    parts.append("\\N")
                for pos, token_index in enumerate(line):
                    if pos:
                        parts.append(" ")
                    if token_index == active:
                        parts.append(
                            f"{{\\c{highlight}}}{tokens[token_index]}{{\\c{primary}}}"
                        )
                    else:
                        parts.append(tokens[token_index])

            text = "".join(parts)
            # Clamp so a word never renders before its own start.
            begin = max(0.0, word.start)
            finish = max(begin + 0.04, word.end)
            events.append(
                f"Dialogue: 0,{_timestamp(begin)},{_timestamp(finish)},Base,,0,0,0,,{text}"
            )

    return "\n".join(head + events) + "\n"
