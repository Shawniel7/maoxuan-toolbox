"""Vectorize chunks → faiss index.

Default: BAAI/bge-small-zh-v1.5 via sentence-transformers (local, free, ~100MB DL).
Providers are selected via env var EMBEDDING_PROVIDER: local | voyage | openai.

Features:
    - batch processing (batch_size=32) to bound memory
    - tqdm progress bar with ETA
    - checkpoint every 500 chunks so Ctrl-C / crash doesn't lose hours of work
    - on resume: reads existing checkpoints, skips already-embedded chunks, continues
    - output: corpus/index.faiss + corpus/chunk_id_map.json (row → chunk_id)
    - checkpoint dir removed after successful consolidation

CLI:
    python -m backend.ingest.embedder
    python -m backend.ingest.embedder --resume   # forced resume
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Protocol

CORPUS = Path(__file__).resolve().parents[2] / "corpus"
CHUNKS_JSONL = CORPUS / "chunks.jsonl"
INDEX_FAISS = CORPUS / "index.faiss"
CHUNK_ID_MAP = CORPUS / "chunk_id_map.json"
CHECKPOINT_DIR = CORPUS / "_embed_checkpoints"

EMBED_BATCH = 32
CHECKPOINT_EVERY = 500


class Embedder(Protocol):
    """Shared interface across local/voyage/openai backends."""
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def get_embedder() -> Embedder:
    """Instantiate the embedder selected by EMBEDDING_PROVIDER (default: local)."""
    provider = os.environ.get("EMBEDDING_PROVIDER", "local").lower()
    if provider == "local":
        return _LocalBGE()
    if provider == "voyage":
        raise NotImplementedError("Voyage provider not implemented — set EMBEDDING_PROVIDER=local")
    if provider == "openai":
        raise NotImplementedError("OpenAI provider not implemented — set EMBEDDING_PROVIDER=local")
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {provider}")


class _LocalBGE:
    """BAAI/bge-small-zh-v1.5 via sentence-transformers. dim=512."""
    dim = 512
    model_id = "BAAI/bge-small-zh-v1.5"

    def __init__(self) -> None:
        # Check HF cache to decide if this is a first-run download
        hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
        model_dir_name = f"models--{self.model_id.replace('/', '--')}"
        already_cached = (hf_cache / model_dir_name).exists()
        if not already_cached:
            print(f"[embedder] First run: downloading {self.model_id} (~100MB) to HF cache, one-time.",
                  flush=True)
        else:
            print(f"[embedder] {self.model_id} already in HF cache — loading.", flush=True)

        # Heavy import deferred to actual use
        from sentence_transformers import SentenceTransformer
        import numpy as np  # noqa: F401  (just checking availability early)

        self.model = SentenceTransformer(self.model_id)
        # bge-small-zh default dim is 512; confirm at runtime
        self.dim = self.model.get_sentence_embedding_dimension()

    def embed(self, texts: list[str]) -> "list[list[float]]":
        # bge recommends prefixing queries (not passages) with an instruction; for
        # corpus passages we pass raw text. normalize_embeddings=True so inner product
        # = cosine similarity (faiss.IndexFlatIP is correct).
        emb = self.model.encode(
            texts,
            batch_size=EMBED_BATCH,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return emb


# ───────────────────────── checkpointing ─────────────────────────

def _load_chunks() -> list[dict]:
    chunks = []
    with open(CHUNKS_JSONL, encoding="utf-8") as f:
        for line in f:
            chunks.append(json.loads(line))
    return chunks


def _list_checkpoints() -> list[Path]:
    if not CHECKPOINT_DIR.exists():
        return []
    return sorted(CHECKPOINT_DIR.glob("batch_*.npy"))


def _checkpoint_ids_path(npy_path: Path) -> Path:
    return npy_path.with_name(npy_path.stem.replace("batch_", "chunk_ids_") + ".json")


def _resume_state() -> tuple[list, list[str]]:
    """Reconstruct completed state from checkpoints. Returns (vectors_list, chunk_ids_list)."""
    import numpy as np

    ckpts = _list_checkpoints()
    if not ckpts:
        return [], []
    vectors = []
    ids: list[str] = []
    for p in ckpts:
        vec = np.load(p)
        ids_path = _checkpoint_ids_path(p)
        with open(ids_path, encoding="utf-8") as f:
            ids_batch = json.load(f)
        assert len(ids_batch) == vec.shape[0], f"checkpoint {p} size mismatch"
        vectors.append(vec)
        ids.extend(ids_batch)
    return vectors, ids


def _save_checkpoint(vectors_batch, ids_batch: list[str], batch_idx: int) -> None:
    import numpy as np
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    np_path = CHECKPOINT_DIR / f"batch_{batch_idx:05d}.npy"
    ids_path = CHECKPOINT_DIR / f"chunk_ids_{batch_idx:05d}.json"
    np.save(np_path, vectors_batch)
    with open(ids_path, "w", encoding="utf-8") as f:
        json.dump(ids_batch, f, ensure_ascii=False)


# ───────────────────────── main embed loop ─────────────────────────

def embed_all(resume: bool = True) -> int:
    """Read chunks.jsonl, embed in batches, write faiss index. Returns count."""
    import numpy as np
    import faiss
    from tqdm import tqdm

    chunks = _load_chunks()
    total = len(chunks)
    print(f"[embedder] {total} chunks to embed", flush=True)

    # Resume?
    resumed_vectors, resumed_ids = ([], [])
    start_idx = 0
    if resume:
        resumed_vectors, resumed_ids = _resume_state()
        if resumed_ids:
            # Figure out where we stopped in the full chunk list. We assume chunks
            # are processed in original order; compare id sequence.
            for i, c in enumerate(chunks):
                if i >= len(resumed_ids):
                    start_idx = i
                    break
                if c["chunk_id"] != resumed_ids[i]:
                    # Order mismatch — safer to start over
                    print(f"[embedder] checkpoint order mismatch at row {i}; discarding and restarting",
                          flush=True)
                    resumed_vectors, resumed_ids = [], []
                    start_idx = 0
                    shutil.rmtree(CHECKPOINT_DIR, ignore_errors=True)
                    break
            else:
                start_idx = len(resumed_ids)
            print(f"[embedder] resumed from checkpoint: {start_idx}/{total} already embedded",
                  flush=True)

    model = get_embedder()
    dim = model.dim
    print(f"[embedder] model dim = {dim}", flush=True)

    all_ids: list[str] = list(resumed_ids)

    # Process remaining chunks in batches, checkpoint every CHECKPOINT_EVERY
    pending_vectors: list = []
    pending_ids: list[str] = []
    batch_idx = len(_list_checkpoints())  # start numbering after existing checkpoints

    t0 = time.time()
    remaining_chunks = chunks[start_idx:]
    pbar = tqdm(total=len(remaining_chunks), desc="embed", unit="chunk", ncols=100, mininterval=1.0)

    # Iterate in sub-batches of EMBED_BATCH
    i = 0
    while i < len(remaining_chunks):
        sub = remaining_chunks[i:i + EMBED_BATCH]
        texts = [c["text"] for c in sub]
        ids = [c["chunk_id"] for c in sub]
        vecs = model.embed(texts)
        pending_vectors.append(vecs)
        pending_ids.extend(ids)
        i += len(sub)
        pbar.update(len(sub))

        # Checkpoint if threshold reached
        if len(pending_ids) >= CHECKPOINT_EVERY:
            batch_idx += 1
            combined = np.vstack(pending_vectors)
            _save_checkpoint(combined, pending_ids, batch_idx)
            all_ids.extend(pending_ids)
            pending_vectors = []
            pending_ids = []
    pbar.close()

    # Final partial batch → checkpoint
    if pending_ids:
        import numpy as np
        batch_idx += 1
        combined = np.vstack(pending_vectors)
        _save_checkpoint(combined, pending_ids, batch_idx)
        all_ids.extend(pending_ids)

    elapsed = time.time() - t0
    print(f"[embedder] embedding complete in {elapsed:.1f}s ({elapsed/60:.1f} min)", flush=True)

    # Consolidate: load all checkpoints into a single faiss index
    print(f"[embedder] consolidating checkpoints → faiss index", flush=True)
    all_vecs = []
    all_ids_ordered: list[str] = []
    for p in _list_checkpoints():
        all_vecs.append(np.load(p))
        with open(_checkpoint_ids_path(p), encoding="utf-8") as f:
            all_ids_ordered.extend(json.load(f))
    matrix = np.vstack(all_vecs).astype("float32")
    assert matrix.shape[0] == len(all_ids_ordered) == total, (
        f"consolidation size mismatch: matrix={matrix.shape[0]} "
        f"ids={len(all_ids_ordered)} total={total}"
    )

    # Build faiss IP index (vectors are already L2-normalized, so IP = cosine)
    index = faiss.IndexFlatIP(dim)
    index.add(matrix)
    faiss.write_index(index, str(INDEX_FAISS))

    # chunk_id_map: row-index → chunk_id (list, index is the row)
    with open(CHUNK_ID_MAP, "w", encoding="utf-8") as f:
        json.dump(all_ids_ordered, f, ensure_ascii=False)

    # Verify
    assert index.ntotal == total, f"index.ntotal={index.ntotal} != {total}"
    assert len(set(all_ids_ordered)) == len(all_ids_ordered) == total, "chunk_id bijection broken"

    print(f"[embedder] wrote {INDEX_FAISS} ({INDEX_FAISS.stat().st_size/1024/1024:.1f} MB)")
    print(f"[embedder] wrote {CHUNK_ID_MAP} ({CHUNK_ID_MAP.stat().st_size/1024:.1f} KB)")

    # Clean up checkpoints
    shutil.rmtree(CHECKPOINT_DIR, ignore_errors=True)
    print(f"[embedder] cleaned up {CHECKPOINT_DIR}")

    return total


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true", help="Resume from existing checkpoints")
    ap.add_argument("--no-resume", action="store_true", help="Ignore checkpoints, start fresh")
    args = ap.parse_args()

    resume = True
    if args.no_resume:
        resume = False
        if CHECKPOINT_DIR.exists():
            print(f"[embedder] --no-resume: removing existing checkpoints")
            shutil.rmtree(CHECKPOINT_DIR, ignore_errors=True)

    embed_all(resume=resume)


if __name__ == "__main__":
    main()
