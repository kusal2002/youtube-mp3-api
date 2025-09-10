# 🔧 Koyeb Secrets Setup Guide

## The Problem
Your app is failing because it can't find environment variables. In Koyeb, you need to use **Secrets** for sensitive data.

## ✅ Solution: Create Secrets in Koyeb

### Step 1: Create Secrets
Go to [Koyeb Secrets Dashboard](https://app.koyeb.com/secrets) and create these secrets:

1. **Secret Name**: `SUPABASE_URL`
   - **Value**: Your Supabase project URL (e.g., `https://xyz.supabase.co`)

2. **Secret Name**: `SUPABASE_KEY`  
   - **Value**: Your Supabase anon public key

3. **Secret Name**: `JWT_SECRET`
   - **Value**: Any secure random string (e.g., `your-super-secret-jwt-key-123`)

### Step 2: Configure Environment Variables in Your Service

In your Koyeb service settings, add these environment variables:

```
SUPABASE_URL = @SUPABASE_URL
SUPABASE_KEY = @SUPABASE_KEY
JWT_SECRET = @JWT_SECRET
```

⚠️ **Important**: The `@` symbol tells Koyeb to use the secret value!

### Step 3: Redeploy

After setting up the secrets and environment variables, redeploy your service.

## 🎯 Quick Fix Alternative

If you want to test quickly, you can also set environment variables directly (less secure):

```
SUPABASE_URL = https://your-project.supabase.co
SUPABASE_KEY = your-anon-key-here
JWT_SECRET = your-jwt-secret-here
```

## 📋 Where to Find Your Supabase Credentials

1. Go to [Supabase Dashboard](https://app.supabase.com/)
2. Select your project
3. Go to **Settings** → **API**
4. Copy:
   - **Project URL** → Use for `SUPABASE_URL`
   - **Project API keys** → **anon public** → Use for `SUPABASE_KEY`

## 🚀 After Setup

Once configured, your app will:
- ✅ Start successfully
- ✅ Connect to Supabase database  
- ✅ Serve the homepage at your Koyeb URL
- ✅ Provide API documentation at `/docs`

## 🔍 Verify Setup

Test these endpoints after deployment:
- `https://your-app.koyeb.app/` - Homepage
- `https://your-app.koyeb.app/health` - Health check
- `https://your-app.koyeb.app/docs` - API documentation
