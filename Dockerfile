FROM python:3.12-slim
WORKDIR /app
COPY app.py make_sample.py ./
COPY data ./data
RUN pip install --no-cache-dir fastapi uvicorn python-multipart
EXPOSE 8003
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003"]
