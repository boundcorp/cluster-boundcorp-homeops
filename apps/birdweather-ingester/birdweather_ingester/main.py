from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from hashlib import sha256
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import paho.mqtt.publish as mqtt_publish
import psycopg
import requests
from psycopg.types.json import Jsonb


LOG = logging.getLogger("birdweather_ingester")
REST_BASE_URL = "https://app.birdweather.com/api/v1"
GRAPHQL_URL = "https://app.birdweather.com/graphql"

STATION_QUERY = """
query($id: ID!) {
  station(id: $id) {
    id
    type
    timezone
    latestDetectionAt
    weather {
      timestamp
      temp
      feelsLike
      humidity
      pressure
      cloudiness
      description
      windSpeed
      windDir
      visibility
    }
    airPollution {
      timestamp
      aqi
      co
      no
      no2
      o3
      so2
      pm2_5
      pm10
      nh3
    }
    sensors {
      environment {
        timestamp
        temperature
        humidity
        barometricPressure
        aqi
        voc
        eco2
        soundPressureLevel
      }
      light {
        timestamp
        clear
        f1
        f2
        f3
        f4
        f5
        f6
        f7
        f8
        nir
      }
      system {
        timestamp
        batteryVoltage
        powerSource
        sdAvailable
        sdCapacity
        uploadingCompleted
        uploadingTotal
        wifiRssi
        firmware
      }
      location {
        timestamp
        lat
        lon
        altitude
        satellites
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Config:
    birdweather_token: str
    station_id: str
    poll_interval_seconds: int
    http_timeout_seconds: int
    initial_backfill_hours: int
    detection_overlap_minutes: int
    download_audio: bool
    max_audio_bytes: int
    max_audio_downloads_per_poll: int
    station_timezone: str
    mqtt_host: str | None
    mqtt_port: int
    mqtt_username: str | None
    mqtt_password: str | None
    postgres_host: str
    postgres_port: str
    postgres_user: str
    postgres_db: str
    postgres_password: str

    @property
    def postgres_dsn(self) -> str:
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"user={self.postgres_user} dbname={self.postgres_db} "
            f"password={self.postgres_password} sslmode=prefer"
        )


class Shutdown:
    requested = False


def env_required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    return int(value)


def load_config() -> Config:
    return Config(
        birdweather_token=env_required("BIRDWEATHER_TOKEN"),
        station_id=env_required("BIRDWEATHER_STATION_ID"),
        poll_interval_seconds=env_int("POLL_INTERVAL_SECONDS", 300),
        http_timeout_seconds=env_int("HTTP_TIMEOUT_SECONDS", 20),
        initial_backfill_hours=env_int("INITIAL_BACKFILL_HOURS", 24),
        detection_overlap_minutes=env_int("DETECTION_OVERLAP_MINUTES", 10),
        download_audio=os.environ.get("DOWNLOAD_AUDIO", "true").lower() in {"1", "true", "yes", "on"},
        max_audio_bytes=env_int("MAX_AUDIO_BYTES", 25 * 1024 * 1024),
        max_audio_downloads_per_poll=env_int("MAX_AUDIO_DOWNLOADS_PER_POLL", 20),
        station_timezone=os.environ.get("STATION_TIMEZONE", "America/Los_Angeles"),
        mqtt_host=os.environ.get("MQTT_HOST"),
        mqtt_port=env_int("MQTT_PORT", 1883),
        mqtt_username=os.environ.get("MQTT_USERNAME"),
        mqtt_password=os.environ.get("MQTT_PASSWORD"),
        postgres_host=env_required("POSTGRES_HOST"),
        postgres_port=env_required("POSTGRES_PORT"),
        postgres_user=env_required("POSTGRES_USER"),
        postgres_db=env_required("POSTGRES_DB"),
        postgres_password=env_required("POSTGRES_PASSWORD"),
    )


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def json_clean(value: Any) -> Jsonb:
    return Jsonb(value)


def setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def connect(config: Config) -> psycopg.Connection[Any]:
    return psycopg.connect(config.postgres_dsn, autocommit=False, prepare_threshold=None)


def safe_error(exc: Exception, config: Config) -> str:
    return str(exc).replace(config.birdweather_token, "[redacted]")


def init_schema(conn: psycopg.Connection[Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ingest_state (
              key text PRIMARY KEY,
              value jsonb NOT NULL,
              updated_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS species (
              id bigint PRIMARY KEY,
              common_name text NOT NULL,
              scientific_name text,
              classification text,
              color text,
              image_url text,
              thumbnail_url text,
              png_url text,
              raw jsonb NOT NULL,
              updated_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE TABLE IF NOT EXISTS detections (
              id bigint PRIMARY KEY,
              station_id bigint NOT NULL,
              detected_at timestamptz NOT NULL,
              species_id bigint REFERENCES species(id),
              confidence double precision,
              probability double precision,
              score double precision,
              certainty text,
              algorithm text,
              lat double precision,
              lon double precision,
              favorite boolean,
              soundscape_id bigint,
              soundscape_url text,
              soundscape_start_time double precision,
              soundscape_end_time double precision,
              soundscape_mode text,
              raw jsonb NOT NULL,
              ingested_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE INDEX IF NOT EXISTS detections_detected_at_idx
              ON detections (detected_at DESC);
            CREATE INDEX IF NOT EXISTS detections_species_detected_at_idx
              ON detections (species_id, detected_at DESC);

            CREATE TABLE IF NOT EXISTS soundscape_assets (
              soundscape_id bigint PRIMARY KEY,
              url text NOT NULL,
              content_type text,
              byte_size integer,
              sha256 text,
              audio bytea,
              downloaded_at timestamptz,
              error text,
              raw jsonb,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE INDEX IF NOT EXISTS soundscape_assets_downloaded_at_idx
              ON soundscape_assets (downloaded_at DESC);

            CREATE TABLE IF NOT EXISTS station_snapshots (
              station_id bigint NOT NULL,
              observed_at timestamptz NOT NULL,
              station_type text,
              timezone text,
              latest_detection_at timestamptz,
              raw jsonb NOT NULL,
              ingested_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (station_id, observed_at)
            );

            CREATE TABLE IF NOT EXISTS sensor_readings (
              station_id bigint NOT NULL,
              observed_at timestamptz NOT NULL,
              environment jsonb,
              light jsonb,
              system jsonb,
              location jsonb,
              weather jsonb,
              air_pollution jsonb,
              ingested_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (station_id, observed_at)
            );

            CREATE INDEX IF NOT EXISTS sensor_readings_observed_at_idx
              ON sensor_readings (observed_at DESC);

            CREATE TABLE IF NOT EXISTS poll_runs (
              id bigserial PRIMARY KEY,
              started_at timestamptz NOT NULL,
              finished_at timestamptz,
              ok boolean NOT NULL DEFAULT false,
              detections_seen integer NOT NULL DEFAULT 0,
              detections_inserted integer NOT NULL DEFAULT 0,
              error text
            );
            """
        )
    conn.commit()


