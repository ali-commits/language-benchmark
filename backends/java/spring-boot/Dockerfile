FROM eclipse-temurin:27_35-jdk-resolute@sha256:8a59fff185d42ed6c80a2a8236ff45134063d00b648b7586a18a317de6676478 AS build
ARG MAVEN_VERSION=3.9.16
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/* && curl -fsSLo /tmp/maven.tar.gz https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/${MAVEN_VERSION}/apache-maven-${MAVEN_VERSION}-bin.tar.gz && curl -fsSLo /tmp/maven.tar.gz.sha512 https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/${MAVEN_VERSION}/apache-maven-${MAVEN_VERSION}-bin.tar.gz.sha512 && echo "$(cat /tmp/maven.tar.gz.sha512)  /tmp/maven.tar.gz" | sha512sum -c - && mkdir /opt/maven && tar -xzf /tmp/maven.tar.gz --strip-components=1 -C /opt/maven
COPY backends/java/spring-boot/pom.xml ./
RUN /opt/maven/bin/mvn -B -q dependency:go-offline
COPY backends/java/spring-boot/dependencies.lock backends/java/spring-boot/verify-lock.sh ./
RUN MAVEN_CMD=/opt/maven/bin/mvn ./verify-lock.sh
COPY backends/java/spring-boot/src ./src
RUN /opt/maven/bin/mvn -B -q verify
FROM eclipse-temurin:27_35-jre-resolute@sha256:9274ccf4c2bb3dd950acee56da64c00a8333e847ebd6b7e031d1d15dec575020
LABEL org.language-benchmark.language="java" \
    org.language-benchmark.runtime-version="27" \
    org.language-benchmark.framework="spring-boot" \
    org.language-benchmark.framework-version="4.1.1" \
    org.language-benchmark.workers="1" \
    org.language-benchmark.connections-per-worker="20"
WORKDIR /app
COPY --from=build /build/target/social-api-1.0.0.jar /app/api.jar
RUN useradd --uid 10001 --no-create-home benchmark
USER 10001
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=65 -XX:ActiveProcessorCount=2"
ARG HASH_CONCURRENCY=2
RUN case "$HASH_CONCURRENCY" in 1|2|3|4) ;; *) echo "invalid HASH_CONCURRENCY" >&2; exit 1 ;; esac
ENV HASH_CONCURRENCY=$HASH_CONCURRENCY
EXPOSE 8000
ENTRYPOINT ["java","-jar","/app/api.jar"]
