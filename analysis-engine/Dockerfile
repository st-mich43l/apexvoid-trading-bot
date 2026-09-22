FROM golang:1.23.12-alpine3.21 AS build

WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o /out/analysis-engine ./cmd/analysis-engine \
    && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -trimpath -ldflags='-s -w' -o /out/kafka-admin ./cmd/kafka-admin

FROM alpine:3.21
RUN addgroup -S apexvoid && adduser -S -G apexvoid apexvoid && apk add --no-cache wget
COPY --from=build /out/analysis-engine /app/analysis-engine
COPY --from=build /out/kafka-admin /app/kafka-admin
USER apexvoid
WORKDIR /app
ENTRYPOINT ["/app/analysis-engine"]