def get_state(conn: psycopg.Connection[Any], key: str) -> Any | None:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM ingest_state WHERE key = %s", (key,))
        row = cur.fetchone()
    return row[0] if row else None


def set_state(conn: psycopg.Connection[Any], key: str, value: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ingest_state (key, value, updated_at)
            VALUES (%s, %s, now())
            ON CONFLICT (key) DO UPDATE
              SET value = excluded.value,
                  updated_at = excluded.updated_at
            """,
            (key, json_clean(value)),
        )


def fetch_detections(
    session: requests.Session,
    config: Config,
    since: datetime,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "limit": 100,
        "from": since.isoformat(),
        "classification": "avian",
    }
    detections: list[dict[str, Any]] = []
    cursor: int | None = None

    while True:
        if cursor is not None:
            params["cursor"] = cursor
        response = session.get(
            f"{REST_BASE_URL}/stations/{config.birdweather_token}/detections",
            params=params,
            timeout=config.http_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("detections") or []
        detections.extend(batch)

        if len(batch) < 100:
            break
        next_cursor = batch[-1].get("id")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = int(next_cursor)

    return detections


def fetch_station_snapshot(
    session: requests.Session,
    config: Config,
) -> dict[str, Any]:
    response = session.post(
        GRAPHQL_URL,
        json={"query": STATION_QUERY, "variables": {"id": config.station_id}},
        timeout=config.http_timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"]))
    station = payload.get("data", {}).get("station")
    if not station:
        raise RuntimeError("BirdWeather GraphQL returned no station data")
    return station


def fetch_audio_asset(
    session: requests.Session,
    config: Config,
    url: str,
) -> tuple[bytes, str | None]:
    response = session.get(url, stream=True, timeout=config.http_timeout_seconds)
    response.raise_for_status()

    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > config.max_audio_bytes:
        raise RuntimeError(f"audio asset is too large: {content_length} bytes")

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=1024 * 256):
        if not chunk:
            continue
        total += len(chunk)
        if total > config.max_audio_bytes:
            raise RuntimeError(f"audio asset exceeded {config.max_audio_bytes} bytes")
        chunks.append(chunk)

    return b"".join(chunks), response.headers.get("Content-Type")


def upsert_species(cur: psycopg.Cursor[Any], species: dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO species (
          id, common_name, scientific_name, classification, color,
          image_url, thumbnail_url, png_url, raw, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (id) DO UPDATE
          SET common_name = excluded.common_name,
              scientific_name = excluded.scientific_name,
              classification = excluded.classification,
              color = excluded.color,
              image_url = excluded.image_url,
              thumbnail_url = excluded.thumbnail_url,
              png_url = excluded.png_url,
              raw = excluded.raw,
              updated_at = excluded.updated_at
        """,
        (
            species["id"],
            species["commonName"],
            species.get("scientificName"),
            species.get("classification"),
            species.get("color"),
            species.get("imageUrl"),
            species.get("thumbnailUrl"),
            species.get("pngUrl"),
            json_clean(species),
        ),
    )


