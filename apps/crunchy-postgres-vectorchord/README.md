# Crunchy PostgreSQL with VectorChord

This image extends the exact Crunchy PostgreSQL image used by PGO 5.6.1 with
VectorChord. It preserves Crunchy's Patroni and pgBackRest integration while
adding the extension files required by Immich.

The VectorChord source is pinned to the 1.1.1 release commit. When upgrading the
Crunchy base image, PostgreSQL major version, or VectorChord, rebuild this image
and run the database extension upgrade/reindex procedure before changing the
cluster.
