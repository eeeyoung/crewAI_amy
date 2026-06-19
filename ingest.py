"""Hybrid ingestion engine — walks a CA project folder and builds the
relational+vector document store.

Architecture (from AMY_Architecture_and_Roadmap.md, Layer 1):
  ┌──────────────────────────────────────────────────┐
  │  FileRegistry (SQLite)                           │
  │  • project index, file_id, md5_hash, path        │
  │  • incremental change detection                  │
  │  • per-project statistics                        │
  └────────────┬─────────────────────────────────────┘
               │ file_id FK
  ┌────────────▼─────────────────────────────────────┐
  │  ChromaDB (vectors)                              │
  │  • chunked text embeddings                       │
  │  • project metadata for row-level filtering      │
  │  • cosine similarity search                      │
  └──────────────────────────────────────────────────┘

Usage:
    uv run python ingest.py                         # uses LILAMY_DATA_DIR
    uv run python ingest.py --data-dir D:/Projects  # custom path
    uv run python ingest.py --project ARCO          # single project only
    uv run python ingest.py --dry-run               # scan only, no embedding
    uv run python ingest.py --stats                 # show registry summary
    uv run python ingest.py --reset ARCO            # re-index a project
"""

import os
import sys
import time
from pathlib import Path

# ── .env support ───────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(_env_path)
except ImportError:
    pass

# ── CLI ────────────────────────────────────────────────────────────────

def _parse_args():
    args = sys.argv[1:]
    flags = {
        "data_dir": None,
        "project": None,
        "dry_run": False,
        "stats": False,
        "reset": None,
        "verbose": False,
    }
    while args:
        a = args.pop(0)
        if a == "--data-dir" and args:
            flags["data_dir"] = args.pop(0)
        elif a == "--project" and args:
            flags["project"] = args.pop(0)
        elif a == "--dry-run":
            flags["dry_run"] = True
        elif a == "--stats":
            flags["stats"] = True
        elif a == "--reset" and args:
            flags["reset"] = args.pop(0)
        elif a in ("--verbose", "-v"):
            flags["verbose"] = True
        elif a in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
        else:
            print(f"Unknown flag: {a}")
            print("Usage: uv run python ingest.py [--data-dir PATH] [--project NAME] [--dry-run] [--stats] [--reset NAME] [--verbose]")
            sys.exit(1)
    return flags


def _fmt_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def _fmt_duration(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m:.0f}m {s:.0f}s"
    h, m = divmod(m, 60)
    return f"{h:.0f}h {m:.0f}m {s:.0f}s"


# ── Project detection ──────────────────────────────────────────────────

def _detect_project(file_path: Path, root: Path) -> str:
    """Return the project name from the folder structure.

    Uses the first subdirectory under the data root as the project name.
    Files directly in the root are assigned to 'General'."""
    try:
        rel = file_path.relative_to(root)
        parts = rel.parts
        if len(parts) >= 2:
            return parts[0]
        return "General"
    except ValueError:
        return "General"


def _scan_directory(root: Path, target_project: str | None = None):
    """Walk the data root and yield (file_path, project_name) tuples.

    Skips hidden directories, .git, __pycache__, .chromadb, and the
    registry database itself.
    """
    skip_dirs = {
        ".git", "__pycache__", ".chromadb", ".lilamy_registry.db",
        "node_modules", ".venv", "venv", ".tox", ".mypy_cache",
    }
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip directories
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]

        for fname in filenames:
            fp = Path(dirpath) / fname
            project = _detect_project(fp, root)
            if target_project and project != target_project:
                continue
            yield fp, project


# All file types we attempt to process.  For binary/image formats where text
# extraction is impossible (DWG, TIF, PNG, SKP, ZIP), ingest_file_with_id()
# creates a metadata-only chunk so the file is still findable by name + path.
SUPPORTED_SUFFIXES = {
    # full text extraction
    ".txt", ".md", ".eml", ".json", ".csv", ".html",
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".xlsm",
    # Outlook message — try extract_msg, fall back to metadata-only
    ".msg",
    # metadata-only (no text extractor available)
    ".dwg", ".tif", ".png", ".skp", ".log",
    # archives — metadata-only (content is binary)
    ".zip",
}


def _is_supported(file_path: Path) -> bool:
    """Check if the file type can be extracted."""
    return file_path.suffix.lower() in SUPPORTED_SUFFIXES


# ── Main ───────────────────────────────────────────────────────────────