def insert_detection(cur: psycopg.Cursor[Any], detection: dict[str, Any]) -> bool:
    species = detection.get("species") or {}
    if species:
        upsert_species(cur, species)

    soundscape = detection.get("soundscape") or {}
    cur.execute(
        """
        INSERT INTO detections (
          id, station_id, detected_at, species_id, confidence, probability,
          score, certainty, algorithm, lat, lon, favorite, soundscape_id,
          soundscape_url, soundscape_start_time, soundscape_end_time,
          soundscape_mode, raw
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
        RETURNING id
        """,
        (
            detection["id"],
            detection["stationId"],
            parse_ts(detection["timestamp"]),
            species.get("id"),
            detection.get("confidence"),
            detection.get("probability"),
            detection.get("score"),
            detection.get("certainty"),
            detection.get("algorithm"),
            detection.get("lat"),
            detection.get("lon"),
            detection.get("favorite"),
            soundscape.get("id"),
            soundscape.get("url"),
            soundscape.get("startTime"),
            soundscape.get("endTime"),
            soundscape.get("mode"),
            json_clean(detection),
        ),
    )
    return cur.fetchone() is not None


def soundscape_needs_download(cur: psycopg.Cursor[Any], soundscape_id: int) -> bool:
    cur.execute(
        """
        SELECT audio IS NULL
        FROM soundscape_assets
        WHERE soundscape_id = %s
        """,
        (soundscape_id,),
    )
    row = cur.fetchone()
    return row is None or bool(row[0])


def mark_soundscape_pending(cur: psycopg.Cursor[Any], soundscape: dict[str, Any]) -> None:
    cur.execute(
        """
        INSERT INTO soundscape_assets (soundscape_id, url, raw, updated_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (soundscape_id) DO UPDATE
          SET url = excluded.url,
              raw = excluded.raw,
              updated_at = now()
        """,
        (soundscape["id"], soundscape["url"], json_clean(soundscape)),
    )


def save_soundscape_asset(
    cur: psycopg.Cursor[Any],
    soundscape: dict[str, Any],
    audio: bytes,
    content_type: str | None,
) -> None:
    cur.execute(
        """
        INSERT INTO soundscape_assets (
          soundscape_id, url, content_type, byte_size, sha256, audio,
          downloaded_at, error, raw, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, now(), NULL, %s, now())
        ON CONFLICT (soundscape_id) DO UPDATE
          SET url = excluded.url,
              content_type = excluded.content_type,
              byte_size = excluded.byte_size,
              sha256 = excluded.sha256,
              audio = excluded.audio,
              downloaded_at = excluded.downloaded_at,
              error = NULL,
              raw = excluded.raw,
              updated_at = excluded.updated_at
        """,
        (
            soundscape["id"],
            soundscape["url"],
            content_type,
            len(audio),
            sha256(audio).hexdigest(),
            audio,
            json_clean(soundscape),
        ),
    )


