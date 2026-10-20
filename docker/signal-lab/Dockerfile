# Frischer Aufbau: keine Schichten der lokalen Container-Snapshots übernehmen.
FROM bbernhard/signal-cli-rest-api@sha256:2cf09d66a86fdd210e42ee91662216364acc27da1163a8c44167a77ea3a6559a
USER root
RUN apt-get update && apt-get install -y --no-install-recommends socat perl && apt-get clean
COPY artifacts/signal-call-tunnel.real /opt/signal-cli-0.14.8/bin/signal-call-tunnel.real
RUN echo '68277110239ba60ac090a999f6e41699543a4a6141a6ef0e156b6bb25706c326  /opt/signal-cli-0.14.8/bin/signal-call-tunnel.real' | sha256sum -c - && chmod 755 /opt/signal-cli-0.14.8/bin/signal-call-tunnel.real
COPY signal-call-tunnel /opt/signal-cli-0.14.8/bin/signal-call-tunnel
COPY audio-relay.pl /opt/signal-lab/audio-relay.pl
RUN chmod 755 /opt/signal-cli-0.14.8/bin/signal-call-tunnel && perl -c /opt/signal-lab/audio-relay.pl
ENV MODE=json-rpc
LABEL org.opencontainers.image.title="Koprotokoll Signal-Labor" \
      org.opencontainers.image.description="Experimenteller Endpunkt-Test; keine passive Handy-Anruftranskription"
