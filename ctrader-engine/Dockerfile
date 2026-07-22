FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src

COPY src/CTraderFeed.csproj src/
RUN dotnet restore src/CTraderFeed.csproj
RUN apt-get update \
  && apt-get install -y --no-install-recommends clang zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

COPY src/ src/
ARG PUBLISH_AOT=true
RUN if [ "$PUBLISH_AOT" = "true" ]; then \
    dotnet publish src/CTraderFeed.csproj \
      -c Release \
      -r linux-x64 \
      --self-contained true \
      -o /out \
      /p:PublishAot=true \
      /p:PublishTrimmed=true \
      /p:PublishSingleFile=false; \
  else \
    dotnet publish src/CTraderFeed.csproj \
      -c Release \
      -r linux-x64 \
      --self-contained true \
      -o /out \
      /p:PublishAot=false \
      /p:PublishTrimmed=true \
      /p:PublishSingleFile=true; \
  fi

FROM mcr.microsoft.com/dotnet/runtime-deps:8.0 AS runtime
WORKDIR /app
COPY --from=build /out/ctrader-feed /app/ctrader-feed
COPY docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod 0755 /app/docker-entrypoint.sh

ENTRYPOINT ["/app/docker-entrypoint.sh"]
