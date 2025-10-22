## Tentris Server (Dockerized)

This directory contains the necessary files to build and run a pre-loaded, read-only Tentris database in a Docker container.

This setup is intended to provide a central, high-performance SPARQL endpoint for an event like a hackathon.

### Quick Start

1. Prerequisites

Docker must be installed.

You must have the instance_data.ttl file (which is not in Git) placed in this directory.

2. Build the Image

From the root of the open-pulse repository, run the docker build command. This will execute the Dockerfile, which includes the (long) tentris load step. This may take 5-10 minutes.

docker build -t tentris-hackathon -f my-tentris-server/Dockerfile .


Note: We use -f to point to the Dockerfile's location, and . to set the build context to the repo root, which is needed to copy the files.

3. Run the Container

Once the image is built, you can run it:

docker run -d \
  -p 9080:9080 \
  --name hackathon-db \
  tentris-hackathon


-d: Detached mode (runs in the background).

-p 9080:9080: Maps your host machine's port 9080 to the container's port 9080.

--name hackathon-db: Gives your container a friendly name.

The server is now running and accessible at http://<your-server-ip>:9080.