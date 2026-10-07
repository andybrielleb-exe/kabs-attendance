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
    from zoneinfo import ZoneInfo
    PH_TZ = ZoneInfo("Asia/Manila")
except Exception:
    PH_TZ = timezone(timedelta(hours=8))

try:
    import psycopg2
except ImportError:
    psycopg2 = None

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kabs_attendance_secret_key_2026_v33")

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
                            <button type="submit" class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-sm rounded-lg shadow-sm transition-all">Access Portal & Pass</button>
                        </form>
                    </div>
                </div>

                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                    <h2 class="text-base font-bold text-slate-900 mb-1">Register New Volunteer</h2>
                    <p class="text-xs text-slate-500 mb-4">Be an official KABS Youth Volunteer of SK Payatas.</p>
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

                        <div class="pt-2 border-t border-slate-100">
                            <div class="bg-amber-50/70 border border-amber-200 rounded-xl p-3 space-y-2">
                                <div class="flex items-center justify-between">
                                    <span class="text-xs font-bold text-amber-900">Manual Agreement</span>
                                    <button type="button" onclick="openManualModal()" class="text-xs bg-amber-600 hover:bg-amber-700 text-white font-bold px-3 py-1 rounded-lg shadow-sm transition-all">
                                        Read to the End ↗
                                    </button>
                                </div>
                                <div class="flex items-start gap-2 pt-1">
                                    <input type="checkbox" id="agree_terms" name="agree_terms" required disabled class="mt-0.5 w-4 h-4 text-emerald-600 rounded border-slate-300 cursor-not-allowed">
                                    <label for="agree_terms" id="agree_label" class="text-[11px] text-slate-500 leading-snug select-none">
                                        🔒 <i>Scroll the KABS Manual to the very end to unlock this agreement.</i>
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
            <!-- LOGGED IN VIEW -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                    {% if user.get('volunteer_status') != 'Active' %}
                        <!-- LOCKED STATUS -->
                        <div class="text-center py-6 space-y-3">
                            <div class="w-12 h-12 bg-rose-100 text-rose-600 rounded-full flex items-center justify-center mx-auto text-xl font-bold">
                                🔒
                            </div>
                            <h3 class="text-sm font-bold text-slate-900">Attendance Logging Locked</h3>
                            <div class="p-3 bg-rose-50 border border-rose-200 rounded-xl text-left text-xs text-rose-800 leading-relaxed">
                                ⚠️ <b>Your volunteer status is currently Inactive.</b><br>
                                You cannot punch Time In or Time Out at this time. Please contact the SK Admin to re-activate your account status.
                            </div>
                            <button type="button" disabled class="w-full py-2.5 bg-slate-200 text-slate-400 text-xs font-bold rounded-lg cursor-not-allowed">
                                Punch Attendance Disabled
                            </button>
                        </div>
                    {% else %}
                        {% if active_record %}
                            <div class="flex items-center justify-between mb-2">
                                <h2 class="text-base font-bold text-slate-900">Active Duty Session</h2>
                                <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 animate-pulse">
                                    ● Clocked In
                                </span>
                            </div>
                            <p class="text-xs text-slate-500 mb-4">Your session will auto-sync and clock out if submitted on another device.</p>

                            <div class="bg-slate-50 border border-slate-200 rounded-xl p-3 mb-4 space-y-2 text-xs">
                                <div class="flex justify-between">
                                    <span class="text-slate-500 font-medium">Agenda:</span>
                                    <span class="font-bold text-slate-800">{{ active_record[1] }}</span>
                                </div>
                                <div class="flex justify-between">
                                    <span class="text-slate-500 font-medium">Assigned Task:</span>
                                    <span class="font-bold text-slate-800">{{ active_record[2] }}</span>
                                </div>
                                <div class="flex justify-between">
                                    <span class="text-slate-500 font-medium">Time In:</span>
                                    <span class="font-bold text-emerald-700" id="session-time-in">{{ active_record[3] }}</span>
                                </div>
                                <div class="flex justify-between items-center pt-1 border-t border-slate-200">
                                    <span class="text-slate-500 font-medium">Running Duty Time:</span>
                                    <span class="font-mono font-bold text-blue-700 text-sm bg-blue-50 px-2 py-0.5 rounded border border-blue-200" id="live-timer">
                                        00:00:00
                                    </span>
                                </div>
                            </div>

                            <form action="/log-self-attendance" method="POST">
                                <button type="submit" class="w-full py-3 bg-rose-600 hover:bg-rose-700 text-white font-bold text-sm rounded-xl shadow transition-all flex items-center justify-center gap-2">
                                    <span>🔴</span> Punch Time Out
                                </button>
                            </form>
                        {% else %}
                            <h2 class="text-base font-bold text-slate-900 mb-1">Punch Time In</h2>
                            <p class="text-xs text-slate-500 mb-4">Enter current agenda and task assignment to record attendance.</p>
                            
                            <form action="/log-self-attendance" method="POST" class="space-y-3">
                                <div>
                                    <label class="block text-xs font-semibold text-slate-600 mb-1">Agenda / Event</label>
                                    <input type="text" name="agenda" required placeholder="e.g. Clean-Up Drive / Assembly" class="w-full text-sm py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                                </div>
                                <div>
                                    <label class="block text-xs font-semibold text-slate-600 mb-1">Assigned Task</label>
                                    <input type="text" name="task" required placeholder="e.g. Waste Segregation" class="w-full text-sm py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                                </div>
                                <button type="submit" class="w-full py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-sm rounded-lg shadow-sm transition-all flex items-center justify-center gap-2">
                                    <span>🟢</span> Punch Time In
                                </button>
                            </form>
                        {% endif %}
                    {% endif %}
                </div>

                <!-- OFFICIAL QR PASS -->
                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm text-center">
                    <span class="inline-block bg-slate-100 text-slate-600 text-xs font-bold px-3 py-1 rounded-full uppercase mb-2">
                        Official Pass
                    </span>
                    <div class="my-3 flex justify-center">
                        <img src="/qr-code/{{ user.get('volunteer_code') }}" alt="QR Code" class="w-36 h-36 rounded-lg border p-1 bg-white shadow-sm">
                    </div>
                    <div class="text-xs font-mono font-bold bg-slate-100 py-1.5 px-3 rounded inline-block mb-3 border border-dashed border-slate-400">{{ user.get('volunteer_code') }}</div><br>
                    <div class="flex justify-center">
                        <a href="/qr-code/{{ user.get('volunteer_code') }}" download="{{ user.get('volunteer_code') }}_pass.png" class="inline-block py-2 px-6 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg shadow-sm">Download Pass</a>
                    </div>
                </div>
            </div>

            <!-- ATTENDANCE LOG TABLE -->
            <div class="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
                <div class="p-4 border-b flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 bg-slate-50/50">
                    <div>
                        <h3 class="font-bold text-sm text-slate-800">Attendance Log</h3>
                        <p class="text-xs text-slate-500">Official logs of volunteer hours, duties, and attendance (PST Time).</p>
                    </div>
                    
                    <div class="flex items-center gap-2 flex-wrap w-full sm:w-auto justify-end">
                        <!-- SORT DROPDOWN -->
                        <form method="GET" action="/" class="flex items-center gap-1.5 text-xs">
                            <label for="sort_by" class="font-semibold text-slate-600">Sort by:</label>
                            <select name="sort_by" id="sort_by" onchange="this.form.submit()" class="bg-white border border-slate-300 rounded-lg px-2.5 py-1.5 font-medium text-slate-700 outline-none focus:ring-1 focus:ring-blue-600">
                                <option value="latest" {% if current_sort == 'latest' %}selected{% endif %}>Latest Duty</option>
                                <option value="name_asc" {% if current_sort == 'name_asc' %}selected{% endif %}>Name (A-Z)</option>
                                <option value="name_desc" {% if current_sort == 'name_desc' %}selected{% endif %}>Name (Z-A)</option>
                                <option value="hours_desc" {% if current_sort == 'hours_desc' %}selected{% endif %}>Highest Hours</option>
                            </select>
                        </form>

                        {% if is_admin %}
                            <button type="button" id="batch-delete-btn" onclick="submitBatchDelete()" class="hidden items-center gap-1 bg-rose-600 hover:bg-rose-700 text-white text-xs font-bold px-3 py-1.5 rounded-lg shadow-sm transition-all">
                                🗑️ Delete Selected (<span id="selected-count">0</span>)
                            </button>

                            {% if logs and logs|length > 0 %}
                                <a href="/export-attendance" class="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg shadow-sm transition-all">
                                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                                    Export Excel
                                </a>
                            {% endif %}
                        {% endif %}
                    </div>
                </div>

                <form id="batch-delete-form" action="/delete-logs-batch" method="POST">
                    <div class="overflow-x-auto">
                        <table class="w-full text-left text-xs sm:text-sm">
                            <thead class="bg-slate-50 text-slate-500 uppercase text-xs font-semibold">
                                <tr>
                                    {% if is_admin %}
                                    <th class="py-3 px-3 text-center w-8">
                                        <input type="checkbox" id="select-all-checkbox" onchange="toggleSelectAll(this)" class="w-4 h-4 text-rose-600 rounded border-slate-300 cursor-pointer">
                                    </th>
                                    {% endif %}
                                    <th class="py-3 px-4">Volunteer</th>
                                    <th class="py-3 px-4">Agenda</th>
                                    <th class="py-3 px-4">Task</th>
                                    <th class="py-3 px-4">Time In</th>
                                    <th class="py-3 px-4">Time Out</th>
                                    <th class="py-3 px-4 text-center">Total Hours</th>
                                    {% if is_admin %}
                                    <th class="py-3 px-4 text-center">Action</th>
                                    {% endif %}
                                </tr>
                            </thead>
                            <tbody class="divide-y divide-slate-100">
                                {% for log in logs %}
                                <tr>
                                    {% if is_admin %}
                                    <td class="py-3 px-3 text-center">
                                        {% if log[4] %}
                                        <input type="checkbox" name="log_ids" value="{{ log[5] }}" onchange="updateSelectedCount()" class="row-checkbox w-4 h-4 text-rose-600 rounded border-slate-300 cursor-pointer">
                                        {% else %}
                                        <span class="text-slate-300 text-[10px]" title="Cannot delete active duty session">🔒</span>
                                        {% endif %}
                                    </td>
                                    {% endif %}
                                    <td class="py-3 px-4 font-bold">
                                        {% if is_admin %}
                                            <a href="/profile/{{ log[7] }}" class="text-blue-600 hover:text-blue-800 hover:underline flex items-center gap-1">
                                                <span>👤</span> {{ log[0] }}
                                            </a>
                                        {% else %}
                                            <span class="text-slate-800 flex items-center gap-1">
                                                <span>👤</span> {{ log[0] }}
                                            </span>
                                        {% endif %}
                                    </td>
                                    <td class="py-3 px-4 text-blue-700 font-medium">{{ log[1] if log[1] else '-' }}</td>
                                    <td class="py-3 px-4 text-slate-600">{{ log[2] if log[2] else '-' }}</td>
                                    <td class="py-3 px-4 text-emerald-600 font-semibold whitespace-nowrap">{{ log[3] }}</td>
                                    <td class="py-3 px-4 font-medium whitespace-nowrap {% if log[4] %}text-rose-600{% else %}text-amber-500 italic{% endif %}">
                                        {{ log[4] if log[4] else 'Clocked In' }}
                                    </td>
                                    <td class="py-3 px-4 text-center">
                                        {% if log[6] %}
                                            <span class="inline-block bg-blue-50 text-blue-800 border border-blue-200 font-bold px-2 py-0.5 rounded text-xs whitespace-nowrap">
                                                ⏱️ {{ log[6] }}
                                            </span>
                                        {% elif log[4] %}
                                            <span class="text-slate-400 text-xs">N/A</span>
                                        {% else %}
                                            <span class="inline-block bg-amber-50 text-amber-700 border border-amber-200 font-semibold px-2 py-0.5 rounded text-xs animate-pulse">
                                                In Progress
                                            </span>
                                        {% endif %}
                                    </td>
                                    {% if is_admin %}
                                    <td class="py-3 px-4 text-center">
                                        {% if log[4] %}
                                            <button type="button" onclick="deleteSingleLog({{ log[5] }})" class="bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 text-xs font-semibold px-2.5 py-1 rounded-lg transition-all">
                                                Delete
                                            </button>
                                        {% else %}
                                            <button type="button" disabled class="opacity-50 cursor-not-allowed bg-slate-100 text-slate-400 border border-slate-200 text-xs font-medium px-2 py-1 rounded-lg">
                                                Clocked In
                                            </button>
                                        {% endif %}
                                    </td>
                                    {% endif %}
                                </tr>
                                {% else %}
                                <tr><td colspan="{% if is_admin %}8{% else %}6{% endif %}" class="text-center py-6 text-slate-400">No attendance records found.</td></tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </form>
            </div>

            <!-- KABS VOLUNTEER MANUAL: COLLAPSIBLE ACCORDION NA MAY ARROW TOGGLE -->
            <div id="manual-section" class="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
                <!-- CLICKABLE HEADER NA MAY ARROW -->
                <button type="button" onclick="toggleManualAccordion()" class="w-full p-4 sm:p-5 bg-slate-900 hover:bg-slate-800 text-white flex justify-between items-center transition-colors text-left outline-none cursor-pointer">
                    <div>
                        <h3 class="text-sm sm:text-base font-extrabold tracking-wide flex items-center gap-2">
                            <span>📖</span> KABS YOUTH VOLUNTEERS PROGRAM MANUAL
                        </h3>
                        <p class="text-[11px] text-slate-300">Sangguniang Kabataan of Barangay Payatas, Quezon City</p>
                    </div>
                    <!-- ARROW BUTTON ICON NA UMIIKOT -->
                    <div class="flex items-center gap-2 pl-3 flex-shrink-0">
                        <span id="accordion-status-label" class="text-[11px] text-blue-300 font-semibold hidden sm:inline">Click to open</span>
                        <div id="accordion-arrow" class="w-8 h-8 rounded-full bg-slate-800 flex items-center justify-center text-slate-300 transition-transform duration-300">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M19 9l-7 7-7-7"></path>
                            </svg>
                        </div>
                    </div>
                </button>

                <!-- COLLAPSIBLE CONTENT (DEFAULT: HIDDEN) -->
                <div id="accordion-content" class="hidden border-t border-slate-200 p-6 max-h-[500px] overflow-y-auto space-y-6 text-xs sm:text-sm text-slate-700 leading-relaxed text-justify transition-all duration-300">
                    <section class="space-y-2 border-b pb-4">
                        <h4 class="font-extrabold text-slate-900 text-base">Program Rationale</h4>
                        <p>Young people are recognized as vital partners in nation-building. With their energy, creativity, and commitment to social good, youth have the capacity to become catalysts for meaningful change in their communities. According to a Gallup study reported by The Philippine Star, the Filipino youth are among the world's most dedicated volunteers despite a global decline in overall charitable behavior; 44% of Filipino adults reported volunteering in 2024, ranking the Philippines 4th highest globally in volunteerism rates. However, many young people lack structured opportunities to channel their talents and ideals into sustainable service initiatives.</p>
                        <p>According to the study entitled <i>Evaluating the National Volunteering through the Bayanihang Bayan Program</i> by Ma. Ella Oplas, volunteer work—particularly informal activities—remains largely absent from national accounting systems, limiting the visibility of its true economic and social contributions.</p>
                    </section>

                    <section class="space-y-2 border-b pb-4">
                        <h4 class="font-extrabold text-slate-900 text-base">Program Description & Objectives</h4>
                        <p>The KABS program is a youth volunteer program that seeks to strengthen the culture of volunteerism among youth in Payatas. It was institutionalized under the Barangay Payatas Comprehensive Youth Code Ordinance and SK Payatas Resolution No. 012 S. 2024 and Resolution No. 42 S. 2025.</p>
                        <ul class="list-disc pl-5 space-y-1">
                            <li>Promote active youth participation in community development and local governance.</li>
                            <li>Develop leadership, teamwork, and civic responsibility among young volunteers.</li>
                            <li>Provide structured deployment, recognition, and skill-building opportunities.</li>
                        </ul>
                    </section>

                    <section class="space-y-2 border-b pb-4">
                        <h4 class="font-extrabold text-slate-900 text-base">KABS 3 Pillars</h4>
                        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                            <div class="bg-blue-50/70 p-3 rounded-xl border border-blue-200">
                                <h5 class="font-bold text-blue-900 text-sm mb-1">⚡ Action</h5>
                                <p class="text-xs">Represents the energy and initiative of the youth to step forward and create change.</p>
                            </div>
                            <div class="bg-emerald-50/70 p-3 rounded-xl border border-emerald-200">
                                <h5 class="font-bold text-emerald-900 text-sm mb-1">🤝 Bayanihan</h5>
                                <p class="text-xs">Embodies communal unity and shared responsibility.</p>
                            </div>
                            <div class="bg-rose-50/70 p-3 rounded-xl border border-rose-200">
                                <h5 class="font-bold text-rose-900 text-sm mb-1">❤️ Service</h5>
                                <p class="text-xs">Selflessness, dedication, and accountability to uplift lives.</p>
                            </div>
                        </div>
                    </section>

                    <section class="space-y-2 border-b pb-4">
                        <h4 class="font-extrabold text-slate-900 text-base">Volunteer Committees & Deployment</h4>
                        <p class="text-xs">Volunteers are deployed across 4 specialized committees: <b>Operations</b> (Logistics, Registration, Food), <b>Production</b> (Program flow, tabulators, emcee, technical), <b>Services</b> (Venue, Crowd control, First Aid), and <b>Engagement</b> (Media, Publicity, Graphics).</p>
                    </section>

                    <section class="space-y-2 pb-2">
                        <h4 class="font-extrabold text-slate-900 text-base">Code of Conduct & Rights of Volunteers</h4>
                        <p class="text-xs">All volunteers are expected to maintain professional conduct, integrity, and mutual respect. Intoxicants, illegal drugs, and falsification of attendance records are strictly prohibited. Every volunteer is entitled to a safe deployment environment, fair treatment, and official recognition.</p>
                    </section>
                </div>
            </div>
        {% endif %}
    </main>

    <!-- KABS MANUAL MODAL -->
    <div id="manual-modal" class="fixed inset-0 bg-slate-900/80 backdrop-blur-sm z-50 hidden flex items-center justify-center p-2 sm:p-4">
        <div class="bg-white rounded-2xl max-w-4xl w-full max-h-[92vh] flex flex-col shadow-2xl border border-slate-200">
            <div class="p-4 sm:p-5 border-b border-slate-200 flex justify-between items-center bg-slate-900 text-white rounded-t-2xl">
                <div>
                    <h3 class="text-base sm:text-lg font-extrabold tracking-wide">KABS YOUTH VOLUNTEERS PROGRAM MANUAL</h3>
                    <p class="text-xs text-slate-300">Sangguniang Kabataan of Barangay Payatas, Quezon City</p>
                </div>
                <button type="button" onclick="closeManualModal()" class="text-slate-400 hover:text-white text-2xl font-bold p-1 leading-none">&times;</button>
            </div>
            
            <div id="manual-modal-scroll" onscroll="checkManualModalScroll(this)" class="p-6 overflow-y-auto space-y-6 text-xs sm:text-sm text-slate-700 leading-relaxed text-justify">
                <section class="space-y-2 border-b pb-4">
                    <h4 class="font-extrabold text-slate-900 text-base">Program Rationale</h4>
                    <p>Young people are recognized as vital partners in nation-building. With their energy, creativity, and commitment to social good, youth have the capacity to become catalysts for meaningful change in their communities. According to a Gallup study reported by The Philippine Star, the Filipino youth are among the world's most dedicated volunteers despite a global decline in overall charitable behavior; 44% of Filipino adults reported volunteering in 2024, ranking the Philippines 4th highest globally in volunteerism rates. However, many young people lack structured opportunities to channel their talents and ideals into sustainable service initiatives.</p>
                    <p>According to the study entitled <i>Evaluating the National Volunteering through the Bayanihang Bayan Program</i> by Ma. Ella Oplas, volunteer work—particularly informal activities—remains largely absent from national accounting systems, limiting the visibility of its true economic and social contributions.</p>
                </section>

                <section class="space-y-2 border-b pb-4">
                    <h4 class="font-extrabold text-slate-900 text-base">Program Description & Objectives</h4>
                    <p>The KABS program is a youth volunteer program that seeks to strengthen the culture of volunteerism among youth in Payatas. It was institutionalized under the Barangay Payatas Comprehensive Youth Code Ordinance and SK Payatas Resolution No. 012 S. 2024 and Resolution No. 42 S. 2025.</p>
                    <ul class="list-disc pl-5 space-y-1">
                        <li>Promote active youth participation in community development and local governance.</li>
                        <li>Develop leadership, teamwork, and civic responsibility among young volunteers.</li>
                        <li>Provide structured deployment, recognition, and skill-building opportunities.</li>
                    </ul>
                </section>

                <section class="space-y-2 border-b pb-4">
                    <h4 class="font-extrabold text-slate-900 text-base">KABS 3 Pillars</h4>
                    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        <div class="bg-blue-50/70 p-3 rounded-xl border border-blue-200">
                            <h5 class="font-bold text-blue-900 text-sm mb-1">⚡ Action</h5>
                            <p class="text-xs">Represents the energy and initiative of the youth to step forward and create change.</p>
                        </div>
                        <div class="bg-emerald-50/70 p-3 rounded-xl border border-emerald-200">
                            <h5 class="font-bold text-emerald-900 text-sm mb-1">🤝 Bayanihan</h5>
                            <p class="text-xs">Embodies communal unity and shared responsibility.</p>
                        </div>
                        <div class="bg-rose-50/70 p-3 rounded-xl border border-rose-200">
                            <h5 class="font-bold text-rose-900 text-sm mb-1">❤️ Service</h5>
                            <p class="text-xs">Selflessness, dedication, and accountability to uplift lives.</p>
                        </div>
                    </div>
                </section>

                <section class="space-y-2 border-b pb-4">
                    <h4 class="font-extrabold text-slate-900 text-base">Volunteer Committees & Deployment</h4>
                    <p class="text-xs">Volunteers are deployed across 4 specialized committees: <b>Operations</b> (Logistics, Registration, Food), <b>Production</b> (Program flow, tabulators, emcee, technical), <b>Services</b> (Venue, Crowd control, First Aid), and <b>Engagement</b> (Media, Publicity, Graphics).</p>
                </section>

                <section class="space-y-2 pb-2">
                    <h4 class="font-extrabold text-slate-900 text-base">Code of Conduct & Rights of Volunteers</h4>
                    <p class="text-xs">All volunteers are expected to maintain professional conduct, integrity, and mutual respect. Intoxicants, illegal drugs, and falsification of attendance records are strictly prohibited. Every volunteer is entitled to a safe deployment environment, fair treatment, and official recognition.</p>
                    <div class="mt-4 p-3 bg-emerald-50 border border-emerald-200 rounded-xl text-center">
                        <p class="text-xs font-bold text-emerald-900">You have reached the end of the KABS Volunteer Manual.</p>
                        <p class="text-[11px] text-emerald-700">You may now confirm your agreement below to unlock registration.</p>
                    </div>
                </section>
            </div>

            <div class="p-4 border-t border-slate-200 flex justify-between items-center bg-slate-50 rounded-b-2xl">
                <span id="scroll-prompt-text" class="text-xs font-semibold text-amber-700 animate-pulse">
                    ⬇️ Scroll down to the bottom to unlock...
                </span>
                <button type="button" id="agree-modal-btn" disabled onclick="acceptManualTerms()" class="py-2.5 px-6 bg-slate-400 text-white font-bold text-xs rounded-xl shadow cursor-not-allowed transition-all">
                    I Agree (Unlock Registration)
                </button>
            </div>
        </div>
    </div>

    <form id="single-delete-form" method="POST" action="" class="hidden"></form>

    <script>
        const FIVE_HOURS_MS = 5 * 60 * 60 * 1000;

        function clearLoginStorage() {
            localStorage.removeItem('kabs_volunteer_code');
            localStorage.removeItem('kabs_login_timestamp');
            sessionStorage.removeItem('kabs_auto_restore_attempted');
        }

        function handleLogoutClick() {
            clearLoginStorage();
            window.location.href = '/logout';
        }

        // --- ARROW TOGGLE PARA SA KABS MANUAL ACCORDION ---
        function toggleManualAccordion() {
            const content = document.getElementById('accordion-content');
            const arrow = document.getElementById('accordion-arrow');
            const label = document.getElementById('accordion-status-label');

            if (!content || !arrow) return;

            if (content.classList.contains('hidden')) {
                content.classList.remove('hidden');
                arrow.classList.add('rotate-180');
                if (label) label.innerText = "Click to close";
            } else {
                content.classList.add('hidden');
                arrow.classList.remove('rotate-180');
                if (label) label.innerText = "Click to open";
            }
        }

        function toggleSelectAll(masterCheckbox) {
            const checkboxes = document.querySelectorAll('.row-checkbox');
            checkboxes.forEach(cb => cb.checked = masterCheckbox.checked);
            updateSelectedCount();
        }

        function updateSelectedCount() {
            const checkedBoxes = document.querySelectorAll('.row-checkbox:checked');
            const count = checkedBoxes.length;
            const batchBtn = document.getElementById('batch-delete-btn');
            const countSpan = document.getElementById('selected-count');

            if (countSpan) countSpan.innerText = count;

            if (batchBtn) {
                if (count > 0) {
                    batchBtn.classList.remove('hidden');
                    batchBtn.classList.add('inline-flex');
                } else {
                    batchBtn.classList.add('hidden');
                    batchBtn.classList.remove('inline-flex');
                }
            }

            const allBoxes = document.querySelectorAll('.row-checkbox');
            const masterCheckbox = document.getElementById('select-all-checkbox');
            if (masterCheckbox && allBoxes.length > 0) {
                masterCheckbox.checked = (checkedBoxes.length === allBoxes.length);
            }
        }

        function submitBatchDelete() {
            const count = document.querySelectorAll('.row-checkbox:checked').length;
            if (count === 0) return;
            if (confirm(`Are you sure you want to delete ${count} selected attendance log(s)?`)) {
                document.getElementById('batch-delete-form').submit();
            }
        }

        function deleteSingleLog(logId) {
            if (confirm('Are you sure you want to delete this attendance record?')) {
                const form = document.getElementById('single-delete-form');
                form.action = `/delete-log/${logId}`;
                form.submit();
            }
        }

        let hasReadToEnd = false;

        function openManualModal() {
            const modal = document.getElementById('manual-modal');
            modal.classList.remove('hidden');
            setTimeout(() => {
                const el = document.getElementById('manual-modal-scroll');
                if (el && el.scrollHeight <= el.clientHeight + 25) {
                    unlockManualButton();
                }
            }, 200);
        }

        function closeManualModal() {
            document.getElementById('manual-modal').classList.add('hidden');
        }

        function unlockManualButton() {
            if (hasReadToEnd) return;
            hasReadToEnd = true;
            const btn = document.getElementById('agree-modal-btn');
            if (btn) {
                btn.disabled = false;
                btn.classList.remove('bg-slate-400', 'cursor-not-allowed');
                btn.classList.add('bg-emerald-600', 'hover:bg-emerald-700', 'cursor-pointer');
            }
            const prompt = document.getElementById('scroll-prompt-text');
            if (prompt) {
                prompt.innerText = "✅ You have read the complete manual!";
                prompt.classList.remove('text-amber-700', 'animate-pulse');
                prompt.classList.add('text-emerald-700');
            }
        }

        function checkManualModalScroll(element) {
            if (element.scrollHeight - element.scrollTop <= element.clientHeight + 40) {
                unlockManualButton();
            }
        }

        function acceptManualTerms() {
            if (!hasReadToEnd) return;
            const checkbox = document.getElementById('agree_terms');
            if (checkbox) {
                checkbox.disabled = false;
                checkbox.checked = true;
            }
            const label = document.getElementById('agree_label');
            if (label) {
                label.innerHTML = "✅ <b>I have read to the end</b> and agree to all rules and policies of the KABS Volunteer Manual.";
                label.classList.remove('text-slate-500');
                label.classList.add('text-slate-800');
            }
            const submitBtn = document.getElementById('register_submit_btn');
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.classList.remove('bg-slate-400', 'cursor-not-allowed');
                submitBtn.classList.add('bg-slate-900', 'hover:bg-slate-800', 'cursor-pointer');
            }
            closeManualModal();
        }

        {% if user %}
        localStorage.setItem('kabs_volunteer_code', '{{ user.get("volunteer_code") }}');
        if (!localStorage.getItem('kabs_login_timestamp')) {
            localStorage.setItem('kabs_login_timestamp', Date.now().toString());
        }
        {% else %}
        window.addEventListener("DOMContentLoaded", () => {
            const savedCode = localStorage.getItem('kabs_volunteer_code');
            const loginTimestamp = localStorage.getItem('kabs_login_timestamp');

            if (savedCode && loginTimestamp) {
                const elapsed = Date.now() - parseInt(loginTimestamp, 10);
                if (elapsed > FIVE_HOURS_MS) {
                    clearLoginStorage();
                    return;
                }

                if (!sessionStorage.getItem('kabs_auto_restore_attempted')) {
                    sessionStorage.setItem('kabs_auto_restore_attempted', '1');
                    fetch('/login-qr-api', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        credentials: 'same-origin',
                        body: JSON.stringify({ qr_payload: savedCode })
                    })
                    .then(res => res.json())
                    .then(data => {
                        if (data.success) {
                            window.location.replace('/');
                        }
                    })
                    .catch(() => {});
                }
            } else if (loginTimestamp && Date.now() - parseInt(loginTimestamp, 10) > FIVE_HOURS_MS) {
                clearLoginStorage();
            }
        });
        {% endif %}

        function startLiveDutyTimer() {
            const timeInElem = document.getElementById('session-time-in');
            const timerElem = document.getElementById('live-timer');
            if (!timeInElem || !timerElem) return;

            const timeInText = timeInElem.innerText.trim();
            function parsePSTDate(str) {
                const parts = str.split(' ');
                if (parts.length < 3) return new Date(str);
                const [dPart, tPart, ampm] = parts;
                const [year, month, day] = dPart.split('-').map(Number);
                let [hours, minutes, seconds] = tPart.split(':').map(Number);
                if (ampm.toUpperCase() === 'PM' && hours < 12) hours += 12;
                if (ampm.toUpperCase() === 'AM' && hours === 12) hours = 0;
                return new Date(year, month - 1, day, hours, minutes, seconds);
            }

            const startTime = parsePSTDate(timeInText).getTime();

            function updateTimer() {
                const now = new Date().getTime();
                let diffSec = Math.floor((now - startTime) / 1000);
                if (diffSec < 0) diffSec = 0;

                const hrs = String(Math.floor(diffSec / 3600)).padStart(2, '0');
                const mins = String(Math.floor((diffSec % 3600) / 60)).padStart(2, '0');
                const secs = String(diffSec % 60).padStart(2, '0');

                timerElem.innerText = `${hrs}:${mins}:${secs}`;
            }

            updateTimer();
            setInterval(updateTimer, 1000);
        }

        let currentSystemState = null;
        let isSyncPaused = false;

        function startMultiDeviceSynchronizer() {
            setInterval(() => {
                if (isSyncPaused) return;

                fetch('/sync-state')
                    .then(res => res.json())
                    .then(data => {
                        {% if user %}
                        if (!data.logged_in) {
                            clearLoginStorage();
                            window.location.replace('/');
                            return;
                        }
                        {% endif %}

                        if (currentSystemState === null) {
                            currentSystemState = data.state_hash;
                        } else if (currentSystemState !== data.state_hash) {
                            window.location.reload();
                        }
                    })
                    .catch(() => {});
            }, 3000);
        }

        window.addEventListener("DOMContentLoaded", () => {
            startLiveDutyTimer();
            startMultiDeviceSynchronizer();
            {% if not user %}
            startScanner();
            {% endif %}
        });

        {% if not user %}
        let html5QrCode = null;
        let isProcessingScan = false;

        function onScanSuccess(decodedText) {
            if (isProcessingScan) return;
            isProcessingScan = true;

            const scanStatus = document.getElementById('scan-status');
            if (scanStatus) {
                scanStatus.innerHTML = "<span class='text-emerald-600 font-bold animate-pulse'>✅ QR Detected! Logging in...</span>";
            }

            if (html5QrCode) {
                html5QrCode.stop().catch(() => {}).finally(() => {
                    fetch('/login-qr-api', {
                        method: 'POST',
                        headers: { 
                            'Content-Type': 'application/json',
                            'Accept': 'application/json'
                        },
                        credentials: 'same-origin',
                        body: JSON.stringify({ qr_payload: decodedText })
                    })
                    .then(res => res.json())
                    .then(data => {
                        if (data.success) {
                            localStorage.setItem('kabs_login_timestamp', Date.now().toString());
                            window.location.replace('/');
                        } else {
                            alert("❌ QR Pass not recognized. Please try again.");
                            isProcessingScan = false;
                            window.location.reload();
                        }
                    })
                    .catch(() => {
                        alert("Network connection error during QR login.");
                        isProcessingScan = false;
                        window.location.reload();
                    });
                });
            }
        }

        function startScanner() {
            html5QrCode = new Html5Qrcode("reader");
            const config = { fps: 10, qrbox: { width: 220, height: 220 }, aspectRatio: 1.0 };
            html5QrCode.start({ facingMode: "environment" }, config, onScanSuccess)
                .then(() => {
                    document.getElementById('scan-status').innerText = "📷 Camera active. Point at your QR Pass.";
                })
                .catch(err => {
                    document.getElementById('scan-status').innerHTML = 
                        "<span class='text-rose-500 font-semibold'>⚠️ Please allow camera access or type your volunteer code.</span>";
                });
        }

        function toggleLoginMode() {
            const qrSection = document.getElementById('qr-login-section');
            const formSection = document.getElementById('form-login-section');
            const btn = document.getElementById('toggle-btn');
            const desc = document.getElementById('login-desc');

            if (qrSection.classList.contains('hidden')) {
                qrSection.classList.remove('hidden');
                formSection.classList.add('hidden');
                btn.innerText = "Use Credentials Instead";
                desc.innerText = "Point your QR Pass camera scanner to auto-login.";
                startScanner();
            } else {
                if (html5QrCode && html5QrCode.isScanning) {
                    html5QrCode.stop().catch(() => {});
                }
                qrSection.classList.add('hidden');
                formSection.classList.remove('hidden');
                btn.innerText = "Use QR Scanner Instead";
                desc.innerText = "Already registered? Log in with your email and contact number.";
            }
        }
        {% endif %}
    </script>
