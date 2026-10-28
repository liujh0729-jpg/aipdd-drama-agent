FROM oven/bun:1@sha256:0733e50325078969732ebe3b15ce4c4be5082f18c4ac1a0f0ca4839c2e4e42a7 AS builder

WORKDIR /build/web
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY ./web ./
COPY ./VERSION /build/VERSION
RUN DISABLE_ESLINT_PLUGIN='true' VITE_REACT_APP_VERSION=$(cat /build/VERSION) bun run build

FROM golang:1.26.1-alpine@sha256:2389ebfa5b7f43eeafbd6be0c3700cc46690ef842ad962f6c5bd6be49ed82039 AS builder2
ENV GO111MODULE=on CGO_ENABLED=0 GOWORK=off

ARG TARGETOS
ARG TARGETARCH
# [aipdd fork delta] Two build-time switches that upstream hard-codes, both of which bit
# this deployment locally.
#
# 1) GOARCH must fall back to the *build machine*, not to amd64. Upstream writes
#    ${TARGETARCH:-amd64}, which is right under buildx (where TARGETARCH is always set) and
#    wrong here: compose falls back to the classic builder when buildx is unavailable, the
#    classic builder leaves TARGETARCH empty, and so an arm64 host produced an amd64 binary
#    inside an arm64 image. The gateway then ran emulated and faulted 4x in ~40min of
#    ordinary task traffic, each crash in a different place with a mangled pointer - gorm
#    reading an eface whose type word held response text, compress/flate slicing past its
#    64KiB window, crypto/x509 growing a slice from a corrupt header. Every stack was on the
#    amd64 runtime path (memmove_amd64.s, asm_amd64.s) while `uname -m` inside the same
#    container said aarch64. An empty value lets the toolchain pick its own native default,
#    and buildx still overrides it when it supplies a target.
# 2) Green Tea GC stays off. It is still an experimental collector (Go 1.26.1 does not
#    default to it), this service is on the hot path for every generation the app makes, and
#    an experimental heap scanner on top of an emulated binary buys nothing but risk. Pinning
#    `no` rather than deleting the line means a base-image bump that flips it on cannot
#    silently reintroduce it.
ENV GOOS=${TARGETOS:-} GOARCH=${TARGETARCH:-}
ENV GOEXPERIMENT=nogreenteagc

WORKDIR /build

ADD go.mod go.sum ./
# relaykit is a local submodule referenced via replace; its go.mod must be
# present for go mod download to resolve the main module graph.
ADD relaykit/go.mod ./relaykit/go.mod
RUN go mod download

COPY . .
COPY --from=builder /build/web/dist ./web/dist
RUN go build -ldflags "-s -w -X 'github.com/dramaclaw/localapi/common.Version=$(cat VERSION)'" -o localapi

FROM debian:bookworm-slim@sha256:f06537653ac770703bc45b4b113475bd402f451e85223f0f2837acbf89ab020a

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata libasan8 wget \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

COPY --from=builder2 /build/localapi /
COPY LICENSE NOTICE THIRD-PARTY-LICENSES.md /licenses/
EXPOSE 3000
WORKDIR /data
ENTRYPOINT ["/localapi"]
