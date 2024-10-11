FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install kubectl
RUN curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" \
    && install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# Install Poetry
RUN pip install --no-cache-dir poetry

# Copy project files
COPY . /app

# Install dependencies
RUN poetry install

# Set environment variable for Flask
ENV FLASK_APP=kube_trim.server:create_app

# Expose the port
EXPOSE 8069

# Run the application
CMD ["poetry", "run", "flask", "run", "--host=0.0.0.0", "--port=8069"]