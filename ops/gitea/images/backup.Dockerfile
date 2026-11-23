FROM restic/restic:0.20.0@sha256:93999df80d4ac6cb148e82d2ef1fe6a81996f17ff535e772723538211b31555a AS restic
FROM postgres:17-bookworm@sha256:837bed18782820fe967067cb5328b02b0d9992c1be9a37ee0c9c492cfc03af3b
COPY --from=restic /usr/bin/restic /usr/local/bin/restic
COPY --from=restic /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ADD --chmod=0555 --checksum=sha256:7721f265e18709862655affba5343e85e1980639395d5754473dafaadcaa69e3 https://dl.k8s.io/release/v1.34.1/bin/linux/amd64/kubectl /usr/local/bin/kubectl
USER 1000:1000
ENTRYPOINT ["/bin/bash"]
