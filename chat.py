"""Terminal chatbot grounded strictly in the ChromaDB document index.

Usage:
    uv run python chat.py --data-dir D:/Projects/ClientName
    uv run python chat.py                                # uses LILAMY_DATA_DIR
    uv run python chat.py --top 10                       # more context per query
    uv run python chat.py --verbose                      # show full sources
    uv run python chat.py --project ARCO                 # filter to one project

The LLM is instructed to ONLY use retrieved documents.  If the index doesn't
contain the answer, the bot says so instead of hallucinating.
"""

import os
import sys
import json
import textwrap
from pathlib import Path

# Load .env from repo root before anything reads os.environ
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass

# ── CLI flags ──────────────────────────────────────────────────────────
TOP_K = 10
VERBOSE = False
DATA_DIR = None
PROJECT_FILTER = None
CONTENT_ONLY = True   # default: skip metadata-only chunks (image PDFs, DWGs)
args = sys.argv[1:]
while args:
    a = args.pop(0)
    if a == "--top" and args:
        TOP_K = int(args.pop(0))
    elif a == "--verbose":
        VERBOSE = True
    elif a == "--data-dir" and args:
        DATA_DIR = args.pop(0)
    elif a == "--project" and args:
        PROJECT_FILTER = args.pop(0)
    elif a == "--content-only":
        CONTENT_ONLY = True
    elif a == "--all":
        CONTENT_ONLY = False   # include metadata-only chunks
    elif a in ("--help", "-h"):
        print(__doc__)
        sys.exit(0)
    else:
        print(f"Unknown flag: {a}")
        print("Usage: uv run python chat.py [--data-dir PATH] [--top N] [--project NAME] [--verbose]")
        sys.exit(1)

# Resolve data directory
if DATA_DIR:
    os.environ["LILAMY_DATA_DIR"] = DATA_DIR
else:
    os.environ.setdefault(
        "LILAMY_DATA_DIR", r"C:\crewAI\crewAI_amy\tools\amail\historical_emails"
    )

# ── Imports ────────────────────────────────────────────────────────────
from shared_tools.memory_service import MemoryService


def _fmt_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


# ── LLM caller ─────────────────────────────────────────────────────────
def _get_llm_client():
    """Return a lightweight LLM caller matching the project's provider routing."""
    provider = os.environ.get("AI_PROVIDER", "gem").lower()
    if provider == "ds":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        return {
            "url": "https://api.deepseek.com/v1/chat/completions",
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            "model": "deepseek-chat",
        }
    else:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        return {
            "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "headers": {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            "model": "gemini-2.5-flash",
        }


def ask_llm(system: str, user: str) -> str:
    """Send a single-turn prompt to the LLM.  Uses ``requests`` (already in deps)."""
    import requests

    cfg = _get_llm_client()
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "max_tokens": 800,
    }
    try:
        r = requests.post(cfg["url"], headers=cfg["headers"], json=payload, timeout=40)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[LLM error: {e}]"


# ── System prompt (the "strict grounding" contract) ────────────────────
SYSTEM_PROMPT = textwrap.dedent("""\
    You are Amy's construction-project assistant.  You answer questions using
    ONLY the document excerpts provided below.  These excerpts come from the
    company project archives (47 CBR, ARCO, Econolodge, Kearns Crs, etc.).

    RULES:
    1. Base your answer EXCLUSIVELY on the provided excerpts.
    2. When excerpts are marked "[metadata only]" this means the actual file
       content could not be extracted (scanned PDF, DWG drawing, etc.). Use
       these ONLY to point the user to file names and locations — do NOT
       attempt to answer questions from them.
    3. If no content-extracted excerpts contain enough information, say:
       "I don't have enough in my document index to answer that. However,
       these files might be relevant: [list file names/paths]"
    4. Do NOT bring in outside knowledge about construction, people, companies,
       or projects — even if you recognize names.
    5. Cite the source filenames when you reference specific facts.
    6. Be concise.  If the answer is a simple fact, give it in 1-3 sentences.
       If the question asks for a summary, be thorough but stay grounded.
    7. If excerpts contradict each other, point out the contradiction and cite
       both sources.
""")

