"""Clip directories belonging to a selection that no longer exists.

Renaming happens whenever the selection changes -- an A/B split turns
c01 into c01a + c01b -- and the old directory keeps a finished clip.mp4
that nothing references. Clips are the graded deliverable, so a reviewer
listing the folder must not find one more clip than the run accounts for,
with no way to tell which is current.
"""

from __future__ import annotations

from yvc.stages.s08_render import purge_orphan_clip_dirs


def _clip_dir(root, name):
    d = root / name
    d.mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"rendered")
    return d


def test_a_renamed_clips_old_directory_is_removed(tmp_path):
    _clip_dir(tmp_path, "c01")      # pre-split, now orphaned
    _clip_dir(tmp_path, "c01a")
    _clip_dir(tmp_path, "c01b")

    orphans = purge_orphan_clip_dirs(
        tmp_path, {"c01a", "c01b"}, enabled=True
    )

    assert orphans == ["c01"]
    assert not (tmp_path / "c01").exists()
    assert (tmp_path / "c01a" / "clip.mp4").exists()
    assert (tmp_path / "c01b" / "clip.mp4").exists()


def test_disabled_reports_but_keeps_the_orphan(tmp_path):
    _clip_dir(tmp_path, "c01")
    _clip_dir(tmp_path, "c01a")

    orphans = purge_orphan_clip_dirs(tmp_path, {"c01a"}, enabled=False)

    assert orphans == ["c01"]
    assert (tmp_path / "c01" / "clip.mp4").exists(), (
        "with purging off the directory stays; the caller records it in "
        "render.json instead"
    )


def test_nothing_is_removed_when_every_directory_is_current(tmp_path):
    for name in ("c01", "c02", "c03"):
        _clip_dir(tmp_path, name)

    assert purge_orphan_clip_dirs(
        tmp_path, {"c01", "c02", "c03"}, enabled=True
    ) == []
    assert sorted(d.name for d in tmp_path.iterdir()) == ["c01", "c02", "c03"]


def test_a_missing_clips_root_is_not_an_error(tmp_path):
    assert purge_orphan_clip_dirs(tmp_path / "absent", {"c01"}, enabled=True) == []


def test_keep_must_be_the_whole_selection_not_a_scoped_subset(tmp_path):
    """render_all(only=[...]) filters the clips it renders, but `keep` is
    built from the full clips.json. Passing the filtered subset here
    would delete every clip the scoped re-render was not asked to touch,
    which is why the parameter is documented as whole-selection."""
    for name in ("c01", "c02", "c03"):
        _clip_dir(tmp_path, name)

    # What a caller must NOT do: keep only the --only subset.
    purge_orphan_clip_dirs(tmp_path, {"c02"}, enabled=True)
    assert not (tmp_path / "c01").exists() and not (tmp_path / "c03").exists(), (
        "documents the hazard: a subset keep-set is destructive"
    )
