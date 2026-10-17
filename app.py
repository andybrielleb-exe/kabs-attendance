import base64
import csv
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
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
    send_file,
    url_for,
)
import qrcode

try:
    import psycopg2
except ImportError:
    psycopg2 = None

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kabs_attendance_secret_key_2026_v34")

# Philippine Time Zone (UTC+8) nang walang zoneinfo dependency
PH_TZ = timezone(timedelta(hours=8))

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

ADMIN_EMAILS = [
    "andybrielleb@gmail.com",
    "lawrence.bln19@gmail.com",
    "kevinlucisample@gmail.com",
]

ADMIN_CODES = [
    "KABS-7F2D",
    "KABS-1B57",
    "KABS-BE81",
]


def get_ph_now():
    return datetime.now(PH_TZ)


def format_ph_time(dt_obj):
    return dt_obj.strftime("%Y-%m-%d %I:%M:%S %p")


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
        if hours > 0 and minutes > 0:
            return f"{hours}h {minutes}m"
        elif hours > 0 and minutes == 0:
            return f"{hours}h"
        else:
            return f"{minutes}m"
    except Exception:
        return None


def parse_duration_to_minutes(dur_str):
    if not dur_str:
        return -1
    total = 0
    h_match = re.search(r"(\d+)\s*h", dur_str)
    m_match = re.search(r"(\d+)\s*m", dur_str)
    if h_match:
        total += int(h_match.group(1)) * 60
    if m_match:
        total += int(m_match.group(1))
    return total


def is_admin_user(user_dict):
    if not user_dict:
        return False
    email = str(user_dict.get("email", "")).strip().lower()
    code = str(user_dict.get("volunteer_code", "")).strip().upper()
    return email in ADMIN_EMAILS or code in ADMIN_CODES


