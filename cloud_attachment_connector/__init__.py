# -*- coding: utf-8 -*-
import subprocess
import sys

# Install required packages early, before Odoo checks external_dependencies
REQUIRED_PACKAGES = {
    'boto3': 'boto3',
    'google-api-python-client': 'googleapiclient',
    'google-auth-httplib2': 'google_auth_httplib2',
    'google-auth-oauthlib': 'google_auth_oauthlib',
    'msal': 'msal',
    'requests': 'requests',
}

for pip_name, import_name in REQUIRED_PACKAGES.items():
    try:
        __import__(import_name)
    except ImportError:
        try:
            subprocess.check_call(
                [sys.executable, '-m', 'pip', 'install', '--quiet', pip_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f"Warning: Failed to auto-install {pip_name}: {e}")

import odoo.http

from . import controllers
from . import models
from . import wizard
from .hooks import post_init_hook
from .hooks import uninstall_hook

odoo.http.DEFAULT_MAX_CONTENT_LENGTH = 5 * 1024 * 1024 * 1024
