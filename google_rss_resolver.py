#!/usr/bin/env python3
"""
Google News RSS Resolver
========================

Ez a script képes feloldani a Google News RSS feedekben található 'read' linkeket 
az eredeti forrás URL-re.

Követelmények:
    pip install requests selectolax

Használat:
    from google_rss_resolver import resolve_google_news_url, resolve_google_news_urls_batch

    # Egy URL
    real_url = resolve_google_news_url("https://news.google.com/rss/articles/...")

    # Több URL (gyorsabb, párhuzamos)
    real_urls = resolve_google_news_urls_batch(url_list)
"""

import json
import time
import requests
from urllib.parse import quote, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from selectolax.parser import HTMLParser


# =============================================================================
# INLINED GOOGLENEWSDECODER LOGIC (new_decoderv1.py)
# =============================================================================

def get_base64_str(source_url):
    """Kinyeri a base64 stringet a Google News URL-ből."""
    try:
        url = urlparse(source_url)
        path = url.path.split("/")
        if (
            url.hostname == "news.google.com"
            and len(path) > 1
            and path[-2] in ["articles", "read"]
        ):
            return {"status": True, "base64_str": path[-1]}
        return {"status": False, "message": "Invalid Google News URL format."}
    except Exception as e:
        return {"status": False, "message": f"Error in get_base64_str: {str(e)}"}


def get_decoding_params(base64_str, request_timeout=10):
    """Megszerzi a dekódoláshoz szükséges aláírást és időbélyeget."""
    # Először próbáljuk az articles formátumot
    try:
        url = f"https://news.google.com/articles/{base64_str}"
        response = requests.get(url, timeout=request_timeout)
        response.raise_for_status()

        parser = HTMLParser(response.text)
        data_element = parser.css_first("c-wiz > div[jscontroller]")
        if data_element is None:
            return {
                "status": False,
                "message": "Failed to fetch data attributes from Google News with the articles URL.",
            }

        return {
            "status": True,
            "signature": data_element.attributes.get("data-n-a-sg"),
            "timestamp": data_element.attributes.get("data-n-a-ts"),
            "base64_str": base64_str,
        }

    except requests.exceptions.RequestException as req_err:
        # Hiba esetén fallback az RSS formátumra
        try:
            url = f"https://news.google.com/rss/articles/{base64_str}"
            response = requests.get(url, timeout=request_timeout)
            response.raise_for_status()

            parser = HTMLParser(response.text)
            data_element = parser.css_first("c-wiz > div[jscontroller]")
            if data_element is None:
                return {
                    "status": False,
                    "message": "Failed to fetch data attributes from Google News with the RSS URL.",
                }

            return {
                "status": True,
                "signature": data_element.attributes.get("data-n-a-sg"),
                "timestamp": data_element.attributes.get("data-n-a-ts"),
                "base64_str": base64_str,
            }

        except requests.exceptions.RequestException as rss_req_err:
            return {
                "status": False,
                "message": f"Request error in get_decoding_params with RSS URL: {str(rss_req_err)}",
            }
    except Exception as e:
        return {
            "status": False,
            "message": f"Unexpected error in get_decoding_params: {str(e)}",
        }


def decode_url(signature, timestamp, base64_str, request_timeout=10):
    """Dekódolja a base64 stringet az aláírás és időbélyeg segítségével."""
    try:
        url = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
        payload = [
            "Fbv4je",
            f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{base64_str}",{timestamp},"{signature}"]',
        ]
        headers = {
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        }

        response = requests.post(
            url, headers=headers, data=f"f.req={quote(json.dumps([[payload]]))}",
            timeout=request_timeout
        )
        response.raise_for_status()

        parsed_data = json.loads(response.text.split("\n\n")[1])[:-2]
        decoded_url = json.loads(parsed_data[0][2])[1]

        return {"status": True, "decoded_url": decoded_url}
    except requests.exceptions.RequestException as req_err:
        return {
            "status": False,
            "message": f"Request error in decode_url: {str(req_err)}",
        }
    except (json.JSONDecodeError, IndexError, TypeError) as parse_err:
        return {
            "status": False,
            "message": f"Parsing error in decode_url: {str(parse_err)}",
        }
    except Exception as e:
        return {"status": False, "message": f"Error in decode_url: {str(e)}"}


