# Backend image for a Hugging Face Space (Docker SDK, free CPU tier: 2 vCPU, 16 GB RAM).
# Built from the folder that `uv run python scripts/deploy_hf.py stage` assembles.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/home/user/.cache/huggingface

# Spaces run the container as uid 1000.
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

# CPU-only PyTorch first (the default Linux wheel bundles CUDA: ~2 GB we don't need),
# then the locked dependencies exported from uv.lock.
COPY --chown=user requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu "$(grep -E '^torch==' requirements.txt)" \
 && pip install -r requirements.txt

COPY --chown=user pyproject.toml README.md ./
COPY --chown=user src ./src
COPY --chown=user corpus ./corpus
RUN pip install --no-deps .

USER user
# Bake the e5 model into the image so a cold start doesn't download ~470 MB.
RUN python -c "from sentence_transformers import SentenceTransformer as S; S('intfloat/multilingual-e5-small')" \
 && mkdir -p data models

EXPOSE 7860
# bank.db + the intent model come from a PRIVATE dataset at startup (cardagent.bootstrap).
CMD ["sh", "-c", "python -m cardagent.bootstrap && uvicorn --factory cardagent.api.main:create_app --host 0.0.0.0 --port 7860"]
