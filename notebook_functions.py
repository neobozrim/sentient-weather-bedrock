from geopy.geocoders import Nominatim
import openmeteo_requests
import requests_cache
import pandas as pd
from retry_requests import retry
import json
import os
import time
import requests
from datetime import datetime, timedelta
from flask import url_for
from werkzeug.utils import secure_filename
from typing import Dict, Optional, Set
import boto3
from botocore.exceptions import ClientError
import random
import base64
from flask_caching import Cache
from functools import wraps

def get_city_coordinates(city):
    """Get coordinates for a given city."""
    try:
        geolocator = Nominatim(user_agent="sentient-weather-app")
        location = geolocator.geocode(city)
        return (location.latitude, location.longitude)
    except:
        return None

def get_weather_data(latitude, longitude):
    """Get current weather and forecast data from Open Meteo API."""
    try:
        # Setup the Open-Meteo API client with cache and retry
        cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
        retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
        openmeteo = openmeteo_requests.Client(session=retry_session)

        # API parameters
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": ["temperature_2m", "is_day", "precipitation", 
                       "weather_code", "cloud_cover", "wind_speed_10m"],
            "daily": ["weather_code", "temperature_2m_max", "temperature_2m_min",
                     "precipitation_sum", "precipitation_hours",
                     "precipitation_probability_max", "wind_speed_10m_max"]
        }

        # Make the API request
        response = openmeteo.weather_api(url, params=params)[0]
        
        # Process current weather
        current = response.Current()
        current_data = {
            'temperature': current.Variables(0).Value(),
            'is_day': current.Variables(1).Value(),
            'precipitation': current.Variables(2).Value(),
            'weather_code': current.Variables(3).Value(),
            'cloud_cover': current.Variables(4).Value(),
            'wind_speed': current.Variables(5).Value(),
        }

        # Process forecast
        daily = response.Daily()
        forecast_data = pd.DataFrame({
            "date": pd.date_range(
                start=pd.to_datetime(daily.Time(), unit="s", utc=True),
                end=pd.to_datetime(daily.TimeEnd(), unit="s", utc=True),
                freq=pd.Timedelta(seconds=daily.Interval()),
                inclusive="left"
            ),
            "weather_code": daily.Variables(0).ValuesAsNumpy(),
            "temperature_max": daily.Variables(1).ValuesAsNumpy(),
            "temperature_min": daily.Variables(2).ValuesAsNumpy(),
            "precipitation_sum": daily.Variables(3).ValuesAsNumpy(),
            "precipitation_hours": daily.Variables(4).ValuesAsNumpy(),
            "precipitation_probability": daily.Variables(5).ValuesAsNumpy(),
            "wind_speed_max": daily.Variables(6).ValuesAsNumpy()
        }).to_dict('records')

        return {
            'current': current_data,
            'forecast': forecast_data
        }

    except Exception as e:
        print(f"Error fetching weather data: {str(e)}")
        return None

def get_weather_description(weather_code):
    """Convert weather code to description."""
    weather_codes = {
        0: 'Clear sky',
        1: 'Mainly clear',
        2: 'Partly cloudy',
        3: 'Overcast',
        45: 'Fog',
        48: 'Depositing rime fog',
        51: 'Drizzle: Light intensity',
        53: 'Drizzle: Moderate intensity',
        55: 'Drizzle: Dense intensity',
        56: 'Freezing Drizzle: Light intensity',
        57: 'Freezing Drizzle: Dense intensity',
        61: 'Rain: Slight intensity',
        63: 'Rain: Moderate intensity',
        65: 'Rain: Heavy intensity',
        66: 'Freezing Rain: Light intensity',
        67: 'Freezing Rain: Heavy intensity',
        71: 'Snow fall: Slight intensity',
        73: 'Snow fall: Moderate intensity',
        75: 'Snow fall: Heavy intensity',
        77: 'Snow grains',
        80: 'Rain showers: Slight',
        81: 'Rain showers: Moderate',
        82: 'Rain showers: Violent',
        85: 'Snow showers slight',
        86: 'Snow showers Heavy',
        95: 'Thunderstorm: Slight or moderate',
        96: 'Thunderstorm with slight hail',
        99: 'Thunderstorm with heavy hail'
    }
    return weather_codes.get(int(weather_code), 'Unknown')