def decode_google_news_url(source_url, interval=None, request_timeout=10):
    """
    Ez a fő függvény a dekódoláshoz.
    Feloldja a Google News URL-t az eredeti forrásra.
    
    Args:
        source_url: A Google News URL
        interval: Várakozás a kérések között (másodperc)
        request_timeout: HTTP kérések timeout-ja (másodperc)
    """
    try:
        base64_response = get_base64_str(source_url)
        if not base64_response["status"]:
            return base64_response

        decoding_params_response = get_decoding_params(base64_response["base64_str"], request_timeout)
        if not decoding_params_response["status"]:
            return decoding_params_response

        decoded_url_response = decode_url(
            decoding_params_response["signature"],
            decoding_params_response["timestamp"],
            decoding_params_response["base64_str"],
            request_timeout
        )
        if interval:
            time.sleep(interval)

        return decoded_url_response
    except Exception as e:
        return {
            "status": False,
            "message": f"Error in decode_google_news_url: {str(e)}",
        }


# =============================================================================
# WRAPPER & BATCH PROCESSING FUNCTIONS
# =============================================================================

def resolve_google_news_url(url: str, request_timeout: int = 10) -> str:
    """
    Feloldja egyetlen Google News redirect URL-t a valódi cél URL-re.
    Visszaadja a feloldott URL-t, vagy az eredetit hiba esetén.
    
    Args:
        url: A Google News URL
        request_timeout: HTTP kérések timeout-ja (másodperc)
    """
    # Csak Google News linkeket dolgozunk fel
    if not url.startswith('https://news.google.com/rss/articles/') and not url.startswith('https://news.google.com/articles/'):
        return url
    
    try:
        # A decode_google_news_url a fenti inlined függvény
        result = decode_google_news_url(url, interval=0.05, request_timeout=request_timeout)
        
        if result and result.get('status') and result.get('decoded_url'):
            return result['decoded_url']
            
    except requests.exceptions.Timeout:
        pass  # Timeout - eredeti URL marad
    except Exception:
        pass
    
    return url


