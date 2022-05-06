from flatpak_rebuilder import __version__
from flatpak_rebuilder.main import find_flatpak_commit_for_date, get_available_branches, get_additional_deps, GitNotFoundException
from datetime import datetime as dt
from datetime import timezone as tz
import pytest


def test_version():
    assert __version__ == '0.1.0'

def test_get_commit_for_date():
    result = find_flatpak_commit_for_date("flathub", "system", "org.freedesktop.Sdk.Compat.i386/x86_64/21.08", dt(2022, 4, 5,tzinfo=tz.utc))
    assert result == "44786459a1262065eb9ab26466d6fe29ce912ad94cd27f6f43073e706c2c43b6"

def test_list_available_branches():
    result = get_available_branches("flathub", "system", "org.mozilla.firefox.BaseApp", "x86_64")
    assert result == ['21.08', "20.08"]

def test_list_available_branches_when_only_one_branch():
    result = get_available_branches("flathub", "system", "org.kde.krdc", "x86_64")
    assert result == ['stable']

def test_get_git_repo_raise_exception_on_non_existent_repo():
    with pytest.raises(GitNotFoundException):
        get_additional_deps("flathub", "org.mozilla.firefox") 

def test_get_git_repo_with_valid_values():
    result = get_additional_deps("flathub", "org.gnome.Dictionary")
    assert result == "https://github.com/flathub/org.gnome.Dictionary"
