import os
from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from crewai_tools import DirectoryReadTool, FileReadTool
from pydantic import BaseModel

from amy.tools.outlook_tool import OutlookSendTool


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
            llm=LLM(model="gemini/gemini-2.5-pro"),
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
    """Crew for cleaning a single email body — stripping signatures and boilerplate."""
    agents_config = 'config/filter_agents.yaml'
    tasks_config = 'config/filter_tasks.yaml'

    @agent
    def message_filter(self) -> Agent:
        return Agent(
            config=self.agents_config['message_filter'],
            llm=LLM(model="gemini/gemini-2.5-flash"),
            verbose=True
        )

    @task
    def filter_email_task(self) -> Task:
        return Task(
            config=self.tasks_config['filter_email_task'],
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
            llm=LLM(model="gemini/gemini-2.5-flash"),
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
        
        agent_config = self.agents_config['reply_assistant'].copy()
        agent_config['backstory'] = agent_config.get('backstory', '') + style_injection

        return Agent(
            config=agent_config,
            llm=LLM(model="gemini/gemini-2.5-flash"),
            verbose=True,
            tools=[OutlookSendTool(), FileReadTool()]
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
            memory=True,
            memory_llm=LLM(model="gemini/gemini-2.5-flash"),
            embedder={
                "provider": "google-generativeai",
                "config": {
                    "model": "models/embedding-001"
                }
            }
        )
