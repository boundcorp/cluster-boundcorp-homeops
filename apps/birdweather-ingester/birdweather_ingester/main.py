from __future__ import annotations

import json
import logging
import os
import re
import signal
import sys
import time
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import paho.mqtt.publish as mqtt_publish
from PIL import Image, ImageDraw, ImageFont, ImageOps
import psycopg
import requests
from psycopg import sql
from psycopg.types.json import Jsonb


LOG = logging.getLogger("birdweather_ingester")
REST_BASE_URL = "https://app.birdweather.com/api/v1"
GRAPHQL_URL = "https://app.birdweather.com/graphql"
WIKIPEDIA_REST_URL = "https://en.wikipedia.org/w/rest.php/v1"
WIKIPEDIA_REST_LEGACY_URL = "https://en.wikipedia.org/api/rest_v1"
WIKIPEDIA_ACTION_URL = "https://en.wikipedia.org/w/api.php"
USER_AGENT = "boundcorp-birdweather-ingester/0.1 (https://github.com/boundcorp/cluster-boundcorp-homeops)"

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
    enrich_species: bool
    max_species_enrichments_per_poll: int
    max_photos_per_species: int
    generate_daily_cards: bool
    max_cards_per_poll: int
    max_card_source_image_bytes: int
    media_export_dir: str | None
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
    postgres_schema: str

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
        enrich_species=os.environ.get("ENRICH_SPECIES", "true").lower() in {"1", "true", "yes", "on"},
        max_species_enrichments_per_poll=env_int("MAX_SPECIES_ENRICHMENTS_PER_POLL", 5),
        max_photos_per_species=env_int("MAX_PHOTOS_PER_SPECIES", 8),
        generate_daily_cards=os.environ.get("GENERATE_DAILY_CARDS", "true").lower() in {"1", "true", "yes", "on"},
        max_cards_per_poll=env_int("MAX_CARDS_PER_POLL", 25),
        max_card_source_image_bytes=env_int("MAX_CARD_SOURCE_IMAGE_BYTES", 15 * 1024 * 1024),
        media_export_dir=os.environ.get("MEDIA_EXPORT_DIR"),
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
        postgres_schema=os.environ.get("POSTGRES_SCHEMA", "birdweather"),
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


def configure_session(session: requests.Session) -> None:
    session.headers.update({"User-Agent": USER_AGENT})


def connect(config: Config) -> psycopg.Connection[Any]:
    conn = psycopg.connect(config.postgres_dsn, autocommit=False, prepare_threshold=None)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(config.postgres_schema)))
        cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(config.postgres_schema)))
    conn.commit()
    return conn


def safe_error(exc: Exception, config: Config) -> str:
    return str(exc).replace(config.birdweather_token, "[redacted]")


def strip_html(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"<[^>]+>", "", value).strip()


def split_facts(text: str | None) -> list[str]:
    if not text:
        return []
    normalized = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    return [sentence.strip() for sentence in sentences if 20 <= len(sentence.strip()) <= 240]


def load_font(size: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/local/share/fonts/DejaVuSans-Bold.ttf",
        ])
    elif italic:
        candidates.extend([
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
            "/usr/local/share/fonts/DejaVuSans-Oblique.ttf",
        ])
    candidates.extend([
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/local/share/fonts/DejaVuSans.ttf",
    ])
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    line = ""
    for word in words:
        candidate = word if not line else f"{line} {word}"
        if text_width(draw, candidate, font) <= max_width:
            line = candidate
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    line_gap: int = 8,
) -> int:
    x, y = xy
    line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + line_gap
    for line in wrap_text(draw, text, font, max_width):
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def crop_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(image.convert("RGB"), size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def fetch_image_asset(
    session: requests.Session,
    config: Config,
    url: str,
) -> tuple[Image.Image, bytes, str | None]:
    response = session.get(url, stream=True, timeout=config.http_timeout_seconds)
    response.raise_for_status()
    content_length = response.headers.get("Content-Length")
    if content_length and int(content_length) > config.max_card_source_image_bytes:
        raise RuntimeError(f"source image is too large: {content_length} bytes")

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=1024 * 256):
        if not chunk:
            continue
        total += len(chunk)
        if total > config.max_card_source_image_bytes:
            raise RuntimeError(f"source image exceeded {config.max_card_source_image_bytes} bytes")
        chunks.append(chunk)

    payload = b"".join(chunks)
    image = Image.open(BytesIO(payload))
    image.load()
    return image, payload, response.headers.get("Content-Type")