def get_db_connection():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect("kabs.db")


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
                profile_pic TEXT,
                volunteer_status VARCHAR(20) DEFAULT 'Active',
                volunteer_type VARCHAR(25) DEFAULT 'Auxiliary',
                last_accessed TEXT
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
        for col, col_type in [
            ("profile_pic", "TEXT"),
            ("volunteer_status", "VARCHAR(20) DEFAULT 'Active'"),
            ("volunteer_type", "VARCHAR(25) DEFAULT 'Auxiliary'"),
            ("last_accessed", "TEXT"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE volunteers ADD COLUMN {col} {col_type};")
                conn.commit()
            except Exception:
                conn.rollback()

        for col, col_type in [
            ("agenda", "TEXT"),
            ("task", "TEXT"),
            ("total_hours", "TEXT"),
        ]:
            try:
                cursor.execute(f"ALTER TABLE attendance ADD COLUMN {col} {col_type};")
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
                profile_pic TEXT,
                volunteer_status TEXT DEFAULT 'Active',
                volunteer_type TEXT DEFAULT 'Auxiliary',
                last_accessed TEXT
            );
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
            );
            """
        )
        cursor.execute("PRAGMA table_info(volunteers)")
        v_cols = [c[1] for c in cursor.fetchall()]
        if "profile_pic" not in v_cols:
            cursor.execute("ALTER TABLE volunteers ADD COLUMN profile_pic TEXT;")
        if "volunteer_status" not in v_cols:
            cursor.execute("ALTER TABLE volunteers ADD COLUMN volunteer_status TEXT DEFAULT 'Active';")
        if "volunteer_type" not in v_cols:
            cursor.execute("ALTER TABLE volunteers ADD COLUMN volunteer_type TEXT DEFAULT 'Auxiliary';")
        if "last_accessed" not in v_cols:
            cursor.execute("ALTER TABLE volunteers ADD COLUMN last_accessed TEXT;")

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
        f"SELECT id, name, email, contact, qr_code, volunteer_code, profile_pic, volunteer_status, volunteer_type, last_accessed FROM volunteers WHERE id = {ph}",
        (user_id,),
    )
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return None

    status = row[7] if (len(row) > 7 and row[7]) else "Active"
    v_type = row[8] if (len(row) > 8 and row[8]) else "Auxiliary"
    last_accessed_str = row[9] if (len(row) > 9 and row[9]) else None
    
    now_dt = get_ph_now()
    now_str = format_ph_time(now_dt)

    is_over_six_months = False
    if last_accessed_str:
        try:
            last_dt = datetime.strptime(last_accessed_str.strip(), "%Y-%m-%d %I:%M:%S %p")
            last_dt = last_dt.replace(tzinfo=PH_TZ)
            if (now_dt - last_dt).days >= 180:
                is_over_six_months = True
        except Exception:
            pass

    if is_over_six_months and status != "Inactive":
        status = "Inactive"
        cursor.execute(
            f"UPDATE volunteers SET volunteer_status = 'Inactive', last_accessed = {ph} WHERE id = {ph}",
            (now_str, user_id),
        )
        conn.commit()
    else:
        cursor.execute(
            f"UPDATE volunteers SET last_accessed = {ph} WHERE id = {ph}",
            (now_str, user_id),
        )
        conn.commit()

    cursor.close()
    conn.close()

    return {
        "id": row[0],
        "name": row[1],
        "email": row[2],
        "contact": row[3],
        "qr_code": row[4],
        "volunteer_code": row[5],
        "profile_pic": row[6],
        "volunteer_status": status,
        "volunteer_type": v_type,
        "last_accessed": row[9] if len(row) > 9 else None,
    }


def get_current_system_state_hash(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"

    user_state = ("", "", "", "", "")
    duty_state = ("", "", "", "")

    if user_id:
        cursor.execute(
            f"SELECT name, contact, profile_pic, volunteer_status, volunteer_type FROM volunteers WHERE id = {ph}",
            (user_id,),
        )
        user_state = cursor.fetchone() or ("", "", "", "", "")

        cursor.execute(
            f"SELECT id, time_in, time_out, total_hours FROM attendance WHERE volunteer_id = {ph} ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        duty_state = cursor.fetchone() or ("", "", "", "")

    cursor.execute("SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(COALESCE(time_out, '')), '') FROM attendance")
    log_state = cursor.fetchone() or (0, 0, "")

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
                        
                        <span id="header-type-badge" class="text-[9px] px-1 py-0.5 rounded font-mono font-bold {% if user.get('volunteer_type') == 'Core' %}bg-purple-600/40 text-purple-300{% else %}bg-sky-600/40 text-sky-300{% endif %}">
                            {{ 'Core' if user.get('volunteer_type') == 'Core' else 'Aux' }}
                        </span>

                        <span id="header-status-badge" class="text-[9px] px-1 py-0.5 rounded font-mono font-bold {% if user.get('volunteer_status') == 'Active' %}bg-emerald-600/40 text-emerald-300{% else %}bg-rose-600/40 text-rose-300{% endif %}">
                            {{ user.get('volunteer_status') }}
                        </span>
                    </a>

                    <button type="button" onclick="handleLogoutClick();" class="bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 text-[11px] sm:text-xs font-bold px-2 sm:px-2.5 py-1.5 rounded-lg transition-all flex-shrink-0">
                        Log Out
                    </button>
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
                    <p class="text-xs text-slate-500 mb-4" id="login-desc">Point your QR Pass camera scanner to auto-login.</p>

                    <div id="qr-login-section" class="space-y-3">
                        <div id="reader" class="rounded-xl overflow-hidden border border-slate-200 bg-slate-50 min-h-[220px]"></div>
                        <div id="scan-status" class="text-xs font-medium text-center text-slate-500">Initializing camera...</div>
                        
                        <div class="relative flex py-2 items-center">
                            <div class="flex-grow border-t border-slate-200"></div>
                            <span class="flex-shrink mx-2 text-xs text-slate-400">or enter code</span>
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
                            <button type="submit" class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-sm rounded-lg s
