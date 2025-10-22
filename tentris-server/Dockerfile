# 1. Start from the official Tentris image
FROM tentris/tentris:latest

# 2. Set a working directory inside the container
WORKDIR /app

# 3. Copy your local config and data files into the container
COPY config.toml /app/config.toml
COPY instance_data.ttl /app/instance_data.ttl

# 4. (The magic step) Run the offline data load *during the build*.
# This creates the database at /data inside the image.
# This step will take a few minutes.
RUN tentris load \
    --datastore-path /data \
    --file /app/instance_data.ttl

# 5. Set the default command to run when the container starts.
# It serves the pre-loaded data from /data using your config file.
CMD ["tentris", "serve", \
     "--config", "/app/config.toml", \
     "--datastore-path", "/data"]
