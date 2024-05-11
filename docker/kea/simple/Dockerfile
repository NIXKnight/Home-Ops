# Not setting up CMD or ENTRYPOINT since this image can be used to start multiple Kea services.
FROM debian:bookworm-slim as base

ARG DEBIAN_FRONTEND noninteractive

RUN set -eux; \
    apt-get update; \
    apt-get -y dist-upgrade; \
    apt-get -y install --no-install-recommends \
        gpg \
        curl \
        ca-certificates \
        debian-keyring \
        debian-archive-keyring \
        apt-transport-https; \
    keyring_location=/usr/share/keyrings/isc-kea-2-4-archive-keyring.gpg; \
    curl -1sLf 'https://dl.cloudsmith.io/public/isc/kea-2-4/gpg.0D9D9A1439E23DB9.key' |  gpg --dearmor > ${keyring_location}; \
    curl -1sLf 'https://dl.cloudsmith.io/public/isc/kea-2-4/config.deb.txt?distro=debian&codename=bookworm' > /etc/apt/sources.list.d/isc-kea-2-4.list; \
    apt-get update; \
    apt-get -y install --no-install-recommends \
        isc-kea-hooks \
        isc-kea-common \
        isc-kea-dhcp-ddns \
        isc-kea-ctrl-agent \
        isc-kea-dhcp4-server \
        isc-kea-dhcp6-server; \
    apt-get clean all; \
    rm -rf /var/lib/apt/*;
