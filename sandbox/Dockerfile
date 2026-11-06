FROM python:3.12-slim
RUN pip install --no-cache-dir pymupdf==1.26.* && useradd --uid 10001 --no-create-home parser
COPY parse_doc.py /w/parse_doc.py
USER 10001
ENTRYPOINT ["python", "-I", "/w/parse_doc.py"]
