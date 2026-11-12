"""
Tools available on the TOOL route.

Tools return a readable string — including on errors — instead of raising,
so the agent can explain the problem to the user.
"""

import logging

import httpx
from langchain.tools import tool

from app.ai.constants import (
    HTTP_TIMEOUT,
    INR_PER_USD,
    OPEN_METEO_FORECAST_URL,
    OPEN_METEO_GEOCODE_URL,
)


logger = logging.getLogger(__name__)


# WMO weather codes used by Open-Meteo -> text
WEATHER_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snowfall", 73: "Moderate snowfall", 75: "Heavy snowfall", 77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


@tool
async def weather_tool(city: str) -> str:
    """Get the CURRENT weather (temperature, conditions, humidity, wind) for a city, e.g. "Chennai"."""

    city = city.strip()

    if not city:
        return "Error: no city name was given."

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:

            # 1. City name -> coordinates
            geo = await client.get(
                OPEN_METEO_GEOCODE_URL,
                params={"name": city, "count": 1, "language": "en", "format": "json"}
            )
            geo.raise_for_status()
            places = geo.json().get("results") or []

            if not places:
                return f"Error: could not find a city called '{city}'."

            place = places[0]

            # 2. Coordinates -> current weather
            forecast = await client.get(
                OPEN_METEO_FORECAST_URL,
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "timezone": "auto",
                }
            )
            forecast.raise_for_status()
            current = forecast.json()["current"]

    except httpx.HTTPError as error:
        logger.warning("Weather lookup failed for %s: %s", city, error)
        return f"Error: the weather service is unavailable right now ({type(error).__name__})."

    location = ", ".join(p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p)
    condition = WEATHER_CODES.get(current.get("weather_code"), "Unknown conditions")

    return (
        f"Current weather in {location} (as of {current.get('time')} local time): "
        f"{condition}, {current.get('temperature_2m')}°C, "
        f"humidity {current.get('relative_humidity_2m')}%, "
        f"wind {current.get('wind_speed_10m')} km/h."
    )


@tool
def inr_to_usd(amount_inr: float) -> str:
    """Convert an amount in Indian Rupees (INR) to US Dollars (USD)."""

    if amount_inr < 0:
        return "Error: the amount must not be negative."

    usd = amount_inr / INR_PER_USD

    return f"₹{amount_inr:,.2f} ≈ ${usd:,.2f} USD (rate: {INR_PER_USD} INR per USD)"


tools = [weather_tool, inr_to_usd]
