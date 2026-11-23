FROM restic/restic:0.19.1@sha256:136600b6ff6843d61d355f7f71f460a166429f35de6fd11b568fece3c9a4d510 AS restic
FROM postgres:17-bookworm@sha256:837bed18782820fe967067cb5328b02b0d9992c1be9a37ee0c9c492cfc03af3b
COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY --from=restic /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ADD --chmod=0555 --checksum=sha256:7721f265e18709862655affba5343e85e1980639395d5754473dafaadcaa69e3 https://dl.k8s.io/release/v1.34.1/bin/linux/amd64/kubectl /usr/local/bin/kubectl
USER 1000:1000
ENTRYPOINT ["/bin/bash"]
