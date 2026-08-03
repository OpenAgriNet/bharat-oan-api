from pathlib import Path

import pytest

from scripts.audit_npss_empty_villages import _validated_output_dir


def test_output_dir_accepts_relative_subdirectory(tmp_path: Path):
    report_root = tmp_path / ".local-dev-logs"

    result = _validated_output_dir(report_root, Path("run-1"))

    assert result == (report_root / "run-1").resolve()


@pytest.mark.parametrize(
    "requested_dir",
    [Path(".."), Path("../outside")],
)
def test_output_dir_rejects_parent_traversal(tmp_path: Path, requested_dir: Path):
    report_root = tmp_path / ".local-dev-logs"

    with pytest.raises(ValueError, match="must stay within"):
        _validated_output_dir(report_root, requested_dir)


def test_output_dir_rejects_absolute_path_outside_report_root(tmp_path: Path):
    report_root = tmp_path / ".local-dev-logs"
    outside = tmp_path / "outside"

    with pytest.raises(ValueError, match="must stay within"):
        _validated_output_dir(report_root, outside)


def test_output_dir_rejects_symlink_escape(tmp_path: Path):
    report_root = tmp_path / ".local-dev-logs"
    report_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (report_root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must stay within"):
        _validated_output_dir(report_root, Path("escape"))
