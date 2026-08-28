"""Which flag carries the filtergraph file changed under a working build.

`-filter_complex_script` was removed in ffmpeg 8.0 in favour of the
generic `-/filter_complex` added in 7.0. Both strings below were printed
by a real ffmpeg on this machine.
"""

from __future__ import annotations

from yvc.stages.s08_render import _filtergraph_option_from_probe


def test_a_build_that_rejects_the_new_flag_gets_the_old_one():
    ffmpeg_7 = (
        "Unrecognized option '/filter_complex'.\n"
        "Error splitting the argument list: Option not found\n"
    )

    assert _filtergraph_option_from_probe(ffmpeg_7) == "-filter_complex_script"


def test_a_build_that_only_complains_about_the_file_supports_the_new_flag():
    ffmpeg_9 = (
        "Error opening file __yvc_option_probe__.\n"
        "Error reading the value for option 'filter_complex' from file: "
        "__yvc_option_probe__\n"
        "Error parsing global options: Invalid argument\n"
    )

    assert _filtergraph_option_from_probe(ffmpeg_9) == "-/filter_complex"
