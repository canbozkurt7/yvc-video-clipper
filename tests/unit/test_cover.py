"""Cover-frame selection.

The stage this replaces had no scoring at all: it grabbed the frame at
15% of the clip, from the *rendered* video, so thumbnails shipped with a
caption fragment burnt into them and the speaker mid-blink.

Each signal here was added because a real cover failed on it, so the
tests pin the specific failure rather than the general idea.
"""

from __future__ import annotations

import math
import wave

import numpy as np
import pytest

from yvc.render.cover import (
    GATES,
    WEIGHTS,
    Candidate,
    audio_rms,
    cover_ass,
    crop_box,
    eye_openness,
    frontality,
    gate_penalty,
    mouth_clarity,
    normalise,
    pick,
    score_all,
    thirds,
    wrap,
)


def candidate(**raw) -> Candidate:
    base = {key: 0.5 for key in WEIGHTS}
    base.update(raw)
    return Candidate(t=raw.pop("t", 1.0), face=(10, 10, 40, 40), raw=base)


# --- weights are a distribution --------------------------------------


def test_weights_sum_to_one():
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


# --- eyes: the blink that shipped ------------------------------------


def test_open_eye_scores_above_closed_eye():
    """An open eye has a pupil: dark and high-contrast. A closed eye is
    an eyelid: skin-toned and flat."""
    gray = np.full((60, 60), 180, dtype=np.uint8)
    open_eye = gray.copy()
    open_eye[28:32, 28:32] = 15  # pupil
    closed = eye_openness(gray, [(30, 30)], face_w=40)
    opened = eye_openness(open_eye, [(30, 30)], face_w=40)
    assert opened > closed


def test_the_worse_eye_decides():
    """One eye open and one closed still looks wrong, so the score must
    not average the two into something acceptable."""
    gray = np.full((60, 120), 180, dtype=np.uint8)
    gray[28:32, 28:32] = 15  # only the left eye is open
    both = eye_openness(gray, [(30, 30), (90, 30)], face_w=40)
    just_open = eye_openness(gray, [(30, 30)], face_w=40)
    assert both < just_open


def test_eye_openness_without_landmarks_is_zero():
    assert eye_openness(np.zeros((10, 10), dtype=np.uint8), [], face_w=40) == 0.0


# --- frontality: the profile shot ------------------------------------


def test_nose_between_the_eyes_is_frontal():
    assert frontality([(10, 0), (30, 0), (20, 5)]) == pytest.approx(1.0)


def test_nose_beside_one_eye_is_a_profile():
    assert frontality([(10, 0), (30, 0), (29, 5)]) < 0.2


def test_frontality_needs_three_points():
    assert frontality([(10, 0), (30, 0)]) == 0.0


# --- mouth: the hand-over-mouth cover --------------------------------


def test_hand_over_mouth_scores_below_a_visible_mouth():
    """Measured on the real source: a hand leaves the darkest pixel near
    51/255 with a mean of ~151; an unobstructed mouth reaches 3-10 with a
    mean near 50."""
    hand = np.full((12, 24), 151.0, dtype=np.float32)
    hand[0, 0] = 51.0
    mouth = np.full((12, 24), 49.0, dtype=np.float32)
    mouth[5, 10] = 5.0
    assert mouth_clarity(mouth) > mouth_clarity(hand)
    assert mouth_clarity(hand) < GATES["mouth_clear"]
    assert mouth_clarity(mouth) > GATES["mouth_clear"]


def test_missing_mouth_patch_is_zero():
    assert mouth_clarity(None) == 0.0


# --- gates: absolute, not relative -----------------------------------


def test_a_clean_frame_is_not_penalised():
    assert gate_penalty(candidate(eyes=0.9, frontal=0.9, mouth_clear=0.95)) == 1.0


