# MinIO Community is source-only. Build a fixed upstream release locally.
FROM golang:1.24-bookworm AS builder
ENV CGO_ENABLED=0
RUN go install github.com/minio/minio@RELEASE.2025-09-07T16-13-09Z

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home minio \
    && mkdir /data && chown minio:minio /data
COPY --from=builder /go/bin/minio /usr/local/bin/minio
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
