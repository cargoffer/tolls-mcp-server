FROM python:3.11-alpine
WORKDIR /app
RUN pip install --no-cache-dir httpx
COPY server.py .
EXPOSE 8080
CMD ["python", "server.py"]
