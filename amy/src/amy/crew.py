from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from pydantic import BaseModel
from typing import List
from amy.tools.outlook_tool import OutlookReadTool, OutlookSentMailTool
from amy.tools.outlook_reply_tool import OutlookReplyTool

class DownstreamAgentProposal(BaseModel):
    agent_role: str
    goal: str
    trigger_condition: str

class EmailClassificationType(BaseModel):
    category_name: str
    description: str
    urgency_level: str
    action_required: bool
    proposed_workflow_agents: List[DownstreamAgentProposal]

class TriageReport(BaseModel):
    analyzed_emails_count: int
    discovered_types: List[EmailClassificationType]

@CrewBase
class Amy():
    """Amy crew for reading and replying to Outlook emails"""

    # @agent
    # def email_assistant(self) -> Agent:
    #     return Agent(
    #         config=self.agents_config['email_assistant'],
    #         tools=[OutlookReadTool(), OutlookReplyTool()],
    #         verbose=True
    #     )

    # @task
    # def process_email_task(self) -> Task:
    #     return Task(
    #         config=self.tasks_config['process_email_task'],
    #     )

    @agent
    def triage_analyst(self) -> Agent:
        return Agent(
            config=self.agents_config['triage_analyst'],
            tools=[OutlookSentMailTool()],
            llm=LLM(model="gemini/gemini-2.5-pro"),
            verbose=True
        )

    @task
    def triage_sent_emails_task(self) -> Task:
        return Task(
            config=self.tasks_config['triage_sent_emails_task'],
            output_pydantic=TriageReport
        )
    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