</body>
</html>
"""

PROFILE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0">
    <title>KABS Profile | {{ profile_user.get('name') }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.5.13/cropper.min.css"/>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.5.13/cropper.min.js"></script>
</head>
<body class="bg-slate-50 text-slate-800 antialiased min-h-screen pb-12">
    <header class="bg-slate-900 border-b border-slate-800 sticky top-0 z-30 shadow-md">
        <div class="max-w-4xl mx-auto px-4 py-3 flex justify-between items-center">
            <a href="/" class="flex items-center space-x-2 text-white hover:text-blue-300 transition-all">
                <span>←</span>
                <span class="font-bold text-xs sm:text-sm">Back to Dashboard</span>
            </a>
            <button type="button" onclick="handleLogoutClick();" class="bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 text-xs font-semibold px-2.5 py-1.5 rounded-lg transition-all">Log Out</button>
        </div>
    </header>

    <main class="max-w-2xl mx-auto px-4 pt-6 space-y-6">
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

        <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm space-y-6">
            <div class="flex items-center justify-between border-b pb-4 flex-wrap gap-2">
                <div>
                    <h2 class="text-base sm:text-lg font-extrabold text-slate-900">KABS Official Profile</h2>
                    <p class="text-xs text-slate-500">Official volunteer credentials, standing, and info.</p>
                </div>
                
                <div class="flex items-center gap-2">
                    <!-- 1. CLASSIFICATION -->
                    {% if is_admin %}
                    <div class="relative inline-block text-left">
                        <button type="button" id="type-btn-main" onclick="document.getElementById('type-dropdown-menu').classList.toggle('hidden')" class="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold border transition-all shadow-sm
                            {% if profile_user.get('volunteer_type') == 'Core' %}
                                bg-purple-50 text-purple-700 border-purple-200 hover:bg-purple-100
                            {% else %}
                                bg-sky-50 text-sky-700 border-sky-200 hover:bg-sky-100
                            {% endif %}">
                            <span id="current-classification-text">
                                {% if profile_user.get('volunteer_type') == 'Core' %}
                                    ⭐ CORE VOLUNTEER
                                {% else %}
                                    🤝 AUXILIARY VOLUNTEER
                                {% endif %}
                            </span>
                            <svg class="w-3.5 h-3.5 ml-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                        </button>
                        <div id="type-dropdown-menu" class="hidden absolute right-0 mt-2 w-48 bg-white border border-slate-200 rounded-xl shadow-lg z-50 py-1.5">
                            <button type="button" onclick="updateClassificationFast({{ profile_user.get('id') }}, 'Core')" class="w-full text-left px-3 py-2 text-xs font-semibold hover:bg-purple-50 text-purple-700 flex items-center gap-2">
                                <span>⭐</span> Core Volunteer
                            </button>
                            <button type="button" onclick="updateClassificationFast({{ profile_user.get('id') }}, 'Auxiliary')" class="w-full text-left px-3 py-2 text-xs font-semibold hover:bg-sky-50 text-sky-700 flex items-center gap-2">
                                <span>🤝</span> Auxiliary Volunteer
                            </button>
                        </div>
                    </div>
                    {% else %}
                    <div class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border
                        {% if profile_user.get('volunteer_type') == 'Core' %}
                            bg-purple-50 text-purple-700 border-purple-200
                        {% else %}
                            bg-sky-50 text-sky-700 border-sky-200
                        {% endif %}">
                        {% if profile_user.get('volunteer_type') == 'Core' %}
                            ⭐ CORE VOLUNTEER
                        {% else %}
                            🤝 AUXILIARY VOLUNTEER
                        {% endif %}
                    </div>
                    {% endif %}

                    <!-- 2. STATUS -->
                    {% if is_admin %}
                    <div class="relative inline-block text-left">
                        <button type="button" id="status-btn-main" onclick="document.getElementById('status-dropdown-menu').classList.toggle('hidden')" class="inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold border transition-all shadow-sm
                            {% if profile_user.get('volunteer_status') == 'Active' %}
                                bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100
                            {% else %}
                                bg-rose-50 text-rose-700 border-rose-200 hover:bg-rose-100
                            {% endif %}">
                            <span id="current-status-text">
                                {% if profile_user.get('volunteer_status') == 'Active' %}
                                    🟢 ACTIVE
                                {% else %}
                                    🔴 INACTIVE
                                {% endif %}
                            </span>
                            <svg class="w-3.5 h-3.5 ml-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"></path></svg>
                        </button>
                        <div id="status-dropdown-menu" class="hidden absolute right-0 mt-2 w-48 bg-white border border-slate-200 rounded-xl shadow-lg z-50 py-1.5">
                            <button type="button" onclick="updateVolunteerStatusFast({{ profile_user.get('id') }}, 'Active')" class="w-full text-left px-3 py-2 text-xs font-semibold hover:bg-emerald-50 text-emerald-700 flex items-center gap-2">
                                <span>🟢</span> Active Volunteer
                            </button>
                            <button type="button" onclick="updateVolunteerStatusFast({{ profile_user.get('id') }}, 'Inactive')" class="w-full text-left px-3 py-2 text-xs font-semibold hover:bg-rose-50 text-rose-700 flex items-center gap-2">
                                <span>🔴</span> Inactive Volunteer
                            </button>
                        </div>
                    </div>
                    {% else %}
                    <div class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-bold border
                        {% if profile_user.get('volunteer_status') == 'Active' %}
                            bg-emerald-50 text-emerald-700 border-emerald-200
                        {% else %}
                            bg-rose-50 text-rose-700 border-rose-200
                        {% endif %}">
                        {% if profile_user.get('volunteer_status') == 'Active' %}
                            🟢 ACTIVE
                        {% else %}
                            🔴 INACTIVE
                        {% endif %}
                    </div>
                    {% endif %}
                </div>
            </div>

            <!-- PROFILE PHOTO SECTION -->
            <div class="bg-slate-50 border border-slate-200 rounded-xl p-5 text-center space-y-3">
                <div class="relative w-28 h-28 mx-auto">
                    {% if profile_user.get('profile_pic') %}
                        <img src="{{ profile_user.get('profile_pic') }}" alt="Profile" class="w-28 h-28 rounded-full object-cover border-4 border-white shadow-md mx-auto">
                    {% else %}
                        <div class="w-28 h-28 rounded-full bg-slate-200 border-2 border-dashed border-slate-300 flex items-center justify-center text-slate-400 text-3xl mx-auto">
                            👤
                        </div>
                    {% endif %}
                </div>

                {% if is_owner %}
                <div class="flex justify-center gap-2">
                    <label class="py-2 px-3 bg-white hover:bg-slate-100 text-slate-700 text-xs font-semibold rounded-lg border border-slate-300 cursor-pointer transition-all flex items-center gap-1.5 shadow-sm">
                        <span>📁</span> Choose Photo
                        <input type="file" id="choose-photo-input" accept="image/*" class="hidden" onchange="handleFileSelect(event)">
                    </label>

                    <button type="button" onclick="openSelfieModal()" class="py-2 px-3 bg-blue-50 hover:bg-blue-100 text-blue-700 text-xs font-semibold rounded-lg border border-blue-200 transition-all flex items-center gap-1.5 shadow-sm">
                        <span>📸</span> Take Selfie
                    </button>
                </div>
                {% else %}
                <p class="text-[11px] text-slate-400 italic">Only the account owner is authorized to change this photo.</p>
                {% endif %}
            </div>

            <!-- EDIT INFORMATION FORM -->
            <form action="/edit-profile/{{ profile_user.get('id') }}" method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Full Name</label>
                    <input type="text" name="name" maxlength="50" required value="{{ profile_user.get('name') }}" {% if not is_owner %}disabled{% endif %} class="w-full text-sm py-2.5 px-3 border border-slate-300 rounded-lg {% if is_owner %}focus:ring-2 focus:ring-blue-600 outline-none{% else %}bg-slate-100 text-slate-600 cursor-not-allowed{% endif %}">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Email Address (Registered & Non-editable)</label>
                    <input type="email" value="{{ profile_user.get('email') }}" disabled class="w-full text-sm py-2.5 px-3 border border-slate-200 bg-slate-100 text-slate-500 rounded-lg outline-none cursor-not-allowed">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Contact Number (11 digits, numbers only)</label>
                    <input type="tel" name="contact" maxlength="11" minlength="11" pattern="09[0-9]{9}" inputmode="numeric" oninput="this.value = this.value.replace(/[^0-9]/g, '')" required value="{{ profile_user.get('contact') }}" {% if not is_owner %}disabled{% endif %} class="w-full text-sm py-2.5 px-3 border border-slate-300 rounded-lg {% if is_owner %}focus:ring-2 focus:ring-blue-600 outline-none{% else %}bg-slate-100 text-slate-600 cursor-not-allowed{% endif %}">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Volunteer Pass Code</label>
                    <input type="text" value="{{ profile_user.get('volunteer_code') }}" disabled class="w-full font-mono text-sm py-2.5 px-3 border border-slate-200 bg-slate-100 text-blue-600 font-bold rounded-lg outline-none cursor-not-allowed">
                </div>

                {% if is_owner %}
                <div class="pt-4 border-t flex justify-end">
                    <button type="submit" class="py-2.5 px-6 bg-slate-900 hover:bg-slate-800 text-white text-xs font-bold rounded-xl shadow transition-all">
                        Save Profile Changes
                    </button>
                </div>
                {% endif %}
            </form>

            <!-- DANGER ZONE -->
            {% if is_admin %}
            <div class="pt-6 border-t border-rose-200">
                <div class="bg-rose-50 border border-rose-200 rounded-xl p-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
                    <div>
                        <h4 class="text-xs font-bold text-rose-900">Danger Zone: Admin Management</h4>
                        <p class="text-[11px] text-rose-700">Permanently delete this volunteer account along with associated attendance records.</p>
                    </div>
                    <form action="/delete-volunteer-account/{{ profile_user.get('id') }}" method="POST" onsubmit="return confirm('ARE YOU SURE? This will permanently delete {{ profile_user.get('name') }} and cannot be undone.');">
                        <button type="submit" class="bg-rose-600 hover:bg-rose-700 text-white font-bold text-xs px-4 py-2 rounded-lg shadow transition-all whitespace-nowrap">
                            🗑️ Delete Account
                        </button>
                    </form>
                </div>
            </div>
            {% endif %}
        </div>
    </main>

    <div id="cropper-modal" class="fixed inset-0 bg-slate-900/85 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl max-w-lg w-full p-5 shadow-2xl border border-slate-200 space-y-4">
            <div class="flex justify-between items-center border-b pb-2">
                <h3 class="font-bold text-slate-900 text-sm">✂️ Crop Profile Photo</h3>
                <button type="button" onclick="closeCropperModal()" class="text-slate-400 hover:text-slate-600 text-xl font-bold">&times;</button>
            </div>
            
            <div class="max-h-[55vh] overflow-hidden bg-slate-900 rounded-xl flex items-center justify-center">
                <img id="image-to-crop" src="" class="max-w-full block">
            </div>

            <div class="flex justify-between items-center pt-2">
                <span class="text-xs text-slate-500">Scale image to fit within frame.</span>
                <div class="flex gap-2">
                    <button type="button" onclick="closeCropperModal()" class="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-lg">Cancel</button>
                    <button type="button" id="crop-done-btn" onclick="applyCropAndSave({{ profile_user.get('id') }})" class="px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold rounded-lg shadow-sm transition-all flex items-center gap-1.5">
                        <span>✓</span> Done
                    </button>
                </div>
            </div>
        </div>
    </div>

    <div id="selfie-modal" class="fixed inset-0 bg-slate-900/75 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl max-w-md w-full p-5 shadow-2xl border border-slate-200 space-y-4">
            <div class="flex justify-between items-center border-b pb-2">
                <h3 class="font-bold text-slate-900 text-sm">📸 Take a Selfie</h3>
                <button type="button" onclick="closeSelfieModal()" class="text-slate-400 hover:text-slate-600 text-xl font-bold">&times;</button>
            </div>
            <div class="relative rounded-xl overflow-hidden bg-black aspect-square flex items-center justify-center">
                <video id="selfie-video" autoplay playsinline class="w-full h-full object-cover"></video>
                <canvas id="selfie-canvas" class="hidden"></canvas>
            </div>
            <div class="flex justify-end gap-2 pt-2">
                <button type="button" onclick="closeSelfieModal()" class="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-lg">Cancel</button>
                <button type="button" onclick="captureSelfieToCrop()" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg shadow-sm">Capture & Crop</button>
            </div>
        </div>
    </div>

    <script>
        const FIVE_HOURS_MS = 5 * 60 * 60 * 1000;

        function clearLoginStorage() {
            localStorage.removeItem('kabs_volunteer_code');
            localStorage.removeItem('kabs_login_timestamp');
            sessionStorage.removeItem('kabs_auto_restore_attempted');
        }

        function handleLogoutClick() {
            clearLoginStorage();
            window.location.href = '/logout';
        }

        let isSyncPaused = false;
        function pauseSyncTemporarily() {
            isSyncPaused = true;
            setTimeout(() => { isSyncPaused = false; }, 4000);
        }

        {% if is_admin %}
        function updateClassificationFast(targetUserId, newType) {
            pauseSyncTemporarily();
            document.getElementById('type-dropdown-menu').classList.add('hidden');
            const txt = document.getElementById('current-classification-text');
            const btn = document.getElementById('type-btn-main');

            if (txt) {
                txt.innerText = newType === 'Core' ? '⭐ CORE VOLUNTEER' : '🤝 AUXILIARY VOLUNTEER';
            }
            if (btn) {
                btn.className = newType === 'Core'
                    ? "inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold border transition-all shadow-sm bg-purple-50 text-purple-700 border-purple-200 hover:bg-purple-100"
                    : "inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold border transition-all shadow-sm bg-sky-50 text-sky-700 border-sky-200 hover:bg-sky-100";
            }

            fetch('/update-volunteer-classification', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: targetUserId, volunteer_type: newType })
            })
            .then(res => res.json())
            .then(data => {
                if (data.state_hash) {
                    currentProfileState = data.state_hash;
                }
            })
            .catch(() => {});
        }

        function updateVolunteerStatusFast(targetUserId, newStatus) {
            pauseSyncTemporarily();
            document.getElementById('status-dropdown-menu').classList.add('hidden');
            const txt = document.getElementById('current-status-text');
            const btn = document.getElementById('status-btn-main');

            if (txt) {
                txt.innerText = newStatus === 'Active' ? '🟢 ACTIVE' : '🔴 INACTIVE';
            }
            if (btn) {
                btn.className = newStatus === 'Active'
                    ? "inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold border transition-all shadow-sm bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100"
                    : "inline-flex items-center gap-1 px-3 py-1 rounded-full text-xs font-bold border transition-all shadow-sm bg-rose-50 text-rose-700 border-rose-200 hover:bg-rose-100";
            }

            fetch('/update-volunteer-status', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: targetUserId, status: newStatus })
            })
            .then(res => res.json())
            .then(data => {
                if (data.state_hash) {
                    currentProfileState = data.state_hash;
                }
            })
            .catch(() => {});
        }
        {% endif %}

        let cropper = null;

        function openCropperWithImage(imgUrl) {
            const modal = document.getElementById('cropper-modal');
            const imgElement = document.getElementById('image-to-crop');
            imgElement.src = imgUrl;
            modal.classList.remove('hidden');

            if (cropper) {
                cropper.destroy();
            }

            setTimeout(() => {
                cropper = new Cropper(imgElement, {
                    aspectRatio: 1,
                    viewMode: 1,
                    autoCropArea: 0.85,
                    responsive: true,
                });
            }, 100);
        }

        function closeCropperModal() {
            const modal = document.getElementById('cropper-modal');
            modal.classList.add('hidden');
            if (cropper) {
                cropper.destroy();
                cropper = null;
            }
            const input = document.getElementById('choose-photo-input');
            if (input) input.value = '';
        }

        function handleFileSelect(e) {
            const file = e.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = function(event) {
                    openCropperWithImage(event.target.result);
                };
                reader.readAsDataURL(file);
            }
        }

        function applyCropAndSave(targetUserId) {
            if (!cropper) return;
            const btn = document.getElementById('crop-done-btn');
            btn.innerText = "⏳ Saving...";
            btn.disabled = true;

            const croppedCanvas = cropper.getCroppedCanvas({ width: 256, height: 256 });
            const base64Data = croppedCanvas.toDataURL('image/jpeg', 0.85);

            fetch('/save-cropped-profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: targetUserId, image_data: base64Data })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    location.reload();
                } else {
                    alert(data.message || "Failed to save profile picture.");
                    btn.innerText = "Done";
                    btn.disabled = false;
                }
            })
            .catch(() => {
                alert("Network error saving photo.");
                btn.innerText = "Done";
                btn.disabled = false;
            });
        }

        let selfieStream = null;

        function openSelfieModal() {
            const modal = document.getElementById('selfie-modal');
            modal.classList.remove('hidden');
            const video = document.getElementById('selfie-video');

            navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false })
                .then(stream => {
                    selfieStream = stream;
                    video.srcObject = stream;
                })
                .catch(err => {
                    alert("Unable to open camera. Please check your browser permissions.");
                    closeSelfieModal();
                });
        }

        function closeSelfieModal() {
            const modal = document.getElementById('selfie-modal');
            modal.classList.add('hidden');
            if (selfieStream) {
                selfieStream.getTracks().forEach(track => track.stop());
                selfieStream = null;
            }
        }

        function captureSelfieToCrop() {
            const video = document.getElementById('selfie-video');
            const canvas = document.getElementById('selfie-canvas');
            canvas.width = video.videoWidth || 480;
            canvas.height = video.videoHeight || 480;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
            
            const dataUrl = canvas.toDataURL('image/jpeg');
            closeSelfieModal();
            openCropperWithImage(dataUrl);
        }

        let currentProfileState = null;
        setInterval(() => {
            if (isSyncPaused) return;

            fetch('/sync-state')
                .then(res => res.json())
                .then(data => {
                    if (!data.logged_in) {
                        clearLoginStorage();
                        window.location.replace('/');
                        return;
                    }
                    if (currentProfileState === null) {
                        currentProfileState = data.state_hash;
                    } else if (currentProfileState !== data.state_hash) {
                        window.location.reload();
                    }
                })
                .catch(() => {});
        }, 3000);
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    session_user = session.get("user")
    user = None
    active_record = None
    sort_by = request.args.get("sort_by", "latest")

    if session_user:
        user = get_fresh_user_profile(session_user["id"])
        if not user:
            session.clear()

    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"

    if user:
        cursor.execute(
            f"SELECT id, agenda, task, time_in FROM attendance WHERE volunteer_id = {ph} AND time_out IS NULL ORDER BY id DESC LIMIT 1",
            (user["id"],),
        )
        active_record = cursor.fetchone()

    order_clause = "ORDER BY attendance.id DESC"
    if sort_by == "name_asc":
        order_clause = "ORDER BY volunteers.name ASC, attendance.id DESC"
    elif sort_by == "name_desc":
        order_clause = "ORDER BY volunteers.name DESC, attendance.id DESC"

    cursor.execute(
        f"""
        SELECT volunteers.name, attendance.agenda, attendance.task, attendance.time_in, attendance.time_out, attendance.id, attendance.total_hours, attendance.volunteer_id
        FROM attendance
        JOIN volunteers ON attendance.volunteer_id = volunteers.id
        {order_clause}
        LIMIT 100
        """
    )
    raw_logs = cursor.fetchall()
    cursor.close()
    conn.close()

    if sort_by == "hours_desc":
        logs = sorted(raw_logs, key=lambda x: parse_duration_to_minutes(x[6]), reverse=True)
    else:
        logs = raw_logs

    is_admin = is_admin_user(user)

    if os.path.exists(os.path.join("templates", "index.html")):
        return render_template(
            "index.html", user=user, logs=logs, active_record=active_record, is_admin=is_admin, current_sort=sort_by
        )

    return render_template_string(
        MAIN_TEMPLATE, user=user, logs=logs, active_record=active_record, is_admin=is_admin, current_sort=sort_by
    )


@app.route("/qr-code/<volunteer_code>")
def generate_qr_code(volunteer_code):
    clean_code = str(volunteer_code).strip().upper()
    img = qrcode.make(clean_code)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/sync-state")
def sync_state():
    session_user = session.get("user")
    if not session_user:
        state_hash = get_current_system_state_hash(None)
        return jsonify({"logged_in": False, "state_hash": state_hash})

    state_hash = get_current_system_state_hash(session_user["id"])
    return jsonify({"logged_in": True, "state_hash": state_hash})


@app.route("/qr-auth/<token>")
def qr_direct_auth(token):
    user = authenticate_user_by_qr(token)
    if user:
        session.permanent = True
        session["user"] = {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "volunteer_code": user[5] if len(user) > 5 else "N/A",
        }
        session.modified = True
        flash(f"✅ Welcome back, {user[1]}! (Logged in via QR Pass)", "success")
    else:
        flash("❌ Invalid or expired QR Pass.", "danger")
    return redirect(url_for("index"))


@app.route("/profile")
def profile_self():
    session_user = session.get("user")
    if not session_user:
        flash("Please log in first to view your profile.", "danger")
        return redirect(url_for("index"))

    user = get_fresh_user_profile(session_user["id"])
    if not user:
        session.clear()
        return redirect(url_for("index"))

    is_admin = is_admin_user(user)
    return render_template_string(
        PROFILE_TEMPLATE, profile_user=user, session_user=user, is_admin=is_admin, is_owner=True
    )


@app.route("/profile/<int:user_id>")
def profile_by_id(user_id):
    session_user = session.get("user")
    if not session_user:
        flash("Please log in first.", "danger")
        return redirect(url_for("index"))

    current_user = get_fresh_user_profile(session_user["id"])
    if not is_admin_user(current_user) and session_user["id"] != user_id:
        flash("🔒 Unauthorized to view other volunteer profiles.", "warning")
        return redirect(url_for("index"))

    target_user = get_fresh_user_profile(user_id)
    if not target_user:
        flash("Volunteer profile not found.", "danger")
        return redirect(url_for("index"))

    is_admin = is_admin_user(current_user)
    is_owner = (session_user["id"] == user_id)

    return render_template_string(
        PROFILE_TEMPLATE, profile_user=target_user, session_user=current_user, is_admin=is_admin, is_owner=is_owner
    )


@app.route("/update-volunteer-classification", methods=["POST"])
def update_volunteer_classification():
    session_user = session.get("user")
    if not session_user:
        return jsonify({"success": False, "message": "Authentication required."})

    current_user = get_fresh_user_profile(session_user["id"])
    if not is_admin_user(current_user):
        return jsonify({"success": False, "message": "Unauthorized action."})

    data = request.json or {}
    target_user_id = data.get("user_id")
    new_type = data.get("volunteer_type")
    if new_type not in ["Core", "Auxiliary"]:
        return jsonify({"success": False, "message": "Invalid volunteer type."})

    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"
    cursor.execute(
        f"UPDATE volunteers SET volunteer_type = {ph} WHERE id = {ph}",
        (new_type, target_user_id),
    )
    conn.commit()
    cursor.close()
    conn.close()

    if session_user["id"] == target_user_id:
        session["user"]["volunteer_type"] = new_type
        session.modified = True

    new_hash = get_current_system_state_hash(session_user["id"])
    return jsonify({"success": True, "state_hash": new_hash})


@app.route("/update-volunteer-status", methods=["POST"])
def update_volunteer_status():
    session_user = session.get("user")
    if not session_user:
        return jsonify({"success": False, "message": "Authentication required."})

    current_user = get_fresh_user_profile(session_user["id"])
    if not is_admin_user(current_user):
        return jsonify({"success": False, "message": "Unauthorized action."})

    data = request.json or {}
    target_user_id = data.get("user_id") or session_user["id"]
    new_status = data.get("status")
    if new_status not in ["Active", "Inactive"]:
        return jsonify({"success": False, "message": "Invalid status value."})

    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"
    cursor.execute(
        f"UPDATE volunteers SET volunteer_status = {ph} WHERE id = {ph}",
        (new_status, target_user_id),
    )
    conn.commit()
    cursor.close()
    conn.close()

    if session_user["id"] == target_user_id:
        session["user"]["volunteer_status"] = new_status
        session.modified = True

    new_hash = get_current_system_state_hash(session_user["id"])
    return jsonify({"success": True, "state_hash": new_hash})


@app.route("/delete-volunteer-account/<int:user_id>", methods=["POST"])
def delete_volunteer_account(user_id):
    session_user = session.get("user")
    if not session_user:
        flash("Please log in first.", "danger")
        return redirect(url_for("index"))

    current_user = get_fresh_user_profile(session_user["id"])
    if not is_admin_user(current_user):
        flash("❌ Only SK Admins are authorized to delete volunteer accounts.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"

    cursor.execute(f"SELECT name FROM volunteers WHERE id = {ph}", (user_id,))
    target = cursor.fetchone()

    if target:
        v_name = target[0]
        cursor.execute(f"DELETE FROM attendance WHERE volunteer_id = {ph}", (user_id,))
        cursor.execute(f"DELETE FROM volunteers WHERE id = {ph}", (user_id,))
        conn.commit()
        flash(f"🗑️ Successfully deleted volunteer account for {v_name}.", "success")
    else:
        flash("Volunteer account not found.", "danger")

    cursor.close()
    conn.close()

    if session_user["id"] == user_id:
        session.clear()
        return redirect(url_for("index"))

    return redirect(url_for("index"))


@app.route("/edit-profile/<int:user_id>", methods=["POST"])
def edit_profile(user_id):
    session_user = session.get("user")
    if not session_user:
        flash("Please log in to edit profile information.", "danger")
        return redirect(url_for("index"))

    if session_user["id"] != user_id:
        flash("❌ You are only permitted to edit your own account information.", "danger")
        return redirect(url_for("profile_by_id", user_id=user_id))

    name = request.form.get("name", "").strip()
    contact = request.form.get("contact", "").strip()

    if len(name) > 50 or len(name) < 2:
        flash("❌ Full Name must be between 2 and 50 characters.", "danger")
        return redirect(url_for("profile_by_id", user_id=user_id))

    if not contact.isdigit() or len(contact) != 11 or not contact.startswith("09"):
        flash("❌ Contact number must be an 11-digit mobile number starting with '09'.", "danger")
        return redirect(url_for("profile_by_id", user_id=user_id))

    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"
    cursor.execute(
        f"UPDATE volunteers SET name = {ph}, contact = {ph} WHERE id = {ph}",
        (name, contact, user_id),
    )
    conn.commit()
    cursor.close()
    conn.close()

    session["user"]["name"] = name
    session.modified = True

    flash("✅ Successfully updated profile details!", "success")
    return redirect(url_for("profile_by_id", user_id=user_id))


@app.route("/save-cropped-profile", methods=["POST"])
def save_cropped_profile():
    session_user = session.get("user")
    if not session_user:
        return jsonify({"success": False, "message": "Authentication required."})

    data = request.json or {}
    target_user_id = data.get("user_id") or session_user["id"]

    if session_user["id"] != target_user_id:
        return jsonify({"success": False, "message": "❌ Only the account owner may change this profile picture."})

    image_data = data.get("image_data")
    if not image_data or not image_data.startswith("data:image"):
        return jsonify({"success": False, "message": "No valid cropped image received."})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        ph = "%s" if USE_POSTGRES else "?"
        cursor.execute(
            f"UPDATE volunteers SET profile_pic = {ph} WHERE id = {ph}",
            (image_data, target_user_id),
        )
        conn.commit()
        cursor.close()
        conn.close()

        flash("✅ Profile picture saved successfully!", "success")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/export-attendance")
def export_attendance():
    session_user = session.get("user")
    if not session_user:
        flash("Please log in to export attendance logs.", "danger")
        return redirect(url_for("index"))

    current_user = get_fresh_user_profile(session_user["id"])
    if not is_admin_user(current_user):
        flash("❌ Only SK Admins are authorized to export the attendance records.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT volunteers.volunteer_code, volunteers.name, volunteers.email, volunteers.contact,
               attendance.agenda, attendance.task, attendance.time_in, attendance.time_out, attendance.total_hours
        FROM attendance
        JOIN volunteers ON attendance.volunteer_id = volunteers.id
        ORDER BY attendance.id DESC
        """
    )
    records = cursor.fetchall()
    cursor.close()
    conn.close()

    if not records or len(records) == 0:
        flash("❌ No attendance records available for export.", "warning")
        return redirect(url_for("index"))

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "Volunteer Code",
        "Volunteer Name",
        "Email Address",
        "Contact Number",
        "Agenda / Event",
        "Assigned Task",
        "Time In (PST)",
        "Time Out (PST)",
        "Total Hours",
    ])

    for row in records:
        writer.writerow([
            row[0],
            row[1],
            row[2],
            f"'{row[3]}'",
            row[4] if row[4] else "-",
            row[5] if row[5] else "-",
            row[6],
            row[7] if row[7] else "Clocked In (Active)",
            row[8] if row[8] else ("In Progress" if not row[7] else "-"),
        ])

    csv_data = "\ufeff" + output.getvalue()
    filename = f"kabs_attendance_{get_ph_now().strftime('%Y%m%d_%H%M%S')}.csv"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/login-qr-api", methods=["POST"])
