import os as _os
from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import DirectoryReadTool
from pydantic import BaseModel
from shared_tools.core.llm_config import get_llm

from amail.mail_knowledge import search_facts

_AMAIL_ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), "..", ".."))

class StyleBlueprint(BaseModel):
    sentence_structure: str
    vocabulary_preferences: str
    formatting_habits: str
    reasoning_logic: str


class DeadlineItem(BaseModel):
    """A single deadline/date reference extracted from an email."""
    description: str
    date_type: str          # exact | approximate | range | deadline | tbd
    start_date: str | None  # ISO date string or None
    end_date: str | None    # ISO date string for ranges, or None
    confidence: float       # 0.0 to 1.0


class UnifiedEmailOutput(BaseModel):
    """Single-pass structured output from the unified summarizer.
    Replaces the old 6-stage pipeline for initial triage."""
    chinese_summary: str
    category: str
    urgency: str          # low | medium | high | critical
    assignee: str
    todos: list[str]
    deadlines: list[DeadlineItem]


@CrewBase
class StyleLearnerCrew():
    """Crew for extracting personal writing style from historical emails."""
    agents_config = 'config/style_learner_agents.yaml'
    tasks_config = 'config/style_learner_tasks.yaml'

    @agent
    def style_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config['style_analyst'],
            llm=get_llm("smart"),
            verbose=True,
            tools=[DirectoryReadTool(directory=_os.path.join(_AMAIL_ROOT, "knowledge/historical_emails"))]
        )

    @task
    def extract_style_blueprint_task(self) -> Task:
        return Task(
            config=self.tasks_config['extract_style_blueprint_task'],
            output_pydantic=StyleBlueprint,
            output_file=_os.path.join(_AMAIL_ROOT, 'knowledge/style_blueprint.md')
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )


@CrewBase
class MessageFilterCrew():
    """Crew for cleaning a single email body — strips boilerplate, then restructures threads."""
    agents_config = 'config/filter_agents.yaml'
    tasks_config = 'config/filter_tasks.yaml'

    @agent
    def boilerplate_stripper(self) -> Agent:
        return Agent(
            config=self.agents_config['boilerplate_stripper'],
            llm=get_llm("fast"),
            verbose=True
        )

    @agent
    def thread_structurer(self) -> Agent:
        return Agent(
            config=self.agents_config['thread_structurer'],
            llm=get_llm("fast"),
            verbose=True
        )

    @task
    def strip_boilerplate_task(self) -> Task:
        return Task(
            config=self.tasks_config['strip_boilerplate_task'],
        )

    @task
    def restructure_thread_task(self) -> Task:
        return Task(
            config=self.tasks_config['restructure_thread_task'],
            context=[self.strip_boilerplate_task()],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )


@CrewBase
class TriageSingleCrew():
    """Crew for classifying a single email by urgency and construction domain."""
    agents_config = 'config/triage_agents.yaml'
    tasks_config = 'config/triage_tasks.yaml'

    @agent
    def triage_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config['triage_analyst'],
            llm=get_llm("fast"),
            verbose=True
        )

    @task
    def triage_single_email_task(self) -> Task:
        return Task(
            config=self.tasks_config['triage_single_email_task'],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )


