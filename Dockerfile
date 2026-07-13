FROM python:3.11-alpine
WORKDIR /app
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py requirements.txt ./
EXPOSE 8080
CMD ["python", "server.py", "--http"]