def login_qr_api():
    data = request.json or {}
    payload = data.get("qr_payload", "")
    user = authenticate_user_by_qr(payload)

    if user:
        session.permanent = True
        session["user"] = {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "volunteer_code": user[5] if len(user) > 5 else "N/A",
        }
        session.modified = True
        flash(f"✅ Welcome back, {user[1]}! (Logged in via QR Pass)", "success")
        return jsonify({"success": True, "volunteer_code": user[5]})

    return jsonify(
        {"success": False, "message": "❌ Invalid or unrecognized QR code."}
    )


@app.route("/login-code", methods=["POST"])
def login_code():
    code = request.form.get("volunteer_code", "").strip()
    user = authenticate_user_by_qr(code)

    if user:
        session.permanent = True
        session["user"] = {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "volunteer_code": user[5] if len(user) > 5 else "N/A",
        }
        session.modified = True
        flash(f"✅ Welcome back, {user[1]}!", "success")
    else:
        flash("❌ Invalid volunteer code.", "danger")

    return redirect(url_for("index"))


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()
    contact = request.form.get("contact", "").strip()

    if not email.endswith("@gmail.com"):
        flash("❌ Email must be a valid @gmail.com address.", "danger")
        return redirect(url_for("index"))

    if (
        not contact.isdigit()
        or len(contact) != 11
        or not contact.startswith("09")
    ):
        flash(
            "❌ Contact number must be an 11-digit mobile number starting with '09'.",
            "danger",
        )
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"
    cursor.execute(
        f"SELECT id, name, email, contact, qr_code, volunteer_code FROM volunteers WHERE email = {ph} AND contact = {ph}",
        (email, contact),
    )
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if user:
        session.permanent = True
        session["user"] = {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "volunteer_code": user[5],
        }
        session.modified = True
        flash(f"✅ Welcome back, {user[1]}!", "success")
    else:
        flash(
            "❌ No volunteer account matches the provided email and contact number.",
            "danger",
        )

    return redirect(url_for("index"))