def media_write(config: Config, relative_path: str, payload: bytes) -> str | None:
    if not config.media_export_dir:
        return None
    target = Path(config.media_export_dir) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return relative_path


def media_prune_directory(config: Config, relative_dir: str, keep_paths: set[str]) -> None:
    if not config.media_export_dir:
        return
    target_dir = Path(config.media_export_dir) / relative_dir
    if not target_dir.exists():
        return
    for path in target_dir.iterdir():
        relative_path = f"{relative_dir}/{path.name}"
        if path.is_file() and relative_path not in keep_paths:
            path.unlink()


def image_extension(content_type: str | None, fallback_url: str | None = None) -> str:
    if content_type == "image/png":
        return "png"
    if content_type in {"image/jpeg", "image/jpg"}:
        return "jpg"
    if fallback_url:
        suffix = Path(fallback_url.split("?", 1)[0]).suffix.lower().lstrip(".")
        if suffix in {"jpg", "jpeg", "png"}:
            return "jpg" if suffix == "jpeg" else suffix
    return "jpg"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "species"


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

            CREATE TABLE IF NOT EXISTS species_facts (
              id bigserial PRIMARY KEY,
              species_id bigint NOT NULL REFERENCES species(id) ON DELETE CASCADE,
              fact text NOT NULL,
              category text,
              source_name text,
              source_url text,
              generated_by text,
              confidence double precision,
              reviewed_at timestamptz,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now(),
              UNIQUE (species_id, fact)
            );

            CREATE INDEX IF NOT EXISTS species_facts_species_category_idx
              ON species_facts (species_id, category);

            CREATE TABLE IF NOT EXISTS species_photos (
              id bigserial PRIMARY KEY,
              species_id bigint NOT NULL REFERENCES species(id) ON DELETE CASCADE,
              source_name text NOT NULL,
              source_url text NOT NULL,
              photo_url text NOT NULL,
              thumbnail_url text,
              local_media_path text,
              photographer text,
              license text,
              attribution text,
              width integer,
              height integer,
              display_rank integer NOT NULL DEFAULT 100,
              raw jsonb,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now(),
              UNIQUE (species_id, photo_url)
            );

            ALTER TABLE species_photos
              ADD COLUMN IF NOT EXISTS local_media_path text;

            CREATE INDEX IF NOT EXISTS species_photos_species_rank_idx
              ON species_photos (species_id, display_rank, id);

            CREATE TABLE IF NOT EXISTS species_artifacts (
              id bigserial PRIMARY KEY,
              species_id bigint NOT NULL REFERENCES species(id) ON DELETE CASCADE,
              artifact_type text NOT NULL,
              style text,
              provider text,
              model text,
              prompt text,
              source_photo_id bigint REFERENCES species_photos(id) ON DELETE SET NULL,
              content_type text,
              byte_size integer,
              sha256 text,
              image bytea,
              media_path text,
              metadata jsonb,
              generated_at timestamptz,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now()
            );

            CREATE INDEX IF NOT EXISTS species_artifacts_species_type_style_idx
              ON species_artifacts (species_id, artifact_type, style);

            CREATE TABLE IF NOT EXISTS daily_species_cards (
              species_id bigint NOT NULL REFERENCES species(id) ON DELETE CASCADE,
              local_date date NOT NULL,
              artifact_id bigint REFERENCES species_artifacts(id) ON DELETE SET NULL,
              content_type text,
              byte_size integer,
              sha256 text,
              image bytea,
              media_path text,
              card_data jsonb NOT NULL,
              generated_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (species_id, local_date)
            );

            CREATE TABLE IF NOT EXISTS species_hourly_stats (
              species_id bigint NOT NULL REFERENCES species(id) ON DELETE CASCADE,
              local_date date NOT NULL,
              local_hour smallint NOT NULL CHECK (local_hour >= 0 AND local_hour <= 23),
              detections integer NOT NULL,
              first_detected_at timestamptz NOT NULL,
              latest_detected_at timestamptz NOT NULL,
              updated_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (species_id, local_date, local_hour)
            );

            CREATE INDEX IF NOT EXISTS species_hourly_stats_date_idx
              ON species_hourly_stats (local_date DESC, species_id);

            CREATE TABLE IF NOT EXISTS species_monthly_stats (
              species_id bigint NOT NULL REFERENCES species(id) ON DELETE CASCADE,
              month smallint NOT NULL CHECK (month >= 1 AND month <= 12),
              local_hour smallint NOT NULL CHECK (local_hour >= 0 AND local_hour <= 23),
              detections integer NOT NULL,
              days_seen integer NOT NULL,
              first_detected_at timestamptz NOT NULL,
              latest_detected_at timestamptz NOT NULL,
              updated_at timestamptz NOT NULL DEFAULT now(),
              PRIMARY KEY (species_id, month, local_hour)
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


def search_wikipedia_page(
    session: requests.Session,
    config: Config,
    common_name: str,
    scientific_name: str | None,
) -> dict[str, Any] | None:
    for query in [scientific_name, common_name]:
        if not query:
            continue
        response = session.get(
            f"{WIKIPEDIA_REST_URL}/search/page",
            params={"q": query, "limit": 1},
            timeout=config.http_timeout_seconds,
        )
        response.raise_for_status()
        pages = response.json().get("pages") or []
        if pages:
            return pages[0]
    return None


def fetch_wikipedia_summary(
    session: requests.Session,
    config: Config,
    title: str,
) -> dict[str, Any]:
    response = session.get(
        f"{WIKIPEDIA_REST_LEGACY_URL}/page/summary/{requests.utils.quote(title, safe='')}",
        timeout=config.http_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def fetch_wikipedia_image_titles(
    session: requests.Session,
    config: Config,
    title: str,
) -> list[str]:
    response = session.get(
        WIKIPEDIA_ACTION_URL,
        params={
            "action": "query",
            "format": "json",
            "prop": "images",
            "titles": title,
            "imlimit": 50,
        },
        timeout=config.http_timeout_seconds,
    )
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", {})
    image_titles: list[str] = []
    for page in pages.values():
        for image in page.get("images") or []:
            image_title = image.get("title")
            if image_title and re.search(r"\.(jpe?g|png)$", image_title, re.IGNORECASE):
                image_titles.append(image_title)
    return image_titles


def fetch_wikipedia_image_info(
    session: requests.Session,
    config: Config,
    image_titles: list[str],
) -> list[dict[str, Any]]:
    if not image_titles:
        return []
    response = session.get(
        WIKIPEDIA_ACTION_URL,
        params={
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "titles": "|".join(image_titles[: config.max_photos_per_species]),
            "iiprop": "url|size|mime|extmetadata",
            "iiurlwidth": 1200,
        },
        timeout=config.http_timeout_seconds,
    )
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", {})
    infos: list[dict[str, Any]] = []
    for page in pages.values():
        info = (page.get("imageinfo") or [None])[0]
        if info and (info.get("mime") or "").startswith("image/"):
            info["title"] = page.get("title")
            infos.append(info)
    return infos


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


def species_needing_enrichment(
    conn: psycopg.Connection[Any],
    limit: int,
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.common_name, s.scientific_name
            FROM species s
            WHERE NOT EXISTS (
                SELECT 1 FROM species_facts f WHERE f.species_id = s.id
              )
              OR NOT EXISTS (
                SELECT 1 FROM species_photos p WHERE p.species_id = s.id
              )
            ORDER BY s.updated_at DESC, s.id
            LIMIT %s
            """,
            (limit,),
        )
        return [
            {"id": row[0], "common_name": row[1], "scientific_name": row[2]}
            for row in cur.fetchall()
        ]


def insert_species_fact(
    cur: psycopg.Cursor[Any],
    species_id: int,
    fact: str,
    category: str,
    source_name: str,
    source_url: str | None,
) -> None:
    cur.execute(
        """
        INSERT INTO species_facts (
          species_id, fact, category, source_name, source_url, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, now())
        ON CONFLICT (species_id, fact) DO UPDATE
          SET category = excluded.category,
              source_name = excluded.source_name,
              source_url = excluded.source_url,
              updated_at = excluded.updated_at
        """,
        (species_id, fact, category, source_name, source_url),
    )


def insert_species_photo(
    cur: psycopg.Cursor[Any],
    species_id: int,
    source_name: str,
    info: dict[str, Any],
    display_rank: int,
) -> None:
    metadata = info.get("extmetadata") or {}

    def meta_value(key: str) -> str | None:
        value = metadata.get(key) or {}
        return strip_html(value.get("value"))

    photo_url = info.get("url")
    if not photo_url:
        return
    cur.execute(
        """
        INSERT INTO species_photos (
          species_id, source_name, source_url, photo_url, thumbnail_url,
          photographer, license, attribution, width, height, display_rank,
          raw, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (species_id, photo_url) DO UPDATE
          SET thumbnail_url = excluded.thumbnail_url,
              photographer = excluded.photographer,
              license = excluded.license,
              attribution = excluded.attribution,
              width = excluded.width,
              height = excluded.height,
              display_rank = LEAST(species_photos.display_rank, excluded.display_rank),
              raw = excluded.raw,
              updated_at = excluded.updated_at
        """,
        (
            species_id,
            source_name,
            info.get("descriptionurl") or photo_url,
            photo_url,
            info.get("thumburl"),
            meta_value("Artist"),
            meta_value("LicenseShortName") or meta_value("UsageTerms"),
            meta_value("Credit") or meta_value("Attribution"),
            info.get("width"),
            info.get("height"),
            display_rank,
            json_clean(info),
        ),
    )


def enrich_species(
    conn: psycopg.Connection[Any],
    session: requests.Session,
    config: Config,
) -> int:
    if not config.enrich_species:
        return 0

    enriched = 0
    for species in species_needing_enrichment(conn, config.max_species_enrichments_per_poll):
        try:
            page = search_wikipedia_page(
                session,
                config,
                species["common_name"],
                species.get("scientific_name"),
            )
            if not page:
                continue

            title = page["title"]
            summary = fetch_wikipedia_summary(session, config, title)
            source_url = (
                summary.get("content_urls", {})
                .get("desktop", {})
                .get("page")
            )
            facts = []
            description = summary.get("description")
            if description:
                facts.append((f"Wikipedia describes this species as: {description}.", "overview"))
            facts.extend((fact, "overview") for fact in split_facts(summary.get("extract")))

            image_titles = fetch_wikipedia_image_titles(session, config, title)
            image_infos = fetch_wikipedia_image_info(session, config, image_titles)

            with conn.cursor() as cur:
                for fact, category in facts:
                    insert_species_fact(cur, species["id"], fact, category, "Wikipedia", source_url)
                for rank, info in enumerate(image_infos, start=1):
                    insert_species_photo(cur, species["id"], "Wikimedia Commons", info, rank)
            conn.commit()
            enriched += 1
        except Exception as exc:
            conn.rollback()
            LOG.warning(
                "failed to enrich species_id=%s common_name=%s: %s",
                species["id"],
                species["common_name"],
                safe_error(exc, config),
            )

    return enriched


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


def refresh_species_stats(conn: psycopg.Connection[Any], config: Config) -> None:
    tz = ZoneInfo(config.station_timezone)
    local_date = datetime.now(tz).date()
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM species_hourly_stats WHERE local_date = %s",
            (local_date,),
        )
        cur.execute(
            """
            INSERT INTO species_hourly_stats (
              species_id, local_date, local_hour, detections,
              first_detected_at, latest_detected_at, updated_at
            )
            SELECT
              species_id,
              (detected_at AT TIME ZONE %s)::date AS local_date,
              EXTRACT(hour FROM detected_at AT TIME ZONE %s)::smallint AS local_hour,
              count(*)::integer AS detections,
              min(detected_at) AS first_detected_at,
              max(detected_at) AS latest_detected_at,
              now() AS updated_at
            FROM detections
            WHERE species_id IS NOT NULL
              AND (detected_at AT TIME ZONE %s)::date = %s
            GROUP BY species_id, local_date, local_hour
            """,
            (config.station_timezone, config.station_timezone, config.station_timezone, local_date),
        )

        cur.execute("TRUNCATE species_monthly_stats")
        cur.execute(
            """
            INSERT INTO species_monthly_stats (
              species_id, month, local_hour, detections, days_seen,
              first_detected_at, latest_detected_at, updated_at
            )
            SELECT
              species_id,
              EXTRACT(month FROM detected_at AT TIME ZONE %s)::smallint AS month,
              EXTRACT(hour FROM detected_at AT TIME ZONE %s)::smallint AS local_hour,
              count(*)::integer AS detections,
              count(DISTINCT (detected_at AT TIME ZONE %s)::date)::integer AS days_seen,
              min(detected_at) AS first_detected_at,
              max(detected_at) AS latest_detected_at,
              now() AS updated_at
            FROM detections
            WHERE species_id IS NOT NULL
            GROUP BY species_id, month, local_hour
            """,
            (config.station_timezone, config.station_timezone, config.station_timezone),
        )
    conn.commit()


def species_for_daily_cards(
    conn: psycopg.Connection[Any],
    config: Config,
) -> list[dict[str, Any]]:
    tz = ZoneInfo(config.station_timezone)
    local_date = datetime.now(tz).date()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              s.id,
              s.common_name,
              s.scientific_name,
              sum(h.detections)::integer AS detections_today,
              min(h.first_detected_at) AS first_detected_at,
              max(h.latest_detected_at) AS latest_detected_at,
              array_agg(h.local_hour ORDER BY h.local_hour) AS hours_today
            FROM species_hourly_stats h
            JOIN species s ON s.id = h.species_id
            WHERE h.local_date = %s
            GROUP BY s.id, s.common_name, s.scientific_name
            ORDER BY detections_today DESC, s.common_name ASC
            LIMIT %s
            """,
            (local_date, config.max_cards_per_poll),
        )
        rows = cur.fetchall()
    return [
        {
            "species_id": row[0],
            "common_name": row[1],
            "scientific_name": row[2],
            "detections_today": row[3],
            "first_detected_at": row[4],
            "latest_detected_at": row[5],
            "hours_today": row[6] or [],
            "local_date": local_date,
        }
        for row in rows
    ]


def card_context(
    conn: psycopg.Connection[Any],
    species: dict[str, Any],
) -> dict[str, Any]:
    species_id = species["species_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT fact, category, source_name, source_url
            FROM species_facts
            WHERE species_id = %s
            ORDER BY category NULLS LAST, id
            LIMIT 5
            """,
            (species_id,),
        )
        facts = [
            {
                "fact": row[0],
                "category": row[1],
                "source_name": row[2],
                "source_url": row[3],
            }
            for row in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT
              id, photo_url, thumbnail_url, local_media_path, photographer, license, attribution,
              source_name, source_url, width, height
            FROM species_photos
            WHERE species_id = %s
            ORDER BY display_rank, id
            LIMIT 1
            """,
            (species_id,),
        )
        photo_row = cur.fetchone()

        cur.execute(
            """
            SELECT local_hour, detections
            FROM species_monthly_stats
            WHERE species_id = %s
            ORDER BY detections DESC, local_hour ASC
            LIMIT 4
            """,
            (species_id,),
        )
        common_hours = [{"hour": row[0], "detections": row[1]} for row in cur.fetchall()]

        cur.execute(
            """
            SELECT month, sum(detections)::integer AS detections
            FROM species_monthly_stats
            WHERE species_id = %s
            GROUP BY month
            ORDER BY detections DESC, month ASC
            LIMIT 4
            """,
            (species_id,),
        )
        common_months = [{"month": row[0], "detections": row[1]} for row in cur.fetchall()]

    photo = None
    if photo_row:
        photo = {
            "id": photo_row[0],
            "photo_url": photo_row[1],
            "thumbnail_url": photo_row[2],
            "local_media_path": photo_row[3],
            "photographer": photo_row[4],
            "license": photo_row[5],
            "attribution": photo_row[6],
            "source_name": photo_row[7],
            "source_url": photo_row[8],
            "width": photo_row[9],
            "height": photo_row[10],
        }

    return {
        **species,
        "facts": facts,
        "photo": photo,
        "common_hours": common_hours,
        "common_months": common_months,
    }


def update_species_photo_media_path(
    cur: psycopg.Cursor[Any],
    photo_id: int,
    local_media_path: str,
) -> None:
    cur.execute(
        """
        UPDATE species_photos
        SET local_media_path = %s,
            updated_at = now()
        WHERE id = %s
        """,
        (local_media_path, photo_id),
    )


def format_hours(hours: list[int]) -> str:
    if not hours:
        return "none yet"
    return ", ".join(f"{hour:02d}:00" for hour in hours)


def format_months(months: list[dict[str, Any]]) -> str:
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if not months:
        return "not enough history"
    return ", ".join(names[item["month"] - 1] for item in months)


def render_daily_card(
    session: requests.Session,
    config: Config,
    context: dict[str, Any],
) -> tuple[bytes, dict[str, Any], str | None]:
    width, height = 1920, 1080
    image_panel_width = 680
    card = Image.new("RGB", (width, height), "#f4efe6")
    draw = ImageDraw.Draw(card)

    title_font = load_font(112, bold=True)
    subtitle_font = load_font(52, italic=True)
    section_font = load_font(54, bold=True)
    fact_font = load_font(46)
    small_font = load_font(34)
    chip_font = load_font(44, bold=True)
    stat_label_font = load_font(31, bold=True)
    stat_value_font = load_font(45)

    photo = context.get("photo")
    source_media_path = photo.get("local_media_path") if photo else None
    if photo:
        try:
            source_image, source_payload, source_content_type = fetch_image_asset(session, config, photo["photo_url"])
            if not source_media_path:
                extension = image_extension(source_content_type, photo["photo_url"])
                source_media_path = media_write(
                    config,
                    f"source-photos/{context['species_id']}/{photo['id']}.{extension}",
                    source_payload,
                )
            card.paste(crop_cover(source_image, (image_panel_width, height)), (0, 0))
        except Exception as exc:
            LOG.warning("failed to fetch card photo for species_id=%s: %s", context["species_id"], safe_error(exc, config))
            draw.rectangle((0, 0, image_panel_width, height), fill="#34443b")
    else:
        draw.rectangle((0, 0, image_panel_width, height), fill="#34443b")

    overlay = Image.new("RGBA", (image_panel_width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle((0, height - 150, image_panel_width, height), fill=(0, 0, 0, 150))
    card.paste(overlay, (0, 0), overlay)

    x = image_panel_width + 76
    y = 62
    max_text_width = width - x - 68

    draw.text((x, y), context["common_name"], font=title_font, fill="#1f3029")
    y += 128
    if context.get("scientific_name"):
        draw.text((x, y), context["scientific_name"], font=subtitle_font, fill="#58665e")
        y += 82

    chip_text = f"{context['detections_today']} detections today"
    chip_bbox = draw.textbbox((0, 0), chip_text, font=chip_font)
    chip_width = chip_bbox[2] - chip_bbox[0] + 70
    draw.rounded_rectangle((x, y, x + chip_width, y + 88), radius=24, fill="#1f6f5b")
    draw.text((x + 35, y + 18), chip_text, font=chip_font, fill="#ffffff")
    y += 122

    stats = [
        ("Seen today", format_hours(context.get("hours_today") or [])),
        ("Common hours", format_hours([item["hour"] for item in context.get("common_hours") or []])),
        ("Common months", format_months(context.get("common_months") or [])),
    ]
    stat_gap = 24
    stat_width = (max_text_width - (stat_gap * 2)) // 3
    stat_top = y
    for index, (label, value) in enumerate(stats):
        stat_x = x + index * (stat_width + stat_gap)
        draw.rounded_rectangle((stat_x, stat_top, stat_x + stat_width, stat_top + 158), radius=20, fill="#ebe1d2")
        draw.text((stat_x + 26, stat_top + 22), label.upper(), font=stat_label_font, fill="#6d746b")
        draw_wrapped(draw, value, (stat_x + 26, stat_top + 74), stat_value_font, "#1f3029", stat_width - 52, line_gap=6)
    y += 198

    draw.line((x, y, width - 70, y), fill="#d5c8b5", width=4)
    y += 34
    draw.text((x, y), "Field Notes", font=section_font, fill="#30443a")
    y += 74
    for fact in (context.get("facts") or [])[:2]:
        y = draw_wrapped(draw, fact["fact"], (x, y), fact_font, "#24342d", max_text_width, line_gap=13)
        y += 34
        if y > 915:
            break

    latest = context.get("latest_detected_at")
    if latest:
        latest_text = latest.astimezone(ZoneInfo(config.station_timezone)).strftime("Latest: %b %-d, %-I:%M %p")
        draw.text((x, height - 78), latest_text, font=small_font, fill="#58665e")

    if photo:
        attribution = photo.get("photographer") or photo.get("attribution") or photo.get("source_name")
        license_name = photo.get("license")
        credit = " / ".join(part for part in [attribution, license_name] if part)
        if credit:
            draw_wrapped(draw, credit, (34, height - 112), small_font, "#ffffff", image_panel_width - 68, line_gap=6)

    out = BytesIO()
    card.save(out, format="PNG", optimize=True)
    payload = out.getvalue()
    card_data = {
        "species_id": context["species_id"],
        "common_name": context["common_name"],
        "scientific_name": context.get("scientific_name"),
        "detections_today": context["detections_today"],
        "hours_today": context.get("hours_today") or [],
        "common_hours": context.get("common_hours") or [],
        "common_months": context.get("common_months") or [],
        "facts": context.get("facts") or [],
        "photo": photo,
        "source_media_path": source_media_path,
        "width": width,
        "height": height,
    }
    return payload, card_data, source_media_path


def save_daily_card(
    cur: psycopg.Cursor[Any],
    config: Config,
    context: dict[str, Any],
    png: bytes,
    card_data: dict[str, Any],
) -> str:
    media_path = f"bird-cards/{context['species_id']}-{slugify(context['common_name'])}.png"
    media_write(config, media_path, png)
    cur.execute(
        """
        INSERT INTO daily_species_cards (
          species_id, local_date, content_type, byte_size, sha256,
          image, media_path, card_data, generated_at
        )
        VALUES (%s, %s, 'image/png', %s, %s, %s, %s, %s, now())
        ON CONFLICT (species_id, local_date) DO UPDATE
          SET content_type = excluded.content_type,
              byte_size = excluded.byte_size,
              sha256 = excluded.sha256,
              image = excluded.image,
              media_path = excluded.media_path,
              card_data = excluded.card_data,
              generated_at = excluded.generated_at
        """,
        (
            context["species_id"],
            context["local_date"],
            len(png),
            sha256(png).hexdigest(),
            png,
            media_path,
            json_clean(card_data),
        ),
    )
    return media_path


def generate_daily_cards(
    conn: psycopg.Connection[Any],
    session: requests.Session,
    config: Config,
) -> int:
    if not config.generate_daily_cards:
        return 0

    generated = 0
    generated_paths: set[str] = set()
    for species in species_for_daily_cards(conn, config):
        try:
            context = card_context(conn, species)
            png, card_data, source_media_path = render_daily_card(session, config, context)
            with conn.cursor() as cur:
                if source_media_path and context.get("photo"):
                    update_species_photo_media_path(cur, context["photo"]["id"], source_media_path)
                media_path = save_daily_card(cur, config, context, png, card_data)
            conn.commit()
            generated_paths.add(media_path)
            generated += 1
        except Exception as exc:
            conn.rollback()
            LOG.warning(
                "failed to generate daily card for species_id=%s common_name=%s: %s",
                species["species_id"],
                species["common_name"],
                safe_error(exc, config),
            )

    media_prune_directory(config, "bird-cards", generated_paths)
    return generated


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
    configure_session(session)
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
    refresh_species_stats(conn, config)
    species_enriched = enrich_species(conn, session, config)
    if species_enriched:
        LOG.info("enriched %s species", species_enriched)
    cards_generated = generate_daily_cards(conn, session, config)
    if cards_generated:
        LOG.info("generated %s daily species cards", cards_generated)
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
    configure_session(session)

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
