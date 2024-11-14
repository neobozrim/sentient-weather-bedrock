import os
import json
from flask import Flask, render_template, request, jsonify
from dotenv import find_dotenv, load_dotenv
from flask_caching import Cache
import asyncio
from concurrent.futures import ThreadPoolExecutor
import redis
from functools import wraps
import time
from werkzeug.contrib.cache import RedisCache
from ratelimit import limits, sleep_and_retry
from notebook_functions import (
    get_city_coordinates,
    get_weather_data,
    get_weather_description,
    generate_color_palette,
    generate_city_image,
    generate_font_recommendations,
    get_css_variables,
    get_default_fonts,
    get_default_colors
)

# Initialize Flask app
app = Flask(__name__)

# Configure Redis Cache (if available) or fallback to SimpleCache
if os.getenv('REDIS_URL'):
    cache = Cache(config={
        'CACHE_TYPE': 'redis',
        'CACHE_REDIS_URL': os.getenv('REDIS_URL'),
        'CACHE_DEFAULT_TIMEOUT': 3600
    })
else:
    cache = Cache(config={
        'CACHE_TYPE': 'simple',
        'CACHE_DEFAULT_TIMEOUT': 3600
    })
cache.init_app(app)

# Rate limiting configuration
CALLS_PER_MINUTE = 30

# Load environment variables
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path)
else:
    print("Warning: .env file not found")

# Verify environment variables
required_vars = ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_DEFAULT_REGION']
for var in required_vars:
    if not os.getenv(var):
        print(f"Error: Required environment variable {var} not found")
        exit(1)

# Cache decorator
def cache_response(timeout=3600):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            cache_key = f"{f.__name__}:{str(args)}:{str(kwargs)}"
            cached_response = cache.get(cache_key)
            if cached_response:
                print(f"Cache hit for {cache_key}")
                return cached_response
            print(f"Cache miss for {cache_key}")
            result = f(*args, **kwargs)
            cache.set(cache_key, result, timeout=timeout)
            return result
        return wrapper
    return decorator

# Rate limiting decorator
@sleep_and_retry
@limits(calls=CALLS_PER_MINUTE, period=60)
def rate_limited_call(func, *args, **kwargs):
    return func(*args, **kwargs)

# Async content generation
async def generate_all_content(city, weather_data, weather_description):
    """Generate colors, fonts, and image in parallel"""
    try:
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            # Create tasks for all generations
            color_future = loop.run_in_executor(
                executor, 
                rate_limited_call,
                generate_color_palette,
                city, 
                weather_data,
                weather_description
            )
            
            font_future = loop.run_in_executor(
                executor,
                rate_limited_call,
                generate_font_recommendations,
                city,
                weather_data
            )
            
            image_future = loop.run_in_executor(
                executor,
                rate_limited_call,
                generate_city_image,
                city,
                weather_description
            )
            
            # Wait for all tasks to complete
            colors, fonts, image = await asyncio.gather(
                color_future,
                font_future,
                image_future
            )
            
            return {
                'colors': colors,
                'fonts': fonts,
                'image_path': image,
                'status': 'complete'
            }
    except Exception as e:
        print(f"Error in generate_all_content: {str(e)}")
        return None

@app.route('/check-content')
def check_content():
    """Check if content generation is complete"""
    city = request.args.get('city')
    cache_key = f"weather_response:{city}"
    content = cache.get(cache_key)
    return jsonify({'ready': content is not None})

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            city = request.form['city']
            
            # Check cache first
            cache_key = f"weather_response:{city}"
            cached_response = cache.get(cache_key)
            if cached_response:
                print(f"Using cached response for {city}")
                return render_template('index.html', **cached_response)
            
            # Get coordinates with rate limiting
            coordinates = rate_limited_call(get_city_coordinates, city)
            if not coordinates:
                return render_template('index.html', 
                                     error="City not found",
                                     fonts=get_default_fonts(),
                                     font_css_vars=get_css_variables(get_default_fonts()),
                                     colors=get_default_colors())
            
            # Get weather data with caching and rate limiting
            weather_data = rate_limited_call(get_weather_data, *coordinates)
            if not weather_data:
                return render_template('index.html', 
                                     error="Could not fetch weather data",
                                     fonts=get_default_fonts(),
                                     font_css_vars=get_css_variables(get_default_fonts()),
                                     colors=get_default_colors())
            
            # Get weather description
            weather_description = get_weather_description(weather_data['current']['weather_code'])
            print(f"Weather description: {weather_description}")
            
            # Return initial response with loading state
            initial_response = render_template(
                'index.html',
                city=city,
                weather_data=weather_data,
                weather_description=weather_description,
                loading=True,
                fonts=get_default_fonts(),
                font_css_vars=get_css_variables(get_default_fonts()),
                colors=get_default_colors()
            )
            
            # Generate content asynchronously
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            content = loop.run_until_complete(
                generate_all_content(city, weather_data, weather_description)
            )
            loop.close()
            
            if content and content['status'] == 'complete':
                response_data = {
                    'city': city,
                    'weather_data': weather_data,
                    'weather_description': weather_description,
                    'colors': content['colors'],
                    'fonts': content['fonts'],
                    'font_css_vars': get_css_variables(content['fonts']),
                    'image_path': content['image_path'],
                    'loading': False
                }
                
                # Cache the successful response
                cache.set(cache_key, response_data, timeout=3600)
                print(f"Cached response for {city}")
                
                return render_template('index.html', **response_data)
            else:
                return render_template('index.html',
                                     error="Error generating content",
                                     fonts=get_default_fonts(),
                                     font_css_vars=get_css_variables(get_default_fonts()),
                                     colors=get_default_colors())
            
        except Exception as e:
            print(f"Error in main route handler: {str(e)}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")
            default_fonts = get_default_fonts()
            return render_template('index.html', 
                                 error=str(e),
                                 fonts=default_fonts,
                                 font_css_vars=get_css_variables(default_fonts),
                                 colors=get_default_colors())
    
    # GET request - return initial page with default styling
    default_fonts = get_default_fonts()
    return render_template('index.html',
                         fonts=default_fonts,
                         font_css_vars=get_css_variables(default_fonts),
                         colors=get_default_colors())

if __name__ == '__main__':
    app.run(debug=True)