# One shared Debian/compiler stage for all source-built interpreter cells.
ARG BASE=debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a
FROM ${BASE} AS compiler
RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates gnupg python3 pkg-config libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libffi-dev liblzma-dev libgdbm-dev uuid-dev tk-dev && rm -rf /var/lib/apt/lists/*
FROM compiler AS build
ARG PYTHON_VERSION
ARG SOURCE_SHA256
ARG BUILD_MODE=standard
ARG JOBS=4
ARG OPTIMIZE=1
RUN curl -fsSL "https://www.python.org/ftp/python/$(echo "$PYTHON_VERSION" | sed 's/rc.*//')/Python-${PYTHON_VERSION}.tar.xz" -o /tmp/python.tar.xz && echo "$SOURCE_SHA256  /tmp/python.tar.xz" | sha256sum -c - && tar -xf /tmp/python.tar.xz -C /tmp
# LLVM 21 is only a build dependency for JIT stencils; package identity is captured below.
RUN if [ "$BUILD_MODE" = jit ]; then curl -fsSL https://apt.llvm.org/llvm-snapshot.gpg.key | gpg --dearmor -o /usr/share/keyrings/llvm.gpg && echo 'deb [signed-by=/usr/share/keyrings/llvm.gpg] https://apt.llvm.org/trixie/ llvm-toolchain-trixie-21 main' > /etc/apt/sources.list.d/llvm.list && apt-get update && apt-get install -y clang-21 llvm-21; fi
RUN cd /tmp/Python-${PYTHON_VERSION} && flags='' && if [ "$OPTIMIZE" = 1 ]; then flags='--enable-optimizations --with-lto'; fi && if [ "$BUILD_MODE" = jit ]; then flags="$flags --enable-experimental-jit=yes-off"; elif [ "$BUILD_MODE" = free-threaded ]; then flags="$flags --disable-gil"; fi && ./configure --prefix=/opt/python --with-ensurepip=install $flags && make -j${JOBS} && make install && /opt/python/bin/python3 -VV > /opt/python/build-info.txt && gcc --version >> /opt/python/build-info.txt && dpkg-query -W >> /opt/python/build-info.txt
FROM ${BASE} AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libssl3t64 zlib1g libbz2-1.0 libreadline8t64 libsqlite3-0 libffi8 liblzma5 libgdbm6t64 libuuid1 libpq5 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/python /opt/python
ENV PATH=/opt/python/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