def save_soundscape_error(
    cur: psycopg.Cursor[Any],
    soundscape: dict[str, Any],
    error: str,
) -> None:
    cur.execute(
        """
        INSERT INTO soundscape_assets (soundscape_id, url, error, raw, updated_at)
        VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (soundscape_id) DO UPDATE
          SET url = excluded.url,
              error = excluded.error,
              raw = excluded.raw,
              updated_at = now()
        """,
        (soundscape["id"], soundscape["url"], error, json_clean(soundscape)),
    )


def download_soundscapes(
    conn: psycopg.Connection[Any],
    session: requests.Session,
    config: Config,
    detections: list[dict[str, Any]],
) -> int:
    if not config.download_audio:
        return 0

    soundscapes = {
        soundscape["id"]: soundscape
        for detection in detections
        if (soundscape := detection.get("soundscape") or {}).get("id") and soundscape.get("url")
    }
    downloaded = 0

    for soundscape in soundscapes.values():
        if downloaded >= config.max_audio_downloads_per_poll:
            break

        with conn.cursor() as cur:
            mark_soundscape_pending(cur, soundscape)
            needs_download = soundscape_needs_download(cur, int(soundscape["id"]))
        conn.commit()

        if not needs_download:
            continue

        try:
            audio, content_type = fetch_audio_asset(session, config, soundscape["url"])
            with conn.cursor() as cur:
                save_soundscape_asset(cur, soundscape, audio, content_type)
            conn.commit()
            downloaded += 1
        except Exception as exc:
            conn.rollback()
            with conn.cursor() as cur:
                save_soundscape_error(cur, soundscape, safe_error(exc, config))
            conn.commit()
            LOG.warning(
                "failed to download soundscape_id=%s: %s",
                soundscape["id"],
                safe_error(exc, config),
            )

    return downloaded


def station_observed_at(station: dict[str, Any]) -> datetime:
    sensors = station.get("sensors") or {}
    candidates = [
        parse_ts((sensors.get("environment") or {}).get("timestamp")),
        parse_ts((sensors.get("system") or {}).get("timestamp")),
        parse_ts((sensors.get("light") or {}).get("timestamp")),
        parse_ts((sensors.get("location") or {}).get("timestamp")),
        parse_ts((station.get("weather") or {}).get("timestamp")),
    ]
    return max(candidate for candidate in candidates if candidate) if any(candidates) else datetime.now(UTC)


def insert_station_snapshot(cur: psycopg.Cursor[Any], station: dict[str, Any]) -> None:
    station_id = int(station["id"])
    observed_at = station_observed_at(station)
    sensors = station.get("sensors") or {}
    cur.execute(
        """
        INSERT INTO station_snapshots (
          station_id, observed_at, station_type, timezone, latest_detection_at, raw
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (station_id, observed_at) DO UPDATE
          SET station_type = excluded.station_type,
              timezone = excluded.timezone,
              latest_detection_at = excluded.latest_detection_at,
              raw = excluded.raw,
              ingested_at = now()
        """,
        (
            station_id,
            observed_at,
            station.get("type"),
            station.get("timezone"),
            parse_ts(station.get("latestDetectionAt")),
            json_clean(station),
        ),
    )
    cur.execute(
        """
        INSERT INTO sensor_readings (
          station_id, observed_at, environment, light, system, location, weather, air_pollution
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (station_id, observed_at) DO UPDATE
          SET environment = excluded.environment,
              light = excluded.light,
              system = excluded.system,
              location = excluded.location,
              weather = excluded.weather,
              air_pollution = excluded.air_pollution,
              ingested_at = now()
        """,
        (
            station_id,
            observed_at,
            json_clean(sensors.get("environment")),
            json_clean(sensors.get("light")),
            json_clean(sensors.get("system")),
            json_clean(sensors.get("location")),
            json_clean(station.get("weather")),
            json_clean(station.get("airPollution")),
        ),
    )


