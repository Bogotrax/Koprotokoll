# Tunnel aus festgelegten Quellen bauen; keine lokalen Binärdateien erforderlich.
FROM rust@sha256:e259494b14672950a909b4c54527e19d29cae6ebf980672114c8bb85e5594e4a AS tunnel-build
RUN apt-get update && apt-get install -y --no-install-recommends cmake pkg-config libssl-dev clang ninja-build protobuf-compiler python3 && apt-get clean
RUN git clone https://github.com/michaelburch/signal-call-tunnel.git /src
WORKDIR /src
RUN git checkout --detach 2f924268e1d3083eef70dd82718f1e7e760b13d1 && git submodule update --init --recursive third-party/ringrtc
RUN test "$(git -C third-party/ringrtc rev-parse HEAD)" = a86f8a6832cec291ef4f77e4ee9b941e5b91a01f
RUN bash build.sh build --release --locked

# Frischer Aufbau: keine Schichten der lokalen Container-Snapshots übernehmen.
FROM bbernhard/signal-cli-rest-api@sha256:2cf09d66a86fdd210e42ee91662216364acc27da1163a8c44167a77ea3a6559a
USER root
RUN apt-get update && apt-get install -y --no-install-recommends socat perl && apt-get clean
COPY --from=tunnel-build /src/signal-call-tunnel/target/release/signal-call-tunnel /opt/signal-cli-0.14.8/bin/signal-call-tunnel.real
RUN chmod 755 /opt/signal-cli-0.14.8/bin/signal-call-tunnel.real
COPY signal-call-tunnel /opt/signal-cli-0.14.8/bin/signal-call-tunnel
COPY audio-relay.pl /opt/signal-lab/audio-relay.pl
RUN chmod 755 /opt/signal-cli-0.14.8/bin/signal-call-tunnel && perl -c /opt/signal-lab/audio-relay.pl
ENV MODE=json-rpc
LABEL org.opencontainers.image.title="Koprotokoll Signal-Labor" \
      org.opencontainers.image.description="Experimenteller Endpunkt-Test; keine passive Handy-Anruftranskription" \
      org.opencontainers.image.source="https://github.com/Bogotrax/Koprotokoll/tree/experiment/signal-handoff" \
      org.opencontainers.image.licenses="NOASSERTION" \
      de.koprotokoll.tunnel.revision="2f924268e1d3083eef70dd82718f1e7e760b13d1" \
      de.koprotokoll.ringrtc.revision="a86f8a6832cec291ef4f77e4ee9b941e5b91a01f"
