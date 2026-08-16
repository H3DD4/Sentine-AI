# Model Setup

The Python packages and model weights are separate deployment artifacts.
`requirements.txt` installs the application and model-loading libraries. The
weights are cached locally by the supervised warm-up command and are not
downloaded while the application is serving requests.

## CPU setup

```powershell
python -m pip install -r requirements.txt
python -m scripts.warm_models --only dense
python -m scripts.warm_models --only sparse
```

The default local profile uses multilingual BGE-M3 plus BM25 hybrid retrieval.
The large cross-encoder reranker is optional and is disabled in the delivered
local profile to keep query latency predictable.

## NVIDIA setup

Use this only on a machine with an NVIDIA driver that supports CUDA 12.1 or
newer:

```powershell
python -m pip install -r requirements-gpu.txt
python -m scripts.warm_models --only dense
python -m scripts.warm_models --only sparse
```

Verify CUDA before indexing:

```powershell
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

## Optional reranker

The multilingual reranker is already configured and can be warmed explicitly:

```powershell
python -m scripts.warm_models --only reranker
```

It adds inference latency and is not required for serving. Enable it only on a
machine where the measured response time and available memory are acceptable.

## First indexing

After the dense and sparse models are cached, rebuild indexes after changing
the embedding model:

```powershell
python -m scripts.rebuild_vector_indexes --source all
```

The rebuild is a one-time migration per source. PostgreSQL remains the source
of truth; Qdrant stores the derived searchable vectors and can be rebuilt from
PostgreSQL if its persistent volume is replaced.