def today_summary(conn: psycopg.Connection[Any], config: Config) -> dict[str, Any]:
    tz = ZoneInfo(config.station_timezone)
    now_local = datetime.now(tz)
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    since_utc = midnight_local.astimezone(UTC)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*)::int, count(DISTINCT species_id)::int
            FROM detections
            WHERE detected_at >= %s
            """,
            (since_utc,),
        )
        detections_today, species_today = cur.fetchone()

        cur.execute(
            """
            SELECT
              s.id,
              s.common_name,
              s.scientific_name,
              count(*)::int AS detections,
              max(d.detected_at) AS latest_detected_at
            FROM detections d
            JOIN species s ON s.id = d.species_id
            WHERE d.detected_at >= %s
            GROUP BY s.id, s.common_name, s.scientific_name
            ORDER BY detections DESC, s.common_name ASC
            LIMIT 100
            """,
            (since_utc,),
        )
        species = [
            {
                "id": row[0],
                "common_name": row[1],
                "scientific_name": row[2],
                "detections": row[3],
                "latest_detected_at": row[4].isoformat() if row[4] else None,
            }
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT
              d.id,
              d.detected_at,
              d.confidence,
              d.certainty,
              s.common_name,
              s.scientific_name
            FROM detections d
            JOIN species s ON s.id = d.species_id
            ORDER BY d.detected_at DESC
            LIMIT 1
            """
        )
        latest = cur.fetchone()

    latest_detection = None
    if latest:
        latest_detection = {
            "id": latest[0],
            "detected_at": latest[1].isoformat(),
            "confidence": latest[2],
            "certainty": latest[3],
            "common_name": latest[4],
            "scientific_name": latest[5],
        }

    return {
        "station_id": int(config.station_id),
        "updated_at": datetime.now(UTC).isoformat(),
        "local_date": midnight_local.date().isoformat(),
        "detections_today": detections_today,
        "species_today": species_today,
        "latest_species": latest_detection["common_name"] if latest_detection else "Unknown",
        "latest_detection_at": latest_detection["detected_at"] if latest_detection else None,
        "latest_detection": latest_detection,
        "species": species,
    }


def mqtt_auth(config: Config) -> dict[str, str] | None:
    if config.mqtt_username and config.mqtt_password:
        return {"username": config.mqtt_username, "password": config.mqtt_password}
    return None


def mqtt_sensor_config(
    config: Config,
    object_id: str,
    name: str,
    value_template: str,
    icon: str,
    device_class: str | None = None,
    state_class: str | None = None,
) -> tuple[str, str]:
    payload: dict[str, Any] = {
        "name": name,
        "unique_id": f"birdweather_{config.station_id}_{object_id}",
        "object_id": f"birdweather_{object_id}",
        "state_topic": f"birdweather/{config.station_id}/state",
        "value_template": value_template,
        "json_attributes_topic": f"birdweather/{config.station_id}/state",
        "icon": icon,
        "device": {
            "identifiers": [f"birdweather_{config.station_id}"],
            "name": "BirdWeather",
            "manufacturer": "BirdWeather",
            "model": "PUC",
        },
    }
    if device_class:
        payload["device_class"] = device_class
    if state_class:
        payload["state_class"] = state_class
    return (
        f"homeassistant/sensor/birdweather_{object_id}/config",
        json.dumps(payload, separators=(",", ":")),
    )


