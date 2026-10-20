"""Authenticated Home Assistant access to the read-only BirdNET dashboard."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from aiohttp import ClientError, ClientTimeout, web
import voluptuous as vol

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.const import KEY_HASS_USER
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

DOMAIN = "birdnet_dashboard"
CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required("url"): cv.url,
        vol.Required("token_file"): cv.string,
        vol.Required("allowed_user_ids"): vol.All(cv.ensure_list, [cv.string], vol.Length(min=1)),
    }),
}, extra=vol.ALLOW_EXTRA)


def day_window(value, timezone_name):
    """Use calendar days in the station timezone, including 23/25-hour DST days."""
    zone = ZoneInfo(timezone_name)
    today = datetime.now(zone).date()
    chosen = date.fromisoformat(value) if value else today
    if chosen > today or chosen.year < 2000:
        raise ValueError("Choose a date from 2000 through today")
    start = datetime.combine(chosen, time.min, zone)
    end = datetime.combine(chosen + timedelta(days=1), time.min, zone)
    first, last = int(start.timestamp()), int(end.timestamp())
    labels = [datetime.fromtimestamp(t, zone).strftime("%H:%M") for t in range(first, last, 3600)]
    return chosen.isoformat(), first, last, labels


def allowed(request, user_ids):
    user = request.get(KEY_HASS_USER)
    return user is not None and user.id in user_ids


async def async_setup(hass, config):
    settings = config[DOMAIN]
    token = (await hass.async_add_executor_job(Path(settings["token_file"]).read_text)).strip()
    if len(token) < 32:
        raise ValueError("BirdNET dashboard token is missing or too short")
    session = async_get_clientsession(hass)
    url = settings["url"].rstrip("/")
    users = frozenset(settings["allowed_user_ids"])
    hass.http.register_view(DashboardView(hass, session, url, token, users))
    hass.http.register_view(AudioView(hass, session, url, token, users))
    return True


class DashboardView(HomeAssistantView):
    url = "/api/birdnet_dashboard/dashboard"
    name = "api:birdnet_dashboard:dashboard"
    requires_auth = True

    def __init__(self, hass, session, upstream, token, users):
        self.hass, self.session, self.upstream, self.token, self.users = hass, session, upstream, token, users

    async def get(self, request):
        if not allowed(request, self.users):
            return web.json_response({"error": "Bird dashboard access is limited to the household"}, status=403)
        query = request.query
        if set(query) - {"date", "source", "confidence", "search", "offset", "rejected"}:
            return web.json_response({"error": "Unknown dashboard filter"}, status=400)
        try:
            chosen, first, last, labels = day_window(query.get("date"), self.hass.config.time_zone)
        except (ValueError, OverflowError):
            return web.json_response({"error": "Choose a valid date through today"}, status=400)
        parameters = {k: query[k] for k in ("source", "confidence", "search", "offset", "rejected") if k in query}
        parameters.update(start=str(first), end=str(last))
        try:
            async with self.session.get(self.upstream + "/v1/dashboard", params=parameters,
                                        headers={"Authorization": "Bearer " + self.token},
                                        timeout=ClientTimeout(total=15), allow_redirects=False) as response:
                if response.status == 400:
                    return web.json_response({"error": "Check the selected filters"}, status=400)
                if response.status != 200:
                    return web.json_response({"error": "BirdNET data is temporarily unavailable"}, status=503)
                data = await response.json()
        except (ClientError, asyncio.TimeoutError, ValueError):
            return web.json_response({"error": "BirdNET data is temporarily unavailable"}, status=503)
        data.update(date=chosen, timezone=self.hass.config.time_zone, hour_labels=labels,
                    updated_at=datetime.now(timezone.utc).isoformat())
        return web.json_response(data, headers={"Cache-Control": "no-store"})


class AudioView(HomeAssistantView):
    url = "/api/birdnet_dashboard/audio/{detection_id}"
    name = "api:birdnet_dashboard:audio"
    # HA's normal authentication also validates short-lived signed media URLs.
    requires_auth = True

    def __init__(self, hass, session, upstream, token, users):
        self.hass, self.session, self.upstream, self.token, self.users = hass, session, upstream, token, users

    async def get(self, request, detection_id):
        if not allowed(request, self.users):
            return web.json_response({"error": "Not permitted"}, status=403)
        if not re.fullmatch(r"[1-9][0-9]{0,18}", detection_id):
            return web.json_response({"error": "Invalid detection"}, status=400)
        headers = {"Authorization": "Bearer " + self.token}
        if value := request.headers.get("Range"):
            headers["Range"] = value
        try:
            async with self.session.get(self.upstream + "/v1/audio/" + detection_id,
                                        headers=headers, timeout=ClientTimeout(total=45),
                                        allow_redirects=False) as upstream:
                if upstream.status not in (200, 206):
                    status = upstream.status if upstream.status in (404, 416) else 503
                    result_headers = {"Content-Range": upstream.headers["Content-Range"]} if "Content-Range" in upstream.headers else {}
                    return web.Response(status=status, headers=result_headers)
                result_headers = {k: v for k, v in upstream.headers.items()
                                  if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges")}
                result_headers.update({"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
                result = web.StreamResponse(status=upstream.status, headers=result_headers)
                await result.prepare(request)
                async for chunk in upstream.content.iter_chunked(65536):
                    await result.write(chunk)
                await result.write_eof()
                return result
        except (ClientError, asyncio.TimeoutError):
            return web.Response(status=503)
