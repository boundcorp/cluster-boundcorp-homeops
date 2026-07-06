# Speakr

Speakr is deployed in the `home` namespace with:

- `learnedmachine/speakr:lite` for the web application.
- `onerahmet/openai-whisper-asr-webservice:latest` for a CPU-hosted Whisper ASR endpoint.
- The shared Crunchy Postgres cluster, via the `speakr` user/database in `kubernetes/apps/database/crunchy/cluster.yaml`.
- An ingress at `https://speakr.home.boundcorp.net`.

The CPU Whisper service starts with `ASR_MODEL=base`. Increase this to `small`,
`medium`, or `large-v3` if the node has enough CPU/RAM budget, or switch to a
CUDA WhisperX deployment if an NVIDIA node is added later.

## Secrets

Runtime secrets live in `app/secret.sops.yaml`:

- `ADMIN_EMAIL` reuses the existing cluster ACME email.
- `ADMIN_PASSWORD` was generated during setup.
- `SECRET_KEY` was generated during setup.
- `TEXT_MODEL_BASE_URL` is `https://api.openai.com/v1`.
- `TEXT_MODEL_API_KEY` reuses the existing Birdweather OpenAI key.
- `TEXT_MODEL_NAME` is `gpt-5.4-mini`.
