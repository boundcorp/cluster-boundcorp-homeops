#!/usr/bin/env python3
"""Render BirdWeather card previews from exported card context JSON."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from birdweather_ingester.main import Config, configure_session, render_daily_card, slugify  # noqa: E402


def parse_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: parse_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [parse_value(item) for item in value]
    if isinstance(value, str):
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            try:
                return date.fromisoformat(value)
            except ValueError:
                return value
        if "T" in value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
    return value


def preview_config() -> Config:
    return Config(
        birdweather_token="preview",
        station_id="preview",
        poll_interval_seconds=300,
        http_timeout_seconds=20,
        initial_backfill_hours=24,
        detection_overlap_minutes=10,
        download_audio=False,
        max_audio_bytes=0,
        max_audio_downloads_per_poll=0,
        enrich_species=False,
        max_species_enrichments_per_poll=0,
        max_photos_per_species=0,
        generate_daily_cards=True,
        max_cards_per_poll=25,
        max_card_source_image_bytes=15 * 1024 * 1024,
        media_export_dir=None,
        generate_species_art=False,
        openai_api_key=None,
        openai_image_model="gpt-image-2",
        openai_image_quality="low",
        openai_image_size="704x1088",
        generated_art_styles=("watercolor-field-guide",),
        max_art_generations_per_poll=0,
        station_timezone="America/Los_Angeles",
        mqtt_host=None,
        mqtt_port=1883,
        mqtt_username=None,
        mqtt_password=None,
        postgres_host="",
        postgres_port="5432",
        postgres_user="",
        postgres_db="",
        postgres_password="",
        postgres_schema="birdweather",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("contexts", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("/tmp/birdweather-card-previews"))
    args = parser.parse_args()

    contexts = json.loads(args.contexts.read_text())
    if isinstance(contexts, dict):
        contexts = [contexts]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    config = preview_config()
    with requests.Session() as session:
        configure_session(session)
        for raw_context in contexts:
            context = parse_value(raw_context)
            png, _, _ = render_daily_card(session, config, context)
            filename = f"{context['species_id']}-{slugify(context['common_name'])}.png"
            target = args.out_dir / filename
            target.write_bytes(png)
            print(target)


if __name__ == "__main__":
    main()