def generate_color_palette(city, weather_data, weather_description):
    """Generate color palette using AWS Bedrock's Claude 3.5."""
    try:
        print(f"Generating palette for: {city}")
        print(f"Weather data received: {weather_data}")
        print(f"Weather description: {weather_description}")

        if not weather_data or 'current' not in weather_data:
            raise ValueError("Weather data is missing or incomplete")

        current_weather = weather_data['current']
        required_fields = ['temperature', 'precipitation', 'cloud_cover', 'wind_speed', 'is_day']
        for field in required_fields:
            if field not in current_weather:
                raise ValueError(f"Missing required weather field: {field}")

        bedrock = boto3.client('bedrock-runtime')
        
        prompt = f"""
        You are an expert UI designer specializing in color theory. Your goal is to generate a well balanced color palette for a weather app webpage.
        The color palette is inspired by the unique atmosphere of a city and the current weather conditions.
        The city and current weather conditions are:
        - location: {city}
        - weather description: {weather_description}
        - current temperature: {current_weather['temperature']}°C
        - current precipitation: {current_weather['precipitation']}mm
        - current cloud cover: {current_weather['cloud_cover']}%
        - current wind speed: {current_weather['wind_speed']}km/h

        Based on the unique atmosphere that the city is known for and the current weather, here are the colors that need to be in the color palette:
           * color_page_background 
           * color_tiles_container  
           * color_tiles 
        Then generate the following colors of the contents shown in the weather tiles, ensuring excellent readability: 
           * color_tile_heading
           * color_tile_temp_high
           * color_tile_temp_low
           * color_tile_weather_details

        IMPORTANT! Ensure excellent readability and proper contrast when the colors are combined on a webpage. 
        Be creative and remember to have it inspired by the unique atmosphere of the city and the current weather conditions.
        Return colors in hexadecimal format (e.g., #RRGGBB).

        Requirements:
        - The output must be valid JSON
        - Use ONLY the following keys: color_page_background, color_tiles_container, color_tiles, color_tile_heading, color_tile_temp_high, color_tile_temp_low, color_tile_weather_details
        - Each key should get a hexadecimal color code (e.g., #RRGGBB)
        - Do not include any explanation or other text
        - Each value should be of type string (str)
        """

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.7
        }

        response = bedrock.invoke_model(
            modelId='anthropic.claude-3-sonnet-20240229-v1:0',
            body=json.dumps(body)
        )

        response_body = json.loads(response.get('body').read())
        completion = response_body.get('content')[0].get('text')
        
        colors = json.loads(completion)

        required_colors = [
            'color_page_background', 'color_tiles_container', 'color_tiles',
            'color_tile_heading', 'color_tile_temp_high', 'color_tile_temp_low',
            'color_tile_weather_details'
        ]

        def rgb_to_hex(rgb_str):
            if rgb_str.startswith('#'):
                return rgb_str
            try:
                rgb_vals = [int(x.strip()) for x in rgb_str.strip('rgb()').split(',')]
                return '#{:02x}{:02x}{:02x}'.format(*rgb_vals)
            except:
                raise ValueError(f"Invalid color format: {rgb_str}")

        for color in required_colors:
            if color not in colors:
                raise ValueError(f"Missing required color: {color}")
            if not isinstance(colors[color], str):
                raise ValueError(f"Invalid color format for {color}: {colors[color]}")
            colors[color] = rgb_to_hex(colors[color])

        return colors

    except ClientError as e:
        print(f"AWS Bedrock API error: {str(e)}")
        print(f"Error code: {e.response['Error']['Code']}")
        print(f"Error message: {e.response['Error']['Message']}")
        return None
    except Exception as e:
        print(f"Error generating color palette: {str(e)}")
        print(f"Error type: {type(e)}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return None

def generate_font_recommendations(city: str, weather_data: Dict) -> Optional[Dict]:
    """Generate font recommendations using AWS Bedrock's Claude 3.5."""
    try:
        bedrock = boto3.client('bedrock-runtime')
        
        current_weather = weather_data['current']
        weather_description = get_weather_description(current_weather['weather_code'])
        
        prompt = f"""
        You are an expert typography designer specializing in creating unique digital experiences. 
        Generate font recommendations for {city} that reflect its unique character and current weather conditions:
        - Weather: {weather_description}
        - Temperature: {current_weather['temperature']}°C
        - Cloud Cover: {current_weather['cloud_cover']}%
        
        Consider these aspects of the city:
        1. Historical significance and age
        2. Cultural characteristics
        3. Primary industries/identity (tech hub, cultural center, financial district, etc.)
        4. Geographic location and regional influences
        
        For each font category, recommend a specific Google Font that best matches the city's character:

        The response must include these exact categories:
        1. primary_heading: For the main city name and temperature (should be distinctive)
        2. secondary_heading: For weather condition descriptions and daily forecasts
        3. body_text: For detailed weather information
        4. accent_text: For small labels and secondary information
        """

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": 1024,
            "temperature": 0.7
        }

        response = bedrock.invoke_model(
            modelId='anthropic.claude-3-sonnet-20240229-v1:0',
            body=json.dumps(body)
        )

        response_body = json.loads(response.get('body').read())
        completion = response_body.get('content')[0].get('text')
        
        font_data = json.loads(completion)
        print("Font API Response:", font_data)
        
        required_categories = ['primary_heading', 'secondary_heading', 'body_text', 'accent_text']
        required_properties = ['family', 'weight', 'style', 'fallback']
        
        for category in required_categories:
            if category not in font_data:
                raise ValueError(f"Missing required category: {category}")
            for prop in required_properties:
                if prop not in font_data[category]:
                    raise ValueError(f"Missing property {prop} in {category}")

        return font_data

    except ClientError as e:
        print(f"AWS Bedrock API error: {str(e)}")
        print(f"Error code: {e.response['Error']['Code']}")
        print(f"Error message: {e.response['Error']['Message']}")
        return None
    except Exception as e:
        print(f"Error generating font recommendations: {str(e)}")
        return None