def publish_home_assistant_state(conn: psycopg.Connection[Any], config: Config) -> None:
    if not config.mqtt_host:
        return

    summary = today_summary(conn, config)
    configs = [
        mqtt_sensor_config(
            config,
            "detections_today",
            "BirdWeather Detections Today",
            "{{ value_json.detections_today }}",
            "mdi:bird",
            state_class="measurement",
        ),
        mqtt_sensor_config(
            config,
            "species_today",
            "BirdWeather Species Today",
            "{{ value_json.species_today }}",
            "mdi:feather",
            state_class="measurement",
        ),
        mqtt_sensor_config(
            config,
            "latest_species",
            "BirdWeather Latest Species",
            "{{ value_json.latest_species }}",
            "mdi:bird",
        ),
        mqtt_sensor_config(
            config,
            "latest_detection_at",
            "BirdWeather Latest Detection",
            "{{ value_json.latest_detection_at or 'unknown' }}",
            "mdi:clock-outline",
            device_class="timestamp",
        ),
    ]
    messages = [
        {"topic": topic, "payload": payload, "retain": True}
        for topic, payload in configs
    ]
    messages.append(
        {
            "topic": f"birdweather/{config.station_id}/state",
            "payload": json.dumps(summary, separators=(",", ":"), default=str),
            "retain": True,
        }
    )
    mqtt_publish.multiple(
        messages,
        hostname=config.mqtt_host,
        port=config.mqtt_port,
        auth=mqtt_auth(config),
        client_id=f"birdweather-ingester-{config.station_id}",
    )


def poll_once(
    conn: psycopg.Connection[Any],
    session: requests.Session,
    config: Config,
) -> tuple[int, int]:
    state = get_state(conn, "detections")
    if state and state.get("latest_detected_at"):
        since = parse_ts(state["latest_detected_at"]) or datetime.now(UTC)
        since = since - timedelta(minutes=config.detection_overlap_minutes)
    else:
        since = datetime.now(UTC) - timedelta(hours=config.initial_backfill_hours)

    detections = fetch_detections(session, config, since)
    station = fetch_station_snapshot(session, config)

    inserted = 0
    latest_detected_at = parse_ts(state.get("latest_detected_at")) if state else None
    latest_detection_id = state.get("latest_detection_id") if state else None

    with conn.cursor() as cur:
        for detection in detections:
            if insert_detection(cur, detection):
                inserted += 1
            detected_at = parse_ts(detection.get("timestamp"))
            if detected_at and (latest_detected_at is None or detected_at > latest_detected_at):
                latest_detected_at = detected_at
                latest_detection_id = detection.get("id")

        insert_station_snapshot(cur, station)

        if latest_detected_at:
            set_state(
                conn,
                "detections",
                {
                    "latest_detected_at": latest_detected_at.isoformat(),
                    "latest_detection_id": latest_detection_id,
                },
            )

    conn.commit()
    audio_downloaded = download_soundscapes(conn, session, config, detections)
    if audio_downloaded:
        LOG.info("downloaded %s new soundscape assets", audio_downloaded)
    publish_home_assistant_state(conn, config)
    return len(detections), inserted


def record_poll_start(conn: psycopg.Connection[Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO poll_runs (started_at) VALUES (now()) RETURNING id",
        )
        row = cur.fetchone()
    conn.commit()
    return int(row[0])


def record_poll_finish(
    conn: psycopg.Connection[Any],
    poll_id: int,
    ok: bool,
    detections_seen: int = 0,
    detections_inserted: int = 0,
    error: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE poll_runs
            SET finished_at = now(),
                ok = %s,
                detections_seen = %s,
                detections_inserted = %s,
                error = %s
            WHERE id = %s
            """,
            (ok, detections_seen, detections_inserted, error, poll_id),
        )
    conn.commit()


def handle_signal(signum: int, _frame: Any) -> None:
    LOG.info("received signal %s, shutting down", signum)
    Shutdown.requested = True


def run() -> None:
    setup_logging()
    config = load_config()
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    session = requests.Session()
    session.headers.update({"User-Agent": "boundcorp-birdweather-ingester/0.1"})

    with connect(config) as conn:
        init_schema(conn)
        while not Shutdown.requested:
            poll_id = record_poll_start(conn)
            try:
                seen, inserted = poll_once(conn, session, config)
                record_poll_finish(conn, poll_id, True, seen, inserted)
                LOG.info("poll complete: detections_seen=%s detections_inserted=%s", seen, inserted)
            except Exception as exc:
                conn.rollback()
                error = safe_error(exc, config)
                LOG.error("poll failed: %s", error)
                record_poll_finish(conn, poll_id, False, error=error)

            deadline = time.monotonic() + config.poll_interval_seconds
            while not Shutdown.requested and time.monotonic() < deadline:
                time.sleep(min(1, deadline - time.monotonic()))


def main() -> None:
    run()


if __name__ == "__main__":
    main()
