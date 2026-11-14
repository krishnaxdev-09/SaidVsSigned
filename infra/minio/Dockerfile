# Q-16 (option b): the official minio/minio and minio/mc images can no longer be pulled,
# so we build the same pinned releases from the upstream source (AGPL-3.0, unmodified).
ARG GO_VERSION=1.24
FROM golang:${GO_VERSION}-bookworm AS build
ARG MINIO_TAG=RELEASE.2025-04-22T22-12-26Z
ARG MC_TAG=RELEASE.2025-04-16T18-13-26Z
ENV CGO_ENABLED=0
RUN mkdir -p /src/minio && curl -fsSL --retry 5 --retry-all-errors https://github.com/minio/minio/archive/refs/tags/${MINIO_TAG}.tar.gz \
  | tar -xz --strip-components=1 -C /src/minio \
 && cd /src/minio && GOFLAGS=-mod=mod go build -trimpath -ldflags "-s -w" -o /out/minio .
RUN mkdir -p /src/mc && curl -fsSL --retry 5 --retry-all-errors https://github.com/minio/mc/archive/refs/tags/${MC_TAG}.tar.gz \
  | tar -xz --strip-components=1 -C /src/mc \
 && cd /src/mc && GOFLAGS=-mod=mod go build -trimpath -ldflags "-s -w" -o /out/mc .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* && useradd -r -u 1000 minio && mkdir /data && chown minio /data
COPY --from=build /out/minio /out/mc /usr/local/bin/
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
