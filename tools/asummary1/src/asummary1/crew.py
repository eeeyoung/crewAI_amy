from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from shared_tools.llm_config import get_llm


@CrewBase
class SummarizerCrew():
    """Crew for producing Chinese summary, assignee, and todo items from an email."""
    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    @agent
    def chinese_summarizer(self) -> Agent:
        return Agent(
            config=self.agents_config['chinese_summarizer'],
            llm=get_llm("fast"),
            verbose=False
        )

    @task
    def summarize_email_task(self) -> Task:
        return Task(
            config=self.tasks_config['summarize_email_task'],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=False,
        )
