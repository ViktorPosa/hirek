"""
LEÍRÁS:
Gemini Chat API kliens (nem hivatalos).
Alternatív interfész a Gemini webes chat felületéhez, sütik használatával.
Főként fallback megoldásként szolgál, ha a hivatalos API nem elérhető vagy korlátozott.

BEMENET:
- Promptok
- Sütik (Input/gemini_cookies.json)

KIMENET:
- (válasz_szöveg, sikeresség, hibaüzenet)
"""


import os
import time
import json
from gemini_chat_api import Chatbot, Model

# Statistics
_success_count = 0
_failure_count = 0

def get_stats():
    return {"success": _success_count, "failure": _failure_count}

def call_gemini_chat(prompt, model=None, timeout=120):
    """
    Call Gemini using the Gemini-Chat-API (synchronous wrapper).
    
    Args:
        prompt: User prompt
        model: Model enum or None
        timeout: Timeout in seconds
        
    Returns:
        (content, success, error_message)
    """
    global _success_count, _failure_count
    
    cookie_path = os.path.join(os.path.dirname(__file__), 'Input', 'gemini_cookies.json')
    
    if not os.path.exists(cookie_path):
        return None, False, f"Cookie file not found at {cookie_path}"
        
    try:
        # Use specific model if provided, else default
        # The library uses Model enum.
        # We can pass Model.G_2_5_PRO or similar if needed.
        # For now let's let it default or use what's passed.
        
        # Initialize Chatbot
        # Note: Chatbot initialization can be slow as it verifies cookies
        chatbot = Chatbot(
            cookie_path=cookie_path,
            timeout=timeout,
            model=model if model else Model.UNSPECIFIED
        )
        
        print(f"    🔍 Sending request to Gemini Chat API...")
        
        response = chatbot.ask(prompt)
        
        if response and not response.get("error"):
            content = response.get("content")
            if content:
                print(f"    ✅ Gemini response received ({len(content)} chars)")
                _success_count += 1
                return content, True, None
            else:
                return None, False, "Empty content in response"
        else:
            error_msg = response.get("content") if response else "Unknown error"
            print(f"    ❌ Gemini error: {error_msg}")
            _failure_count += 1
            return None, False, error_msg

    except Exception as e:
        error_str = str(e)
        print(f"    ❌ Exception calling Gemini Chat API: {error_str}")
        _failure_count += 1
        return None, False, error_str

def call_with_fallback(prompt, use_pro=True, api_key=None):
    """
    Call Gemini with fallback strategy. 
    Matches interface of other clients.
    
    Args:
        prompt: The prompt to send
        use_pro: Whether to prefer Pro/Thinking models (if applicable)
        api_key: Ignored, used for compatibility signature
        
    Returns:
        (content, model_used) tuple
    """
    # 1. Try with Gemini 2.5 Pro (if use_pro) or default
    first_model = Model.G_2_5_PRO if use_pro else Model.UNSPECIFIED
    model_name = "gemini-2.5-pro" if use_pro else "gemini-default"
    
    content, success, error = call_gemini_chat(prompt, model=first_model)
    
    if success and content:
        return content, f"gemini-chat-{model_name}"
        
    # 2. Fallback: If Pro failed, try Flash or Default
    if use_pro:
        print("    ⚠️ Fallback to Gemini 3.0 Flash...")
        content, success, error = call_gemini_chat(prompt, model=Model.GEMINI_3_0_FLASH)
        if success and content:
            return content, "gemini-chat-flash-latest"
            
    return None, None

if __name__ == "__main__":
    print("Testing Gemini Chat API Integration...")
    prompt = "Hello, reply with 'Gemini is working' if you can read this."
    content, model = call_with_fallback(prompt)
    if content:
        print(f"\nSUCCESS! Model: {model}\nResponse: {content}")
    else:
        print("\nFAILURE!")
