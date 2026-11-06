# Runtime only: Python, docker CLI, kubectl and a few inspection commands.
# The app code is NOT in the image; compose.yaml mounts ./src, so a code fix is
# "edit the file, run again". The app uses only Python's standard library.
#
# Kept small so a scan stays clean: no pip, no compilers, no systemd. journalctl
# is the host's own, reached through "chroot /host". The last stage copies the
# result into one fresh layer, so removed files aren't left in older layers.
# Before a release: trivy image --severity HIGH,CRITICAL <this image>

# kubectl from its official modules, with the two libraries below newer than
# upstream ships: every released kubectl (1.34 to 1.37) still scans HIGH on them.
# When a release is clean, download it instead and drop this stage.
FROM golang:1.26.10-alpine AS kubectl
ARG K8S_MINOR=34
ARG K8S_PATCH=13
WORKDIR /src
COPY docker/kubectl-main.go main.go
RUN go mod init kubectl \
 && go get k8s.io/kubectl@v0.${K8S_MINOR}.${K8S_PATCH} k8s.io/client-go@v0.${K8S_MINOR}.${K8S_PATCH} \
           k8s.io/component-base@v0.${K8S_MINOR}.${K8S_PATCH} \
           golang.org/x/net@v0.62.0 golang.org/x/text@v0.43.0 \
 && go mod tidy \
 && v="v1.${K8S_MINOR}.${K8S_PATCH}" \
 && flags="" \
 && for pkg in k8s.io/client-go/pkg/version k8s.io/component-base/version; do \
      flags="$flags -X $pkg.gitVersion=$v -X $pkg.gitMajor=1 -X $pkg.gitMinor=${K8S_MINOR}"; \
    done \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w $flags" -o /kubectl .

FROM python:3.13-alpine AS build

ARG DOCKER_VERSION=29.9.3

RUN apk upgrade --no-cache \
 && apk add --no-cache procps-ng iproute2-minimal iproute2-ss \
 && apk add --no-cache --virtual .fetch curl \
 && curl -fsSL "https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz" \
    | tar -xz -C /usr/local/bin --strip-components=1 docker/docker \
 && apk del --no-cache .fetch \
 && pip uninstall -y --root-user-action=ignore pip \
 && cd /usr/local/lib/python3.13 \
 && rm -rf ensurepip idlelib tkinter turtledemo turtle.py pydoc_data lib2to3 \
 && find / -xdev -name __pycache__ -type d -prune -exec rm -rf {} + \
 && rm -rf /tmp/* /root/.cache \
 && printf '#!/bin/sh\nexec python -m troubleshooter.cli "$@"\n' > /usr/local/bin/ts \
 && chmod 755 /usr/local/bin/ts

FROM scratch
COPY --from=build / /
COPY --from=kubectl /kubectl /usr/local/bin/kubectl
ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin \
    LANG=C.UTF-8 PYTHONPATH=/app/src PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
CMD ["sleep", "infinity"]
