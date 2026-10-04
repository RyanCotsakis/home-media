#!/usr/bin/env python3
"""Idempotently connect the media services after their first startup.

Run from ``infra/docker`` after copying and completing ``.env``. The script
never prints credentials. It configures the connections this repository owns;
it deliberately does not choose or create indexers for the operator.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


class Api:
    def __init__(self, base_url: str, api_key: str, version: str):
        self.base_url = f"{base_url.rstrip('/')}/api/{version}"
        self.api_key = api_key

    def request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=data,
            method=method,
            headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                content = response.read()
        except HTTPError as exc:
            # Arr validation responses can echo secret-valued fields. Report
            # only the endpoint and status.
            raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"Cannot reach {self.base_url}: {exc.reason}") from exc
        return json.loads(content) if content else {}

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def save(self, path: str, body: dict, existing_id: int | None = None) -> Any:
        if existing_id is None:
            return self.request("POST", path, body)
        return self.request("PUT", f"{path}/{existing_id}", body)


def require(env: dict[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value or value.startswith("replace-"):
        raise RuntimeError(f"{name} must be set in .env")
    return value


def set_field(resource: dict, name: str, value: Any) -> None:
    field = next((item for item in resource.get("fields", []) if item.get("name") == name), None)
    if field is None:
        raise RuntimeError(f"{resource.get('implementation')} has no {name!r} setting")
    field["value"] = value


def configure_prowlarr_application(
    service: str, env: dict[str, str], prowlarr: Api, apply: bool
) -> str:
    implementation = service.title()
    existing = next(
        (item for item in prowlarr.get("applications") if item.get("implementation") == implementation),
        None,
    )
    if existing and not apply:
        return "configured"
    schemas = prowlarr.get("applications/schema")
    resource = dict(existing or next(item for item in schemas if item.get("implementation") == implementation))
    resource["name"] = implementation
    resource["syncLevel"] = "fullSync"
    set_field(resource, "prowlarrUrl", "http://prowlarr:9696")
    ports = {"radarr": 7878, "sonarr": 8989}
    set_field(resource, "baseUrl", f"http://{service}:{ports[service]}")
    set_field(resource, "apiKey", require(env, f"{service.upper()}_API_KEY"))
    if apply:
        prowlarr.request("POST", "applications/test", resource)
        prowlarr.save("applications", resource, existing.get("id") if existing else None)
    return "updated" if existing else "created"


def configure_download_client(service: str, env: dict[str, str], api: Api, apply: bool) -> str:
    existing = next(
        (item for item in api.get("downloadclient") if item.get("implementation") == "QBittorrent"),
        None,
    )
    if existing and not apply:
        return "configured"
    schemas = api.get("downloadclient/schema")
    resource = dict(existing or next(item for item in schemas if item.get("implementation") == "QBittorrent"))
    resource.update({
        "name": "qBittorrent",
        "enable": True,
        "priority": 1,
        "removeCompletedDownloads": True,
        "removeFailedDownloads": True,
    })
    set_field(resource, "host", "qbittorrent")
    set_field(resource, "port", 8080)
    set_field(resource, "useSsl", False)
    set_field(resource, "username", require(env, "QBITTORRENT_USERNAME"))
    set_field(resource, "password", require(env, "QBITTORRENT_PASSWORD"))
    category_fields = {"radarr": "movieCategory", "sonarr": "tvCategory"}
    categories = {"radarr": "movies", "sonarr": "tv"}
    set_field(resource, category_fields[service], categories[service])
    if apply:
        api.request("POST", "downloadclient/test", resource)
        api.save("downloadclient", resource, existing.get("id") if existing else None)
    return "updated" if existing else "created"


def check_root_and_profile(
    service: str, env: dict[str, str], api: Api, apply: bool
) -> tuple[str, str]:
    root = require(env, f"{service.upper()}_ROOT_FOLDER")
    roots = api.get("rootfolder")
    root_result = "configured" if any(item.get("path") == root for item in roots) else "missing"
    if root_result == "missing" and apply:
        api.request("POST", "rootfolder", {"path": root})
        root_result = "created"

    profile_name = require(env, f"{service.upper()}_QUALITY_PROFILE")
    profiles = api.get("qualityprofile")
    if not any(item.get("name") == profile_name for item in profiles):
        raise RuntimeError(
            f"{service.title()} has no quality profile named {profile_name!r}; "
            "create it or change the matching .env value"
        )
    return root_result, profile_name


def configure_webhook(service: str, api: Api, token: str, apply: bool) -> str:
    name = "Home Media API"
    existing = next(
        (item for item in api.get("notification") if item.get("name") == name),
        None,
    )
    if existing and not apply:
        return "configured"
    schemas = api.get("notification/schema")
    resource = dict(existing or next(item for item in schemas if item.get("implementation") == "Webhook"))
    resource["name"] = name
    resource["onDownload"] = True
    set_field(resource, "url", "http://api:8000/v1/automation/events/arr")
    set_field(resource, "method", 1)
    set_field(resource, "headers", [{"key": "X-Automation-Token", "value": token}])
    if apply:
        api.request("POST", "notification/test", resource)
        api.save("notification", resource, existing.get("id") if existing else None)
    return "updated" if existing else "created"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="create or update the connections")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    env = load_env(args.env_file)

    config_root = Path(require(env, "CONFIG_ROOT"))
    prowlarr_key = ET.parse(config_root / "prowlarr" / "config.xml").getroot().findtext("ApiKey")
    if not prowlarr_key:
        raise RuntimeError("Prowlarr API key is unavailable; complete its first-run setup")

    apis = {
        "radarr": Api("http://127.0.0.1:7878", require(env, "RADARR_API_KEY"), "v3"),
        "sonarr": Api("http://127.0.0.1:8989", require(env, "SONARR_API_KEY"), "v3"),
    }
    prowlarr = Api("http://127.0.0.1:9696", prowlarr_key, "v1")
    actions: dict[str, str] = {}
    for service, api in apis.items():
        actions[f"Prowlarr -> {service.title()}"] = configure_prowlarr_application(
            service, env, prowlarr, args.apply
        )
        actions[f"{service.title()} -> qBittorrent"] = configure_download_client(
            service, env, api, args.apply
        )
        root_result, profile = check_root_and_profile(service, env, api, args.apply)
        actions[f"{service.title()} root folder"] = root_result
        actions[f"{service.title()} quality profile"] = profile
    token = require(env, "AUTOMATION_WEBHOOK_TOKEN")
    for service, api in apis.items():
        actions[f"{service.title()} import webhook"] = configure_webhook(service, api, token, args.apply)
    mode = "Applied" if args.apply else "Checked"
    print(f"{mode} media-service connections:")
    for name, result in actions.items():
        print(f"- {name}: {result}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ET.ParseError) as exc:
        print(f"Configuration failed: {exc}", file=sys.stderr)
        sys.exit(1)
