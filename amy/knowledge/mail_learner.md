# TASK: Build a Hybrid Style-Learning CrewAI System

I need a Python-based CrewAI architecture that uses a "Hybrid Learning" approach to emulate my personal writing and thinking style. 

## Part 1: The Style Blueprint Extraction
Create an "Analyst Agent" and a Task that can read a provided directory of my sent email history (Markdown or Text files). 
- **Goal:** Generate a `style_blueprint.md` file.
- **Requirements:** It must extract:
    1. Sentence structure (average length, complexity).
    2. Vocabulary preferences (words I use vs. words I avoid).
    3. Formatting habits (sign-offs, bullet points vs. prose).
    4. "Reasoning Logic" (how I handle follow-up questions or direct requests).
    5. The signature of the owner.
- **Note:** 
    1. The owner (or the first person view) of the emails is always Amy Chen (Contract Admin of Construction Projects of Welink)

## Part 2: The Implementation (The Apprentice)
Improve the structure for the current "Reply Assistant" with the following specs:
- **Style Injection:** The agent's `backstory` should be dynamically loaded from the `style_blueprint.md` generated in Part 1.
- **Cognitive Memory:** Enable the unified memory system (`memory=True`). Configure it to use a local LanceDB instance for long-term "fact" and "habit" storage.
- **Tooling:** Include a placeholder for an Email Tool (Gmail/Outlook) and a File Read Tool.

## Part 3: Learning Loop (CLI Training)
Include a separate execution block or a CLI wrapper that demonstrates how to run the `crewai train` command on this specific Crew.
- The training should be configured to save iterations to `my_style_training.pkl`.
- Ensure the agent is set to `verbose=True` so I can see its internal reasoning during the training phase.

## Technical Constraints:
- Use the 2026 `CrewAI` unified Memory API (replacing the old short/long-term separate classes).
- Use `Pydantic` for structured output of the style blueprint.
- Ensure the code is modular so I can swap the "Historian" data source easily later.