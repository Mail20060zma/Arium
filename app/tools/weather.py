import logging
import requests

logger = logging.getLogger(__name__)

def get_coordinates_by_ip() -> dict:
    try:
        res = requests.get("https://ipinfo.io/json", timeout=5)
        res.raise_for_status()
        data = res.json()
        lat, lon = (None, None)
        if "loc" in data:
            lat, lon = map(float, data["loc"].split(","))
        return {
            "city": data.get("city"),
            "region": data.get("region"),
            "country": data.get("country"),
            "lat": lat,
            "lon": lon
        }
    except Exception as e:
        logger.error(f"Ошибка при получении координат по IP: {e}")
        return {"error": str(e)}
    
def get_coordinates_by_city(city_name: str) -> dict:
    """
    Получаем координаты города через геокодинг Open-Meteo
    """
    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": city_name, "count": 1}
    
    try:
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        if "results" in data and len(data["results"]) > 0:
            loc = data["results"][0]
            return {"lat": loc["latitude"], "lon": loc["longitude"], "city": loc.get("name"), "country": loc.get("country")}
        else:
            return {"lat": None, "lon": None, "city": None, "country": None}
    except requests.RequestException as e:
        logger.error(f"Ошибка при геокодинге города {city_name}: {e}")
        return {"lat": None, "lon": None, "city": None, "country": None}
    
def get_weather(location: str = None) -> dict:
    """
    Инструмент для получения погоды.
    Если location не указан, использует текущее местоположение по IP.
    """
    lat, lon, city, country = None, None, "Unknown", "Unknown"
    
    if location:
        coordinates = get_coordinates_by_city(location)
        lat = coordinates.get("lat")
        lon = coordinates.get("lon")
        city = coordinates.get("city", location)
        country = coordinates.get("country", "Unknown")
        if lat is None:
            return {"error": f"Город '{location}' не найден"}
    else:
        coordinates = get_coordinates_by_ip()
        lat = coordinates.get("lat")
        lon = coordinates.get("lon")
        city = coordinates.get("city", "Unknown")
        country = coordinates.get("country", "Unknown")
        
        if lat is None or lon is None:
            return {"error": "Не удалось определить местоположение. Пожалуйста, укажите город явно."}

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current_weather": True,
        "forecast_days": 3,
        "timezone": "auto",
        "daily": [
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
        ],
    }

    try:
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        # Формируем компактный ответ для LLM
        result = {
            "location": f"{city}, {country}",
            "current_weather": data.get("current_weather", {}),
            "forecast": []
        }
        
        daily = data.get("daily", {})
        if daily and "time" in daily:
            for i in range(len(daily["time"])):
                result["forecast"].append({
                    "date": daily["time"][i],
                    "max_temp": daily["temperature_2m_max"][i],
                    "min_temp": daily["temperature_2m_min"][i],
                    "precipitation": daily["precipitation_sum"][i]
                })
                
        return result
    except requests.RequestException as e:
        logger.error(f"Ошибка при получении погоды для {city}: {e}")
        return {"error": str(e)}
