# BirdWeather Ingester

Long-running worker that polls BirdWeather and stores station detections plus PUC sensor snapshots in PostgreSQL.

Required environment:

- `BIRDWEATHER_TOKEN`
- `BIRDWEATHER_STATION_ID`
- `POSTGRES_HOST`
- `POSTGRES_PORT`
- `POSTGRES_USER`
- `POSTGRES_DB`
- `POSTGRES_PASSWORD`
- `POSTGRES_SCHEMA` default `birdweather`

Optional environment:

- `POLL_INTERVAL_SECONDS` default `300`
- `HTTP_TIMEOUT_SECONDS` default `20`
- `INITIAL_BACKFILL_HOURS` default `24`
- `DETECTION_OVERLAP_MINUTES` default `10`
- `DOWNLOAD_AUDIO` default `true`
- `MAX_AUDIO_BYTES` default `26214400`
- `MAX_AUDIO_DOWNLOADS_PER_POLL` default `20`
- `ENRICH_SPECIES` default `true`
- `MAX_SPECIES_ENRICHMENTS_PER_POLL` default `5`
- `MAX_PHOTOS_PER_SPECIES` default `8`
- `STATION_TIMEZONE` default `America/Los_Angeles`
- `MQTT_HOST` enables Home Assistant MQTT discovery/state publishing
- `MQTT_PORT` default `1883`
- `MQTT_USERNAME`
- `MQTT_PASSWORD`
- `LOG_LEVEL` default `INFO`