def resolve_google_news_urls_batch(
    urls: list, 
    max_workers: int = 20, 
    per_url_timeout: int = 15, 
    batch_timeout: int = 180,
    show_progress: bool = True
) -> list:
    """
    Párhuzamosan feloldja a Google News URL-eket timeout védelemmel.
    
    Args:
        urls: URL-ek listája
        max_workers: Párhuzamos szálak száma (default: 5)
        per_url_timeout: Egy URL feldolgozásának max ideje (default: 15 sec)
        batch_timeout: Teljes batch feldolgozásának max ideje (default: 180 sec = 3 perc)
        show_progress: Progress kiírása
    
    Returns:
        Feloldott URL-ek listája (sikertelen esetben az eredeti URL marad)
    """
    if not urls:
        return []
    
    # Szűrjük ki a Google News URL-eket
    google_urls = []
    for i, u in enumerate(urls):
        if u.startswith('https://news.google.com/rss/articles/') or u.startswith('https://news.google.com/articles/'):
            google_urls.append((i, u))
    
    if not google_urls:
        return urls  # Nincs Google News URL, gyors visszatérés
    
    results = list(urls)  # Másolat az eredményhez
    total = len(google_urls)
    resolved = 0
    failed = 0
    timed_out = 0
    
    if show_progress:
        print(f"   Google News URL-ek feloldasa ({total} db, max {batch_timeout}s)...")
    
    batch_start_time = time.time()
    
    def resolve_single(item):
        idx, url = item
        try:
            resolved_url = resolve_google_news_url(url, request_timeout=per_url_timeout)
            success = resolved_url != url and 'google.com' not in resolved_url
            return (idx, resolved_url, success, None)
        except requests.exceptions.Timeout as e:
            return (idx, url, False, "timeout")
        except Exception as e:
            return (idx, url, False, str(e))
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {executor.submit(resolve_single, item): item for item in google_urls}
        pending_futures = set(future_to_idx.keys())
        
        try:
            while pending_futures:
                # Ellenőrizzük a globális batch timeout-ot
                elapsed = time.time() - batch_start_time
                remaining_batch_time = batch_timeout - elapsed
                
                if remaining_batch_time <= 0:
                    # Batch timeout - megszakítjuk a maradék feladatokat
                    if show_progress:
                        print(f"\n   ⚠️ Batch timeout ({batch_timeout}s) elérve! Megszakítás...")
                    
                    # Cancelláljuk a maradék future-öket
                    for future in pending_futures:
                        future.cancel()
                    
                    timed_out = len(pending_futures)
                    break
                
                # Várunk a következő befejezésre (max a maradék batch idővel)
                import concurrent.futures
                done, pending_futures = concurrent.futures.wait(
                    pending_futures, 
                    timeout=min(remaining_batch_time, per_url_timeout + 5),
                    return_when=concurrent.futures.FIRST_COMPLETED
                )
                
                for future in done:
                    idx, url_tuple = future_to_idx[future]
                    try:
                        result_idx, resolved_url, success, error = future.result(timeout=1)
                        results[result_idx] = resolved_url
                        
                        if success:
                            resolved += 1
                        else:
                            failed += 1
                            if show_progress and error:
                                if error == "timeout":
                                    print(f"      Timeout: URL #{idx}")
                                else:
                                    # Rövidített hiba kiírás
                                    short_error = error[:30] + "..." if len(error) > 30 else error
                                    print(f"      Hiba/Skip: {short_error}")
                        
                        if show_progress and (resolved + failed) % 5 == 0:
                            elapsed = time.time() - batch_start_time
                            print(f"      {resolved + failed}/{total} feldolgozva ({resolved} ok, {failed} fail) [{elapsed:.1f}s]")
                            
                    except concurrent.futures.TimeoutError:
                        failed += 1
                        if show_progress:
                            print(f"      ⏱️ Future timeout: URL #{idx}")
                    except Exception as e:
                        failed += 1
                        if show_progress:
                            print(f"      Hiba: {e}")
                            
        except KeyboardInterrupt:
            print("\n      Megszakitas erzekelve! Leallitas...")
            executor.shutdown(wait=False, cancel_futures=True)
            raise
    
    if show_progress:
        elapsed = time.time() - batch_start_time
        if timed_out > 0:
            print(f"   Kesz: {resolved}/{total} sikeres, {failed} sikertelen, {timed_out} timeout miatt kihagyva [{elapsed:.1f}s]")
        else:
            print(f"   Kesz: {resolved}/{total} sikeres feloldas ({failed} sikertelen) [{elapsed:.1f}s]")
    
    return results


if __name__ == "__main__":
    print("Tesztelés...")
    test_url = "https://news.google.com/rss/articles/CBMiVkFVX3lxTE4zaGU2bTY2ZGkzdTRkSkJ0cFpsTGlDUjkxU2FBRURaTWU0c3QzVWZ1MHZZNkZ5Vzk1ZVBnTDFHY2R6ZmdCUkpUTUJsS1pqQTlCRzlzbHV3?oc=5"
    print(f"Eredeti: {test_url}")
    
    resolved = resolve_google_news_url(test_url)
    print(f"Feloldott: {resolved}")
    
    if resolved != test_url:
        print("\n✅ SIKERES feloldás!")
    else:
        print("\n❌ SIKERTELEN feloldás (vagy nem Google URL, vagy hiba)")
