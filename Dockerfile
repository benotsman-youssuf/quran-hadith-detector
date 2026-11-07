FROM python:3.12-slim AS index-build
WORKDIR /build
COPY requirements-build.txt .
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements-build.txt
COPY hadith_engine/ hadith_engine/
COPY tools/ tools/
COPY youtube_transcript.py .
COPY youtube_transcript.py .
RUN python -m tools.prepare_hadith
RUN python -m tools.build_index --corpus-dir data/prepared --output-dir hadith_engine/_data
COPY youtube_transcript.py .

FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static/ static/
COPY hadith_engine/ hadith_engine/
COPY tools/ tools/
COPY youtube_transcript.py .
COPY --from=index-build /build/hadith_engine/_data hadith_engine/_data
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
