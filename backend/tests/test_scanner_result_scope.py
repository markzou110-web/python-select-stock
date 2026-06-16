import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.scanner import _should_include_sector_watch


def test_sector_watch_candidates_are_only_added_for_explicit_strategy():
    assert _should_include_sector_watch("sector_watch") is True
    assert _should_include_sector_watch("tv_dual_strict") is False
    assert _should_include_sector_watch("tv_dual") is False
    assert _should_include_sector_watch("consensus") is False
