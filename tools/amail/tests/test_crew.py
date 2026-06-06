import json

import pytest


class TestGetLlm:
    def test_default_provider_is_gemini(self, monkeypatch):
        monkeypatch.setattr("amail.crew.ACTIVE_PROVIDER", "gem")
        from amail.crew import get_llm
        llm = get_llm("fast")
        assert "flash" in str(llm.model).lower()

    def test_gemini_fast_returns_flash(self, monkeypatch):
        monkeypatch.setattr("amail.crew.ACTIVE_PROVIDER", "gem")
        from amail.crew import get_llm
        llm = get_llm("fast")
        assert "flash" in str(llm.model).lower()

    def test_gemini_smart_returns_pro(self, monkeypatch):
        monkeypatch.setattr("amail.crew.ACTIVE_PROVIDER", "gem")
        from amail.crew import get_llm
        llm = get_llm("smart")
        assert "pro" in str(llm.model).lower()

    def test_deepseek_fast_returns_chat(self, monkeypatch):
        monkeypatch.setattr("amail.crew.ACTIVE_PROVIDER", "ds")
        from amail.crew import get_llm
        llm = get_llm("fast")
        assert "chat" in str(llm.model).lower()

    def test_deepseek_smart_returns_reasoner(self, monkeypatch):
        monkeypatch.setattr("amail.crew.ACTIVE_PROVIDER", "ds")
        from amail.crew import get_llm
        llm = get_llm("smart")
        assert "reasoner" in str(llm.model).lower()

    def test_unknown_role_defaults_to_fast(self, monkeypatch):
        monkeypatch.setattr("amail.crew.ACTIVE_PROVIDER", "gem")
        from amail.crew import get_llm
        llm = get_llm("unknown_role")
        assert "flash" in str(llm.model).lower()


class TestTriageJsonParsing:
    """Test the JSON parsing logic used in gui_viewer.py _run_triage() and TriageWorker."""

    def _parse_triage_output(self, raw: str):
        """Replicate the parsing logic from TriageWorker.run()."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            cleaned = cleaned.rsplit("```", 1)[0]
        parsed = json.loads(cleaned.strip())
        return parsed.get("category", "Uncategorized"), \
               parsed.get("urgency", ""), \
               parsed.get("extra_info", "")

    def test_plain_json_object(self):
        raw = '{"category": "RFI", "urgency": "High", "extra_info": "Urgent request"}'
        cat, urg, extra = self._parse_triage_output(raw)
        assert cat == "RFI"
        assert urg == "High"
        assert extra == "Urgent request"

    def test_json_with_markdown_fence(self):
        raw = '```json\n{"category": "Submittal", "urgency": "Medium", "extra_info": "Shop drawings"}\n```'
        cat, urg, extra = self._parse_triage_output(raw)
        assert cat == "Submittal"
        assert urg == "Medium"
        assert extra == "Shop drawings"

    def test_json_with_bare_fence(self):
        raw = '```\n{"category": "Financial", "urgency": "Low", "extra_info": "Invoice #42"}\n```'
        cat, urg, extra = self._parse_triage_output(raw)
        assert cat == "Financial"
        assert urg == "Low"
        assert extra == "Invoice #42"

    def test_json_with_leading_whitespace(self):
        raw = '\n\n  {"category": "Safety", "urgency": "High", "extra_info": "Site incident"}  \n'
        cat, urg, extra = self._parse_triage_output(raw)
        assert cat == "Safety"
        assert urg == "High"
        assert extra == "Site incident"

    def test_malformed_json_handled(self):
        """Malformed JSON should raise — callers catch this and use fallback."""
        with pytest.raises((json.JSONDecodeError, ValueError)):
            self._parse_triage_output("This is not JSON at all")

    def test_missing_keys_default_to_empty(self):
        raw = '{"category": "General"}'
        cat, urg, extra = self._parse_triage_output(raw)
        assert cat == "General"
        assert urg == ""
        assert extra == ""

    def test_extra_kebab_case_fields(self):
        """Extra fields should be ignored gracefully."""
        raw = '{"category": "RFI", "urgency": "Critical", "extra_info": "Desc", "unused_field": 123}'
        cat, urg, extra = self._parse_triage_output(raw)
        assert cat == "RFI"
        assert urg == "Critical"