@app.route("/log-self-attendance", methods=["POST"])
def log_self_attendance():
    session_user = session.get("user")
    if not session_user:
        flash("Please log in first.", "danger")
        return redirect(url_for("index"))

    profile = get_fresh_user_profile(session_user["id"])
    if not profile or profile.get("volunteer_status") != "Active":
        flash("❌ Your volunteer status is Inactive. Please contact SK Admins before recording attendance.", "warning")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    now_pst = format_ph_time(get_ph_now())
    ph = "%s" if USE_POSTGRES else "?"

    cursor.execute(
        f"SELECT id, time_in FROM attendance WHERE volunteer_id = {ph} AND time_out IS NULL ORDER BY id DESC LIMIT 1",
        (session_user["id"],),
    )
    active_record = cursor.fetchone()

    if active_record:
        rec_id = active_record[0]
        time_in_val = active_record[1]
        duration_str = calculate_duration(time_in_val, now_pst)

        cursor.execute(
            f"UPDATE attendance SET time_out = {ph}, total_hours = {ph} WHERE id = {ph}",
            (now_pst, duration_str, rec_id),
        )
        conn.commit()
        flash(
            f"🔴 TIME OUT recorded for {session_user.get('name')} ({now_pst}) | Total Hours: {duration_str}",
            "success",
        )
    else:
        agenda = request.form.get("agenda", "").strip()
        task = request.form.get("task", "").strip()
        agenda_val = agenda if agenda else "General Assembly"
        task_val = task if task else "Volunteer Duty"

        cursor.execute(
            f"INSERT INTO attendance (volunteer_id, agenda, task, time_in, total_hours) VALUES ({ph}, {ph}, {ph}, {ph}, NULL)",
            (session_user["id"], agenda_val, task_val, now_pst),
        )
        conn.commit()
        flash(
            f"🟢 TIME IN recorded for {session_user.get('name')} | Agenda: {agenda_val} ({now_pst})",
            "success",
        )

    cursor.close()
    conn.close()

    return redirect(url_for("index"))


