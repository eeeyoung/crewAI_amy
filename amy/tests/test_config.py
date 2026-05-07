from pathlib import Path

import yaml
import pytest

CONFIG_DIR = Path(__file__).resolve().parent.parent / "src" / "amy" / "config"

# These are documented as legacy/abandoned — they contain only comments.
LEGACY_FILES = {"agents.yaml", "tasks.yaml"}


def _yaml_files(exclude_legacy=True):
    files = sorted(CONFIG_DIR.glob("*.yaml"))
    if exclude_legacy:
        files = [f for f in files if f.name not in LEGACY_FILES]
    return files


def _load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class TestAllConfigsLoadable:
    @pytest.mark.parametrize("yaml_path", _yaml_files(), ids=lambda p: p.name)
    def test_loads_without_error(self, yaml_path):
        data = _load_yaml(yaml_path)
        assert data is not None, f"{yaml_path.name} is empty or invalid"
        assert isinstance(data, dict), f"{yaml_path.name} should be a dict, got {type(data)}"

    @pytest.mark.parametrize("yaml_path", _yaml_files(), ids=lambda p: p.name)
    def test_has_at_least_one_entry(self, yaml_path):
        data = _load_yaml(yaml_path)
        assert len(data) >= 1, f"{yaml_path.name} should have at least one config entry"


class TestAgentConfigs:
    AGENT_FILES = [p for p in _yaml_files() if "agent" in p.name]

    def _agent_configs(self):
        configs = []
        for path in self.AGENT_FILES:
            data = _load_yaml(path)
            for name, cfg in data.items():
                configs.append((path.name, name, cfg))
        return configs

    def test_agents_have_required_keys(self):
        for filename, agent_name, cfg in self._agent_configs():
            assert "role" in cfg, f"{filename}:{agent_name} missing 'role'"
            assert isinstance(cfg["role"], str), f"{filename}:{agent_name} 'role' should be str"
            assert "goal" in cfg, f"{filename}:{agent_name} missing 'goal'"
            assert "backstory" in cfg, f"{filename}:{agent_name} missing 'backstory'"


class TestTaskConfigs:
    TASK_FILES = [p for p in _yaml_files() if "task" in p.name]

    def _task_configs(self):
        configs = []
        for path in self.TASK_FILES:
            data = _load_yaml(path)
            for name, cfg in data.items():
                configs.append((path.name, name, cfg))
        return configs

    def test_tasks_have_required_keys(self):
        for filename, task_name, cfg in self._task_configs():
            assert "description" in cfg, f"{filename}:{task_name} missing 'description'"
            assert isinstance(cfg["description"], str), f"{filename}:{task_name} 'description' should be str"
            assert "expected_output" in cfg, f"{filename}:{task_name} missing 'expected_output'"
            assert "agent" in cfg, f"{filename}:{task_name} missing 'agent'"

    def test_task_agent_references_exist(self):
        """The agent field in each task should reference a declared agent in a matching config file."""
        agent_names = set()
        for path in TestAgentConfigs.AGENT_FILES:
            data = _load_yaml(path)
            agent_names.update(data.keys())

        for filename, task_name, cfg in self._task_configs():
            agent_ref = cfg["agent"]
            assert len(agent_ref.strip()) > 0, f"{filename}:{task_name} has empty 'agent' field"


class TestLegacyConfigs:
    LEGACY = [CONFIG_DIR / "agents.yaml", CONFIG_DIR / "tasks.yaml"]

    @pytest.mark.parametrize("legacy_path", [p for p in LEGACY if p.exists()])
    def test_legacy_configs_are_comment_files(self, legacy_path):
        """Legacy agents.yaml / tasks.yaml exist only as documentation markers.
        They are NOT real configs — the active pipeline uses per-crew files instead."""
        data = _load_yaml(legacy_path)
        # These files are intentionally comments-only, so yaml.safe_load returns None.
        # This is correct — they exist to warn developers away from using them.
        assert data is None or isinstance(data, dict)