def generate_city_image(city, weather_description):
    """Generate or retrieve a cached city image using AWS Bedrock's SDXL."""
    try:
        project_root = os.path.abspath(os.path.dirname(__file__))
        static_dir = os.path.join(project_root, 'static', 'images')
        os.makedirs(static_dir, exist_ok=True)

        safe_city_name = secure_filename(city.lower())
        safe_weather = secure_filename(weather_description.lower())
        cache_key = f"{safe_city_name}_{safe_weather}"
        
        cache_file = os.path.join(static_dir, f"{cache_key}.json")

        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cache_data = json.load(f)
                
            cache_timestamp = datetime.fromtimestamp(cache_data['timestamp'])
            if datetime.now() - cache_timestamp < timedelta(hours=24):
                image_path = cache_data['image_path']
                static_image_path = os.path.join(project_root, 'static', image_path.lstrip('/static/'))
                if os.path.exists(static_image_path):
                    print(f"Using cached image for {city} with {weather_description}")
                    return image_path

        bedrock = boto3.client('bedrock-runtime')

        timestamp = int(time.time())
        filename = f"{cache_key}_{timestamp}.png"
        image_path = os.path.join(static_dir, filename)

        existing_images = sorted(
            ((os.path.join(static_dir, f), os.path.getctime(os.path.join(static_dir, f)))
             for f in os.listdir(static_dir) if f.endswith('.png')),
            key=lambda x: x[1],
            reverse=True
        )
        if len(existing_images) > 20:
            for old_image, _ in existing_images[20:]:
                try:
                    os.remove(old_image)
                    old_cache = os.path.join(static_dir, f"{os.path.splitext(os.path.basename(old_image))[0]}.json")
                    if os.path.exists(old_cache):
                        os.remove(old_cache)
                except Exception as e:
                    print(f"Error removing old file {old_image}: {str(e)}")

        # Prepare request for SDXL
        prompt = f"An oil painting of the most iconic scenery from {city} where the weather is {weather_description}, high quality, detailed, photorealistic"
        negative_prompt = "text, watermark, signature, blurry, distorted, low quality, deformed"

        request_body = {
            "text_prompts": [
                {
                    "text": prompt,
                    "weight": 1.0
                },
                {
                    "text": negative_prompt,
                    "weight": -1.0
                }
            ],
            "cfg_scale": 7.0,
            "seed": random.randint(0, 4294967295),
            "steps": 30,  # Reduced from 50 for better performance
            "width": 512,  # Reduced for better performance
            "height": 512,
            "style_preset": "photographic",
            "image_strength": 0.8
        }

        try:
            response = bedrock.invoke_model(
                modelId='stability.stable-diffusion-xl-v1',
                body=json.dumps(request_body)
            )
            
            response_body = json.loads(response.get('body').read())
            image_data = base64.b64decode(response_body['artifacts'][0]['base64'])
            
            with open(image_path, 'wb') as f:
                f.write(image_data)

            relative_path = f'/static/images/{filename}'
            
            cache_data = {
                'timestamp': timestamp,
                'image_path': relative_path,
                'city': city,
                'weather': weather_description
            }
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f)

            print(f"Generated new image for {city} with {weather_description}")
            return relative_path

        except ClientError as e:
            print(f"Bedrock API error: {str(e)}")
            print(f"Error code: {e.response['Error']['Code']}")
            print(f"Error message: {e.response['Error']['Message']}")
            return None

    except Exception as e:
        print(f"Error generating city image: {str(e)}")
        print(f"Error type: {type(e)}")
        import traceback
        print(f"Full traceback: {traceback.format_exc()}")
        return None

