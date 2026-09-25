import base64
import csv
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    render_template_string,
    request,
    session,
    url_for,
)
import qrcode

try:
    import psycopg2
except ImportError:
    psycopg2 = None

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kabs_attendance_secret_key_2026_v11")

# Nakatakda sa eksaktong 5 oras ang session bago mag-auto log out
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=5),
)

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

USE_POSTGRES = bool(DATABASE_URL and psycopg2)

QR_FOLDER = os.path.join("static", "qrcodes")
os.makedirs(QR_FOLDER, exist_ok=True)


def get_db_connection():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect("kabs.db")


def calculate_duration(time_in_str, time_out_str):
    if not time_in_str or not time_out_str:
        return None
    time_format = "%Y-%m-%d %I:%M:%S %p"
    try:
        t_in = datetime.strptime(time_in_str.strip(), time_format)
        t_out = datetime.strptime(time_out_str.strip(), time_format)
        diff = t_out - t_in
        total_seconds = int(diff.total_seconds())
        if total_seconds < 0:
            return "0m"
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return None


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    if USE_POSTGRES:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS volunteers (
                id SERIAL PRIMARY KEY,
                name VARCHAR(50) NOT NULL,
                email TEXT UNIQUE NOT NULL,
                contact TEXT NOT NULL,
                auth_token TEXT NOT NULL,
                volunteer_code TEXT UNIQUE,
                qr_code TEXT,
                profile_pic TEXT
            );
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id SERIAL PRIMARY KEY,
                volunteer_id INTEGER REFERENCES volunteers(id),
                agenda TEXT,
                task TEXT,
                time_in TEXT,
                time_out TEXT,
                total_hours TEXT
            );
        """
        )
        try:
            cursor.execute("ALTER TABLE volunteers ADD COLUMN profile_pic TEXT;")
            conn.commit()
        except Exception:
            conn.rollback()

        try:
            cursor.execute("ALTER TABLE attendance ADD COLUMN agenda TEXT;")
            conn.commit()
        except Exception:
            conn.rollback()

        try:
            cursor.execute("ALTER TABLE attendance ADD COLUMN task TEXT;")
            conn.commit()
        except Exception:
            conn.rollback()

        try:
            cursor.execute("ALTER TABLE attendance ADD COLUMN total_hours TEXT;")
            conn.commit()
        except Exception:
            conn.rollback()
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS volunteers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(50) NOT NULL,
                email TEXT UNIQUE NOT NULL,
                contact TEXT NOT NULL,
                auth_token TEXT NOT NULL,
                volunteer_code TEXT UNIQUE,
                qr_code TEXT,
                profile_pic TEXT
            )
        """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                volunteer_id INTEGER,
                agenda TEXT,
                task TEXT,
                time_in TEXT,
                time_out TEXT,
                total_hours TEXT,
                FOREIGN KEY (volunteer_id) REFERENCES volunteers (id)
            )
        """
        )
        cursor.execute("PRAGMA table_info(volunteers)")
        v_cols = [c[1] for c in cursor.fetchall()]
        if "profile_pic" not in v_cols:
            cursor.execute("ALTER TABLE volunteers ADD COLUMN profile_pic TEXT;")

        cursor.execute("PRAGMA table_info(attendance)")
        a_cols = [c[1] for c in cursor.fetchall()]
        if "agenda" not in a_cols:
            cursor.execute("ALTER TABLE attendance ADD COLUMN agenda TEXT;")
        if "task" not in a_cols:
            cursor.execute("ALTER TABLE attendance ADD COLUMN task TEXT;")
        if "total_hours" not in a_cols:
            cursor.execute("ALTER TABLE attendance ADD COLUMN total_hours TEXT;")

    conn.commit()
    cursor.close()
    conn.close()


init_db()


def authenticate_user_by_qr(raw_qr_input):
    if not raw_qr_input:
        return None

    raw = str(raw_qr_input).strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()

    v_id = None
    v_token = None
    v_name = None
    v_code = None

    try:
        fixed_json = raw.replace("'", '"')
        data = json.loads(fixed_json)
        if isinstance(data, dict):
            v_id = data.get("id")
            v_token = data.get("token")
            v_name = data.get("name")
            v_code = data.get("code")
    except Exception:
        pass

    if not v_code:
        code_match = re.search(r"KABS-[A-Za-z0-9]+", raw, re.IGNORECASE)
        if code_match:
            v_code = code_match.group(0).upper().strip()

    if not v_token:
        url_match = re.search(r"/qr-auth/([A-Za-z0-9_\-]+)", raw)
        if url_match:
            v_token = url_match.group(1).strip()

    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"
    user = None

    def try_execute(query, params):
        try:
            cursor.execute(query, params)
            return cursor.fetchone()
        except Exception:
            if USE_POSTGRES:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return None

    if v_id and v_name:
        user = try_execute(
            f"SELECT id, name, email, contact, qr_code, volunteer_code FROM volunteers WHERE id = {ph} AND LOWER(name) = LOWER({ph})",
            (v_id, v_name.strip()),
        )

    if not user and v_code:
        user = try_execute(
            f"SELECT id, name, email, contact, qr_code, volunteer_code FROM volunteers WHERE UPPER(volunteer_code) = UPPER({ph})",
            (v_code,),
        )

    if not user and v_id:
        user = try_execute(
            f"SELECT id, name, email, contact, qr_code, volunteer_code FROM volunteers WHERE id = {ph}",
            (v_id,),
        )

    if not user and v_token:
        user = try_execute(
            f"SELECT id, name, email, contact, qr_code, volunteer_code FROM volunteers WHERE auth_token = {ph}",
            (v_token,),
        )

    if not user and v_name:
        user = try_execute(
            f"SELECT id, name, email, contact, qr_code, volunteer_code FROM volunteers WHERE LOWER(name) = LOWER({ph})",
            (v_name.strip(),),
        )

    if not user:
        user = try_execute(
            f"SELECT id, name, email, contact, qr_code, volunteer_code FROM volunteers WHERE UPPER(volunteer_code) = UPPER({ph}) OR auth_token = {ph}",
            (raw.upper(), raw),
        )

    cursor.close()
    conn.close()
    return user


def get_fresh_user_profile(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"
    cursor.execute(
        f"SELECT id, name, email, contact, qr_code, volunteer_code, profile_pic FROM volunteers WHERE id = {ph}",
        (user_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return {
            "id": row[0],
            "name": row[1],
            "email": row[2],
            "contact": row[3],
            "qr_code": row[4],
            "volunteer_code": row[5],
            "profile_pic": row[6],
        }
    return None


def get_current_system_state_hash(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"

    cursor.execute(
        f"SELECT name, contact, profile_pic FROM volunteers WHERE id = {ph}",
        (user_id,),
    )
    user_state = cursor.fetchone() or ("", "", "")

    cursor.execute(
        f"SELECT id, time_in, time_out FROM attendance WHERE volunteer_id = {ph} AND time_out IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    duty_state = cursor.fetchone() or ("", "", "")

    cursor.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM attendance")
    log_state = cursor.fetchone() or (0, 0)

    cursor.close()
    conn.close()

    raw_signature = f"{user_state}_{duty_state}_{log_state}"
    return hashlib.md5(raw_signature.encode("utf-8")).hexdigest()


MAIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
    <title>KABS Attendance Portal</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/html5-qrcode"></script>
</head>
<body class="bg-slate-50 text-slate-800 antialiased min-h-screen pb-12">
    <!-- STICKY TOPBAR -->
    <header class="bg-slate-900 border-b border-slate-800 sticky top-0 z-30 shadow-md">
        <div class="max-w-6xl mx-auto px-2 sm:px-4 py-2.5 flex justify-between items-center gap-1.5 sm:gap-3">
            <a href="/" class="flex items-center space-x-1.5 sm:space-x-2.5 flex-shrink min-w-0">
                <img src="/static/images/logo.jpg" alt="Logo" class="w-8 h-8 sm:w-9 sm:h-9 rounded-lg object-cover bg-white p-0.5 border border-slate-700 flex-shrink-0" onerror="this.src='/static/images/logo.png';">
                <div class="min-w-0 leading-tight">
                    <h1 class="font-extrabold text-white text-xs sm:text-sm tracking-tight truncate">KABS PORTAL</h1>
                    <p class="text-[9px] text-slate-400 hidden md:block">SK Payatas Youth Volunteers</p>
                </div>
            </a>

            <div class="flex items-center space-x-1.5 sm:space-x-2 flex-shrink-0">
                {% if user %}
                <a href="/profile" class="flex items-center gap-1.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs px-2 sm:px-2.5 py-1.5 rounded-lg text-slate-200 transition-all">
                    {% if user.get('profile_pic') %}
                        <img src="{{ user.get('profile_pic') }}" class="w-4 h-4 sm:w-5 sm:h-5 rounded-full object-cover flex-shrink-0">
                    {% else %}
                        <span class="text-xs">👤</span>
                    {% endif %}
                    <span class="font-semibold text-white max-w-[75px] sm:max-w-[130px] truncate text-[11px] sm:text-xs">{{ user.get('name') }}</span>
                    <span class="text-[9px] bg-blue-600/40 text-blue-300 px-1 py-0.5 rounded font-mono hidden xs:inline">Profile</span>
                </a>
                <a href="/logout" onclick="clearLoginStorage();" class="bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 text-[11px] sm:text-xs font-bold px-2 sm:px-2.5 py-1.5 rounded-lg transition-all flex-shrink-0">
                    Log Out
                </a>
                {% endif %}
            </div>
        </div>
    </header>

    <main class="max-w-6xl mx-auto px-4 pt-6 space-y-6">
        {% with messages = get_flashed_messages(with_categories=true) %}
          {% if messages %}
            <div class="space-y-2">
            {% for category, message in messages %}
              <div class="rounded-xl p-4 text-sm font-medium border shadow-sm
                  {% if category == 'success' %} bg-emerald-50 text-emerald-800 border-emerald-200
                  {% elif category == 'danger' %} bg-rose-50 text-rose-800 border-rose-200
                  {% else %} bg-amber-50 text-amber-800 border-amber-200 {% endif %}">
                  {{ message | safe }}
              </div>
            {% endfor %}
            </div>
          {% endif %}
        {% endwith %}

        {% if not user %}
            <!-- LOGGED OUT VIEW -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                    <div class="flex items-center justify-between mb-1">
                        <h2 class="text-base font-bold text-slate-900">Volunteer Login</h2>
                        <button type="button" onclick="toggleLoginMode()" id="toggle-btn" class="text-xs text-blue-600 hover:text-blue-800 font-semibold underline">
                            Use Credentials Instead
                        </button>
                    </div>
                    <p class="text-xs text-slate-500 mb-4" id="login-desc">Itapat ang iyong QR pass sa camera para mag-auto login.</p>

                    <div id="qr-login-section" class="space-y-3">
                        <div id="reader" class="rounded-xl overflow-hidden border border-slate-200 bg-slate-50 min-h-[220px]"></div>
                        <div id="scan-status" class="text-xs font-medium text-center text-slate-500">Initializing camera...</div>
                        
                        <div class="relative flex py-2 items-center">
                            <div class="flex-grow border-t border-slate-200"></div>
                            <span class="flex-shrink mx-2 text-xs text-slate-400">o i-type ang code</span>
                            <div class="flex-grow border-t border-slate-200"></div>
                        </div>

                        <form action="/login-code" method="POST" class="flex space-x-2">
                            <input type="text" name="volunteer_code" placeholder="E.G. KABS-4F2A" required class="uppercase text-sm w-full py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                            <button type="submit" class="bg-slate-900 hover:bg-slate-800 text-white text-xs font-semibold px-4 rounded-lg">Enter</button>
                        </form>
                    </div>

                    <div id="form-login-section" class="hidden">
                        <form action="/login" method="POST" class="space-y-3">
                            <div>
                                <label class="block text-xs font-semibold text-slate-600 mb-1">Email Address (@gmail.com)</label>
                                <input type="email" name="email" placeholder="juandelacruz@gmail.com" class="w-full text-sm py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                            </div>
                            <div>
                                <label class="block text-xs font-semibold text-slate-600 mb-1">Contact Number (11 digits, numbers only)</label>
                                <input type="tel" name="contact" maxlength="11" minlength="11" pattern="09[0-9]{9}" inputmode="numeric" oninput="this.value = this.value.replace(/[^0-9]/g, '')" placeholder="09xxxxxxxxx" class="w-full text-sm py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                            </div>
                            <button type="submit" class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-sm rounded-lg shadow-sm transition-all">Access Portal & Pass</button>
                        </form>
                    </div>
                </div>

                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                    <h2 class="text-base font-bold text-slate-900 mb-1">Register New Volunteer</h2>
                    <p class="text-xs text-slate-500 mb-4">Maging opisyal na KABS Youth Volunteer ng SK Payatas.</p>
                    <form action="/register" method="POST" class="space-y-3">
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 mb-1">Full Name</label>
                            <input type="text" name="name" maxlength="50" required placeholder="Juan Dela Cruz" class="w-full text-sm py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 mb-1">Email (@gmail.com only)</label>
                            <input type="email" name="email" required placeholder="juandelacruz@gmail.com" class="w-full text-sm py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold text-slate-600 mb-1">Contact Number (11 digits, numbers only)</label>
                            <input type="tel" name="contact" maxlength="11" minlength="11" pattern="09[0-9]{9}" inputmode="numeric" oninput="this.value = this.value.replace(/[^0-9]/g, '')" required placeholder="09xxxxxxxxx" class="w-full text-sm py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                        </div>

                        <!-- STRICT MANUAL AGREEMENT -->
                        <div class="pt-2 border-t border-slate-100">
                            <div class="bg-amber-50/70 border border-amber-200 rounded-xl p-3 space-y-2">
                                <div class="flex items-center justify-between">
                                    <span class="text-xs font-bold text-amber-900">Manual Agreement</span>
                                    <button type="button" onclick="openManualModal()" class="text-xs bg-amber-600 hover:bg-amber-700 text-white font-bold px-3 py-1 rounded-lg shadow-sm transition-all">
                                        Basahin Hanggang Dulo ↗
                                    </button>
                                </div>
                                <div class="flex items-start gap-2 pt-1">
                                    <input type="checkbox" id="agree_terms" name="agree_terms" required disabled class="mt-0.5 w-4 h-4 text-emerald-600 rounded border-slate-300 cursor-not-allowed">
                                    <label for="agree_terms" id="agree_label" class="text-[11px] text-slate-500 leading-snug select-none">
                                        🔒 <i>I-scroll ang KABS Manual hanggang dulo bago ma-unlock ang confirmation na ito.</i>
                                    </label>
                                </div>
                            </div>
                        </div>

                        <button type="submit" id="register_submit_btn" disabled class="w-full py-2.5 bg-slate-400 text-white font-semibold text-sm rounded-lg shadow-sm cursor-not-allowed transition-all mt-1">
                            Register & Generate Pass
                        </button>
                    </form>
                </div>
            </div>
        {% else %}
            <!-- DASHBOARD: PUNCH ATTENDANCE AT OFFICIAL PASS -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
                
                <!-- PUNCH TIME IN / TIME OUT CARD NA MAY LIVE TIMER -->
                <div class="bg-white border border-slate-200 rounded-2xl p-6
