from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task


@CrewBase
class MessageFilterCrew():
    """Crew for cleaning a single email body — stripping signatures and boilerplate."""
    agents_config = 'config/filter_agents.yaml'
    tasks_config = 'config/filter_tasks.yaml'

    @agent
    def message_filter(self) -> Agent:
        return Agent(
            config=self.agents_config['message_filter'],
            llm=LLM(model="gemini/gemini-2.5-pro"),
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
            llm=LLM(model="gemini/gemini-2.5-pro"),
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
        return Agent(
            config=self.agents_config['reply_assistant'],
            llm=LLM(model="gemini/gemini-2.5-pro"),
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
