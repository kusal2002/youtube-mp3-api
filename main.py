from fastapi import FastAPI, Query, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse, JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import yt_dlp
import subprocess
import os
import re
import jwt
import requests
import json
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Templates for web interface
templates = Jinja2Templates(directory="templates")

# Supabase config (now loaded from .env file or environment variables)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("🚨 Environment Variables Missing!")
    print("Required variables: SUPABASE_URL, SUPABASE_KEY")
    print("In Koyeb: Create secrets and reference them in environment variables")
    print("Example: SUPABASE_URL = @SUPABASE_URL (references secret)")
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set as environment variables or secrets")

# JWT Secret for user authentication (loaded from environment)
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    print("🚨 JWT_SECRET environment variable missing!")
    print("Create a secret in Koyeb and reference it: JWT_SECRET = @JWT_SECRET")
    raise ValueError("JWT_SECRET must be set as environment variable or secret")

# Initialize Supabase client with workaround for version compatibility
try:
    from supabase import create_client, Client
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except TypeError as e:
    if "proxy" in str(e) or "unexpected keyword argument" in str(e):
        # Workaround for version compatibility issues
        print("Using compatibility workaround for Supabase client")
        import httpx
        from supabase.lib.client_options import ClientOptions
        
        # Create a custom httpx client
        custom_http_client = httpx.Client()
        
        # Import and initialize SyncClient directly
        from supabase._sync.client import SyncClient
        options = ClientOptions()
        supabase = SyncClient(SUPABASE_URL, SUPABASE_KEY, options)
    else:
        raise

security = HTTPBearer()

# User authentication helper
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=["HS256"])
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# FastAPI app with custom description for /docs
app = FastAPI(
    title="Music Stream API",
    description="""
This API allows you to:

- Search for music on YouTube and YouTube Music and stream the first result as MP3 (`/search`)
- Fetch YouTube Music playlist metadata and tracklist (`/playlist_info`)
- Save playlist and track details to Supabase (`/save_playlist`)
- Stream any YouTube video as MP3 (`/stream_mp3`)
- Search for multiple tracks and pick one to stream (`/search_results`)
- User authentication and personal playlists like Spotify (`/register`, `/login`, `/my_playlists`)

**Note:** Only metadata is saved to Supabase, not audio files.
""",
    version="1.0.0"
)

@app.get("/", response_class=HTMLResponse)
async def homepage(request: Request):
    """Serve the homepage with API information"""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "version": "2.0.0",
        "endpoints_count": "15"
    })

# Standard yt-dlp configuration to avoid bot detection
def get_ydl_opts(search=False):
    """Get standard yt-dlp options with advanced bot detection avoidance"""
    opts = {
        'quiet': True,
        'no_warnings': True,
        # Advanced headers to mimic real browser
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'http_headers': {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0',
        },
        # Extractor arguments to bypass restrictions
        'extractor_args': {
            'youtube': {
                'skip': ['dash'],  # Only skip dash, keep hls
                'player_skip': ['js'],
                'player_client': ['android', 'web'],  # Use multiple clients
                'include_dash_manifest': False,
            }
        },
        # Advanced retry and rate limiting
        'retries': 5,
        'fragment_retries': 5,
        'socket_timeout': 30,
        'sleep_interval': 2,
        'max_sleep_interval': 10,
        'sleep_interval_subtitles': 1,
        # Use cookies if available
        'cookiesfrombrowser': None,
        # Additional options to avoid detection
        'ignoreerrors': False,
        'no_check_certificate': True,
        'prefer_insecure': False,
    }
    
    if search:
        opts.update({
            'default_search': 'ytsearch',
            'extract_flat': False,
        })
    else:
        opts.update({
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=mp3]/bestaudio',
        })
    
    return opts

def extract_video_id(url):
    """Extract YouTube video ID from various URL formats"""
    import re
    patterns = [
        r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})',
        r'([a-zA-Z0-9_-]{11})'  # Just the ID itself
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

@app.get("/stream_safe", summary="Safe streaming with video ID extraction", tags=["Streaming"])
async def stream_safe(url: str = Query(..., description="YouTube video URL or video ID")):
    """
    Ultra-safe streaming endpoint that uses video ID extraction
    and multiple bypass techniques.
    """
    
    # Extract video ID and create clean URL
    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")
    
    clean_url = f"https://www.youtube.com/watch?v={video_id}"
    
    # Try the most reliable method first
    try:
        # Use simple approach that often bypasses detection
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'bestaudio[ext=m4a]',
            'user_agent': 'com.google.android.youtube/17.36.4',
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                }
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            if info and info.get('url'):
                audio_url = info['url']
                
                # Simple streaming without complex FFmpeg
                command = [
                    'ffmpeg', '-hide_banner', '-loglevel', 'quiet',
                    '-i', audio_url,
                    '-f', 'mp3', '-ab', '128k',
                    '-vn', 'pipe:1'
                ]
                
                proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                
                def generate():
                    try:
                        while True:
                            chunk = proc.stdout.read(4096)
                            if not chunk:
                                break
                            yield chunk
                    finally:
                        if proc.poll() is None:
                            proc.terminate()
                
                return StreamingResponse(
                    generate(), 
                    media_type="audio/mpeg",
                    headers={'Content-Disposition': f'inline; filename="{video_id}.mp3"'}
                )
                
    except Exception as e:
        # Fallback to robust method
        return await stream_robust(clean_url)

