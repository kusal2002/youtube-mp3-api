#!/usr/bin/env python3.10

import os
import sys

# Add your project directory to Python path
path = '/home/yourusername/mysite'  # Change 'yourusername' to your actual username
if path not in sys.path:
    sys.path.append(path)

# Set environment variables
os.environ.setdefault('PYTHONPATH', path)

from main import app
application = app
