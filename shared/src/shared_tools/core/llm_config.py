import os
from crewai import LLM

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