@app.get("/stream_ultimate", summary="Ultimate bypass with all methods", tags=["Streaming"])
async def stream_ultimate(url: str = Query(..., description="YouTube video URL or video ID")):
    """
    Ultimate streaming endpoint that tries EVERYTHING to bypass restrictions.
    Uses multiple libraries, APIs, and techniques.
    """
    
    # Extract video ID
    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")
    
    # Method 1: Direct API approach (often works when yt-dlp fails)
    try:
        # Use YouTube's internal API with mobile client
        api_url = "https://www.youtube.com/youtubei/v1/player"
        
        # Multiple client configurations to try
        clients = [
            {
                "clientName": "ANDROID",
                "clientVersion": "17.36.4",
                "userAgent": "com.google.android.youtube/17.36.4 (Linux; U; Android 11)"
            },
            {
                "clientName": "IOS", 
                "clientVersion": "17.33.2",
                "userAgent": "com.google.ios.youtube/17.33.2 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)"
            },
            {
                "clientName": "MWEB",
                "clientVersion": "2.20221123.09.00"
            }
        ]
        
        for client in clients:
            try:
                payload = {
                    "videoId": video_id,
                    "context": {
                        "client": client
                    }
                }
                
                headers = {
                    'User-Agent': client.get('userAgent', 'Mozilla/5.0 (Linux; Android 11)'),
                    'Content-Type': 'application/json',
                    'X-YouTube-Client-Name': '3' if client['clientName'] == 'ANDROID' else '1',
                    'X-YouTube-Client-Version': client['clientVersion']
                }
                
                response = requests.post(api_url, json=payload, headers=headers, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Extract audio URL from response
                    streaming_data = data.get('streamingData', {})
                    
                    # Try adaptive formats first
                    adaptive_formats = streaming_data.get('adaptiveFormats', [])
                    for fmt in adaptive_formats:
                        mime_type = fmt.get('mimeType', '')
                        if 'audio' in mime_type and fmt.get('url'):
                            audio_url = fmt['url']
                            
                            # Stream directly without yt-dlp
                            return await stream_direct_url(audio_url, video_id)
                    
                    # Try regular formats
                    formats = streaming_data.get('formats', [])
                    for fmt in formats:
                        if fmt.get('url'):
                            audio_url = fmt['url']
                            return await stream_direct_url(audio_url, video_id)
                            
            except Exception as e:
                continue  # Try next client
                
    except Exception as e:
        pass  # Continue to next method
    
    # Method 2: Embed page extraction
    try:
        embed_url = f"https://www.youtube.com/embed/{video_id}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.youtube.com/',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }
        
        response = requests.get(embed_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            # Look for player config
            patterns = [
                r'ytInitialPlayerResponse\s*=\s*({.+?});',
                r'"streamingData":({.+?})',
                r'var ytInitialPlayerResponse = ({.+?});'
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, response.text)
                for match in matches:
                    try:
                        if isinstance(match, str):
                            data = json.loads(match)
                            streaming_data = data.get('streamingData', {})
                            
                            # Extract audio URL
                            for fmt in streaming_data.get('adaptiveFormats', []):
                                if 'audio' in fmt.get('mimeType', '') and fmt.get('url'):
                                    return await stream_direct_url(fmt['url'], video_id)
                                    
                    except:
                        continue
                        
    except Exception as e:
        pass
    
    # Method 3: Fallback to enhanced yt-dlp with extreme settings
    try:
        # Most aggressive yt-dlp configuration
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'format': 'worst[ext=m4a]/worst[ext=mp3]/worst',  # Use worst quality for better success
            'user_agent': 'Mozilla/5.0 (Linux; Android 10; SM-G981B) AppleWebKit/537.36',
            'referer': 'https://m.youtube.com/',
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                    'skip': ['dash', 'hls'],
                    'player_skip': ['configs', 'webpage', 'js'],
                }
            },
            'http_headers': {
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'identity',  # Don't use gzip to avoid detection
                'Connection': 'keep-alive',
                'DNT': '1'
            },
            'socket_timeout': 60,
            'retries': 10,
            'fragment_retries': 10,
            'ignoreerrors': True,
            'no_check_certificate': True,
        }
        
        clean_url = f"https://www.youtube.com/watch?v={video_id}"
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
            if info and info.get('url'):
                return await stream_direct_url(info['url'], video_id)
                
    except Exception as e:
        pass
    
    # All methods failed
    raise HTTPException(
        status_code=503, 
        detail=f"All extraction methods failed for video {video_id}. This video may be geo-blocked, age-restricted, or unavailable."
    )

