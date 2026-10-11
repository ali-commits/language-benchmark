FROM mcr.microsoft.com/dotnet/sdk:10.0.401-noble@sha256:e70cdb7f80b0348f5cb85f19a8f670fca061f033d57eed12fa003d58b0e06317 AS build
WORKDIR /src
COPY backends/csharp/aspnet-core/global.json backends/csharp/aspnet-core/BenchmarkApi.csproj backends/csharp/aspnet-core/packages.lock.json ./
RUN dotnet restore --locked-mode
COPY backends/csharp/aspnet-core/*.cs ./
COPY backends/csharp/aspnet-core/tests/BenchmarkApi.Tests.csproj backends/csharp/aspnet-core/tests/packages.lock.json ./tests/
RUN dotnet restore tests/BenchmarkApi.Tests.csproj --locked-mode
COPY backends/csharp/aspnet-core/tests/*.cs ./tests/
RUN dotnet test --project tests/BenchmarkApi.Tests.csproj --configuration Release --no-restore && dotnet publish BenchmarkApi.csproj --configuration Release --no-restore --output /out
FROM mcr.microsoft.com/dotnet/aspnet:10.0.12-noble@sha256:222759b391a1aaf241166672c8f99b2d4ada452e7b5319f3c6e8f265a37b5ad4
ARG DB_POOL_MAX_SIZE=20
ARG DB_POOL_MIN_SIZE=0
LABEL org.language-benchmark.language="csharp" \
 org.language-benchmark.runtime-version="10.0.12" \
 org.language-benchmark.framework="aspnet-core" \
 org.language-benchmark.framework-version="10.0.12" \
 org.language-benchmark.workers="1" \
 org.language-benchmark.connections-per-worker="$DB_POOL_MAX_SIZE"
WORKDIR /app
COPY --from=build /out ./
ENV BIND_IP=0.0.0.0 PORT=8000 DOTNET_PROCESSOR_COUNT=2 DOTNET_GCHeapHardLimitPercent=0x41
USER $APP_UID
ARG HASH_CONCURRENCY=2
RUN case "$HASH_CONCURRENCY" in 1|2|3|4) ;; *) echo "invalid HASH_CONCURRENCY" >&2; exit 1 ;; esac
ENV HASH_CONCURRENCY=$HASH_CONCURRENCY
ENV DB_POOL_MAX_SIZE=$DB_POOL_MAX_SIZE DB_POOL_MIN_SIZE=$DB_POOL_MIN_SIZE
ARG DB_PREPARE_ENABLED=false
ENV DB_PREPARE_ENABLED=$DB_PREPARE_ENABLED
EXPOSE 8000
ENTRYPOINT ["dotnet","BenchmarkApi.dll"]
