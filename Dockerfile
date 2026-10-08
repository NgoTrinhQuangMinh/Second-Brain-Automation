FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY requirements-api.txt ./
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements-api.txt
ENV HF_HOME=/app/model-cache
RUN python -c "from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions; from docling.models.utils.hf_model_download import download_hf_model; from docling.models.stages.table_structure.table_structure_model import TableStructureModel; spec=LayoutObjectDetectionOptions().model_spec; download_hf_model(spec.repo_id, revision=spec.revision); TableStructureModel.download_models()"
COPY src/ ./src/
COPY label_rules.yaml ./
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 DATA_DIR=/data OMP_NUM_THREADS=2 HF_HUB_OFFLINE=1
CMD ["sh", "-c", "exec uvicorn brain_loader.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
