from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from amy.tools.outlook_tool import OutlookReadTool
from amy.tools.outlook_reply_tool import OutlookReplyTool

@CrewBase
class Amy():
    """Amy crew for reading and replying to Outlook emails"""

    @agent
    def email_assistant(self) -> Agent:
        return Agent(
            config=self.agents_config['email_assistant'],
            tools=[OutlookReadTool(), OutlookReplyTool()],
            verbose=True
        )

    @task
    def process_email_task(self) -> Task:
        return Task(
            config=self.tasks_config['process_email_task'],
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
