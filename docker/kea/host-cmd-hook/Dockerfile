# Not setting up CMD or ENTRYPOINT since this image can be used to start multiple Kea services.
FROM debian:bookworm-slim as base

ARG DEBIAN_FRONTEND noninteractive

RUN set -eux; \
    groupadd --system --gid 101 _kea; \
    useradd --system --gid _kea --no-create-home --shell /bin/false --uid 101 _kea

RUN set -eux; \
    apt-get update; \
    apt-get -y dist-upgrade; \
    apt-get -y install --no-install-recommends \
        mariadb-client \
        jq \
        curl \
        socat \
        python3 \
        python3-pip \
        python3-requests \
        libpq5 \
        libmariadb3 \
        libpython3.11 \
        liblog4cplus-2.0.5 \
        libffi8 \
        libboost-system1.74.0; \
    savedAptMark="$(apt-mark showmanual)"; \
    apt-mark auto '.*' > /dev/null; \
    [ -z "$savedAptMark" ] || apt-mark manual $savedAptMark > /dev/null; \
    apt-get clean all; \
    rm -rf /var/lib/apt/*; \
    echo '/usr/local/lib' > /etc/ld.so.conf.d/compiled.conf


FROM base as builder

# Install Kea
RUN set -eux; \
    { \
      echo "deb-src http://deb.debian.org/debian bookworm main"; \
      echo "deb-src http://deb.debian.org/debian bookworm-updates main"; \
      echo "deb-src http://security.debian.org/ bookworm-security main"; \
    } | tee /etc/apt/sources.list; \
    apt-get update; \
    apt-get build-dep -y kea-dhcp4-server; \
    curl https://dl.cloudsmith.io/public/isc/kea-2-4/raw/versions/2.4.0/kea-2.4.0.tar.gz -o /tmp/kea-2.4.0.tar.gz; \
    cd /tmp && tar zxf kea-2.4.0.tar.gz; \
    cd kea-2.4.0; \
    ./configure \
        --prefix=/usr/local \
        --sysconfdir=/usr/local/etc \
        --localstatedir=/usr/local/var \
        --with-site-packages=/usr/local/lib/python3.11/dist-packages \
        --with-openssl \
        --with-mysql \
        --with-pgsql \
        --with-boost-libs=-lboost_system \
        --enable-shell \
        --disable-rpath \
        --enable-generate-parser \
        --without-werror; \
    make; \
    make install

# Install kea_python https://github.com/invite-networks/kea_python
RUN set -eux; \
    apt-get install -y git; \
    git clone https://github.com/invite-networks/kea_python.git /tmp/kea_python; \
    cd /tmp/kea_python; \
    make clean install


FROM base as system

COPY --from=builder /usr/local /usr/local
COPY keahook.py /usr/local/lib/kea/keahook.py
COPY init.sh /usr/local/bin/init.sh

RUN set -eux; \
    mkdir -v /usr/local/var/run/kea/kea-dhcp4-server /usr/local/var/run/kea/kea-ctrl-agent; \
    chown -R _kea:_kea /usr/local/var/run/kea; \
    ldconfig
