from flask import Flask, render_template, request
import firebase_admin
from firebase_admin import credentials, firestore
import requests
import json
import os
import logging

app = Flask(__name__)

logging.basicConfig(level=logging.DEBUG)

# Initialize Firebase Admin SDK
cred = credentials.Certificate('xxx')  # Replace with your service account key path
firebase_admin.initialize_app(cred)
db = firestore.client()

room_type_mapping = {
        'Living Room': 'living_room',
        'Bedroom': 'bedroom',
        'Dining Room': 'dining_room',
        # Add other mappings as needed
    }


furniture_type_mapping = {
        'Couch': 'sofa',
        'Media Console': 'media_table',
        'Accent Chair': 'chair',
        'Area Rug': 'rug',
        'Coffee Table': 'coffee_table',
        'Ottoman': 'ottoman',
        'Side Table': 'side_table',
        # Add other mappings as needed
    }

@app.route('/')
def index():
    # Step 1: Read the latest user request from 'User_request' collection
    user_requests_ref = db.collection('User_request')
    user_requests = user_requests_ref.order_by('submit_time', direction=firestore.Query.DESCENDING).limit(1).get()
    if not user_requests:
        logging.error("No user requests found.")
        return "No user requests found."

    user_request = user_requests[0].to_dict()
    logging.debug(f"User Request: {user_request}")

    # Extract parameters from the user request
    budget = user_request.get('budget', 0)
    furniture_brands = user_request.get('furniture_brands', [])
    furniture_types = user_request.get('furniture_types', [])
    room_type = user_request.get('room_type', '').strip()
    aesthetic = user_request.get('aesthetic', '')

    # Ensure furniture_brands and furniture_types are lists
    if isinstance(furniture_brands, str):
        furniture_brands = [brand.strip() for brand in furniture_brands.split(',')]
    logging.debug(f"Furniture Brands List: {furniture_brands}")

    if isinstance(furniture_types, str):
        furniture_types = [ftype.strip() for ftype in furniture_types.split(',')]
    logging.debug(f"Furniture Types List: {furniture_types}")

    # Map the room_type to match the furniture_inventory naming
    room_type_mapped = room_type_mapping.get(room_type, '').strip()
    logging.debug(f"Mapped Room Type: {room_type_mapped}")

    # Map the furniture_types to match the furniture_inventory naming
    furniture_types_mapped = [furniture_type_mapping.get(ft.strip(), '').strip() for ft in furniture_types]
    furniture_types_mapped = [ft for ft in furniture_types_mapped if ft]  # Remove empty strings
    logging.debug(f"Mapped Furniture Types: {furniture_types_mapped}")

    # Step 2: Query the 'furniture_inventory' collection
    furniture_items = query_furniture_inventory(budget, furniture_brands, furniture_types_mapped, room_type_mapped)
    if not furniture_items:
        logging.warning("No matching furniture items found after filtering.")
        return "No matching furniture items found after filtering."

    # Step 3: Use GPT API to recommend a final list
    recommended_items = get_gpt_recommendations(furniture_items, user_request)
    if not recommended_items:
        logging.warning("GPT did not return any recommendations.")
        return "GPT did not return any recommendations."
    
    

    # Calculate the total price
    total_price = sum(item.get('price', 0) for item in recommended_items)

    # Remove tuple structure and keep only the dictionaries
    normalized_items = [item[1] for item in enumerate(recommended_items, start=1)]

    # Log the items and total price to debug
    logging.debug(f"Final Items Passed to Template: {normalized_items}")
    logging.debug(f"Total Price Passed to Template: {total_price}")

    # Pass normalized data to the template
    return render_template('results.html', items=normalized_items, total_price=total_price)
   

def query_furniture_inventory(budget, furniture_brands, furniture_types_mapped, room_type_mapped):
    """
    Query furniture inventory from Firestore and filter results in Python to avoid composite indexes.
    """
    try:
        # Fetch all items from the collection
        results = db.collection('furniture_inventory').get()

        # Convert Firestore documents to Python dictionaries
        furniture_items = [doc.to_dict() for doc in results]

        # Filter results in Python
        filtered_items = []
        for item in furniture_items:
            if budget and item.get('price', 0) > budget:
                continue
            if furniture_brands and item.get('brand') not in furniture_brands:
                continue
            if furniture_types_mapped and item.get('type') not in furniture_types_mapped:
                continue
            if room_type_mapped and item.get('roomType') != room_type_mapped:
                continue
            filtered_items.append(item)

        if not filtered_items:
            logging.warning("No matching furniture items found after filtering.")
            return []

        return filtered_items

    except Exception as e:
        logging.error(f"Error querying furniture inventory: {e}")
        return []

    
import re

def get_gpt_recommendations(furniture_list, user_request):
    # Prepare the prompt for GPT
    prompt = f"""
Given the following list of furniture items:

{json.dumps(furniture_list, indent=2)}

User request:
- Budget: {user_request.get('budget')}
- Aesthetic: {user_request.get('aesthetic')}
- Desired Brands: {', '.join(user_request.get('furniture_brands', []))}
- Furniture Types: {', '.join(user_request.get('furniture_types', []))}
- Room Type: {user_request.get('room_type')}

Please recommend a list of furniture items that:
- Total price is below the budget but close to it.
- Matches the user's aesthetic preferences.
- Considers the desired brands, furniture types, and room type.

Provide the list in JSON format, including brand, name, price, roomType, and type for each item.
"""

    logging.debug(f"GPT Prompt: {prompt}")

    # Call the GPT API
    api_url = 'https://go.apis.huit.harvard.edu/ais-openai-direct/v1/chat/completions'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer xxx'  # Replace with your actual API key
    }
    payload = {
        'model': 'gpt-4o',
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 1000,
        'temperature': 0.7
    }

    try:
        response = requests.post(api_url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        gpt_response = response.json()
        logging.debug(f"GPT Response: {gpt_response}")

        # Extract the content
        assistant_message = gpt_response['choices'][0]['message']['content']

        # Extract JSON using regex to locate the block between ```json and ```
        json_match = re.search(r'```json\n(.*?)\n```', assistant_message, re.DOTALL)
        if json_match:
            json_content = json_match.group(1)
            try:
                recommended_items = json.loads(json_content)
                logging.debug(f"Recommended Items: {recommended_items}")
                return recommended_items
            except json.JSONDecodeError as e:
                logging.error(f"Failed to parse GPT response content as JSON: {e}")
                return None
        else:
            logging.error("No JSON block found in GPT response.")
            return None
    except requests.exceptions.RequestException as e:
        logging.error(f"Error calling GPT API: {e}")
        return None


if __name__ == '__main__':
    app.run(debug=True)





