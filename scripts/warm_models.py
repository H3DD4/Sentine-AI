"""
Download and cache the local ML models, deliberately.

Serving never downloads: `ALLOW_MODEL_DOWNLOADS` defaults to false, so an
uncached model degrades the query instead of blocking it on a multi-gigabyte
fetch. That is the right default for a machine on a constrained link — but the
models do have to arrive somehow, and this is the supervised place for it.

Run it when you have bandwidth to spare:

    ./.venv/Scripts/python.exe -m scripts.warm_models

It reports what is already cached before fetching anything, so you can see the
cost before paying it, and it can be interrupted and resumed — HuggingFace
keeps partial blobs and continues from where it stopped.

    --check    report cache status and exit without downloading
    --only X   warm just one model: dense | sparse | reranker
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from app.config import settings

# Approximate on-disk sizes, for the pre-flight report. Being told "this will
# fetch ~1.1 GB" before it starts is the whole point of the --check pass.
_APPROX_MB = {"dense": 420, "sparse": 45, "reranker": 1100}


def _cache_root() -> str:
    return os.environ.get(
        "HF_HOME", os.path.join(os.path.expanduser("~"), ".cache", "huggingface")
    )


def _repo_dir(model_id: str) -> str:
    return os.path.join(
        _cache_root(), "hub", "models--" + model_id.replace("/", "--")
    )


def _dir_mb(path: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1024 * 1024)


#: fastembed does not use the HF hub layout — it unpacks into its own cache
#: directory, so BM25 has to be probed there instead.
_FASTEMBED_CACHE = os.path.join(
    os.environ.get("TEMP", "/tmp"), "fastembed_cache"
)


def _status(model_id: str) -> tuple[str, float, bool]:
    """
    (state, cached_mb, complete).

    Completeness is judged by whether the snapshot holds real weights, not by
    the absence of `.incomplete` files. Those two differ in practice: an
    abandoned fetch of an *optional* file (an ONNX export, an alternate
    precision) leaves a zero-byte `.incomplete` behind forever, and treating
    that as "partial" reported a fully working model as unusable — which would
    send the operator off to re-download 420 MB they already had.
    """
    if model_id == "Qdrant/bm25":
        d = _FASTEMBED_CACHE
        if not os.path.isdir(d):
            return "missing", 0.0, False
        mb = _dir_mb(d)
        config_found = any(
            "snapshots" in root and "config.json" in files
            for root, _dirs, files in os.walk(d)
        )
        return ("cached", mb, True) if config_found else ("partial", mb, False)

    d = _repo_dir(model_id)
    if not os.path.isdir(d):
        return "missing", 0.0, False
    mb = _dir_mb(d)

    snaps = os.path.join(d, "snapshots")
    weights = ("model.safetensors", "pytorch_model.bin", "model.onnx")
    have_weights = False
    if os.path.isdir(snaps):
        for root, _ds, files in os.walk(snaps):
            for f in files:
                if f in weights:
                    # Snapshot entries are symlinks into blobs/; a link whose
                    # target is missing or empty is not a usable weight file.
                    try:
                        if os.path.getsize(os.path.join(root, f)) > 1024:
                            have_weights = True
                    except OSError:
                        pass
    if have_weights:
        return "cached", mb, True
    return ("partial", mb, False) if mb > 1 else ("missing", mb, False)


def _models() -> dict[str, str]:
    from app.ingestion.embedder import SPARSE_MODEL_NAME

    return {
        "dense": settings.EMBEDDING_MODEL,
        "sparse": SPARSE_MODEL_NAME,
        "reranker": settings.RERANKER_MODEL,
    }


def report() -> dict[str, tuple[str, float, bool]]:
    out = {}
    print(f"  cache root : {_cache_root()}\n")
    for kind, model_id in _models().items():
        state, mb, complete = _status(model_id)
        out[kind] = (state, mb, complete)
        need = "" if complete else f"  (~{_APPROX_MB[kind]} MB to fetch)"
        print(f"  {kind:<9} {state:<8} {mb:7.0f} MB on disk   {model_id}{need}")
    return out


def warm(kind: str) -> bool:
    """Load one model with downloads enabled. Returns True on success."""
    # The loaders read this flag; overriding the in-memory settings object is
    # what separates this script from serving, which must never download.
    settings.ALLOW_MODEL_DOWNLOADS = True

    started = time.monotonic()
    try:
        if kind == "dense":
            from app.ingestion.embedder import load_model_sync

            load_model_sync()
        elif kind == "sparse":
            from app.ingestion.embedder import load_sparse_model_sync

            load_sparse_model_sync()
        elif kind == "reranker":
            from app.services.retrieval import load_reranker_sync, reranker_status

            load_reranker_sync()
            if reranker_status():
                print(f"  ERROR: reranker did not load: {reranker_status()}")
                return False
        else:
            raise ValueError(f"unknown model kind: {kind}")
    except Exception as exc:
        print(f"  ERROR: {kind} failed: {type(exc).__name__}: {exc}")
        return False

    print(f"  OK: {kind} ready in {time.monotonic() - started:.0f}s")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="report and exit")
    ap.add_argument("--only", choices=["dense", "sparse", "reranker"])
    args = ap.parse_args()

    print("\n=== Model cache status " + "=" * 50)
    before = report()

    if args.check:
        missing = [k for k, (_s, _m, c) in before.items() if not c]
        print(
            f"\n  {len(before) - len(missing)}/{len(before)} models cached."
            + (f" Missing: {', '.join(missing)}" if missing else " Nothing to fetch.")
        )
        # Retrieval runs without the reranker, so an uncached reranker alone is
        # not an error condition — only the dense model is load-bearing.
        return 1 if not before["dense"][2] else 0

    kinds = [args.only] if args.only else ["dense", "sparse", "reranker"]
    todo = [k for k in kinds if not before[k][2]]
    if not todo:
        print("\n  Everything requested is already cached — nothing to download.")
        return 0

    total = sum(_APPROX_MB[k] for k in todo)
    print(f"\n=== Warming {', '.join(todo)} (~{total:.0f} MB) " + "=" * 30)
    print("  Interrupt with Ctrl-C at any time; partial downloads resume.\n")

    ok = True
    for kind in todo:
        ok = warm(kind) and ok

    print("\n=== Final status " + "=" * 56)
    report()
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  Interrupted — partial downloads are kept and will resume.")
        sys.exit(130)