async def stream_direct_url(audio_url: str, video_id: str):
    """Stream audio directly from URL without yt-dlp processing"""
    try:
        # Method 1: Direct streaming (fastest)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36',
            'Range': 'bytes=0-'  # Support range requests
        }
        
        # Check if URL is accessible
        head_response = requests.head(audio_url, headers=headers, timeout=5)
        
        if head_response.status_code in [200, 206]:
            # Stream directly without FFmpeg conversion
            def generate_direct():
                try:
                    with requests.get(audio_url, headers=headers, stream=True, timeout=30) as r:
                        r.raise_for_status()
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                yield chunk
                except Exception as e:
                    print(f"Direct streaming error: {e}")
            
            # Detect content type
            content_type = head_response.headers.get('content-type', 'audio/mp4')
            if 'mp4' in content_type or 'm4a' in content_type:
                media_type = "audio/mp4"
                filename = f"{video_id}.m4a"
            else:
                media_type = "audio/mpeg"
                filename = f"{video_id}.mp3"
            
            return StreamingResponse(
                generate_direct(),
                media_type=media_type,
                headers={'Content-Disposition': f'inline; filename="{filename}"'}
            )
        
        # Method 2: FFmpeg conversion (if direct streaming fails)
        command = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-i', audio_url,
            '-f', 'mp3', '-ab', '96k',  # Lower bitrate for faster processing
            '-vn', '-y', 'pipe:1'
        ]
        
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        def generate_converted():
            try:
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()
        
        return StreamingResponse(
            generate_converted(),
            media_type="audio/mpeg",
            headers={'Content-Disposition': f'inline; filename="{video_id}.mp3"'}
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stream audio: {str(e)}")

@app.get("/test_extraction", summary="Test video extraction without streaming", tags=["Debug"])
async def test_extraction(url: str = Query(..., description="YouTube video URL or video ID")):
    """
    Test endpoint to debug video extraction without actually streaming.
    Returns extraction details and available formats.
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")
    
    results = {"video_id": video_id, "methods": []}
    
    # Test Method 1: YouTube API
    try:
        api_url = "https://www.youtube.com/youtubei/v1/player"
        payload = {
            "videoId": video_id,
            "context": {
                "client": {
                    "clientName": "ANDROID",
                    "clientVersion": "17.36.4"
                }
            }
        }
        
        response = requests.post(api_url, json=payload, timeout=10)
        if response.status_code == 200:
            data = response.json()
            streaming_data = data.get('streamingData', {})
            
            audio_formats = []
            for fmt in streaming_data.get('adaptiveFormats', []):
                if 'audio' in fmt.get('mimeType', ''):
                    audio_formats.append({
                        'itag': fmt.get('itag'),
                        'mimeType': fmt.get('mimeType'),
                        'bitrate': fmt.get('bitrate'),
                        'has_url': bool(fmt.get('url'))
                    })
            
            results["methods"].append({
                "name": "YouTube API",
                "status": "success",
                "audio_formats_found": len(audio_formats),
                "formats": audio_formats[:3]  # Show first 3
            })
        else:
            results["methods"].append({
                "name": "YouTube API", 
                "status": "failed",
                "error": f"HTTP {response.status_code}"
            })
            
    except Exception as e:
        results["methods"].append({
            "name": "YouTube API",
            "status": "failed", 
            "error": str(e)
        })
    
    # Test Method 2: yt-dlp
    try:
        ydl_opts = {
            'quiet': True,
            'format': 'bestaudio',
            'user_agent': 'com.google.android.youtube/17.36.4',
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                }
            }
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            
            results["methods"].append({
                "name": "yt-dlp",
                "status": "success",
                "title": info.get('title', 'Unknown'),
                "duration": info.get('duration'),
                "has_audio_url": bool(info.get('url'))
            })
            
    except Exception as e:
        results["methods"].append({
            "name": "yt-dlp",
            "status": "failed",
            "error": str(e)
        })
    
    return results

@app.get("/stream_proxy", summary="Stream via proxy services", tags=["Streaming"])
async def stream_proxy(url: str = Query(..., description="YouTube video URL or video ID")):
    """
    Stream using alternative proxy services when YouTube blocks direct access.
    Uses Invidious, Piped, and other YouTube proxy services.
    """
    
    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")
    
    # Alternative services that can bypass YouTube restrictions
    services_to_try = [
        # Service 1: Invidious instances
        {
            'name': 'Invidious',
            'instances': [
                'https://invidious.io',
                'https://yewtu.be', 
                'https://invidious.kavin.rocks',
                'https://vid.puffyan.us'
            ]
        },
        # Service 2: Piped instances  
        {
            'name': 'Piped',
            'instances': [
                'https://pipedapi.kavin.rocks',
                'https://api.piped.video'
            ]
        }
    ]
    
    for service in services_to_try:
        for instance in service['instances']:
            try:
                if service['name'] == 'Invidious':
                    # Try Invidious API
                    api_url = f"{instance}/api/v1/videos/{video_id}"
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                    
                    response = requests.get(api_url, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        # Get audio formats
                        adaptive_formats = data.get('adaptiveFormats', [])
                        for fmt in adaptive_formats:
                            if fmt.get('type', '').startswith('audio'):
                                audio_url = fmt.get('url')
                                if audio_url:
                                    return await stream_from_proxy_url(audio_url, video_id, f"Invidious-{instance}")
                
                elif service['name'] == 'Piped':
                    # Try Piped API
                    api_url = f"{instance}/streams/{video_id}"
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                    
                    response = requests.get(api_url, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        # Get audio streams
                        audio_streams = data.get('audioStreams', [])
                        if audio_streams:
                            audio_url = audio_streams[0].get('url')
                            if audio_url:
                                return await stream_from_proxy_url(audio_url, video_id, f"Piped-{instance}")
                                
            except Exception as e:
                # Try next instance
                continue
    
    # If proxy services fail, try direct extraction with different approach
    try:
        # Last resort: Use youtube-dl style extraction with different user agent
        headers = {
            'User-Agent': 'yt-dlp/2023.07.06',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Sec-Fetch-Mode': 'navigate'
        }
        
        # Try to get video page with different headers
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        response = requests.get(video_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            # Look for any streaming URLs in the page
            patterns = [
                r'"url":"([^"]*audioonly[^"]*)"',
                r'"url":"([^"]*audio[^"]*)"',
                r'{"url":"([^"]*)"[^}]*"mimeType":"audio',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, response.text)
                for match in matches:
                    # Decode URL
                    audio_url = match.replace('\\u0026', '&').replace('\/', '/')
                    if 'googlevideo.com' in audio_url:
                        return await stream_from_proxy_url(audio_url, video_id, "Direct-Extraction")
                        
    except Exception as e:
        pass
    
    # All methods failed
    raise HTTPException(
        status_code=503,
        detail=f"All proxy services failed for video {video_id}. Video may be geo-blocked, age-restricted, or removed."
    )

async def stream_from_proxy_url(audio_url: str, video_id: str, service_name: str):
    """Stream audio from proxy service URL"""
    try:
        # First check if URL is accessible
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.youtube.com/',
            'Range': 'bytes=0-1023'  # Test with small range first
        }
        
        test_response = requests.head(audio_url, headers=headers, timeout=5)
        
        if test_response.status_code in [200, 206, 416]:  # 416 = Range not satisfiable but file exists
            # Stream directly without conversion for speed
            def generate_proxy_stream():
                try:
                    stream_headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        'Referer': 'https://www.youtube.com/'
                    }
                    
                    with requests.get(audio_url, headers=stream_headers, stream=True, timeout=30) as r:
                        r.raise_for_status()
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                yield chunk
                                
                except Exception as e:
                    print(f"Proxy streaming error: {e}")
            
            # Detect format from URL or content type
            content_type = test_response.headers.get('content-type', '')
            if 'mp4' in content_type or 'm4a' in audio_url:
                media_type = "audio/mp4"
                filename = f"{video_id}_via_{service_name}.m4a"
            elif 'webm' in content_type or 'webm' in audio_url:
                media_type = "audio/webm"
                filename = f"{video_id}_via_{service_name}.webm"
            else:
                media_type = "audio/mpeg"
                filename = f"{video_id}_via_{service_name}.mp3"
            
            return StreamingResponse(
                generate_proxy_stream(),
                media_type=media_type,
                headers={
                    'Content-Disposition': f'inline; filename="{filename}"',
                    'X-Service-Used': service_name
                }
            )
        else:
            # URL not accessible, try with FFmpeg conversion
            command = [
                'ffmpeg', '-hide_banner', '-loglevel', 'error',
                '-user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                '-referer', 'https://www.youtube.com/',
                '-i', audio_url,
                '-f', 'mp3', '-ab', '128k', '-vn', 'pipe:1'
            ]
            
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            def generate_converted_proxy():
                try:
                    while True:
                        chunk = proc.stdout.read(4096)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    if proc.poll() is None:
                        proc.terminate()
            
            return StreamingResponse(
                generate_converted_proxy(),
                media_type="audio/mpeg",
                headers={
                    'Content-Disposition': f'inline; filename="{video_id}_via_{service_name}.mp3"',
                    'X-Service-Used': service_name
                }
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stream from {service_name}: {str(e)}")

@app.get("/stream_fallback", summary="Ultimate fallback with all methods", tags=["Streaming"])
async def stream_fallback(url: str = Query(..., description="YouTube video URL or video ID")):
    """
    Ultimate fallback that tries every possible method in sequence:
    1. Direct YouTube API
    2. Proxy services (Invidious, Piped)
    3. Enhanced yt-dlp
    4. Direct page scraping
    """
    
    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")
    
    # Try each method in order of success probability
    methods = [
        ("Proxy Services", lambda: stream_proxy(url)),
        ("Ultimate Extraction", lambda: stream_ultimate(url)),
        ("Safe Method", lambda: stream_safe(url)),
        ("Robust Method", lambda: stream_robust(url))
    ]
    
    errors = []
    
    for method_name, method_func in methods:
        try:
            result = await method_func()
            # If we get here, the method succeeded
            return result
            
        except HTTPException as e:
            errors.append(f"{method_name}: {e.detail}")
            continue
        except Exception as e:
            errors.append(f"{method_name}: {str(e)}")
            continue
    
    # All methods failed, return comprehensive error
    raise HTTPException(
        status_code=503,
        detail={
            "message": f"All extraction methods failed for video {video_id}",
            "video_id": video_id,
            "methods_tried": len(methods),
            "errors": errors,
            "suggestions": [
                "Video may be geo-blocked or age-restricted",
                "Try a different video",
                "Check if video is still available on YouTube",
                "Some videos may require login to access"
            ]
        }
    )

@app.get("/debug_video", summary="Debug video availability", tags=["Debug"])
async def debug_video(url: str = Query(..., description="YouTube video URL or video ID")):
    """
    Comprehensive debug endpoint to test video availability across all methods.
    """
    
    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")
    
    debug_results = {
        "video_id": video_id,
        "video_url": f"https://www.youtube.com/watch?v={video_id}",
        "timestamp": datetime.now().isoformat(),
        "tests": []
    }
    
    # Test 1: Basic video accessibility
    try:
        response = requests.get(f"https://www.youtube.com/watch?v={video_id}", timeout=10)
        if response.status_code == 200:
            if "Video unavailable" in response.text:
                debug_results["tests"].append({
                    "method": "Basic Access",
                    "status": "FAILED",
                    "error": "Video unavailable"
                })
            elif "Private video" in response.text:
                debug_results["tests"].append({
                    "method": "Basic Access", 
                    "status": "FAILED",
                    "error": "Private video"
                })
            elif "age-restricted" in response.text.lower():
                debug_results["tests"].append({
                    "method": "Basic Access",
                    "status": "FAILED", 
                    "error": "Age-restricted"
                })
            else:
                debug_results["tests"].append({
                    "method": "Basic Access",
                    "status": "SUCCESS",
                    "note": "Video page accessible"
                })
        else:
            debug_results["tests"].append({
                "method": "Basic Access",
                "status": "FAILED",
                "error": f"HTTP {response.status_code}"
            })
    except Exception as e:
        debug_results["tests"].append({
            "method": "Basic Access",
            "status": "FAILED",
            "error": str(e)
        })
    
    # Test 2: YouTube oEmbed API
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        response = requests.get(oembed_url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            debug_results["tests"].append({
                "method": "oEmbed API",
                "status": "SUCCESS",
                "title": data.get("title", "Unknown"),
                "author": data.get("author_name", "Unknown")
            })
        else:
            debug_results["tests"].append({
                "method": "oEmbed API",
                "status": "FAILED",
                "error": f"HTTP {response.status_code}"
            })
    except Exception as e:
        debug_results["tests"].append({
            "method": "oEmbed API",
            "status": "FAILED", 
            "error": str(e)
        })
    
    # Test 3: Invidious instances
    invidious_instances = [
        "https://invidious.io",
        "https://yewtu.be",
        "https://invidious.kavin.rocks"
    ]
    
    for instance in invidious_instances:
        try:
            api_url = f"{instance}/api/v1/videos/{video_id}"
            response = requests.get(api_url, timeout=8)
            
            if response.status_code == 200:
                data = response.json()
                audio_formats = [f for f in data.get('adaptiveFormats', []) if 'audio' in f.get('type', '')]
                
                debug_results["tests"].append({
                    "method": f"Invidious ({instance})",
                    "status": "SUCCESS",
                    "title": data.get("title", "Unknown"),
                    "length": data.get("lengthSeconds", 0),
                    "audio_formats": len(audio_formats)
                })
                break  # Found working instance
            else:
                debug_results["tests"].append({
                    "method": f"Invidious ({instance})",
                    "status": "FAILED",
                    "error": f"HTTP {response.status_code}"
                })
        except Exception as e:
            debug_results["tests"].append({
                "method": f"Invidious ({instance})",
                "status": "FAILED",
                "error": str(e)
            })
    
    # Test 4: yt-dlp with different configs
    yt_dlp_configs = [
        {
            "name": "Android Client",
            "config": {
                'quiet': True,
                'extractor_args': {'youtube': {'player_client': ['android']}}
            }
        },
        {
            "name": "Basic Config",
            "config": {
                'quiet': True,
                'format': 'worst'
            }
        }
    ]
    
    for config in yt_dlp_configs:
        try:
            with yt_dlp.YoutubeDL(config["config"]) as ydl:
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                
                debug_results["tests"].append({
                    "method": f"yt-dlp ({config['name']})",
                    "status": "SUCCESS",
                    "title": info.get("title", "Unknown"),
                    "duration": info.get("duration", 0),
                    "has_audio_url": bool(info.get("url"))
                })
                break  # Found working config
                
        except Exception as e:
            debug_results["tests"].append({
                "method": f"yt-dlp ({config['name']})",
                "status": "FAILED",
                "error": str(e)[:200]  # Truncate long errors
            })
    
    # Summary
    successful_methods = [t for t in debug_results["tests"] if t["status"] == "SUCCESS"]
    debug_results["summary"] = {
        "total_methods_tested": len(debug_results["tests"]),
        "successful_methods": len(successful_methods),
        "success_rate": f"{len(successful_methods)}/{len(debug_results['tests'])}",
        "recommendation": "Video appears accessible" if successful_methods else "Video may be restricted or unavailable"
    }
    
    return debug_results

@app.get("/stream_direct", summary="Direct extraction bypass", tags=["Streaming"])
async def stream_direct(url: str = Query(..., description="YouTube video URL or video ID")):
    """
    Most direct approach - extracts streaming URL from YouTube page source
    without using yt-dlp or external services.
    """
    
    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")
    
    # Method 1: Direct page source extraction
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Use headers that mimic a real browser visit
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        }
        
        # Add a delay to avoid rate limiting
        time.sleep(1)
        
        response = requests.get(video_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            page_content = response.text
            
            # Extract player response from page
            patterns = [
                r'var ytInitialPlayerResponse = ({.+?});',
                r'"ytInitialPlayerResponse":({.+?}),"',
                r'ytInitialPlayerResponse":\s*({.+?})\s*[,}]',
                r'ytInitialPlayerResponse=({.+?});',
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, page_content)
                for match in matches:
                    try:
                        player_data = json.loads(match)
                        streaming_data = player_data.get('streamingData', {})
                        
                        # Look for audio formats
                        adaptive_formats = streaming_data.get('adaptiveFormats', [])
                        
                        # Find best audio format
                        audio_formats = []
                        for fmt in adaptive_formats:
                            mime_type = fmt.get('mimeType', '')
                            if 'audio' in mime_type and fmt.get('url'):
                                audio_formats.append({
                                    'url': fmt['url'],
                                    'bitrate': fmt.get('averageBitrate', fmt.get('bitrate', 0)),
                                    'mime': mime_type
                                })
                        
                        if audio_formats:
                            # Sort by bitrate and pick the best one
                            best_audio = max(audio_formats, key=lambda x: x['bitrate'])
                            audio_url = best_audio['url']
                            
                            # Stream directly
                            return await stream_audio_direct(audio_url, video_id, "Direct-Page-Extract")
                        
                        # Also try regular formats if no adaptive formats
                        formats = streaming_data.get('formats', [])
                        for fmt in formats:
                            if fmt.get('url'):
                                audio_url = fmt['url']
                                return await stream_audio_direct(audio_url, video_id, "Direct-Format")
                                
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        continue
                        
    except Exception as e:
        pass
    
    # Method 2: Try mobile page
    try:
        mobile_url = f"https://m.youtube.com/watch?v={video_id}"
        mobile_headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Mobile/15E148 Safari/604.1',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5'
        }
        
        response = requests.get(mobile_url, headers=mobile_headers, timeout=10)
        
        if response.status_code == 200:
            # Look for any streaming URLs in mobile page
            streaming_patterns = [
                r'"url":"([^"]*googlevideo\.com[^"]*)"',
                r'googlevideo\.com[^"\']*',
                r'"signatureCipher":"([^"]*)"'
            ]
            
            for pattern in streaming_patterns:
                matches = re.findall(pattern, response.text)
                for match in matches:
                    if 'googlevideo.com' in match:
                        # Clean up the URL
                        clean_url = match.replace('\\u0026', '&').replace('\/', '/')
                        if 'mime=audio' in clean_url or 'itag=140' in clean_url:
                            return await stream_audio_direct(clean_url, video_id, "Mobile-Extract")
                            
    except Exception as e:
        pass
    
    # Method 3: Try embed page
    try:
        embed_url = f"https://www.youtube.com/embed/{video_id}"
        embed_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.youtube.com/'
        }
        
        response = requests.get(embed_url, headers=embed_headers, timeout=10)
        
        if response.status_code == 200:
            # Look for player config in embed
            embed_patterns = [
                r'"url":"([^"]*googlevideo\.com[^"]*audio[^"]*)"',
                r'"url":"([^"]*itag=140[^"]*)"',  # itag 140 is audio
                r'googlevideo\.com[^"\']*audio[^"\']*'
            ]
            
            for pattern in embed_patterns:
                matches = re.findall(pattern, response.text)
                for match in matches:
                    if 'googlevideo.com' in match:
                        clean_url = match.replace('\\u0026', '&').replace('\/', '/')
                        return await stream_audio_direct(clean_url, video_id, "Embed-Extract")
                        
    except Exception as e:
        pass
    
    # Method 4: Try getting info via different endpoint
    try:
        # Try the get_video_info endpoint (sometimes still works)
        info_url = f"https://www.youtube.com/get_video_info?video_id={video_id}&el=embedded&ps=default&eurl="
        info_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(info_url, headers=info_headers, timeout=10)
        
        if response.status_code == 200:
            # Parse the response (it's URL encoded)
            from urllib.parse import parse_qs, unquote
            
            data = parse_qs(response.text)
            
            # Look for streaming data
            if 'player_response' in data:
                player_response = json.loads(data['player_response'][0])
                streaming_data = player_response.get('streamingData', {})
                
                adaptive_formats = streaming_data.get('adaptiveFormats', [])
                for fmt in adaptive_formats:
                    if 'audio' in fmt.get('mimeType', '') and fmt.get('url'):
                        return await stream_audio_direct(fmt['url'], video_id, "VideoInfo-API")
                        
    except Exception as e:
        pass
    
    # All direct methods failed
    raise HTTPException(
        status_code=503,
        detail=f"All direct extraction methods failed for video {video_id}. The video exists but audio streams are not accessible through direct methods."
    )

async def stream_audio_direct(audio_url: str, video_id: str, method: str):
    """Stream audio directly from extracted URL"""
    try:
        # Test if the URL is accessible
        test_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Range': 'bytes=0-1023'
        }
        
        test_response = requests.head(audio_url, headers=test_headers, timeout=5)
        
        if test_response.status_code in [200, 206, 416]:
            # URL is accessible, stream directly
            def generate_direct_audio():
                try:
                    stream_headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                    
                    with requests.get(audio_url, headers=stream_headers, stream=True, timeout=30) as r:
                        r.raise_for_status()
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                yield chunk
                                
                except Exception as e:
                    print(f"Direct audio streaming error: {e}")
            
            # Determine file type from URL or headers
            content_type = test_response.headers.get('content-type', '')
            if 'mp4' in content_type or 'm4a' in audio_url:
                media_type = "audio/mp4"
                filename = f"{video_id}_{method}.m4a"
            elif 'webm' in content_type or 'webm' in audio_url:
                media_type = "audio/webm" 
                filename = f"{video_id}_{method}.webm"
            else:
                media_type = "audio/mpeg"
                filename = f"{video_id}_{method}.mp3"
            
            return StreamingResponse(
                generate_direct_audio(),
                media_type=media_type,
                headers={
                    'Content-Disposition': f'inline; filename="{filename}"',
                    'X-Extraction-Method': method
                }
            )
        
        else:
            # URL not directly accessible, try with FFmpeg
            command = [
                'ffmpeg', '-hide_banner', '-loglevel', 'error',
                '-user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                '-i', audio_url,
                '-f', 'mp3', '-ab', '128k', '-vn', 'pipe:1'
            ]
            
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            def generate_converted_audio():
                try:
                    while True:
                        chunk = proc.stdout.read(4096)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    if proc.poll() is None:
                        proc.terminate()
            
            return StreamingResponse(
                generate_converted_audio(),
                media_type="audio/mpeg",
                headers={
                    'Content-Disposition': f'inline; filename="{video_id}_{method}.mp3"',
                    'X-Extraction-Method': f"{method}-Converted"
                }
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stream via {method}: {str(e)}")

@app.get("/simple_stream", summary="Simplest possible streaming", tags=["Streaming"])
async def simple_stream(url: str = Query(..., description="YouTube video URL or video ID")):
    """
    Absolutely simplest streaming approach - just tries to get any working audio URL
    """
    
    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL or video ID")
    
    # Super simple approach - just get the page and look for any audio URL
    try:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Minimal headers to avoid detection
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(video_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            # Look for any googlevideo.com URLs that contain audio
            audio_patterns = [
                r'(https://[^"\']*googlevideo\.com[^"\']*audio[^"\']*)',
                r'(https://[^"\']*googlevideo\.com[^"\']*itag=140[^"\']*)',
                r'(https://[^"\']*googlevideo\.com[^"\']*mime=audio[^"\']*)'
            ]
            
            for pattern in audio_patterns:
                matches = re.findall(pattern, response.text)
                for match in matches:
                    clean_url = match.replace('\\u0026', '&').replace('\/', '/')
                    
                    # Test if this URL works
                    try:
                        test_response = requests.head(clean_url, headers=headers, timeout=3)
                        if test_response.status_code in [200, 206]:
                            # This URL works, stream it
                            def simple_generate():
                                with requests.get(clean_url, headers=headers, stream=True) as r:
                                    for chunk in r.iter_content(chunk_size=8192):
                                        if chunk:
                                            yield chunk
                            
                            return StreamingResponse(
                                simple_generate(),
                                media_type="audio/mp4",
                                headers={'Content-Disposition': f'inline; filename="{video_id}_simple.m4a"'}
                            )
                    except:
                        continue
        
        # If direct extraction fails, return a helpful error
        raise HTTPException(
            status_code=404,
            detail=f"Could not find accessible audio stream for video {video_id}. Video may require sign-in or be geo-restricted."
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simple streaming failed: {str(e)}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "service": "Music Stream API"
    }

@app.get("/search_results", summary="Search for multiple tracks (songs only)", tags=["Search"])
async def search_results(
    query: str = Query(..., description="Song or artist to search"),
    limit: int = Query(5, description="Number of results to return"),
    min_duration: int = Query(60, description="Minimum duration in seconds (default 60)"),
    max_duration: int = Query(900, description="Maximum duration in seconds (default 900, 15min)")
):
    """
    Search YouTube for a track and return a list of the top N results (title, channel, duration, video_id, url), filtered to likely music tracks only.
    Only results with duration between min_duration and max_duration are returned.
    """
    # Add 'music' to query to bias results toward songs
    search_query = f"{query} music"
    ydl_opts = get_ydl_opts(search=True)
    ydl_opts.update({
        'noplaylist': True,
        'extract_flat': True,
    })
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch{limit*2}:{search_query}", download=False)
            entries = info.get('entries', [])
        results = []
        for entry in entries:
            duration = entry.get('duration')
            # Only keep results with a reasonable song duration
            if duration is not None and min_duration <= duration <= max_duration:
                results.append({
                    'title': entry.get('title'),
                    'channel': entry.get('uploader'),
                    'duration': duration,
                    'video_id': entry.get('id'),
                    'url': f"https://www.youtube.com/watch?v={entry.get('id')}" if entry.get('id') else None,
                    'thumbnail': entry.get('thumbnail') or f"https://img.youtube.com/vi/{entry.get('id')}/maxresdefault.jpg" if entry.get('id') else None
                })
            if len(results) >= limit:
                break
        return {"results": results}
        
    except Exception as e:
        error_msg = str(e)
        if "Sign in to confirm you're not a bot" in error_msg:
            raise HTTPException(
                status_code=429, 
                detail="YouTube bot detection triggered. Try again later or search for a different query."
            )
        else:
            raise HTTPException(status_code=500, detail=f"Search failed: {error_msg}")

@app.get("/playlist_info", summary="Get YouTube Music playlist info", tags=["YouTube Music"])
async def get_playlist_info(url: str = Query(..., description="YouTube Music playlist URL")):
    """
    Fetch playlist metadata and tracklist from a YouTube Music playlist URL.
    Returns playlist details and a list of tracks.
    """
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'force_generic_extractor': False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    playlist = {
        'id': info.get('id'),
        'title': info.get('title'),
        'uploader': info.get('uploader'),
        'webpage_url': info.get('webpage_url'),
        'track_count': len(info.get('entries', [])),
        'tracks': [
            {
                'id': entry.get('id'),
                'title': entry.get('title'),
                'url': entry.get('url'),
                'duration': entry.get('duration'),
                'thumbnail': entry.get('thumbnail') or f"https://img.youtube.com/vi/{entry.get('id')}/maxresdefault.jpg" if entry.get('id') else None
            } for entry in info.get('entries', [])
        ]
    }
    return JSONResponse(content=playlist)

@app.post("/save_playlist", summary="Save playlist and tracks to Supabase", tags=["Supabase"])
async def save_playlist_to_supabase(url: str = Query(..., description="YouTube Music playlist URL")):
    """
    Fetch playlist and tracks from YouTube Music, then save metadata to Supabase tables `playlists` and `tracks`.
    Only metadata is saved, not audio files.
    """
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'no_warnings': True,
        'force_generic_extractor': False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    playlist_data = {
        'id': info.get('id'),
        'title': info.get('title'),
        'uploader': info.get('uploader'),
        'webpage_url': info.get('webpage_url'),
        'track_count': len(info.get('entries', [])),
    }
    # Save playlist
    supabase.table("playlists").upsert(playlist_data).execute()
    # Save tracks
    for entry in info.get('entries', []):
        track_data = {
            'id': entry.get('id'),
            'playlist_id': info.get('id'),
            'title': entry.get('title'),
            'url': entry.get('url'),
            'duration': entry.get('duration'),
        }
        supabase.table("tracks").upsert(track_data).execute()
    return {"status": "success", "playlist_id": info.get('id')}

@app.get("/stream_mp3", summary="Stream YouTube video as MP3", tags=["Streaming"])
async def stream_mp3(url: str = Query(..., description="YouTube video URL")):
    """
    Stream the audio of a YouTube video as MP3 using yt-dlp and FFmpeg.
    Uses multiple fallback methods to avoid bot detection.
    """
    
    # Try multiple extraction methods
    extraction_methods = [
        # Method 1: Standard with Android client
        {
            **get_ydl_opts(search=False),
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                    'skip': ['dash'],
                }
            }
        },
        # Method 2: Web client with different format
        {
            **get_ydl_opts(search=False),
            'format': 'bestaudio[ext=m4a]',
            'extractor_args': {
                'youtube': {
                    'player_client': ['web'],
                    'skip': ['dash'],
                }
            }
        },
        # Method 3: iOS client
        {
            **get_ydl_opts(search=False),
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios'],
                    'skip': ['dash', 'hls'],
                }
            }
        },
        # Method 4: Basic extraction
        {
            'quiet': True,
            'format': 'worst[ext=m4a]/worst',
            'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15',
        }
    ]
    
    audio_url = None
    extraction_error = None
    
    # Try each method until one works
    for i, ydl_opts in enumerate(extraction_methods):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    continue
                    
                # Get the best audio URL
                audio_url = info.get('url')
                if not audio_url:
                    # Try to get from formats
                    formats = info.get('formats', [])
                    audio_formats = [f for f in formats if f.get('acodec') != 'none']
                    if audio_formats:
                        # Sort by quality and pick the best
                        audio_formats.sort(key=lambda x: x.get('abr', 0), reverse=True)
                        audio_url = audio_formats[0]['url']
                
                if audio_url:
                    break  # Success! Exit the loop
                    
        except Exception as e:
            extraction_error = str(e)
            if i < len(extraction_methods) - 1:
                # Not the last method, try next one
                continue
            else:
                # Last method failed, handle error
                break
    
    # If all methods failed, return appropriate error
    if not audio_url:
        if extraction_error:
            if "Sign in to confirm you're not a bot" in extraction_error:
                raise HTTPException(
                    status_code=429, 
                    detail="All extraction methods failed due to bot detection. Video may be restricted."
                )
            elif "Video unavailable" in extraction_error:
                raise HTTPException(status_code=404, detail="Video is unavailable or private")
            elif "Private video" in extraction_error:
                raise HTTPException(status_code=403, detail="Cannot access private videos")
            else:
                raise HTTPException(status_code=500, detail=f"Failed to extract audio: {extraction_error}")
        else:
            raise HTTPException(status_code=404, detail="No audio stream found")

    try:
        command = [
            'ffmpeg',
            '-i', audio_url,
            '-f', 'mp3',
            '-vn',
            '-ab', '128k',
            '-ar', '44100',
            'pipe:1'
        ]
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**8)

        def generate():
            try:
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    yield chunk
            finally:
                proc.stdout.close()
                proc.terminate()
                proc.wait()

        headers = {
            'Content-Disposition': 'inline; filename="stream.mp3"',
        }
        return StreamingResponse(generate(), media_type="audio/mpeg", headers=headers)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process audio: {str(e)}")

@app.get("/stream_robust", summary="Robust streaming with multiple fallbacks", tags=["Streaming"])
async def stream_robust(url: str = Query(..., description="YouTube video URL")):
    """
    More robust streaming endpoint that tries multiple extraction methods
    and different audio qualities to bypass restrictions.
    """
    
    # Multiple strategies with different priorities
    strategies = [
        # Strategy 1: Mobile client (often works better)
        {
            'name': 'Mobile Client',
            'opts': {
                'quiet': True,
                'format': 'bestaudio[ext=m4a]',
                'user_agent': 'com.google.android.youtube/17.36.4 (Linux; U; Android 12; GB) gzip',
                'extractor_args': {
                    'youtube': {
                        'player_client': ['android'],
                        'skip': ['dash'],
                    }
                }
            }
        },
        # Strategy 2: Basic web extraction
        {
            'name': 'Basic Web',
            'opts': {
                'quiet': True,
                'format': 'worst[ext=m4a]/worst',
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
        },
        # Strategy 3: Alternative extraction
        {
            'name': 'Alternative',
            'opts': {
                'quiet': True,
                'format': 'bestaudio[filesize<50M]',
                'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) AppleWebKit/605.1.15',
                'http_headers': {
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                }
            }
        }
    ]
    
    for strategy in strategies:
        try:
            with yt_dlp.YoutubeDL(strategy['opts']) as ydl:
                info = ydl.extract_info(url, download=False)
                if info and info.get('url'):
                    audio_url = info['url']
                    
                    # Stream with simple FFmpeg conversion
                    command = [
                        'ffmpeg', '-hide_banner', '-loglevel', 'error',
                        '-i', audio_url,
                        '-f', 'mp3', '-ab', '128k', '-ar', '44100',
                        '-vn', 'pipe:1'
                    ]
                    
                    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    
                    def generate():
                        try:
                            while True:
                                chunk = proc.stdout.read(8192)
                                if not chunk:
                                    break
                                yield chunk
                        finally:
                            proc.terminate()
                    
                    return StreamingResponse(
                        generate(), 
                        media_type="audio/mpeg",
                        headers={'Content-Disposition': f'inline; filename="audio.mp3"'}
                    )
                    
        except Exception as e:
            # Try next strategy
            continue
    
    # All strategies failed
    raise HTTPException(
        status_code=503, 
        detail="All streaming methods failed. Video may be restricted or unavailable."
    )

@app.get("/search", summary="Search and stream music as MP3", tags=["Search", "Streaming"])
def search_and_stream(query: str = Query(..., description="Song or artist to search")):
    """
    Search YouTube and YouTube Music for a track and stream the first result as MP3.
    """
    ydl_opts = {
        'format': 'bestaudio',
        'quiet': True,
        'no_warnings': True,
        'default_search': 'ytsearch',
        'noplaylist': True,
        'extract_flat': False,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch1:{query}", download=False)
        if 'entries' in info and info['entries']:
            track = info['entries'][0]
        else:
            return JSONResponse(content={"error": "No results found."}, status_code=404)
        audio_url = track['url']

    command = [
        'ffmpeg',
        '-i', audio_url,
        '-f', 'mp3',
        '-vn',
        '-ab', '128k',
        '-ar', '44100',
        'pipe:1'
    ]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=10**8)

    def generate():
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.stdout.close()
            proc.terminate()
            proc.wait()

    # Sanitize filename to ASCII only
    raw_title = track.get('title', 'stream')
    safe_title = re.sub(r'[^a-zA-Z0-9_\-\. ]', '', raw_title)
    if not safe_title:
        safe_title = 'stream'
    headers = {
        'Content-Disposition': f'inline; filename="{safe_title}.mp3"',
    }
    return StreamingResponse(generate(), media_type="audio/mpeg", headers=headers)

# User Authentication Endpoints
@app.post("/register", summary="Register a new user", tags=["Authentication"])
async def register_user(username: str = Query(...), email: str = Query(...), password: str = Query(...)):
    """
    Register a new user. Returns user_id and access token.
    """
    try:
        # Check if user already exists
        existing = supabase.table("users").select("*").eq("email", email).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="User already exists")
        
        # Create user
        user_data = {
            "username": username,
            "email": email,
            "password_hash": password,  # In production, hash this!
            "created_at": datetime.now().isoformat()
        }
        result = supabase.table("users").insert(user_data).execute()
        user_id = result.data[0]["id"]
        
        # Generate JWT token
        token = jwt.encode(
            {"user_id": user_id, "exp": datetime.utcnow() + timedelta(days=30)},
            JWT_SECRET,
            algorithm="HS256"
        )
        
        return {"user_id": user_id, "access_token": token, "username": username}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/login", summary="Login user", tags=["Authentication"])
async def login_user(email: str = Query(...), password: str = Query(...)):
    """
    Login user. Returns user_id and access token.
    """
    try:
        # Find user
        result = supabase.table("users").select("*").eq("email", email).eq("password_hash", password).execute()
        if not result.data:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        user = result.data[0]
        # Generate JWT token
        token = jwt.encode(
            {"user_id": user["id"], "exp": datetime.utcnow() + timedelta(days=30)},
            JWT_SECRET,
            algorithm="HS256"
        )
        
        return {"user_id": user["id"], "access_token": token, "username": user["username"]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/save_my_playlist", summary="Save playlist to user's library", tags=["User Playlists"])
async def save_my_playlist(url: str = Query(...), user_id: str = Depends(get_current_user)):
    """
    Save a YouTube Music playlist to the current user's library.
    """
    try:
        ydl_opts = {
            'extract_flat': True,
            'quiet': True,
            'no_warnings': True,
            'force_generic_extractor': False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        
        playlist_data = {
            'id': info.get('id'),
            'user_id': user_id,
            'title': info.get('title'),
            'uploader': info.get('uploader'),
            'webpage_url': info.get('webpage_url'),
            'track_count': len(info.get('entries', [])),
            'thumbnail': info.get('thumbnail'),
            'created_at': datetime.now().isoformat()
        }
        supabase.table("user_playlists").upsert(playlist_data).execute()
        
        # Save tracks with user association
        for entry in info.get('entries', []):
            track_data = {
                'id': entry.get('id'),
                'playlist_id': info.get('id'),
                'user_id': user_id,
                'title': entry.get('title'),
                'url': entry.get('url'),
                'duration': entry.get('duration'),
                'thumbnail': entry.get('thumbnail') or f"https://img.youtube.com/vi/{entry.get('id')}/maxresdefault.jpg" if entry.get('id') else None
            }
            supabase.table("user_tracks").upsert(track_data).execute()
        
        return {"status": "success", "playlist_id": info.get('id'), "message": "Playlist saved to your library"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/my_playlists", summary="Get user's playlists", tags=["User Playlists"])
async def get_my_playlists(user_id: str = Depends(get_current_user)):
    """
    Get all playlists saved by the current user.
    """
    try:
        result = supabase.table("user_playlists").select("*").eq("user_id", user_id).execute()
        return {"playlists": result.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/my_playlist_tracks", summary="Get tracks from user's playlist", tags=["User Playlists"])
async def get_my_playlist_tracks(playlist_id: str = Query(...), user_id: str = Depends(get_current_user)):
    """
    Get all tracks from a specific playlist in user's library.
    """
    try:
        # Verify playlist belongs to user
        playlist_check = supabase.table("user_playlists").select("*").eq("id", playlist_id).eq("user_id", user_id).execute()
        if not playlist_check.data:
            raise HTTPException(status_code=404, detail="Playlist not found or access denied")
        
        tracks = supabase.table("user_tracks").select("*").eq("playlist_id", playlist_id).eq("user_id", user_id).execute()
        return {"tracks": tracks.data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)