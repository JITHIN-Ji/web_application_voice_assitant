FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y build-essential

# Copy dependency file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy all project files
COPY . .

# Render sets PORT automatically
ENV PORT=8080

# Expose the port
EXPOSE 8080

# Start FastAPI using uvicorn and entry file app.py
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
