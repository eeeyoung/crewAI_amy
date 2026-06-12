"""Test MemoryService ingestion — preprocess historical emails first."""

import os
import re
from pathlib import Path

os.environ['LILAMY_DATA_DIR'] = r'C:\crewAI\crewAI_amy\tools\amail\historical_emails'
from shared_tools.memory_service import MemoryService

# ---- Step 1: Fast HTML-strip for all files (no LLM) ------------------
def strip_html(text: str) -> str:
    """Remove HTML tags, CSS, and common boilerplate from raw Outlook exports."""
    # Remove <style> blocks
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove <script> blocks (unlikely but safe)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Replace <br>, <p>, <div>, <tr>, <li> with newlines
    text = re.sub(r'<(br|p|div|tr|li|h\d)[^>]*/?>', '\n', text, flags=re.IGNORECASE)
    # Replace </p>, </div>, </td>, </li>, </tr>, </h\d> with newlines
    text = re.sub(r'</(p|div|td|li|tr|h\d)>', '\n', text, flags=re.IGNORECASE)
    # Replace </td> and <td> with tab
    text = re.sub(r'</?td[^>]*>', '\t', text, flags=re.IGNORECASE)
    # Remove remaining HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common entities
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<')
    text = text.replace('&gt;', '>').replace('&quot;', '"').replace('&#39;', "'")
    # Collapse multiple blank lines
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    # Strip leading/trailing whitespace per line
    lines = [l.strip() for l in text.splitlines()]
    text = '\n'.join(lines)
    return text.strip()


# ---- Step 2: Override the text extractor to apply HTML-strip ----------
from shared_tools import memory_service as mem_svc
original_extract = mem_svc._extract_text

def clean_extract(file_path):
    text = original_extract(file_path)
    if text and ('<html' in text.lower() or '<body' in text.lower() or '<div' in text.lower()):
        text = strip_html(text)
    return text

mem_svc._extract_text = clean_extract  # monkey-patch for this run

# ---- Step 3: Pre-ingestion safety check ----------------------------
import time, gc

data_dir = Path(os.environ['LILAMY_DATA_DIR'])
files = list(data_dir.rglob('*.txt'))
total_files = len(files)

# Estimate total text size
sample_size = min(20, total_files)
sample_bytes = 0
for f in files[:sample_size]:
    try:
        sample_bytes += f.stat().st_size
    except Exception:
        pass
avg_size = sample_bytes / sample_size if sample_size else 0
est_total_mb = (avg_size * total_files) / (1024 * 1024)
est_chunks = int((avg_size * total_files) / 500)  # ~500 chars per chunk
est_vector_mb = est_chunks * 384 * 4 / (1024 * 1024)  # float32 × 384-dim

print(f'Files: {total_files}')
print(f'Estimated total text: {est_total_mb:.0f} MB')
print(f'Estimated chunks: ~{est_chunks}')
print(f'Estimated vector RAM: ~{est_vector_mb:.0f} MB')
print(f'Embedding model: all-MiniLM-L6-v2 (SentenceTransformers, ~500MB RAM)')
print(f'Batch size: 200 chunks/flush (reduces I/O by ~200×)')
print()

# ---- Step 4: Ingest ------------------------------------------------
svc = MemoryService()
svc.ingest_progress = type('sig', (), {'emit': lambda s, m: print(m)})()

start = time.time()
print('Starting ingestion (HTML stripped, batched)...\n')
n = svc.ingest_all()
elapsed = time.time() - start

print(f'\n--- Done: {n} files indexed, {svc.stats()["chunk_count"]} chunks '
      f'in {elapsed:.0f}s ({n/elapsed:.1f} files/s) ---')

if svc.is_ready:
    print('\n=== Semantic search test ===')
    queries = ['concrete pour schedule', 'RFI submission', 'progress claim', 'cashflow forecast']
    for q in queries:
        print(f'\n--- "{q}" ---')
        results = svc.search(q, top_k=2)
        for r in results:
            print(f'  [{r["project"]}] {r["file_name"][:55]} (score={r["score"]:.3f})')
            snippet = r['text'][:150].replace('\n', ' ')
            print(f'    {snippet}...')

    print('\n\n=== LLM cleaning demo (1 email only, for comparison) ===')
    print('Running MessageFilterCrew on a single email...')
    # Pick the first indexed file
    sample = list(Path(os.environ['LILAMY_DATA_DIR']).rglob('*.txt'))[0]
    raw = original_extract(sample)
    from amail.crew import MessageFilterCrew
    try:
        result = MessageFilterCrew().crew().kickoff(inputs={
            'email_body': raw[:4000],
            'email_subject': sample.stem,
            'email_sender': 'unknown',
            'email_received_time': '2025',
        })
        cleaned = result.raw if hasattr(result, 'raw') else str(result)
        print(f'  Raw length: {len(raw)} chars')
        print(f'  LLM-cleaned length: {len(cleaned)} chars')
        print(f'  Preview: {cleaned[:300]}...')
    except Exception as e:
        print(f'  LLM cleaning failed: {e}')
