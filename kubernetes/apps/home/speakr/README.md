# Speakr

Speakr is deployed in the `home` namespace with:

- `learnedmachine/speakr:lite` for the web application.
- OpenAI `gpt-4o-transcribe-diarize` for hosted transcription and speaker diarization.
- The shared Crunchy Postgres cluster, via the `speakr` user/database in `kubernetes/apps/database/crunchy/cluster.yaml`.
- An ingress at `https://speakr.boundcorp.net`.

Speakr chunks recordings longer than the OpenAI connector's duration limit and
uses speaker references from the first chunk to preserve identities where possible.

## Secrets

Runtime secrets live in `app/secret.sops.yaml`:

- `ADMIN_EMAIL` reuses the existing cluster ACME email.
- `ADMIN_PASSWORD` was generated during setup.
- `SECRET_KEY` was generated during setup.
- `TEXT_MODEL_BASE_URL` is `https://api.openai.com/v1`.
- `TEXT_MODEL_API_KEY` reuses the existing Birdweather OpenAI key for both text generation and transcription.
- `TEXT_MODEL_NAME` is `gpt-5.4-mini`.

## Database Bootstrap

The Crunchy `postgres-pguser-speakr` secret is generated in the `database`
namespace. A copy must exist in `home`:

```sh
bash kubernetes/apps/database/copy-secrets-from-database-ns.sh home speakr
```

Speakr creates tables in the `public` schema, so the `speakr` database needs:

```sql
GRANT CREATE ON SCHEMA public TO speakr;
ALTER SCHEMA public OWNER TO speakr;
```