def get_default_fonts():
    """Return default font configuration when font generation fails"""
    return {
        'google_fonts_url': 'https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700&family=Montserrat:wght@600&family=Open+Sans&family=Roboto+Condensed&display=swap',
        'primary_heading': {
            'family': 'Playfair Display',
            'fallback': 'serif',
            'weight': '700',
            'style': 'normal'
        },
        'secondary_heading': {
            'family': 'Montserrat',
            'fallback': 'sans-serif',
            'weight': '600',
            'style': 'normal'
        },
        'body_text': {
            'family': 'Open Sans',
            'fallback': 'sans-serif',
            'weight': '400',
            'style': 'normal'
        },
        'accent_text': {
            'family': 'Roboto Condensed',
            'fallback': 'sans-serif',
            'weight': '400',
            'style': 'normal'
        }
    }

def get_default_colors():
    """Return default color configuration when color generation fails"""
    return {
        'color_page_background': '#F5E6D3',
        'color_tiles_container': '#FFB366',
        'color_tiles': '#FFFFFF',
        'color_tile_heading': '#994D00',
        'color_tile_temp_high': '#CC3300',
        'color_tile_temp_low': '#336699',
        'color_tile_weather_details': '#4D4D4D'
    }

def get_css_variables(fonts):
    """Generate CSS variables with font-family declarations"""
    if not fonts:
        return get_css_variables(get_default_fonts())
        
    css_vars = {}
    
    try:
        for category, font in fonts.items():
            if category != 'google_fonts_url':
                var_name = f"--font-{category.replace('_', '-')}"
                family = f"'{font['family']}'" if ' ' in font['family'] else font['family']
                css_vars[var_name] = f"{family}, {font['fallback']}"
                
                css_vars[f"{var_name}-weight"] = font['weight']
                css_vars[f"{var_name}-style"] = font['style']
                
        print("Generated CSS variables:", css_vars)
        return css_vars
        
    except Exception as e:
        print(f"Error generating CSS variables: {str(e)}")
        return get_css_variables(get_default_fonts())