# ── Chat loop ──────────────────────────────────────────────────────────
def main():
    svc = MemoryService()
    stats = svc.stats()
    if stats["chunk_count"] == 0:
        print("No index found.  Run 'uv run python ingest.py --data-dir <path>' first.")
        sys.exit(1)

    # ── SQLite registry stats (hybrid store) ───────────────────────
    data_root = os.environ["LILAMY_DATA_DIR"]
    reg_stats = None
    try:
        from shared_tools.file_registry import FileRegistry
        reg = FileRegistry(data_root)
        reg.init_db()
        reg_stats = reg.stats()
    except Exception:
        pass

    print(f"╔══════════════════════════════════════════════════════════════╗")
    print(f"║  Amy's Document Chat  │  {stats['chunk_count']} chunks indexed  "
         f"│  top-{TOP_K} per query  ║")
    if reg_stats:
        print(f"╠══════════════════════════════════════════════════════════════╣")
        print(f"║  {reg_stats['total_files']} files across {len(reg_stats['projects'])} projects"
             f"  │  {_fmt_bytes(reg_stats['total_bytes'])} total")
        proj_strs = []
        for p in reg_stats["projects"][:4]:
            ps = reg.project_stats(p)
            proj_strs.append(f"{p} ({ps['file_count']} files)")
        print(f"║  Projects: {', '.join(proj_strs)}")
    if PROJECT_FILTER:
        print(f"║  Filtered to: {PROJECT_FILTER}")
    print(f"╠══════════════════════════════════════════════════════════════╣")
    print(f"║  /quit to exit  │  /stats for details  │  /projects for list║")
    print(f"╚══════════════════════════════════════════════════════════════╝")
    print()

    while True:
        try:
            query = input("You → ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not query:
            continue
        if query.lower() in ("/quit", "/exit", "/q"):
            print("Goodbye.")
            break
        if query.lower() == "/stats":
            s = svc.stats()
            print(f"  Chunks indexed: {s['chunk_count']}")
            print(f"  Collection:     {s['collection_name']}")
            print(f"  Data root:      {s['data_root']}")
            if reg_stats:
                print(f"  Registry:       {reg_stats['total_files']} files, {_fmt_bytes(reg_stats['total_bytes'])}, {reg_stats['total_chunks']} chunks")
                print(f"  Projects:")
                for p in reg_stats["projects"]:
                    ps = reg.project_stats(p)
                    print(f"    {p:<30} {ps['file_count']:>4} files  {_fmt_bytes(ps['total_bytes']):>8}  {ps['total_chunks']:>5} chunks")
            print()
            continue
        if query.lower() == "/projects":
            if reg_stats:
                for p in reg_stats["projects"]:
                    ps = reg.project_stats(p)
                    print(f"  {p:<30} {ps['file_count']:>4} files  {_fmt_bytes(ps['total_bytes']):>8}  {ps['total_chunks']:>5} chunks")
            else:
                print("  Registry not available.")
            print()
            continue

        # ── 1. Search the index ──────────────────────────────────
        results = svc.search(query, project=PROJECT_FILTER, top_k=TOP_K,
                             content_only=CONTENT_ONLY)

        if not results:
            print("Amy → (no matching documents found in index)\n")
            continue

        # ── 2. Build context block ───────────────────────────────
        context_blocks = []
        for i, r in enumerate(results, 1):
            meta_note = ""
            if not r.get("content_extracted", True):
                meta_note = " [metadata only — file name/path indexed]"

            header = (
                f"[DOC {i}] project={r['project']}  file={r['file_name']}  "
                f"path={r.get('file_path', '?')}  "
                f"type={r['doc_type']}  relevance={r['score']:.3f}{meta_note}"
            )
            context_blocks.append(f"{header}\n{r['text']}")

        context = "\n\n───\n\n".join(context_blocks)

        # ── 3. Ask the LLM (strictly grounded) ───────────────────
        user_prompt = f"QUESTION: {query}\n\nRETRIEVED DOCUMENTS:\n\n{context}"

        print("Amy → ", end="", flush=True)
        answer = ask_llm(SYSTEM_PROMPT, user_prompt)
        print(answer)
        print()

        # ── 4. Show sources (if verbose) ─────────────────────────
        if VERBOSE:
            print("─" * 60)
            print("Sources:")
            for r in results:
                print(f"  [{r['project']}] {r['file_path']} "
                      f"(score={r['score']:.3f})")
            print()


if __name__ == "__main__":
    main()
