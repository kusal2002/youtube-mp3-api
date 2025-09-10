# Alternative YouTube Audio Extraction
# Using different libraries and approaches

import requests
import re
import json
from urllib.parse import parse_qs, urlparse
import base64

class AlternativeExtractor:
    """Alternative YouTube audio extractor using different methods"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
    
    def extract_from_embed(self, video_id):
        """Extract using YouTube embed page (often bypasses restrictions)"""
        try:
            embed_url = f"https://www.youtube.com/embed/{video_id}"
            response = self.session.get(embed_url)
            
            # Look for player config in embed page
            pattern = r'ytInitialPlayerResponse\s*=\s*({.+?});'
            match = re.search(pattern, response.text)
            
            if match:
                player_data = json.loads(match.group(1))
                return self._extract_audio_url(player_data)
                
        except Exception as e:
            print(f"Embed extraction failed: {e}")
        
        return None
    
    def extract_from_mobile_api(self, video_id):
        """Extract using mobile YouTube API (different rate limits)"""
        try:
            # Use mobile API endpoint
            api_url = "https://www.youtube.com/youtubei/v1/player"
            
            payload = {
                "videoId": video_id,
                "context": {
                    "client": {
                        "clientName": "ANDROID",
                        "clientVersion": "17.36.4",
                        "androidSdkVersion": 30,
                        "userAgent": "com.google.android.youtube/17.36.4 (Linux; U; Android 11)"
                    }
                }
            }
            
            response = self.session.post(api_url, json=payload)
            data = response.json()
            
            return self._extract_audio_url(data)
            
        except Exception as e:
            print(f"Mobile API extraction failed: {e}")
        
        return None
    
    def extract_from_oembed(self, video_id):
        """Extract using YouTube oEmbed API"""
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
            response = self.session.get(oembed_url)
            
            if response.status_code == 200:
                data = response.json()
                # This gives us metadata, we can use it to verify video exists
                # Then try other extraction methods
                return True
                
        except Exception as e:
            print(f"oEmbed check failed: {e}")
        
        return False
    
    def _extract_audio_url(self, player_data):
        """Extract audio URL from player data"""
        try:
            streaming_data = player_data.get('streamingData', {})
            
            # Try adaptive formats first (usually better quality)
            adaptive_formats = streaming_data.get('adaptiveFormats', [])
            for fmt in adaptive_formats:
                if fmt.get('mimeType', '').startswith('audio/'):
                    return fmt.get('url')
            
            # Fallback to regular formats
            formats = streaming_data.get('formats', [])
            for fmt in formats:
                if fmt.get('mimeType', '').startswith('audio/'):
                    return fmt.get('url')
                    
        except Exception as e:
            print(f"Audio URL extraction failed: {e}")
        
        return None
