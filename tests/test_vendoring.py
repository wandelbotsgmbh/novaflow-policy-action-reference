"""The dummy policy server ships its own copy of the codec; the two must match."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_dummy_codec_is_a_verbatim_copy():
    ours = ROOT / "src/actions/internal/openpi_msgpack.py"
    theirs = ROOT / "policies/dummy/dummy_policy/openpi_msgpack.py"
    assert ours.read_text() == theirs.read_text(), f"Re-copy {ours} to {theirs}"
