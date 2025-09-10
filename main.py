from fastapi import FastAPI, Query, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import yt_dlp
import subprocess
import os
import re
import jwt
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Supabase config (now loaded from .env file)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env file")

# JWT Secret for user authentication (loaded from .env)
JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    raise ValueError("JWT_SECRET must be set in .env file")

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

@app.get("/")
async def root():
    return {"message": "Music Stream API is running"}

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
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'default_search': 'ytsearch',
        'noplaylist': True,
        'extract_flat': True,
    }
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
    """
    ydl_opts = {
        'format': 'bestaudio',
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        audio_url = info['url']

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