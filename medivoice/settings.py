import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

# ==========================================
# BASE DIR + ENV
# ==========================================
BASE_DIR = Path(__file__).resolve().parent.parent

# Load local .env (for local development)
load_dotenv(BASE_DIR / ".env")


# ==========================================
# SECURITY
# ==========================================
SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "django-insecure-change-this-in-render-production"
)

DEBUG = os.getenv("DEBUG", "False").lower() == "true"

# Add your Render domain here
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    ".onrender.com",
]

# If you want to add custom domain later:
# ALLOWED_HOSTS += ["yourdomain.com", "www.yourdomain.com"]


# ==========================================
# INSTALLED APPS
# ==========================================
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "assistant",   # your app
]


# ==========================================
# MIDDLEWARE
# ==========================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",   # WhiteNoise before SessionMiddleware
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ==========================================
# URLS / WSGI
# ==========================================
ROOT_URLCONF = "project.urls"   # change if your project folder name is different

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],   # optional project-level templates
        "APP_DIRS": True,                   # important for assistant/templates/home.html
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "project.wsgi.application"   # change if project folder name differs


# ==========================================
# DATABASE
# ==========================================
# If DATABASE_URL exists (Render Postgres), use it
# Else fallback to SQLite
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=600,
            ssl_require=True
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# ==========================================
# PASSWORD VALIDATION
# ==========================================
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# ==========================================
# INTERNATIONALIZATION
# ==========================================
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True


# ==========================================
# STATIC FILES (Render + WhiteNoise)
# ==========================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Optional extra static folder if you create project-level static/
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

# WhiteNoise storage
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"


# ==========================================
# MEDIA FILES
# ==========================================
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# ==========================================
# DEFAULT AUTO FIELD
# ==========================================
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ==========================================
# RENDER / PRODUCTION SECURITY
# ==========================================
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Only force HTTPS in production
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
else:
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_SSL_REDIRECT = False


# ==========================================
# UPLOAD LIMITS (important for camera base64 images)
# ==========================================
# Increase limits because camera captures can be large
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024   # 20 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024   # 20 MB


# ==========================================
# EXTERNAL API KEYS
# ==========================================
OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "")

SERVAM_API_KEY = os.getenv("SERVAM_API_KEY", "")
SERVAM_API_URL = os.getenv("SERVAM_API_URL", "")


# ==========================================
# LOGGING (optional but useful on Render)
# ==========================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
