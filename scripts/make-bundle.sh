#!/bin/sh
# Pack everything the air-gapped host needs into dist/troubleshooter-<version>.tar:
#   image.tar.xz  runtime image (docker load)
#   src/          the app code, mounted into the container
#   compose.yaml  with the prod model block switched on, and no build step
#   ts, README.md, INSTALL.txt
# No model weights: prod already serves its models. No secrets: .env stays behind.
set -eu
cd "$(dirname "$0")/.."

image=$(docker compose config --images)
version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/troubleshooter/__init__.py)
name="troubleshooter-$version"
out="dist/$name"

echo "Building runtime image $image ..."
docker compose build --quiet

echo "Running tests in the runtime image ..."
docker run --rm -v "$PWD":/app:ro -w /app -e PYTHONPATH=src "$image" \
  python -m unittest discover -s tests \
  || { echo "Tests failed: no bundle made."; exit 1; }

echo "Scanning $image ..."
trivy image -q --exit-code 1 --severity HIGH,CRITICAL "$image" \
  || { echo "HIGH or CRITICAL findings, or the scan failed: no bundle made."; exit 1; }

echo "Packing $name ..."
rm -rf "$out" && mkdir -p "$out/logs" "$out/kube" "$out/certs" "$out/runbooks" "$out/scripts"
docker save "$image" | xz -6 -T0 > "$out/image.tar.xz"
# Live never builds. Dev's free online model must not stay active on prod.
sed -e '/^    build: \.$/d' \
    -e 's|^  llm-url:   &llm-url https://integrate.*|  # (dev model removed)|' \
    -e 's|^  llm-model: &llm-model nvidia/nemotron-3.5-super-vl.*|  #|' \
    -e 's|^  # llm-url:   &llm-url http://<vllm-host>|  llm-url:   \&llm-url http://<vllm-host>|' \
    -e 's|^  # llm-model: &llm-model nvidia/nemotron-3-super|  llm-model: \&llm-model nvidia/nemotron-3-super|' \
    compose.yaml > "$out/compose.yaml"
cp ts README.md "$out/"
cp -r k8s "$out/"
cp scripts/make-kubeconfig.sh "$out/scripts/"
tar -cf - --exclude=__pycache__ src | tar -C "$out" -xf -
printf '# Secrets only. GPT-5.4 needs its key; vLLM needs none.\n# LLM_API_KEY=\n' > "$out/.env"
chmod 600 "$out/.env"

cat > "$out/INSTALL.txt" <<EOF
Troubleshooter $version: install on the air-gapped host (no internet needed)

  tar xf $name.tar && cd $name
  docker load < image.tar.xz
  vi compose.yaml      # settings block: llm-url
  docker compose up -d
  kubectl config use-context <your-context>
  ./ts check           # every line must say ok
  ./ts                 # chat

Update: load the new image if its tag changed, replace src/, run ./ts.
EOF
tar -C dist -cf "dist/$name.tar" "$name"
rm -rf "$out"
ls -lh "dist/$name.tar"
