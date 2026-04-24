from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, crew, task
from pydantic import BaseModel
from typing import List


class TriageSingleCrew():
    """Standalone crew for classifying a single email.
    Built manually (no @CrewBase) to avoid YAML config conflicts.
    """

    def crew(self) -> Crew:
        triage_agent = Agent(
            role="Lead Construction Communications Triage Analyst",
            goal=(
                "Analyze a single inbound contractor communication. Categorize it "
                "by urgency and construction domain (e.g., RFIs, Submittals, Financial, Safety, Scheduling)."
            ),
            backstory=(
                "You are a veteran construction project manager turned AI architect. You understand the nuances "
                "of construction communications, knowing exactly what constitutes an RFI, a Submittal, or a "
                "billing inquiry. You have an exceptional ability to read between the lines and assess urgency."
            ),
            llm=LLM(model="gemini/gemini-2.5-pro"),
            verbose=True,
        )

        triage_task = Task(
            description=(
                "Analyze the following email and classify it:\n"
                "Subject: {email_subject}\n"
                "Sender: {email_sender}\n"
                "Content: {email_content}\n\n"
                "Categorize the email based on:\n"
                "a. Urgency (Does it need a response ASAP?)\n"
                "b. Actionability (Is it an assignment requiring extra work?)\n"
                "c. Pure Text (Can it be replied to directly?)\n"
                "d. Business vs. Personal\n"
                "e. Construction Domain (RFIs, Submittals/Approvals, Financial/Invoicing, "
                "Scheduling/Logistics, Safety & Compliance, etc.)\n\n"
                "Return ONLY a JSON object with exactly two keys:\n"
                '- "category": the assigned category name (string)\n'
                '- "extra_info": additional context about the classification (string)'
            ),
            expected_output=(
                'A JSON object like: {"category": "RFI", "extra_info": "Urgent RFI from subcontractor..."}'
            ),
            agent=triage_agent,
        )

        return Crew(
            agents=[triage_agent],
            tasks=[triage_task],
            process=Process.sequential,
            verbose=True,
        )


@CrewBase
class ReplyGeneratorCrew():
    """Crew for generating email replies one-by-one"""
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