def main():
    flags = _parse_args()

    # Resolve data directory
    data_dir = flags["data_dir"] or os.environ.get("LILAMY_DATA_DIR")
    if not data_dir:
        print("ERROR: Set LILAMY_DATA_DIR env var or pass --data-dir")
        print("Usage: uv run python ingest.py --data-dir /path/to/projects")
        sys.exit(1)
    root = Path(data_dir).resolve()
    if not root.exists():
        print(f"ERROR: Data directory not found: {root}")
        sys.exit(1)

    os.environ["LILAMY_DATA_DIR"] = str(root)

    # Late imports (after env setup)
    from shared_tools.memory.file_registry import FileRegistry
    from shared_tools.memory.memory_service import MemoryService, _md5, _extract_text

    registry = FileRegistry(root)
    registry.init_db()

    # ── --stats mode ───────────────────────────────────────────────
    if flags["stats"]:
        _print_stats(registry)
        return

    # ── --reset mode ───────────────────────────────────────────────
    if flags["reset"]:
        n = registry.reset_project(flags["reset"])
        print(f"Reset {n} file(s) in project '{flags['reset']}' — will re-index on next run.")
        return

    # ── Scan phase ─────────────────────────────────────────────────
    print(f"Data root : {root}")
    print(f"Registry  : {registry.db_path}")
    target = flags["project"]
    if target:
        print(f"Project   : {target} (filtered)")
    print()

    # Step 1 — register all files in the SQLite registry.
    # For files where text extraction yields nothing (image PDFs, DWGs, etc.)
    # we use a surrogate hash based on path+size+mtime and let
    # ingest_file_with_id() create a metadata-only chunk.
    scan_start = time.time()
    registered = 0
    skipped = 0
    unsupported = 0
    metadata_only = 0
    total_bytes = 0

    for fp, project in _scan_directory(root, target):
        if not _is_supported(fp):
            unsupported += 1
            continue
        try:
            file_size = fp.stat().st_size
            if file_size == 0:
                continue

            mtime = None
            try:
                mtime = fp.stat().st_mtime
                from datetime import datetime, timezone
                mtime = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
            except Exception:
                pass

            text = _extract_text(fp)
            has_text = bool(text and text.strip())
            rel = str(fp.relative_to(root))

            if has_text:
                file_hash = _md5(text)
            else:
                # Surrogate hash for metadata-only files — based on path+size+mtime
                file_hash = _md5(f"{rel}:{file_size}:{mtime or ''}")
                metadata_only += 1

            total_bytes += file_size
            file_type = fp.suffix.lower().lstrip(".")

            # Check if already indexed with this hash
            if registry.is_file_indexed(project, rel, file_hash):
                skipped += 1
                continue

            registry.register(
                project=project,
                rel_path=rel,
                md5_hash=file_hash,
                file_size_bytes=file_size,
                file_type=file_type,
                last_modified=mtime,
            )
            registered += 1
        except Exception as e:
            if flags["verbose"]:
                print(f"  ⚠️  {fp.name}: {e}")

    scan_elapsed = time.time() - scan_start
    print(f"Scan complete in {_fmt_duration(scan_elapsed)}")
    print(f"  Files to index  : {registered}  (new or modified)")
    print(f"  Already indexed : {skipped}")
    print(f"  Of which metadata-only : {metadata_only}  (image PDFs, DWGs, etc.)")
    print(f"  Unsupported     : {unsupported}  (extension not recognised)")
    print(f"  Total file size : {_fmt_bytes(total_bytes)}")
    print()

    if registered == 0:
        print("Nothing new to index — everything is up to date.")
        _print_stats(registry)
        return

    if flags["dry_run"]:
        print("[dry-run] Would index the files listed above.  Exiting without embedding.")
        return

    # ── Index phase ─────────────────────────────────────────────────
    # We query the registry for files that need indexing (status!='indexed')
    # instead of re-scanning the filesystem.  This avoids redundant I/O and
    # correctly handles metadata-only files (image PDFs, DWGs, etc.).
    print(f"Starting embedding (ONNX, CPU, arena=off, 2 threads)...")
    index_start = time.time()
    svc = MemoryService(data_root=str(root))
    svc.ingest_progress = type("sig", (), {"emit": lambda s, m: print(f"  {m}")})()

    pending = registry.get_files_to_index(project=target)
    indexed_count = 0
    total_chunks = 0

    for row in pending:
        fp = root / row["rel_path"]
        file_id = row["file_id"]
        project = row["project"]
        file_hash = row["md5_hash"]

        if not fp.exists():
            registry.mark_error(file_id, "File missing from disk")
            continue

        try:
            n_chunks = svc.ingest_file_with_id(
                file_path=fp,
                root=root,
                file_id=file_id,
                project=project,
                file_hash=file_hash,
            )

            if n_chunks > 0:
                registry.mark_indexed(file_id, chunk_count=n_chunks)
                indexed_count += 1
                total_chunks += n_chunks

            # Flush periodically
            if len(svc._buffer["ids"]) >= 200:
                svc._flush_buffer()

            if flags["verbose"] and n_chunks > 0:
                print(f"  ✓ [{project}] {fp.name}  ({n_chunks} chunks)")

        except Exception as e:
            registry.mark_error(file_id, str(e)[:500])
            if flags["verbose"]:
                print(f"  ✗ {fp.name}: {e}")

    # Final flush
    svc._flush_buffer()
    index_elapsed = time.time() - index_start

    print(f"\nIndexing complete in {_fmt_duration(index_elapsed)}")
    print(f"  Files indexed : {indexed_count}")
    print(f"  Total chunks  : {total_chunks}")
    print(f"  Avg speed     : {indexed_count / index_elapsed:.1f} files/s"
          if index_elapsed > 0 else "")
    print()

    _print_stats(registry)


def _print_stats(registry):
    """Print per-project and global statistics."""
    s = registry.stats()
    print("═" * 55)
    print(f"  Registry: {s['total_files']} files, {_fmt_bytes(s['total_bytes'])}, {s['total_chunks']} chunks")
    print(f"  Projects: {len(s['projects'])}")
    for p in s["projects"]:
        ps = registry.project_stats(p)
        statuses = registry._conn.execute(
            "SELECT status, COUNT(*) AS n FROM file_registry WHERE project=? GROUP BY status",
            (p,),
        ).fetchall()
        status_str = ", ".join(f"{r['n']} {r['status']}" for r in statuses)
        print(f"    {p:<25}  {ps['file_count']:>4} files  {_fmt_bytes(ps['total_bytes']):>8}  {ps['total_chunks']:>5} chunks  [{status_str}]")
    print("═" * 55)


if __name__ == "__main__":
    main()
