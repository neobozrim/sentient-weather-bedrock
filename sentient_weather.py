import os
import json
from flask import Flask, render_template, request, jsonify
from dotenv import find_dotenv, load_dotenv
from notebook_functions import (
    get_city_coordinates,
    get_weather_data,
    get_weather_description,
    generate_color_palette,
    generate_city_image,
    generate_font_recommendations,
    get_css_variables
)

# Load environment variables
dotenv_path = find_dotenv()
if dotenv_path:
    load_dotenv(dotenv_path)
else:
    print("Warning: .env file not found")

# Verify the environment variables are loaded
if not os.getenv('ANTHROPIC_API_KEY') or not os.getenv('OPENAI_API_KEY'):
    print("Error: Required API keys not found in environment variables")
    print("Please ensure your .env file contains ANTHROPIC_API_KEY and OPENAI_API_KEY")
    exit(1)

app = Flask(__name__)

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

def process_colors(color_response):
    """Process color response from Anthropic API"""
    try:
        # If the response is already a dict, return it
        if isinstance(color_response, dict):
            return color_response
            
        # If it's a TextBlock response (from Anthropic), extract the text
        if hasattr(color_response, '__getitem__') and hasattr(color_response[0], 'text'):
            color_response = color_response[0].text
            
        # If it's a string, parse it as JSON
        if isinstance(color_response, str):
            return json.loads(color_response)
            
        return get_default_colors()
    except Exception as e:
        print(f"Error processing colors: {str(e)}")
        return get_default_colors()

def process_fonts(generated_fonts):
    """Process the generated fonts and ensure they have all required fields"""
    try:
        if not generated_fonts:
            print("No fonts generated, using defaults")
            return get_default_fonts()

        # If generated_fonts is a string (JSON), parse it
        if isinstance(generated_fonts, str):
            try:
                generated_fonts = json.loads(generated_fonts)
            except json.JSONDecodeError as e:
                print(f"Error decoding font JSON: {e}")
                return get_default_fonts()
            
        # Create a copy of the generated fonts to avoid modifying the original
        processed_fonts = {}
        
        # Copy all font data from generated fonts
        for font_type in ['primary_heading', 'secondary_heading', 'body_text', 'accent_text']:
            if font_type in generated_fonts:
                processed_fonts[font_type] = generated_fonts[font_type].copy()
            else:
                processed_fonts[font_type] = get_default_fonts()[font_type]
                
        # Validate and ensure all required fields exist
        required_fields = ['family', 'weight', 'style', 'fallback']
        for font_type in processed_fonts:
            for field in required_fields:
                if field not in processed_fonts[font_type]:
                    processed_fonts[font_type][field] = get_default_fonts()[font_type][field]
        
        # Add Google Fonts URL
        fonts_for_url = []
        for font_type, font in processed_fonts.items():
            if font_type != 'google_fonts_url':  # Skip the URL itself when building the URL
                font_family = font['family'].replace(' ', '+')
                font_weight = font['weight']
                fonts_for_url.append(f"family={font_family}:wght@{font_weight}")
        
        processed_fonts['google_fonts_url'] = f"https://fonts.googleapis.com/css2?{'&'.join(fonts_for_url)}"
        
        print(f"Successfully processed fonts: {processed_fonts}")
        return processed_fonts
        
    except Exception as e:
        print(f"Error in process_fonts: {str(e)}")
        return get_default_fonts()

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            city = request.form['city']
            
            # Get coordinates
            coordinates = get_city_coordinates(city)
            if not coordinates:
                return render_template('index.html', 
                                     error="City not found",
                                     fonts=get_default_fonts(),
                                     font_css_vars=get_css_variables(get_default_fonts()),
                                     colors=get_default_colors())
            
            # Get weather data
            weather_data = get_weather_data(*coordinates)
            if not weather_data:
                return render_template('index.html', 
                                     error="Could not fetch weather data",
                                     fonts=get_default_fonts(),
                                     font_css_vars=get_css_variables(get_default_fonts()),
                                     colors=get_default_colors())
            
            print(f"Weather data received: {weather_data}")
            
            # Get weather description
            weather_description = get_weather_description(weather_data['current']['weather_code'])
            print(f"Weather description: {weather_description}")

            # Generate and process color palette
            try:
                print(f"Generating palette for: {city}")
                color_response = generate_color_palette(city, weather_data, weather_description)
                print(f"Color API Response: {color_response}")
                colors = process_colors(color_response)
                print(f"Processed colors: {colors}")
            except Exception as e:
                print(f"Error generating/processing colors: {str(e)}")
                colors = get_default_colors()
            
            # Generate and process font recommendations
            try:
                raw_fonts = generate_font_recommendations(city, weather_data)
                print(f"Font API Response: {json.dumps(raw_fonts, indent=4)}")
                
                if not raw_fonts:
                    print("No font recommendations received")
                    processed_fonts = get_default_fonts()
                else:
                    processed_fonts = process_fonts(raw_fonts)
                    
                font_css_vars = get_css_variables(processed_fonts)
                print(f"Generated CSS variables: {font_css_vars}")
                
            except Exception as e:
                print(f"Error in font generation/processing: {str(e)}")
                processed_fonts = get_default_fonts()
                font_css_vars = get_css_variables(processed_fonts)
            
            # Generate image
            try:
                image_path = generate_city_image(city, weather_description)
                print(f"Generated new image for {city} with {weather_description}")
            except Exception as e:
                print(f"Error generating image: {str(e)}")
                image_path = None
            
            return render_template('index.html',
                                 city=city,
                                 weather_data=weather_data,
                                 weather_description=weather_description,
                                 colors=colors,
                                 fonts=processed_fonts,
                                 font_css_vars=font_css_vars,
                                 image_path=image_path)
            
        except Exception as e:
            print(f"Error in main route handler: {str(e)}")
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