@app.route("/register", methods=["POST"])
def register():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    contact = request.form.get("contact", "").strip()
    agree_terms = request.form.get("agree_terms")

    if not agree_terms:
        flash(
            "❌ You must scroll and read the KABS Volunteer Manual to the end before registering.",
            "danger",
        )
        return redirect(url_for("index"))

    if not email.endswith("@gmail.com") or len(email) <= 10:
        flash("❌ Valid @gmail.com address required!", "danger")
        return redirect(url_for("index"))

    if len(name) > 50 or len(name) < 2:
        flash(
            "❌ Full Name must be between 2 and 50 characters.",
            "danger",
        )
        return redirect(url_for("index"))

    if (
        not contact.isdigit()
        or len(contact) != 11
        or not contact.startswith("09")
    ):
        flash(
            "❌ Contact number must be an 11-digit mobile number starting with '09'.",
            "danger",
        )
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"

    cursor.execute(
        f"SELECT id, name FROM volunteers WHERE email = {ph}",
        (email,),
    )
    existing_user = cursor.fetchone()
    if existing_user:
        cursor.close()
        conn.close()
        flash(
            f"⚠️ You are already registered, {existing_user[1]}! Please log in with your QR pass or credentials.",
            "warning",
        )
        return redirect(url_for("index"))

    auth_token = secrets.token_hex(16)
    unique_volunteer_code = f"KABS-{secrets.token_hex(2).upper()}"
    now_pst = format_ph_time(get_ph_now())

    if USE_POSTGRES:
        cursor.execute(
            "INSERT INTO volunteers (name, email, contact, auth_token, volunteer_code, profile_pic, volunteer_status, volunteer_type, last_accessed) VALUES (%s, %s, %s, %s, %s, NULL, 'Active', 'Auxiliary', %s) RETURNING id",
            (name, email, contact, auth_token, unique_volunteer_code, now_pst),
        )
        v_id = cursor.fetchone()[0]
    else:
        cursor.execute(
            "INSERT INTO volunteers (name, email, contact, auth_token, volunteer_code, profile_pic, volunteer_status, volunteer_type, last_accessed) VALUES (?, ?, ?, ?, ?, NULL, 'Active', 'Auxiliary', ?)",
            (name, email, contact, auth_token, unique_volunteer_code, now_pst),
        )
        v_id = cursor.lastrowid

    cursor.execute(
        f"UPDATE volunteers SET qr_code = {ph} WHERE id = {ph}",
        (unique_volunteer_code, v_id),
    )
    conn.commit()
    cursor.close()
    conn.close()

    session.permanent = True
    session["user"] = {
        "id": v_id,
        "name": name,
        "email": email,
        "volunteer_code": unique_volunteer_code,
    }
    session.modified = True
    flash(f"✅ Registration complete! Welcome, {name}.", "success")
    return redirect(url_for("index"))


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    flash("You have successfully logged out.", "success")
    return redirect(url_for("index"))


