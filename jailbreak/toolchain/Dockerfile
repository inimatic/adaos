ARG BASE_IMAGE=ubuntu:22.04@sha256:076e7bf82319619f28dd0aa9c02ab34b055ad37100395b4f49a2ffba04fb5e11
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
    ca-certificates curl git make build-essential fakeroot rsync perl \
    dpkg-dev xz-utils libtinfo6 libxml2 python3 file unzip \
    && rm -rf /var/lib/apt/lists/*

ENV THEOS=/opt/theos
ARG THEOS_COMMIT
RUN git init /opt/theos && git -C /opt/theos remote add origin https://github.com/theos/theos.git \
    && git -C /opt/theos fetch --depth 1 origin "${THEOS_COMMIT}" \
    && git -C /opt/theos checkout --detach FETCH_HEAD \
    && git -C /opt/theos submodule update --init --recursive --depth 1

COPY iOSToolchain-x86_64.tar.xz /opt/compiler-download.tar.xz
ARG TOOLCHAIN_SHA256
RUN printf '%s  %s\n' "${TOOLCHAIN_SHA256}" /opt/compiler-download.tar.xz | sha256sum --check - \
    && mkdir -p /opt/theos/toolchain \
    && tar -xJf /opt/compiler-download.tar.xz -C /opt/theos/toolchain \
    && /opt/theos/toolchain/linux/iphone/bin/clang --version

# Keep SDK changes after the stable host/compiler layers.
ARG SDK_COMMIT
RUN git init /opt/sdk-source && git -C /opt/sdk-source remote add origin https://github.com/theos/sdks.git \
    && git -C /opt/sdk-source config remote.origin.promisor true \
    && git -C /opt/sdk-source config remote.origin.partialclonefilter blob:none \
    && git -C /opt/sdk-source fetch --depth 1 --filter=blob:none origin "${SDK_COMMIT}" \
    && git -C /opt/sdk-source sparse-checkout set iPhoneOS10.3.sdk \
    && git -C /opt/sdk-source checkout --detach FETCH_HEAD \
    && ln -s /opt/sdk-source/iPhoneOS10.3.sdk /opt/theos/sdks/iPhoneOS10.3.sdk

# Dependencies are read-only at build time. Do not duplicate the entire compiler
# tree in an ownership-change layer; only project/output mounts need writes.
RUN useradd --create-home --uid 1000 builder \
    && mkdir -p /work /out && chown builder:builder /work /out
USER builder
WORKDIR /work
CMD ["make", "package", "THEOS_PACKAGE_DIR=/out"]
