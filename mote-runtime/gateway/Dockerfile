# The command is intentionally a scaffold entry point until inbound invocation
# wiring is supplied by mote-infra/invocation. Keep the image build reproducible
# and free of CGO in the meantime.
FROM golang:1.26.5-alpine AS build

WORKDIR /workspace
COPY src/go.mod ./src/go.mod
COPY src/ ./src/

ARG VERSION=dev
ARG TARGETOS=linux
ARG TARGETARCH=amd64
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} \
	go -C src build -trimpath -ldflags "-s -w -X github.com/bushihelmy-crypto/motev2/mote-runtime/gateway/internal/buildinfo.Version=${VERSION}" \
	-o /out/gateway ./cmd/gateway

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/gateway /gateway
USER nonroot:nonroot
ENTRYPOINT ["/gateway"]