@app.errorhandler(404)
def page_not_found(e):
    return redirect(url_for("index"))


@app.route("/delete-log/<int:log_id>", methods=["POST"])
def delete_log(log_id):
    session_user = session.get("user")
    if not session_user:
        flash("Please log in first.", "danger")
        return redirect(url_for("index"))

    current_user = get_fresh_user_profile(session_user["id"])
    if not is_admin_user(current_user):
        flash("❌ Only SK Admins are authorized to delete attendance records.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    ph = "%s" if USE_POSTGRES else "?"

    cursor.execute(
        f"SELECT time_out FROM attendance WHERE id = {ph}",
        (log_id,),
    )
    target = cursor.fetchone()

    if not target:
        flash("Attendance record not found.", "danger")
    elif target[0] is None:
        flash(
            "❌ Cannot delete an active duty session! Please punch Time Out first.",
            "warning",
        )
    else:
        cursor.execute(
            f"DELETE FROM attendance WHERE id = {ph}",
            (log_id,),
        )
        conn.commit()
        flash("🗑️ Successfully deleted attendance record!", "success")

    cursor.close()
    conn.close()
    return redirect(url_for("index"))


@app.route("/delete-logs-batch", methods=["POST"])
def delete_logs_batch():
    session_user = session.get("user")
    if not session_user:
        flash("Please log in first.", "danger")
        return redirect(url_for("index"))

    current_user = get_fresh_user_profile(session_user["id"])
    if not is_admin_user(current_user):
        flash("❌ Only SK Admins are authorized to delete attendance records.", "danger")
        return redirect(url_for("index"))

    log_ids = request.form.getlist("log_ids")
    if not log_ids:
        flash("No attendance records were selected for deletion.", "warning")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    deleted_count = 0

    for lid in log_ids:
        try:
            lid_int = int(lid)
            ph = "%s" if USE_POSTGRES else "?"
            cursor.execute(f"SELECT time_out FROM attendance WHERE id = {ph}", (lid_int,))
            row = cursor.fetchone()
            if row and row[0] is not None:
                cursor.execute(f"DELETE FROM attendance WHERE id = {ph}", (lid_int,))
                deleted_count += 1
        except Exception:
            pass

    conn.commit()
    cursor.close()
    conn.close()

    if deleted_count > 0:
        flash(f"🗑️ Successfully deleted {deleted_count} selected attendance log(s)!", "success")
    else:
        flash("No logs were deleted. Ensure selected logs are not currently active.", "warning")

    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
