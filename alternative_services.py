# Alternative YouTube Services
# These services can extract YouTube audio when direct methods fail

import requests
import re
import json
from urllib.parse import quote

class AlternativeServices:
    """Use alternative services that specialize in YouTube audio extraction"""
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def extract_via_invidious(self, video_id):
        """Extract using Invidious instances (YouTube proxy)"""
        invidious_instances = [
            "https://invidious.io",
            "https://invidious.snopyta.org", 
            "https://yewtu.be",
            "https://invidious.kavin.rocks",
            "https://vid.puffyan.us"
        ]
        
        for instance in invidious_instances:
            try:
                url = f"{instance}/api/v1/videos/{video_id}"
                response = self.session.get(url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Get audio formats
                    adaptive_formats = data.get('adaptiveFormats', [])
                    for fmt in adaptive_formats:
                        if fmt.get('type', '').startswith('audio'):
                            return {
                                'url': fmt.get('url'),
                                'service': f'Invidious ({instance})',
                                'quality': fmt.get('bitrate', 'unknown')
                            }
                            
            except Exception as e:
                continue
        
        return None
    
    def extract_via_piped(self, video_id):
        """Extract using Piped API (another YouTube proxy)"""
        piped_instances = [
            "https://pipedapi.kavin.rocks",
            "https://api.piped.video",
            "https://pipedapi-libre.kavin.rocks"
        ]
        
        for instance in piped_instances:
            try:
                url = f"{instance}/streams/{video_id}"
                response = self.session.get(url, timeout=10)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Get audio streams
                    audio_streams = data.get('audioStreams', [])
                    if audio_streams:
                        # Get the first available audio stream
                        stream = audio_streams[0]
                        return {
                            'url': stream.get('url'),
                            'service': f'Piped ({instance})',
                            'quality': stream.get('bitrate', 'unknown')
                        }
                        
            except Exception as e:
                continue
        
        return None
    
    def extract_via_cobalt(self, video_id):
        """Extract using Cobalt API (supports many platforms)"""
        cobalt_instances = [
            "https://co.wuk.sh",
            "https://cobalt.tools"
        ]
        
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        for instance in cobalt_instances:
            try:
                api_url = f"{instance}/api/json"
                
                payload = {
                    "url": video_url,
                    "vCodec": "h264",
                    "vQuality": "720",
                    "aFormat": "mp3",
                    "isAudioOnly": True
                }
                
                response = self.session.post(api_url, json=payload, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    if data.get('status') == 'success':
                        return {
                            'url': data.get('url'),
                            'service': f'Cobalt ({instance})',
                            'quality': 'mp3'
                        }
                        
            except Exception as e:
                continue
        
        return None
    
    def extract_via_y2mate(self, video_id):
        """Extract using Y2mate-style API"""
        try:
            # First, get the conversion data
            api_url = "https://www.y2mate.com/mates/analyzeV2/ajax"
            
            payload = {
                'k_query': f"https://www.youtube.com/watch?v={video_id}",
                'k_page': 'home',
                'hl': 'en',
                'q_auto': 0
            }
            
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = self.session.post(api_url, data=payload, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                if data.get('status') == 'ok':
                    # Look for audio formats
                    links = data.get('links', {})
                    mp3_links = links.get('mp3', {})
                    
                    # Get the first available MP3 quality
                    for quality, info in mp3_links.items():
                        if info.get('k'):
                            # Now convert the audio
                            convert_url = "https://www.y2mate.com/mates/convertV2/index"
                            convert_payload = {
                                'vid': video_id,
                                'k': info['k']
                            }
                            
                            convert_response = self.session.post(
                                convert_url, 
                                data=convert_payload, 
                                headers=headers, 
                                timeout=15
                            )
                            
                            if convert_response.status_code == 200:
                                convert_data = convert_response.json()
                                
                                if convert_data.get('status') == 'ok':
                                    download_url = convert_data.get('dlink')
                                    if download_url:
                                        return {
                                            'url': download_url,
                                            'service': 'Y2mate',
                                            'quality': quality
                                        }
                                        
        except Exception as e:
            pass
        
        return None
