FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY birthday_bot ./birthday_bot
COPY main.py README.md .env.example ./
COPY ./*.xlsx ./

CMD ["python", "main.py"]