@CrewBase
class ReplyGeneratorCrew():
    """Crew for generating email replies one-by-one."""
    agents_config = 'config/reply_agents.yaml'
    tasks_config = 'config/reply_tasks.yaml'

    @agent
    def reply_assistant(self) -> Agent:
        # Dynamically load style blueprint if it exists
        style_injection = ""
        blueprint_path = _os.path.join(_AMAIL_ROOT, "knowledge/style_blueprint.md")
        if _os.path.exists(blueprint_path):
            with open(blueprint_path, "r", encoding="utf-8") as f:
                style_injection = f"\n\nYOUR REQUIRED WRITING STYLE BLUEPRINT:\n{f.read()}"

        # Dynamically load few-shot reply examples
        examples_injection = ""
        examples_path = _os.path.join(_AMAIL_ROOT, "knowledge/reply_examples.jsonl")
        if _os.path.exists(examples_path):
            try:
                with open(examples_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    # Only inject the 5 most recent examples to save token costs
                    recent_examples = "".join(lines[-5:])
                    examples_injection = f"\n\nLEARN FROM THESE PAST EXAMPLES OF PERFECT REPLIES (Format: JSON Lines):\n{recent_examples}"
            except Exception:
                pass

        amy_name = _os.environ.get("AMY_NAME", "Amy Chen")
        amy_email = _os.environ.get("AMY_EMAIL", "amy@welink.com.au")
        identity_injection = (
            f"\n\nYOUR IDENTITY: You are {amy_name} ({amy_email}), "
            f"a construction contract administrator. You ALWAYS write as "
            f"yourself — never as anyone else. If an email was only CC'd to "
            f"you (not sent directly to you), write a brief acknowledgment "
            f"and state you will follow up if needed. Never impersonate the "
            f"primary recipient or sign with someone else's name."
        )

        agent_config = self.agents_config['reply_assistant'].copy()
        agent_config['backstory'] = agent_config.get('backstory', '') + style_injection + examples_injection + identity_injection

        return Agent(
            config=agent_config,
            llm=get_llm("fast"),
            verbose=True
        )

    @task
    def generate_reply_task(self) -> Task:
        return Task(
            config=self.tasks_config['generate_reply_task'],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )


@CrewBase
class WorkflowGeneratorCrew():
    """Crew for generating task workflows based on triaged emails."""
    agents_config = 'config/workflow_agents.yaml'
    tasks_config = 'config/workflow_tasks.yaml'

    @agent
    def workflow_admin(self) -> Agent:
        # Dynamically load workflow examples if they exist
        examples_injection = ""
        examples_path = _os.path.join(_AMAIL_ROOT, "knowledge/workflow_examples.jsonl")
        if _os.path.exists(examples_path):
            try:
                with open(examples_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    # Only inject the 10 most recent examples to save token costs and prevent context bloat
                    recent_examples = "".join(lines[-20:])
                    examples_injection = f"\n\nLEARN FROM THESE PAST EXAMPLES OF WORKFLOWS (Format: JSON Lines):\n{recent_examples}"
            except Exception:
                pass
        
        agent_config = self.agents_config['workflow_admin'].copy()
        agent_config['backstory'] = agent_config.get('backstory', '') + examples_injection

        return Agent(
            config=agent_config,
            llm=get_llm("fast"),
            verbose=True
        )

    @task
    def generate_workflow_task(self) -> Task:
        return Task(
            config=self.tasks_config['generate_workflow_task'],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
            memory=False
        )


@CrewBase
class FactExtractorCrew():
    """Crew for extracting critical project facts from processed emails."""
    agents_config = 'config/fact_extractor_agents.yaml'
    tasks_config = 'config/fact_extractor_tasks.yaml'

    @agent
    def fact_extractor(self) -> Agent:
        return Agent(
            config=self.agents_config['fact_extractor'],
            llm=get_llm("fast"),
            verbose=True
        )

    @task
    def extract_facts_task(self) -> Task:
        return Task(
            config=self.tasks_config['extract_facts_task'],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )


@CrewBase
class GrammarPolisherCrew():
    """Crew for polishing email grammar without changing tone or content."""
    agents_config = 'config/grammar_polisher_agents.yaml'
    tasks_config = 'config/grammar_polisher_tasks.yaml'

    @agent
    def grammar_polisher(self) -> Agent:
        return Agent(
            config=self.agents_config['grammar_polisher'],
            llm=get_llm("fast"),
            verbose=True
        )

    @task
    def polish_grammar_task(self) -> Task:
        return Task(
            config=self.tasks_config['polish_grammar_task'],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )


@CrewBase
class UnifiedSummarizerCrew():
    """Single-pass summarizer — one LLM call replacing the old 6-stage pipeline.
    Produces: chinese_summary, category, urgency, assignee, todos, deadlines."""
    agents_config = 'config/summarizer_agents.yaml'
    tasks_config = 'config/summarizer_tasks.yaml'

    @agent
    def unified_summarizer(self) -> Agent:
        return Agent(
            config=self.agents_config['unified_summarizer'],  # type: ignore[index]
            llm=get_llm("fast"),
            verbose=False,
        )

    @task
    def unified_summarize_task(self) -> Task:
        return Task(
            config=self.tasks_config['unified_summarize_task'],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )
