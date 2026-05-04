import os
from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import DirectoryReadTool
from pydantic import BaseModel

from amy.fact_store import search_facts

# Toggle provider here: 'gem' for Gemini, 'ds' for DeepSeek
ACTIVE_PROVIDER = os.environ.get("AI_PROVIDER", "gem").lower()

def get_llm(role="fast"):
    """
    Returns the appropriate LLM based on ACTIVE_PROVIDER.
    'role' can be 'fast' (for standard tasks) or 'smart' (for complex reasoning).
    """
    if ACTIVE_PROVIDER == "ds":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        if role == "smart":
            return LLM(model="deepseek/deepseek-reasoner", api_key=api_key)
        return LLM(model="deepseek/deepseek-chat", api_key=api_key)
    else:
        if role == "smart":
            return LLM(model="gemini/gemini-2.5-pro")
        return LLM(model="gemini/gemini-2.5-flash")

class StyleBlueprint(BaseModel):
    sentence_structure: str
    vocabulary_preferences: str
    formatting_habits: str
    reasoning_logic: str


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
            tools=[DirectoryReadTool(directory="knowledge/historical_emails")]
        )

    @task
    def extract_style_blueprint_task(self) -> Task:
        return Task(
            config=self.tasks_config['extract_style_blueprint_task'],
            output_pydantic=StyleBlueprint,
            output_file='knowledge/style_blueprint.md'
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
        blueprint_path = "knowledge/style_blueprint.md"
        if os.path.exists(blueprint_path):
            with open(blueprint_path, "r", encoding="utf-8") as f:
                style_injection = f"\n\nYOUR REQUIRED WRITING STYLE BLUEPRINT:\n{f.read()}"
        
        # Dynamically load few-shot reply examples
        examples_injection = ""
        examples_path = "knowledge/reply_examples.jsonl"
        if os.path.exists(examples_path):
            try:
                with open(examples_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    # Only inject the 5 most recent examples to save token costs
                    recent_examples = "".join(lines[-5:])
                    examples_injection = f"\n\nLEARN FROM THESE PAST EXAMPLES OF PERFECT REPLIES (Format: JSON Lines):\n{recent_examples}"
            except Exception:
                pass

        agent_config = self.agents_config['reply_assistant'].copy()
        agent_config['backstory'] = agent_config.get('backstory', '') + style_injection + examples_injection

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
        examples_path = "knowledge/workflow_examples.jsonl"
        if os.path.exists(examples_path):
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
