# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Install ffmpeg for yt-dlp media processing
RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your app's code into the container
COPY . .

# Make port 5000 available to the outside
EXPOSE 5000

# Command to run the application
CMD ["python", "app.py"]