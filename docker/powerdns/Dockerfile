# Not setting up CMD or ENTRYPOINT since this image can be used to start multiple PowerDNS services.
FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND noninteractive

RUN set -eux; \
    apt-get update; \
    apt-get -y dist-upgrade; \
    apt-get -y install --no-install-recommends curl ca-certificates gettext-base netcat-openbsd mariadb-client; \
    savedAptMark="$(apt-mark showmanual)"; \
    apt-mark auto '.*' > /dev/null; \
    [ -z "$savedAptMark" ] || apt-mark manual $savedAptMark > /dev/null; \
    { \
       echo "deb [signed-by=/etc/apt/keyrings/pdns-pub.asc arch=amd64] http://repo.powerdns.com/debian bookworm-auth-48 main"; \
       echo "deb [signed-by=/etc/apt/keyrings/pdns-pub.asc arch=amd64] http://repo.powerdns.com/debian bookworm-rec-49 main"; \
    } | tee /etc/apt/sources.list.d/pdns.list; \
    curl https://repo.powerdns.com/FD380FBB-pub.asc | tee /etc/apt/keyrings/pdns-pub.asc; \
    apt-get update; \
    apt-get -y install --no-install-recommends pdns-server pdns-backend-mysql pdns-recursor; \
    mkdir -p /etc/powerdns/template /var/run/pdns-recursor /var/run/pdns; \
    chown -R pdns:pdns /etc/powerdns /var/run/pdns-recursor /var/run/pdns; \
    apt-get clean all; \
    rm -rf /var/lib/apt/lists/*

COPY pdns.conf.template /etc/powerdns/template/pdns.conf.template
COPY recursor.conf.template /etc/powerdns/template/recursor.conf.template
COPY pdns.sh /usr/local/bin/pdns.sh

USER pdns
