import os
import requests
import json
import sys

# Configuration
INPUT_FILE = os.path.join('Input', 'input.txt')
API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL_NAME = "mimo-v2-flash"

def load_api_key():
    """Loads API Key from input.txt."""
    api_key = ""
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith("API_KEY="):
                    api_key = line.replace("API_KEY=", "").strip()
                    break
    except FileNotFoundError:
        print(f"Error: {INPUT_FILE} not found.")
        return None
    
    if not api_key:
        print("Error: API_KEY not found in input.txt")
        return None
        
    return api_key

def chat_with_mimo(api_key, user_input):
    """Sends user input to Mimo API and returns response."""
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "user", "content": user_input}
        ],
        "temperature": 0.7 
    }

    try:
        response = requests.post(API_URL, headers=headers, json=data, timeout=300)

        response.raise_for_status()
        
        result_json = response.json()
        content = result_json['choices'][0]['message']['content']
        return content

    except Exception as e:
        return f"Error: {e}"

def main():
    print("Loading API Key...")
    api_key = load_api_key()
    if not api_key:
        return

    print(f"\n--- Mimo Chat ({MODEL_NAME}) ---")
    print("Type 'exit' or 'quit' to stop.\n")

    while True:
        try:
            user_input = input("You: ")
            if user_input.lower() in ['exit', 'quit']:
                print("Goodbye!")
                break
            
            if not user_input.strip():
                continue

            print("Mimo: ...")
            response = chat_with_mimo(api_key, user_input)
            print(f"Mimo: {response}\n")

        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
