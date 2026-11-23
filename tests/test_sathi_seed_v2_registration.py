import importlib
import shutil

import pytest
import yaml

import agents.tools
from agents import skill_catalog, skill_policy, skills
from agents.skill_catalog import PROMPT_DIR, Manifest, SkillCatalog, apply_skill_replacements
from agents.tools.v2 import sathi_seed as sathi_v2

V1_TOOLS = {"get_sathi_crop_groups", "list_sathi_crops_in_group", "search_sathi_seed_availability"}
V2_TOOLS = {"list_sathi_crops", "search_sathi_seed_availability"}
MASTER_TOOLS = {"get_sathi_crop_groups", "list_sathi_crops_in_group"}


def _raw_manifest():
    return yaml.safe_load((PROMPT_DIR / "manifest.yaml").read_text())


@pytest.fixture
def sathi_flag(monkeypatch):
    def set_flag(value):
        if value is None:
            monkeypatch.delenv("SATHI_BECKN_VERSION", raising=False)
        else:
            monkeypatch.setenv("SATHI_BECKN_VERSION", value)
        return importlib.reload(agents.tools)

    yield set_flag
    monkeypatch.delenv("SATHI_BECKN_VERSION", raising=False)
    importlib.reload(agents.tools)


def _render_v1_prompt(catalog, monkeypatch):
    for module in (skill_policy, skills):
        monkeypatch.setattr(module, "get_catalog", lambda: catalog)
    return skills.render_prompt(skill_policy.SkillState(environment=None), "en", {})


def test_manifest_defines_both_seed_skills():
    raw = _raw_manifest()["skills"]
    assert set(raw["seeds.availability"]["tools"]) == V1_TOOLS
    assert set(raw["seeds.availability_v2"]["tools"]) == V2_TOOLS
    assert raw["seeds.availability_v2"]["requires"] == raw["seeds.availability"]["requires"]
    assert raw["seeds.availability_v2"]["replaces"] == "seeds.availability"


def test_flag_unset_catalog_matches_manifest_without_v2_skill(sathi_flag):
    sathi_flag(None)
    raw = _raw_manifest()
    catalog = SkillCatalog()
    assert list(catalog.manifest.skills) == [n for n in raw["skills"] if n != "seeds.availability_v2"]
    assert catalog.manifest.agents["chat"].enabled_skills == raw["agents"]["chat"]["enabled_skills"]


def test_flag_unset_seed_skill_loads_the_three_v1_tools(sathi_flag):
    sathi_flag(None)
    assert SkillCatalog().tools_for({"seeds.availability"}) == V1_TOOLS | {"forward_geocode", "reverse_geocode"}


def test_flag_unset_registers_v1_tools_and_validates(sathi_flag):
    tools = sathi_flag(None)
    names = {t.name for t in tools.TOOLS}
    assert MASTER_TOOLS <= names
    assert "list_sathi_crops" not in names
    assert tools.search_sathi_seed_availability is not sathi_v2.search_sathi_seed_availability
    SkillCatalog().validate_tools(tools.TOOLS)


def test_flag_unset_system_prompt_matches_manifest_without_v2_skill(sathi_flag, monkeypatch, tmp_path):
    sathi_flag(None)
    today = tmp_path / "prompts"
    shutil.copytree(PROMPT_DIR, today)
    raw = _raw_manifest()
    del raw["skills"]["seeds.availability_v2"]
    (today / "manifest.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    expected = _render_v1_prompt(SkillCatalog(today), monkeypatch)
    assert _render_v1_prompt(SkillCatalog(), monkeypatch) == expected


def test_flag_v2_puts_v2_skill_in_the_v1_position(sathi_flag):
    sathi_flag("v2")
    raw = _raw_manifest()
    catalog = SkillCatalog()
    expected = [n for n in raw["skills"] if n != "seeds.availability_v2"]
    expected[expected.index("seeds.availability")] = "seeds.availability_v2"
    assert list(catalog.manifest.skills) == expected
    enabled = raw["agents"]["chat"]["enabled_skills"]
    assert catalog.manifest.agents["chat"].enabled_skills == [
        "seeds.availability_v2" if n == "seeds.availability" else n for n in enabled
    ]


def test_flag_v2_seed_skill_loads_list_and_search(sathi_flag):
    sathi_flag("v2")
    assert SkillCatalog().tools_for({"seeds.availability_v2"}) == V2_TOOLS | {"forward_geocode", "reverse_geocode"}


def test_flag_v2_registers_v2_tools_without_master_tools(sathi_flag):
    tools = sathi_flag("v2")
    names = {t.name for t in tools.TOOLS}
    assert "list_sathi_crops" in names
    assert not MASTER_TOOLS & names
    assert tools.search_sathi_seed_availability is sathi_v2.search_sathi_seed_availability
    SkillCatalog().validate_tools(tools.TOOLS)


def _tiny_manifest():
    return Manifest.model_validate({
        "skills": {
            "base.old": {"prompt_dir": "old", "tools": ["a"]},
            "other.one": {"prompt_dir": "other", "tools": ["b"]},
            "base.new": {"prompt_dir": "new", "tools": ["c"],
                         "replaces": "base.old", "enabled_by": "TINY_FLAG=on"},
        },
        "agents": {"chat": {"prompt_dir": "chat", "languages": ["en"],
                            "enabled_skills": ["base.old", "other.one"],
                            "preloaded_skills": ["base.old"]}},
    })


def test_replacement_skill_is_removed_when_its_condition_is_false(monkeypatch):
    monkeypatch.delenv("TINY_FLAG", raising=False)
    manifest = apply_skill_replacements(_tiny_manifest())
    assert list(manifest.skills) == ["base.old", "other.one"]
    assert manifest.agents["chat"].enabled_skills == ["base.old", "other.one"]


def test_replacement_skill_takes_the_old_skills_place_when_its_condition_holds(monkeypatch):
    monkeypatch.setenv("TINY_FLAG", "on")
    manifest = apply_skill_replacements(_tiny_manifest())
    assert list(manifest.skills) == ["base.new", "other.one"]
    assert manifest.agents["chat"].enabled_skills == ["base.new", "other.one"]
    assert manifest.agents["chat"].preloaded_skills == ["base.new"]


def test_replacement_of_an_unknown_skill_is_rejected(monkeypatch):
    monkeypatch.delenv("TINY_FLAG", raising=False)
    manifest = Manifest.model_validate({
        "skills": {"base.new": {"prompt_dir": "new", "tools": [], "replaces": "base.gone",
                                "enabled_by": "TINY_FLAG=on"}},
        "agents": {"chat": {"prompt_dir": "chat", "languages": ["en"]}},
    })
    with pytest.raises(ValueError):
        apply_skill_replacements(manifest)