def test_each_gate_penalises_on_its_own():
    clean = candidate(eyes=0.9, frontal=0.9, mouth_clear=0.95)
    for key in GATES:
        flawed = candidate(**{**{"eyes": 0.9, "frontal": 0.9, "mouth_clear": 0.95},
                              key: 0.01})
        assert gate_penalty(flawed) < gate_penalty(clean) * 0.7, key


def test_the_gate_ramp_is_steeper_than_linear():
    """A linear ramp left a hand-over-mouth frame with 84% of its score
    and it still won. Halfway to the threshold must cost much more than
    half the penalty."""
    threshold = GATES["mouth_clear"]
    half = gate_penalty(candidate(eyes=0.9, frontal=0.9,
                                  mouth_clear=threshold / 2))
    full = gate_penalty(candidate(eyes=0.9, frontal=0.9, mouth_clear=threshold))
    midpoint_if_linear = (full + gate_penalty(
        candidate(eyes=0.9, frontal=0.9, mouth_clear=0.0))) / 2
    assert half < midpoint_if_linear


def test_a_faceless_frame_is_heavily_discounted():
    faced = candidate(eyes=0.9, frontal=0.9, mouth_clear=0.95)
    faceless = Candidate(t=1.0, face=None, raw=dict(faced.raw))
    assert gate_penalty(faceless) < gate_penalty(faced) * 0.5


# --- normalisation ---------------------------------------------------


def test_normalisation_is_relative_to_this_clip():
    """Laplacian variance depends on lens and lighting, so 'sharp' only
    means anything against the other frames of the same clip."""
    items = [candidate(sharpness=v) for v in (10.0, 20.0, 30.0)]
    normalise(items, "sharpness")
    assert [c.normalised["sharpness"] for c in items] == [0.0, 0.5, 1.0]


def test_a_constant_signal_normalises_to_neutral():
    items = [candidate(energy=7.0) for _ in range(3)]
    normalise(items, "energy")
    assert all(c.normalised["energy"] == 0.5 for c in items)


# --- ranking ---------------------------------------------------------


def test_the_best_frame_wins():
    poor = candidate(eyes=0.05, frontal=0.1, mouth_clear=0.2, sharpness=1.0)
    good = candidate(eyes=0.9, frontal=0.9, mouth_clear=0.95, sharpness=1.0)
    assert pick([poor, good]) is good


def test_a_sharp_blink_loses_to_a_softer_open_eyed_frame():
    """The regression that produced a facepalm thumbnail: sharpness and
    vocal energy alone select the frame where the speaker rubs his eyes."""
    blink = candidate(eyes=0.02, sharpness=1.0, energy=1.0,
                      frontal=0.9, mouth_clear=0.95)
    awake = candidate(eyes=0.9, sharpness=0.4, energy=0.3,
                      frontal=0.9, mouth_clear=0.95)
    assert pick([blink, awake]) is awake


def test_scoring_an_empty_set_returns_nothing():
    assert score_all([]) == []
    assert pick([]) is None


def test_ranking_is_ordered():
    items = [candidate(eyes=v, frontal=0.9, mouth_clear=0.95)
             for v in (0.9, 0.1, 0.5)]
    ranked = score_all(items)
    assert [r.total for r in ranked] == sorted(
        (r.total for r in ranked), reverse=True)


# --- framing ---------------------------------------------------------


@pytest.mark.parametrize("aspect,ratio", [("9:16", 9 / 16), ("16:9", 16 / 9)])
def test_crop_matches_the_requested_aspect(aspect, ratio):
    _, _, w, h = crop_box(
        Candidate(t=0, face=(320, 100, 90, 110), scale=640 / 1920),
        aspect, 1920, 1080,
    )
    assert w / h == pytest.approx(ratio, rel=0.01)


def test_crop_stays_inside_the_source():
    """A face near the edge must not produce a negative origin; ffmpeg
    accepts it and renders a black band."""
    for fx in (0, 620):
        x, y, w, h = crop_box(
            Candidate(t=0, face=(fx, 20, 40, 50), scale=640 / 1920),
            "9:16", 1920, 1080,
        )
        assert x >= 0 and y >= 0
        assert x + w <= 1920 and y + h <= 1080


