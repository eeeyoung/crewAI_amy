"""Quick semantic search against the indexed memory."""

import os
os.environ['LILAMY_DATA_DIR'] = r'C:\crewAI\crewAI_amy\tools\amail\historical_emails'

from shared_tools.memory.memory_service import MemoryService

svc = MemoryService()

# Stats first
stats = svc.stats()
print(f"Collection: {stats['collection_name']}")
print(f"Chunks indexed: {stats['chunk_count']}")
print(f"Data root: {stats['data_root']}")
print()

# ── Semantic search queries ──────────────────────────────────────────
queries = [
    # Construction-domain queries that should match your emails
    ("concrete pour schedule", None),
    ("RFI submission", None),
    ("cashflow forecast", None),
    ("progress claim invoice", None),
    ("site parking arrangements", None),
    ("Cheops user manual", None),
    ("PO filing approval", None),
    ("hood street QS report", "ARCO"),
    ("Canning Beach project update", "Econolodge"),
]

for query, project_filter in queries:
    label = f' [project={project_filter}]' if project_filter else ''
    print(f'── "{query}"{label} ──')
    results = svc.search(query, project=project_filter, top_k=3)
    if not results:
        print("  (no results)")
    for r in results:
        print(f'  [{r["project"]}] {r["file_name"][:60]}')
        print(f'    score={r["score"]:.3f}  type={r["doc_type"]}')
        snippet = r['text'][:120].replace('\n', ' | ')
        print(f'    "{snippet}..."')
    print()

# ── Project inventory ────────────────────────────────────────────────
print("── Project breakdown ──")
# Use a broad query to scan all projects
broad = svc.search("project update report", top_k=50)
projects = {}
for r in broad:
    p = r.get("project", "Unknown")
    projects[p] = projects.get(p, 0) + 1
for p, count in sorted(projects.items(), key=lambda x: -x[1]):
    print(f"  {p}: {count} chunks in top-50")

print(f"\nDone — memory service is ready.")
