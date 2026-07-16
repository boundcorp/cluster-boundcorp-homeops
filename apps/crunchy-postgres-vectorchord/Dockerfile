FROM rockylinux:8 AS vectorchord

ARG VECTORCHORD_COMMIT=f9b5e234c44cee69b266b8431f92b7deed4e99d3

# Build against EL8's glibc so the module is loadable by Crunchy's UBI8
# image. Upstream release binaries are built on Ubuntu 22.04 and require a
# newer glibc.
RUN dnf install -y \
      epel-release \
      https://download.postgresql.org/pub/repos/yum/reporpms/EL-8-x86_64/pgdg-redhat-repo-latest.noarch.rpm \
    && dnf -qy module disable postgresql \
    && dnf config-manager --set-enabled powertools \
    && dnf install -y \
      clang-devel \
      curl \
      gcc-toolset-12-gcc \
      gcc-toolset-12-gcc-c++ \
      git \
      make \
      postgresql16-devel \
    && dnf clean all

ENV PATH="/root/.cargo/bin:/opt/rh/gcc-toolset-12/root/usr/bin:/usr/pgsql-16/bin:${PATH}"
ENV CC="/opt/rh/gcc-toolset-12/root/usr/bin/gcc"
ENV CXX="/opt/rh/gcc-toolset-12/root/usr/bin/g++"
RUN curl --proto '=https' --tlsv1.2 --fail --silent --show-error \
      https://sh.rustup.rs | sh -s -- -y --profile minimal \
    && git clone https://github.com/tensorchord/VectorChord.git /src/vectorchord \
    && cd /src/vectorchord \
    && git checkout "${VECTORCHORD_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${VECTORCHORD_COMMIT}" \
    && make build

# Keep the PGO/Patroni/pgBackRest integration supplied by Crunchy and add only
# VectorChord. This base already contains pgvector 0.7.3, which is compatible
# with Immich and VectorChord.
FROM registry.developers.crunchydata.com/crunchydata/crunchy-postgres:ubi8-16.4-0

USER 0
COPY --from=vectorchord /src/vectorchord/build/pkglibdir/vchord.so /usr/pgsql-16/lib/vchord.so
COPY --from=vectorchord /src/vectorchord/build/sharedir/extension/ /usr/pgsql-16/share/extension/
RUN chmod 0755 /usr/pgsql-16/lib/vchord.so \
    && chmod 0644 /usr/pgsql-16/share/extension/vchord*
USER 26
