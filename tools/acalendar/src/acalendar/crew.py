from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from pydantic import BaseModel
from shared_tools.core.llm_config import get_llm


class ExtractedDate(BaseModel):
    description: str
    date_type: str             # exact | approximate | range | tbd | deadline
    start_date: str | None     # ISO date "2026-06-15" or None
    end_date: str | None       # ISO date for ranges, or None
    time_of_day: str | None    # "14:00" or "morning" or None
    confidence: float          # 0.0 to 1.0
    project: str | None        # extracted from context, e.g. "ARCO"
    source_email_subject: str
    source_email_sender: str


class ExtractedDates(BaseModel):
    dates: list[ExtractedDate]
    no_dates_found: bool


@CrewBase
class DateExtractorCrew():
    """Crew for extracting construction-related dates from email bodies."""
    agents_config = 'config/date_extractor_agents.yaml'
    tasks_config = 'config/date_extractor_tasks.yaml'

    @agent
    def date_extractor(self) -> Agent:
        return Agent(
            config=self.agents_config['date_extractor'],
            llm=get_llm("fast"),
            verbose=True,
        )

    @task
    def extract_dates_task(self) -> Task:
        return Task(
            config=self.tasks_config['extract_dates_task'],
            output_pydantic=ExtractedDates,
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