def test_the_eyes_sit_above_centre():
    """Centring the face box vertically reads as a mugshot; a composed
    portrait puts the eyes near the upper third."""
    face = (300, 120, 90, 120)
    _, y, _, h = crop_box(
        Candidate(t=0, face=face, scale=640 / 1920), "16:9", 1920, 1080)
    eye_y = (face[1] + face[3] * 0.42) / (640 / 1920)
    assert eye_y - y < h / 2


def test_a_faceless_candidate_crops_centrally():
    x, y, w, h = crop_box(Candidate(t=0, face=None), "9:16", 1920, 1080)
    assert x == (1920 - w) // 2 and y == (1080 - h) // 2


def test_thirds_prefers_a_third_over_the_centre():
    assert thirds(1920 / 3, 1920) > thirds(1920 / 2, 1920)


# --- the overlay -----------------------------------------------------


def test_hook_text_is_uppercased_with_turkish_rules():
    """A dotless i in the overlay is the giveaway that the pipeline used
    str.upper() somewhere."""
    ass = cover_ass("gelir dagilimi", aspect="9:16", accent="#ff6716")
    assert "GELİR" in ass       # i -> İ, the Turkish mapping
    assert "GELIR" not in ass   # what str.upper() would have produced


def test_the_overlay_declares_the_right_canvas():
    assert "PlayResX: 1080" in cover_ass("x", aspect="9:16", accent="#ff6716")
    assert "PlayResY: 1080" in cover_ass("x", aspect="16:9", accent="#ff6716")


def test_the_overlay_is_positioned_and_single_evented():
    ass = cover_ass("bir iki uc", aspect="9:16", accent="#ff6716")
    assert ass.count("Dialogue:") == 1
    assert "\\pos(" in ass


def test_long_hooks_wrap_but_never_past_three_lines():
    lines = wrap("bir iki uc dort bes alti yedi sekiz dokuz on onbir", 10)
    assert len(lines) <= 3
    assert all(len(line) <= 10 or " " not in line for line in lines)


def test_accent_colour_reaches_the_style_in_bgr():
    """ASS orders channels blue-green-red. Getting this backwards yields a
    convincing but wrong colour."""
    assert "&H001667FF" in cover_ass("x", aspect="9:16", accent="#ff6716")


# --- audio -----------------------------------------------------------


def write_wav(path, samples, rate=16000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples.astype("<i2").tobytes())


def test_loud_passages_read_louder_than_quiet_ones(tmp_path):
    rate = 16000
    quiet = np.zeros(rate, dtype=np.int16)
    loud = (np.sin(np.arange(rate) * 0.1) * 12000).astype(np.int16)
    path = tmp_path / "a.wav"
    write_wav(path, np.concatenate([quiet, loud]), rate)

    envelope = audio_rms(path, 0.0, 2.0)
    assert envelope
    first = [rms for t, rms in envelope if t < 0.9]
    second = [rms for t, rms in envelope if t > 1.1]
    assert max(first) < min(second)


def test_a_missing_wav_is_not_fatal(tmp_path):
    """Vocal energy is one signal of nine. Losing it must not stop a
    cover being chosen."""
    assert audio_rms(tmp_path / "nope.wav", 0.0, 1.0) == []


def test_a_range_past_the_end_of_the_file_is_empty(tmp_path):
    path = tmp_path / "b.wav"
    write_wav(path, np.zeros(1600, dtype=np.int16))
    assert audio_rms(path, 50.0, 60.0) == []


def test_size_sanity_peaks_at_a_portrait_sized_face():
    from yvc.render.cover import size_sanity

    quarter = size_sanity(0.26 * 360, 360)
    assert quarter > size_sanity(0.02 * 360, 360)
    assert quarter > size_sanity(0.85 * 360, 360)
    assert math.isclose(quarter, 1.0, rel_tol=1e-6)
