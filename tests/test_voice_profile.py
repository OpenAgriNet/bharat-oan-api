"""The manifest's voice profile: global prompt, shared tools, validation of the new fields."""
import shutil

import pytest
import yaml

from agents.skill_catalog import SkillCatalog, get_catalog
from agents.skill_policy import SkillState


def test_voice_profile_sees_enabled_tools_minus_disabled_all_preloaded():
    catalog = get_catalog()
    config = catalog.manifest.agents["voice"]
    state = SkillState(agent="voice")
    assert config.prompt_stem == "voice"
    assert state.active == state.permitted  # Everything preloaded; load_skill is never offered.
    assert "analyze_crop_image" not in state.visible
    assert not {"get_agristack_farmer_profile", "search_advisory"} & state.visible
    assert state.visible == catalog.tools_for(set(config.enabled_skills)) - {"analyze_crop_image"}


def test_chat_profile_is_unchanged_by_voice_fields():
    chat = get_catalog().manifest.agents["chat"]
    assert chat.prompt_stem == "agrinet" and chat.disabled_tools == []
    assert "analyze_crop_image" in SkillState().allowed_tools


def _catalog_with_voice(tmp_path, **changes):
    src = get_catalog().root
    manifest = yaml.safe_load((src / "manifest.yaml").read_text())
    manifest["agents"]["voice"].update(changes)
    root = tmp_path / "prompts"
    shutil.copytree(src, root)
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    return SkillCatalog(root)


@pytest.mark.parametrize("changes", [
    {"disabled_tools": ["not_a_tool"]},
    {"disabled_tools": ["analyze_crop_image", "analyze_crop_image"]},
    {"prompt_stem": "missing"},
])
def test_invalid_voice_profile_is_rejected(tmp_path, changes):
    with pytest.raises(ValueError):
        _catalog_with_voice(tmp_path, **changes)
