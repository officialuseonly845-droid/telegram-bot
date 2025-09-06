# Use Python 3.11 slim
FROM python:3.11-slim

# Set workdir
WORKDIR /app

# Copy files
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set environment variable placeholder (real token in Cloud Run env)
ENV TELEGRAM_TOKEN="YOUR_TELEGRAM_TOKEN"

# Run bot
CMD ["python", "bot.py"]
