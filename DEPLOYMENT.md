# PythonAnywhere Deployment Guide for Music Stream API

## Prerequisites
1. Create a free account at https://www.pythonanywhere.com
2. Have your Supabase credentials ready

## Step-by-Step Deployment

### 1. Upload Your Files
- Go to PythonAnywhere Dashboard → Files
- Navigate to `/home/yourusername/mysite/` (replace 'yourusername' with your actual username)
- Upload all your files: main.py, requirements.txt, wsgi.py, .env

### 2. Install Dependencies
- Go to Dashboard → Consoles → Bash
- Run these commands:
```bash
cd ~/mysite
pip3.10 install --user -r requirements.txt
```

### 3. Install FFmpeg (Required for MP3 streaming)
FFmpeg is needed but not available on free tier. You'll need to:
- Upgrade to paid plan ($5/month) for system packages, OR
- Remove streaming endpoints and only use metadata features

### 4. Configure Web App
- Go to Dashboard → Web
- Click "Add a new web app"
- Choose "Manual configuration"
- Select "Python 3.10"
- Set source code directory: `/home/yourusername/mysite`
- Set WSGI file: `/home/yourusername/mysite/wsgi.py`

### 5. Update WSGI File
Edit the wsgi.py file path to match your username:
```python
path = '/home/yourusername/mysite'  # Replace 'yourusername'
```

### 6. Set Environment Variables
- In Web tab, scroll to "Environment variables"
- Add your .env variables:
  - SUPABASE_URL: your_supabase_url
  - SUPABASE_KEY: your_supabase_key
  - JWT_SECRET: your_jwt_secret

### 7. Reload Web App
- Click "Reload" button in Web tab
- Your API will be available at: https://yourusername.pythonanywhere.com

## Important Limitations on Free Tier:
1. **No FFmpeg**: Can't stream MP3 (remove /stream_mp3 and /search endpoints)
2. **CPU seconds**: Limited processing time
3. **Internet access**: Only to whitelisted sites (Supabase should work)
4. **File storage**: 512MB limit

## Recommended Changes for Free Tier:
- Remove streaming endpoints
- Keep only metadata and search results endpoints
- Focus on playlist management and user features

## For Full Functionality:
Consider upgrading to Hacker plan ($5/month) for:
- System packages (FFmpeg)
- More CPU time
- Full internet access
