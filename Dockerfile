# ===== Stage 1: Build =====
FROM golang:1.23-alpine AS builder

# CGO is required for go-sqlite3
RUN apk add --no-cache gcc musl-dev sqlite-dev

WORKDIR /app

# Cache dependencies
COPY go.mod go.sum ./
RUN go mod download

# Copy source code
COPY . .

# Build with CGO enabled for SQLite
RUN CGO_ENABLED=1 GOOS=linux go build -a -ldflags '-linkmode external -extldflags "-static"' -o /aggregator ./cmd/aggregator/

# ===== Stage 2: Runtime =====
FROM alpine:3.20

RUN apk add --no-cache \
    ca-certificates \
    git \
    tzdata \
    sqlite

# Create non-root user
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

# Copy binary
COPY --from=builder /aggregator /app/aggregator

# Create directories for data
RUN mkdir -p /app/data /app/blog && \
    chown -R appuser:appgroup /app

USER appuser

# Default config path
ENV CONFIG_PATH=/app/config/config.yaml

EXPOSE 8080

ENTRYPOINT ["/app/aggregator"]
CMD ["server", "--config", "/app/config/config.yaml"]
