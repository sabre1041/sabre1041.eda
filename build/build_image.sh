#!/bin/bash -e

: ${TAG:="latest"}

# Change to script directory
cd "${0%/*}"

# Make sure credentials are set
if [[ -z "$RH_USERNAME" || -z "$RH_PASSWORD" ]]; then
  echo "Please set RH_USERNAME and RH_PASSWORD environment variables (perhaps in .env file)"
  exit 1
fi

# Function to build the Docker image
build_image() {
  docker login -u "$RH_USERNAME" -p "$RH_PASSWORD" registry.redhat.io
  ansible-builder build -t junipernetworks-k8s-de -f de-builder.yml --verbosity=3 --build-arg RH_USERNAME="$RH_USERNAME" --build-arg RH_PASSWORD="$RH_PASSWORD"
}

# Function to tag the Docker image
tag_image() {
  docker tag junipernetworks-k8s-de:latest "$REGISTRY_URL/junipernetworks-k8s-de:$TAG"
}

# Function to push the Docker image
push_image() {
  docker push "$REGISTRY_URL/junipernetworks-k8s-de:$TAG"
  echo "Decision environment image is pushed at $REGISTRY_URL/junipernetworks-k8s-de:$TAG"
}

if [[ -n "$REGISTRY_URL" ]]; then
  echo "Using REGISTRU_URL: $REGISTRY_URL"
else
  echo "REGISTRU_URL is not set. Tag/push will be skipped."
fi
echo "Using TAG: $TAG"

# get the collection version from TAG
collection_version=$(echo $TAG | cut -d'-' -f 1)
if [[ ! "$collecion_version" == "latest" ]]; then
  ansible_galaxy_version_arg="==$collection_version"
fi
if [[ ! -r collections/junipernetworks-eda.tar.gz ]]; then
  # otherwise, download the specific version
  ansible-galaxy collection download junipernetworks.eda${ansible_galaxy_version_arg}
  mv collections/junipernetworks-eda-*.tar.gz collections/junipernetworks-eda.tar.gz
fi

# Build the image
build_image

# Tag and push the image if REGISTRY_URL is set
if [[  -n "$REGISTRY_URL" ]]; then
  # Tag the image
  tag_image "$REGISTRY_URL" "$TAG"

  # Push the image
  push_image "$REGISTRY_URL" "$TAG"
else
  echo "Skipping pushing the image to registry"
  exit 0
fi
