"""Lightweight reply generator for asummary1 — writes as Amy Chen."""

from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from shared_tools.core.llm_config import get_llm


@CrewBase
class ReplyCrew():
    """Crew for generating an auto-reply as Amy Chen."""
    agents_config = 'config/reply_agents.yaml'
    tasks_config = 'config/reply_tasks.yaml'

    @agent
    def amy_chen(self) -> Agent:
        return Agent(
            config=self.agents_config['amy_chen'],
            llm=get_llm("fast"),
            verbose=False
        )

    @task
    def draft_reply_task(self) -> Task:
        return Task(
            config=self.tasks_config['draft_reply_task'],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )
