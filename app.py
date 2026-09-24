import base64
import csv
import io
import json
import os
import secrets
import sqlite3
from datetime import datetime
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
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kabs_attendance_secret_key")

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

QR_FOLDER = os.path.join("static", "qrcodes")
os.makedirs(QR_FOLDER, exist_ok=True)


def get_db_connection():
    if DATABASE_URL and psycopg2:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect("kabs.db")


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    if DATABASE_URL and psycopg2:
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
                time_out TEXT
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

    conn.commit()
    cursor.close()
    conn.close()


init_db()


def authenticate_user_by_qr(qr_data_str):
    qr_data_str = qr_data_str.strip()
    conn = get_db_connection()
    cursor = conn.cursor()
    user = None

    try:
        data = json.loads(qr_data_str)
        if data.get("system") == "KABS_SECURE_AUTH":
            v_id = data.get("id")
            token = data.get("token")
            cursor.execute(
                "SELECT id, name, email, contact, qr_code, volunteer_code, profile_pic FROM volunteers WHERE id = %s AND auth_token = %s"
                if DATABASE_URL
                else "SELECT id, name, email, contact, qr_code, volunteer_code, profile_pic FROM volunteers WHERE id = ? AND auth_token = ?",
                (v_id, token),
            )
            user = cursor.fetchone()
    except Exception:
        pass

    if not user:
        cursor.execute(
            "SELECT id, name, email, contact, qr_code, volunteer_code, profile_pic FROM volunteers WHERE UPPER(volunteer_code) = UPPER(%s)"
            if DATABASE_URL
            else "SELECT id, name, email, contact, qr_code, volunteer_code, profile_pic FROM volunteers WHERE UPPER(volunteer_code) = UPPER(?)",
            (qr_data_str,),
        )
        user = cursor.fetchone()

    cursor.close()
    conn.close()
    return user


MAIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KABS Attendance Portal</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/html5-qrcode"></script>
</head>
<body class="bg-slate-50 text-slate-800 antialiased min-h-screen pb-12">
    <!-- STICKY TOPBAR -->
    <header class="bg-slate-900 border-b border-slate-800 sticky top-0 z-30 shadow-md">
        <div class="max-w-6xl mx-auto px-4 py-3 flex justify-between items-center">
            <a href="/" class="flex items-center space-x-3">
                <img src="/static/images/logo.jpg" alt="KABS Logo" class="w-10 h-10 rounded-lg object-cover bg-white p-0.5 border border-slate-700 shadow-sm" onerror="this.src='/static/images/logo.png';">
                <div>
                    <h1 class="font-extrabold text-white text-sm sm:text-base leading-tight">KABS ATTENDANCE PORTAL</h1>
                    <p class="text-[11px] text-slate-400 hidden sm:block">Kabataan para sa Aksyon, Bayanihan, at Serbisyo | SK Payatas</p>
                </div>
            </a>
            <div class="flex items-center space-x-2 sm:space-x-3">
                {% if user %}
                <!-- LINK PATUNGO SA PROFILE PAGE -->
                <a href="/profile" class="flex items-center gap-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 text-xs px-3 py-1.5 rounded-lg text-slate-200 transition-all">
                    {% if user.get('profile_pic') %}
                        <img src="{{ user.get('profile_pic') }}" class="w-5 h-5 rounded-full object-cover">
                    {% else %}
                        <span>👤</span>
                    {% endif %}
                    <span class="font-bold text-white max-w-[120px] sm:max-w-none truncate">{{ user.get('name') }}</span>
                    <span class="text-[10px] bg-blue-600/40 text-blue-300 px-1.5 py-0.5 rounded font-mono">Profile</span>
                </a>
                <a href="/logout" class="bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 text-xs font-semibold px-2.5 py-1.5 rounded-lg transition-all">Log Out</a>
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
                    <p class="text-xs text-slate-500 mb-4" id="login-desc">Itapat ang iyong QR pass sa camera para mag-login.</p>

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

                        <div class="pt-2 border-t border-slate-100">
                            <div class="bg-amber-50/70 border border-amber-200 rounded-xl p-3 space-y-2">
                                <div class="flex items-center justify-between">
                                    <span class="text-xs font-bold text-amber-900">Mandatory Manual Verification</span>
                                    <a href="#manual-section" class="text-xs bg-amber-600 hover:bg-amber-700 text-white font-bold px-3 py-1 rounded-lg shadow-sm transition-all">
                                        Basahin sa Ibaba ⬇
                                    </a>
                                </div>
                                <div class="flex items-start gap-2 pt-1">
                                    <input type="checkbox" id="agree_terms" name="agree_terms" required disabled class="mt-0.5 w-4 h-4 text-emerald-600 rounded border-slate-300 cursor-not-allowed">
                                    <label for="agree_terms" id="agree_label" class="text-[11px] text-slate-500 leading-snug select-none">
                                        🔒 <i>I-scroll ang KABS Manual sa ibaba hanggang dulo bago ma-unlock ang checkbox na ito.</i>
                                    </label>
                                </div>
                            </div>
                        </div>

                        <button type="submit" id="register_submit_btn" disabled class="w-full py-2.5 bg-slate-400 text-white font-semibold text-sm rounded-lg shadow-sm cursor-not-allowed transition-all">
                            Register & Generate Pass
                        </button>
                    </form>
                </div>
            </div>
        {% else %}
            <!-- DASHBOARD: PUNCH ATTENDANCE AT OFFICIAL PASS LAMANG ANG MAGKASAMA -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
                
                <!-- PUNCH TIME IN / TIME OUT CARD -->
                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                    {% if active_record %}
                        <div class="flex items-center justify-between mb-2">
                            <h2 class="text-base font-bold text-slate-900">Active Duty Session</h2>
                            <span class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800 animate-pulse">
                                ● Clocked In
                            </span>
                        </div>
                        <p class="text-xs text-slate-500 mb-4">Kasalukuyan kang naka-duty. Pindutin lamang ang button para mag-Time Out.</p>

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
                                <span class="font-bold text-emerald-700">{{ active_record[3] }}</span>
                            </div>
                        </div>

                        <form action="/log-self-attendance" method="POST">
                            <button type="submit" class="w-full py-3 bg-rose-600 hover:bg-rose-700 text-white font-bold text-sm rounded-xl shadow transition-all flex items-center justify-center gap-2">
                                <span>🔴</span> Punch Time Out
                            </button>
                        </form>
                    {% else %}
                        <h2 class="text-base font-bold text-slate-900 mb-1">Punch Time In</h2>
                        <p class="text-xs text-slate-500 mb-4">Ilagay ang agenda at task para makapag-simula ng attendance.</p>
                        
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
                </div>

                <!-- OFFICIAL PASS (QR CODE) CARD -->
                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm text-center">
                    <span class="inline-block bg-slate-100 text-slate-600 text-xs font-bold px-3 py-1 rounded-full uppercase mb-2">
                        Official Pass
                    </span>
                    <img src="/static/qrcodes/{{ user.get('qr_code') }}" class="w-36 h-36 mx-auto rounded-lg border p-1 mb-3" onerror="this.outerHTML='<div class=\\'text-xs text-slate-400 my-8\\'>QR Pass Image generated</div>'">
                    <div class="text-xs font-mono font-bold bg-slate-100 py-1.5 px-3 rounded inline-block mb-3 border border-dashed border-slate-400">{{ user.get('volunteer_code') }}</div><br>
                    <div class="flex justify-center gap-2">
                        <a href="/static/qrcodes/{{ user.get('qr_code') }}" download class="inline-block py-2 px-4 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg shadow-sm">Download Pass</a>
                        <a href="#manual-section" class="inline-block py-2 px-3 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-lg border border-slate-300">📖 Manual</a>
                    </div>
                </div>

            </div>
        {% endif %}

        <!-- ATTENDANCE TABLE WITH CONDITIONAL EXPORT BUTTON -->
        <div class="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
            <div class="p-4 border-b flex justify-between items-center bg-slate-50/50">
                <div>
                    <h3 class="font-bold text-sm text-slate-800">Attendance Log</h3>
                    <p class="text-xs text-slate-500">Listahan ng lahat ng pumasok at lumabas.</p>
                </div>
                
                {% if user %}
                    {% if logs and logs|length > 0 %}
                        <a href="/export-attendance" class="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold px-3 py-2 rounded-lg shadow-sm transition-all">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                            </svg>
                            Export to Excel
                        </a>
                    {% else %}
                        <button type="button" disabled title="Kailangan munang magkaroon ng kahit isang attendance log bago makapag-export." class="inline-flex items-center gap-1.5 bg-slate-100 text-slate-400 border border-slate-200 text-xs font-semibold px-3 py-2 rounded-lg cursor-not-allowed opacity-60">
                            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path>
                            </svg>
                            Export to Excel (No Logs)
                        </button>
                    {% endif %}
                {% endif %}
            </div>

            <div class="overflow-x-auto">
                <table class="w-full text-left text-xs sm:text-sm">
                    <thead class="bg-slate-50 text-slate-500 uppercase text-xs font-semibold">
                        <tr>
                            <th class="py-3 px-4">Volunteer</th>
                            <th class="py-3 px-4">Agenda</th>
                            <th class="py-3 px-4">Task</th>
                            <th class="py-3 px-4">Time In</th>
                            <th class="py-3 px-4">Time Out</th>
                            {% if user %}
                            <th class="py-3 px-4 text-center">Action</th>
                            {% endif %}
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-100">
                        {% for log in logs %}
                        <tr>
                            <td class="py-3 px-4 font-bold">{{ log[0] }}</td>
                            <td class="py-3 px-4 text-blue-700 font-medium">{{ log[1] if log[1] else '-' }}</td>
                            <td class="py-3 px-4 text-slate-600">{{ log[2] if log[2] else '-' }}</td>
                            <td class="py-3 px-4 text-emerald-600 font-semibold">{{ log[3] }}</td>
                            <td class="py-3 px-4 font-medium {% if log[4] %}text-rose-600{% else %}text-amber-500 italic{% endif %}">
                                {{ log[4] if log[4] else 'Clocked In' }}
                            </td>
                            {% if user %}
                            <td class="py-3 px-4 text-center">
                                {% if log[4] %}
                                    <form action="/delete-log/{{ log[5] }}" method="POST" onsubmit="return confirm('Sigurado ka bang buburahin ang attendance record na ito?');" class="inline">
                                        <button type="submit" class="bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 text-xs font-semibold px-2.5 py-1 rounded-lg transition-all">
                                            Delete
                                        </button>
                                    </form>
                                {% else %}
                                    <button type="button" disabled class="opacity-50 cursor-not-allowed bg-slate-100 text-slate-400 border border-slate-200 text-xs font-medium px-2 py-1 rounded-lg">
                                        Clocked In
                                    </button>
                                {% endif %}
                            </td>
                            {% endif %}
                        </tr>
                        {% else %}
                        <tr><td colspan="6" class="text-center py-6 text-slate-400">Walang attendance records sa ngayon.</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- BUONG KABS VOLUNTEER MANUAL (NASA ILALIM NG ATTENDANCE LOG) -->
        <div id="manual-section" class="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
            <div class="p-5 bg-slate-900 text-white flex justify-between items-center">
                <div>
                    <h3 class="text-base font-extrabold tracking-wide">📖 KABS YOUTH VOLUNTEERS PROGRAM MANUAL</h3>
                    <p class="text-xs text-slate-300">Sangguniang Kabataan ng Barangay Payatas, Lungsod Quezon</p>
                </div>
            </div>

            <div id="manual-content-scroll" onscroll="checkManualScroll(this)" class="p-6 max-h-[600px] overflow-y-auto space-y-6 text-xs sm:text-sm text-slate-700 leading-relaxed text-justify">
                <section class="space-y-2 border-b pb-4">
                    <h4 class="font-extrabold text-slate-900 text-base">Program Rationale</h4>
                    <p>Young people are recognized as vital partners in nation-building[cite: 5]. With their energy, creativity, and commitment to social good, youth have the capacity to become catalysts for meaningful change in their communities[cite: 5]. According to a Gallup study reported by The Philippine Star, the Filipino youth are among the world's most dedicated volunteers despite a global decline in overall charitable behavior; 44% of Filipino adults reported volunteering in 2024, ranking the Philippines 4th highest globally in volunteerism rates[cite: 5]. However, many young people lack structured opportunities to channel their talents and ideals into sustainable service initiatives[cite: 5].</p>
                    <p>According to the study entitled <i>Evaluating the National Volunteering through the Bayanihang Bayan Program</i> by Ma. Ella Oplas, volunteer work—particularly informal activities—remains largely absent from national accounting systems, limiting the visibility of its true economic and social contributions[cite: 5].</p>
                </section>

                <section class="space-y-2 border-b pb-4">
                    <h4 class="font-extrabold text-slate-900 text-base">Program Description & Objectives</h4>
                    <p>The KABS program is a youth volunteer program that seeks to strengthen the culture of volunteerism among youth in Payatas[cite: 5]. It was institutionalized under the Barangay Payatas Comprehensive Youth Code Ordinance and SK Payatas Resolution No. 012 S. 2024 and Resolution No. 42 S. 2025[cite: 5].</p>
                    <ul class="list-disc pl-5 space-y-1">
                        <li>Promote active youth participation in community development and local governance[cite: 5].</li>
                        <li>Develop leadership, teamwork, and civic responsibility among young volunteers[cite: 5].</li>
                        <li>Provide structured deployment, recognition, and skill-building opportunities[cite: 5].</li>
                    </ul>
                </section>

                <section class="space-y-2 border-b pb-4">
                    <h4 class="font-extrabold text-slate-900 text-base">KABS 3 Pillars</h4>
                    <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        <div class="bg-blue-50/70 p-3 rounded-xl border border-blue-200">
                            <h5 class="font-bold text-blue-900 text-sm mb-1">⚡ Action</h5>
                            <p class="text-xs">Represents the energy and initiative of the youth to step forward and create change[cite: 5].</p>
                        </div>
                        <div class="bg-emerald-50/70 p-3 rounded-xl border border-emerald-200">
                            <h5 class="font-bold text-emerald-900 text-sm mb-1">🤝 Bayanihan</h5>
                            <p class="text-xs">Embodies communal unity and shared responsibility[cite: 5].</p>
                        </div>
                        <div class="bg-rose-50/70 p-3 rounded-xl border border-rose-200">
                            <h5 class="font-bold text-rose-900 text-sm mb-1">❤️ Service</h5>
                            <p class="text-xs">Selflessness, dedication, and accountability to uplift lives[cite: 5].</p>
                        </div>
                    </div>
                </section>

                <section class="space-y-2 border-b pb-4">
                    <h4 class="font-extrabold text-slate-900 text-base">Volunteer Committees & Deployment</h4>
                    <p class="text-xs">Ang mga volunteer ay nahahati sa 4 na komite: <b>Operations</b> (Logistics, Registration, Food), <b>Production</b> (Program flow, tabulators, emcee, technical), <b>Services</b> (Venue, Crowd control, First Aid), at <b>Engagement</b> (Media, Publicity, Graphics)[cite: 5].</p>
                </section>

                <section class="space-y-2 pb-2">
                    <h4 class="font-extrabold text-slate-900 text-base">Code of Conduct & Rights of Volunteers</h4>
                    <p class="text-xs">Inaasahan ang bawat isa na maging magalang, pumasok sa oras, at igalang ang kapwa[cite: 5]. Mahigpit na ipinagbabawal ang alak, droga, o pamemeke sa attendance logs[cite: 5]. May karapatan ang bawat volunteer sa ligtas na lugar, patas na pagtrato, at tamang pagkilala[cite: 5].</p>
                    <div class="mt-4 p-3 bg-emerald-50 border border-emerald-200 rounded-xl text-center">
                        <p class="text-xs font-bold text-emerald-900">Narating mo na ang dulo ng KABS Volunteer Manual[cite: 5].</p>
                        <p class="text-[11px] text-emerald-700">Maaari mo nang markahan ang checkbox sa itaas para mag-register.</p>
                    </div>
                </section>
            </div>
        </div>
    </main>

    <script>
        let hasReadToEnd = false;

        function checkManualScroll(element) {
            if (element.scrollHeight - element.scrollTop <= element.clientHeight + 25) {
                if (!hasReadToEnd) {
                    hasReadToEnd = true;
                    const checkbox = document.getElementById('agree_terms');
                    if (checkbox) {
                        checkbox.disabled = false;
                        checkbox.checked = true;
                    }
                    const label = document.getElementById('agree_label');
                    if (label) {
                        label.innerHTML = "✅ <b>Nabasa ko na hanggang dulo</b> at sumasang-ayon sa KABS Manual[cite: 5].";
                        label.classList.remove('text-slate-500');
                        label.classList.add('text-slate-800');
                    }
                    const submitBtn = document.getElementById('register_submit_btn');
                    if (submitBtn) {
                        submitBtn.disabled = false;
                        submitBtn.classList.remove('bg-slate-400', 'cursor-not-allowed');
                        submitBtn.classList.add('bg-slate-900', 'hover:bg-slate-800', 'cursor-pointer');
                    }
                }
            }
        }

        {% if not user %}
        let html5QrCode = null;

        function onScanSuccess(decodedText) {
            if (html5QrCode) {
                html5QrCode.stop().then(() => {
                    document.getElementById('scan-status').innerHTML = "⏳ Logging in with QR pass...";
                    fetch('/login-qr-api', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ qr_payload: decodedText })
                    })
                    .then(res => res.json())
                    .then(data => {
                        if (data.success) {
                            location.reload();
                        } else {
                            alert(data.message);
                            location.reload();
                        }
                    })
                    .catch(() => {
                        alert("Scan error occurred.");
                        location.reload();
                    });
                }).catch(err => console.error(err));
            }
        }

        function startScanner() {
            html5QrCode = new Html5Qrcode("reader");
            const config = { fps: 10, qrbox: { width: 200, height: 200 }, aspectRatio: 1.0 };
            html5QrCode.start({ facingMode: "environment" }, config, onScanSuccess)
                .then(() => {
                    document.getElementById('scan-status').innerText = "📷 Camera active. Scan your QR.";
                })
                .catch(err => {
                    document.getElementById('scan-status').innerHTML = 
                        "<span class='text-rose-500 font-semibold'>⚠️ Allow camera permissions o mag-type ng code.</span>";
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
                desc.innerText = "Itapat ang iyong QR pass sa camera para mag-login.";
                startScanner();
            } else {
                if (html5QrCode && html5QrCode.isScanning) {
                    html5QrCode.stop().catch(() => {});
                }
                qrSection.classList.add('hidden');
                formSection.classList.remove('hidden');
                btn.innerText = "Use QR Scanner Instead";
                desc.innerText = "Naka-register ka na? Mag-login gamit ang iyong account.";
            }
        }

        window.addEventListener("DOMContentLoaded", () => {
            startScanner();
        });
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KABS Profile | {{ user.get('name') }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.5.13/cropper.min.css"/>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.5.13/cropper.min.js"></script>
</head>
<body class="bg-slate-50 text-slate-800 antialiased min-h-screen pb-12">
    <header class="bg-slate-900 border-b border-slate-800 sticky top-0 z-30 shadow-md">
        <div class="max-w-4xl mx-auto px-4 py-3 flex justify-between items-center">
            <a href="/" class="flex items-center space-x-2 text-white hover:text-blue-300 transition-all">
                <span>←</span>
                <span class="font-bold text-sm">Bumalik sa Attendance Dashboard</span>
            </a>
            <a href="/logout" class="bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 text-xs font-semibold px-2.5 py-1.5 rounded-lg transition-all">Log Out</a>
        </div>
    </header>

    <main class="max-w-2xl mx-auto px-4 pt-8 space-y-6">
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
            <div class="flex items-center justify-between border-b pb-4">
                <div>
                    <h2 class="text-lg font-extrabold text-slate-900">KABS Official Profile</h2>
                    <p class="text-xs text-slate-500">I-manage ang iyong personal na impormasyon at larawan.</p>
                </div>
                <span class="bg-blue-50 text-blue-700 text-xs font-bold px-3 py-1 rounded-full uppercase border border-blue-200">
                    Active Volunteer
                </span>
            </div>

            <!-- PROFILE PHOTO SECTION NA MAY CROP AT SELFIE -->
            <div class="bg-slate-50 border border-slate-200 rounded-xl p-5 text-center space-y-3">
                <div class="relative w-28 h-28 mx-auto">
                    {% if user.get('profile_pic') %}
                        <img src="{{ user.get('profile_pic') }}" alt="Profile" class="w-28 h-28 rounded-full object-cover border-4 border-white shadow-md mx-auto">
                    {% else %}
                        <div class="w-28 h-28 rounded-full bg-slate-200 border-2 border-dashed border-slate-300 flex items-center justify-center text-slate-400 text-3xl mx-auto">
                            👤
                        </div>
                    {% endif %}
                </div>

                <div class="flex justify-center gap-2">
                    <label class="py-2 px-3 bg-white hover:bg-slate-100 text-slate-700 text-xs font-semibold rounded-lg border border-slate-300 cursor-pointer transition-all flex items-center gap-1.5 shadow-sm">
                        <span>📁</span> Choose Photo
                        <input type="file" id="choose-photo-input" accept="image/*" class="hidden" onchange="handleFileSelect(event)">
                    </label>

                    <button type="button" onclick="openSelfieModal()" class="py-2 px-3 bg-blue-50 hover:bg-blue-100 text-blue-700 text-xs font-semibold rounded-lg border border-blue-200 transition-all flex items-center gap-1.5 shadow-sm">
                        <span>📸</span> Take Selfie
                    </button>
                </div>
            </div>

            <!-- EDIT INFORMATION FORM -->
            <form action="/edit-profile" method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Full Name</label>
                    <input type="text" name="name" maxlength="50" required value="{{ user.get('name') }}" class="w-full text-sm py-2.5 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Email Address (Registered & Non-editable)</label>
                    <input type="email" value="{{ user.get('email') }}" disabled class="w-full text-sm py-2.5 px-3 border border-slate-200 bg-slate-100 text-slate-500 rounded-lg outline-none cursor-not-allowed">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Contact Number (11 digits, numbers only)</label>
                    <input type="tel" name="contact" maxlength="11" minlength="11" pattern="09[0-9]{9}" inputmode="numeric" oninput="this.value = this.value.replace(/[^0-9]/g, '')" required value="{{ user.get('contact') }}" class="w-full text-sm py-2.5 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">Volunteer Pass Code</label>
                    <input type="text" value="{{ user.get('volunteer_code') }}" disabled class="w-full font-mono text-sm py-2.5 px-3 border border-slate-200 bg-slate-100 text-blue-600 font-bold rounded-lg outline-none cursor-not-allowed">
                </div>

                <div class="pt-4 border-t flex justify-end">
                    <button type="submit" class="py-2.5 px-6 bg-slate-900 hover:bg-slate-800 text-white text-xs font-bold rounded-xl shadow transition-all">
                        Save Profile Changes
                    </button>
                </div>
            </form>
        </div>
    </main>

    <!-- CROPPER MODAL NA MAY DONE BUTTON -->
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
                <span class="text-xs text-slate-500">I-scale para magkasya sa frame.</span>
                <div class="flex gap-2">
                    <button type="button" onclick="closeCropperModal()" class="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-lg">Cancel</button>
                    <button type="button" id="crop-done-btn" onclick="applyCropAndSave()" class="px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold rounded-lg shadow-sm transition-all flex items-center gap-1.5">
                        <span>✓</span> Done
                    </button>
                </div>
            </div>
        </div>
    </div>

    <!-- SELFIE CAMERA MODAL -->
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

        function applyCropAndSave() {
            if (!cropper) return;
            const btn = document.getElementById('crop-done-btn');
            btn.innerText = "⏳ Saving...";
            btn.disabled = true;

            const croppedCanvas = cropper.getCroppedCanvas({ width: 256, height: 256 });
            const base64Data = croppedCanvas.toDataURL('image/jpeg', 0.85);

            fetch('/save-cropped-profile', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ image_data: base64Data })
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
                    alert("Hindi mabuksan ang camera. Pakisuri ang camera permissions sa browser.");
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
    </script>
</body>
</html>
"""


@app.route("/")
def index():
    user = session.get("user")
    active_record = None

    conn = get_db_connection()
    cursor = conn.cursor()

    if user:
        cursor.execute(
            "SELECT id, agenda, task, time_in FROM attendance WHERE volunteer_id = %s AND time_out IS NULL ORDER BY id DESC LIMIT 1"
            if DATABASE_URL
            else "SELECT id, agenda, task, time_in FROM attendance WHERE volunteer_id = ? AND time_out IS NULL ORDER BY id DESC LIMIT 1",
            (user.get("id"),),
        )
        active_record = cursor.fetchone()

    cursor.execute(
        """
        SELECT volunteers.name, attendance.agenda, attendance.task, attendance.time_in, attendance.time_out, attendance.id
        FROM attendance
        JOIN volunteers ON attendance.volunteer_id = volunteers.id
        ORDER BY attendance.id DESC
        LIMIT 100
    """
    )
    logs = cursor.fetchall()
    cursor.close()
    conn.close()

    if os.path.exists(os.path.join("templates", "index.html")):
        return render_template(
            "index.html", user=user, logs=logs, active_record=active_record
        )

    return render_template_string(
        MAIN_TEMPLATE, user=user, logs=logs, active_record=active_record
    )


@app.route("/profile")
def profile():
    user = session.get("user")
    if not user:
        flash("Kailangan munang mag-login para makita ang iyong profile.", "danger")
        return redirect(url_for("index"))

    return render_template_string(PROFILE_TEMPLATE, user=user)


@app.route("/edit-profile", methods=["POST"])
def edit_profile():
    user = session.get("user")
    if not user:
        flash("Kailangang naka-login muna para makapag-edit ng profile.", "danger")
        return redirect(url_for("index"))

    name = request.form.get("name", "").strip()
    contact = request.form.get("contact", "").strip()

    if len(name) > 50 or len(name) < 2:
        flash("❌ Ang pangalan ay dapat nasa pagitan ng 2 hanggang 50 characters.", "danger")
        return redirect(url_for("profile"))

    if not contact.isdigit() or len(contact) != 11 or not contact.startswith("09"):
        flash("❌ Ang contact number ay dapat 11 digits at nagsisimula sa '09'.", "danger")
        return redirect(url_for("profile"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE volunteers SET name = %s, contact = %s WHERE id = %s"
        if DATABASE_URL
        else "UPDATE volunteers SET name = ?, contact = ? WHERE id = ?",
        (name, contact, user["id"]),
    )
    conn.commit()
    cursor.close()
    conn.close()

    session["user"]["name"] = name
    session["user"]["contact"] = contact
    session.modified = True

    flash("✅ Matagumpay na na-update ang iyong KABS Profile information!", "success")
    return redirect(url_for("profile"))


@app.route("/save-cropped-profile", methods=["POST"])
def save_cropped_profile():
    user = session.get("user")
    if not user:
        return jsonify({"success": False, "message": "Kailangang naka-login muna."})

    data = request.json or {}
    image_data = data.get("image_data")
    if not image_data or not image_data.startswith("data:image"):
        return jsonify({"success": False, "message": "Walang natanggap na cropped photo."})

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE volunteers SET profile_pic = %s WHERE id = %s"
            if DATABASE_URL
            else "UPDATE volunteers SET profile_pic = ? WHERE id = ?",
            (image_data, user["id"]),
        )
        conn.commit()
        cursor.close()
        conn.close()

        session["user"]["profile_pic"] = image_data
        session.modified = True
        flash("✅ Matagumpay na na-crop at na-save ang iyong Profile Picture!", "success")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/export-attendance")
def export_attendance():
    if not session.get("user"):
        flash(
            "Kailangan munang mag-login para makapag-export ng attendance.",
            "danger",
        )
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT volunteers.volunteer_code, volunteers.name, volunteers.email, volunteers.contact,
               attendance.agenda, attendance.task, attendance.time_in, attendance.time_out
        FROM attendance
        JOIN volunteers ON attendance.volunteer_id = volunteers.id
        ORDER BY attendance.id DESC
    """
    )
    records = cursor.fetchall()
    cursor.close()
    conn.close()

    if not records or len(records) == 0:
        flash(
            "❌ Walang attendance records na maaring i-export sa ngayon.",
            "warning",
        )
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
        "Time In",
        "Time Out",
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
        ])

    csv_data = "\ufeff" + output.getvalue()
    filename = (
        f"kabs_attendance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

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
        session["user"] = {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "contact": user[3] if len(user) > 3 and user[3] else "N/A",
            "qr_code": user[4] if len(user) > 4 else None,
            "volunteer_code": user[5] if len(user) > 5 else "N/A",
            "profile_pic": user[6] if len(user) > 6 else None,
        }
        flash(f"✅ Welcome back, {user[1]}! (Logged in via QR Pass)", "success")
        return jsonify({"success": True})

    return jsonify(
        {"success": False, "message": "❌ Invalid o hindi kinikilalang QR Code."}
    )


@app.route("/login-code", methods=["POST"])
def login_code():
    code = request.form.get("volunteer_code", "").strip()
    user = authenticate_user_by_qr(code)

    if user:
        session["user"] = {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "contact": user[3] if len(user) > 3 and user[3] else "N/A",
            "qr_code": user[4] if len(user) > 4 else None,
            "volunteer_code": user[5] if len(user) > 5 else "N/A",
            "profile_pic": user[6] if len(user) > 6 else None,
        }
        flash(f"✅ Welcome back, {user[1]}!", "success")
    else:
        flash("❌ Invalid na Volunteer Code.", "danger")

    return redirect(url_for("index"))


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()
    contact = request.form.get("contact", "").strip()

    if not email.endswith("@gmail.com"):
        flash("❌ Email must end with @gmail.com", "danger")
        return redirect(url_for("index"))

    if (
        not contact.isdigit()
        or len(contact) != 11
        or not contact.startswith("09")
    ):
        flash(
            "❌ Ang contact number ay dapat binubuo lamang ng 11 digits na numero at nagsisimula sa '09'.",
            "danger",
        )
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, email, contact, qr_code, volunteer_code, profile_pic FROM volunteers WHERE email = %s AND contact = %s"
        if DATABASE_URL
        else "SELECT id, name, email, contact, qr_code, volunteer_code, profile_pic FROM volunteers WHERE email = ? AND contact = ?",
        (email, contact),
    )
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if user:
        session["user"] = {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "contact": user[3] if len(user) > 3 and user[3] else "N/A",
            "qr_code": user[4] if len(user) > 4 else None,
            "volunteer_code": user[5] if len(user) > 5 else "N/A",
            "profile_pic": user[6] if len(user) > 6 else None,
        }
        flash(f"✅ Welcome back, {user[1]}!", "success")
    else:
        flash(
            "❌ Walang profile na tumugma sa email o contact number na nilagay.",
            "danger",
        )

    return redirect(url_for("index"))


@app.route("/log-self-attendance", methods=["POST"])
def log_self_attendance():
    user = session.get("user")
    if not user:
        flash("Kailangan munang mag-login.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")

    cursor.execute(
        "SELECT id FROM attendance WHERE volunteer_id = %s AND time_out IS NULL ORDER BY id DESC LIMIT 1"
        if DATABASE_URL
        else "SELECT id FROM attendance WHERE volunteer_id = ? AND time_out IS NULL ORDER BY id DESC LIMIT 1",
        (user.get("id"),),
    )
    active_record = cursor.fetchone()

    if active_record:
        cursor.execute(
            "UPDATE attendance SET time_out = %s WHERE id = %s"
            if DATABASE_URL
            else "UPDATE attendance SET time_out = ? WHERE id = ?",
            (now, active_record[0]),
        )
        flash(f"🔴 TIME OUT recorded for {user.get('name')} ({now})", "success")
    else:
        agenda = request.form.get("agenda", "").strip()
        task = request.form.get("task", "").strip()
        agenda_val = agenda if agenda else "General Assembly"
        task_val = task if task else "Volunteer Duty"

        cursor.execute(
            "INSERT INTO attendance (volunteer_id, agenda, task, time_in) VALUES (%s, %s, %s, %s)"
            if DATABASE_URL
            else "INSERT INTO attendance (volunteer_id, agenda, task, time_in) VALUES (?, ?, ?, ?)",
            (user.get("id"), agenda_val, task_val, now),
        )
        flash(
            f"🟢 TIME IN recorded for {user.get('name')} | Agenda: {agenda_val} ({now})",
            "success",
        )

    conn.commit()
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
            "❌ Kailangan mong basahin at lagyan ng check ang pagsang-ayon sa KABS Volunteer Manual bago makapag-register[cite: 5].",
            "danger",
        )
        return redirect(url_for("index"))

    if not email.endswith("@gmail.com") or len(email) <= 10:
        flash("❌ Valid @gmail.com address lamang ang tinatanggap!", "danger")
        return redirect(url_for("index"))

    if len(name) > 50 or len(name) < 2:
        flash(
            "❌ Ang pangalan ay dapat nasa pagitan ng 2 hanggang 50 characters.",
            "danger",
        )
        return redirect(url_for("index"))

    if (
        not contact.isdigit()
        or len(contact) != 11
        or not contact.startswith("09")
    ):
        flash(
            "❌ Ang contact number ay dapat binubuo lamang ng 11 digits na numero at nagsisimula sa '09'.",
            "danger",
        )
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, name FROM volunteers WHERE email = %s"
        if DATABASE_URL
        else "SELECT id, name FROM volunteers WHERE email = ?",
        (email,),
    )
    existing_user = cursor.fetchone()
    if existing_user:
        cursor.close()
        conn.close()
        flash(
            f"⚠️ Registered ka na, {existing_user[1]}! Mag-login ka na lamang gamit ang iyong QR o Contact Number.",
            "warning",
        )
        return redirect(url_for("index"))

    auth_token = secrets.token_hex(16)
    unique_volunteer_code = f"KABS-{secrets.token_hex(2).upper()}"

    if DATABASE_URL:
        cursor.execute(
            "INSERT INTO volunteers (name, email, contact, auth_token, volunteer_code, profile_pic) VALUES (%s, %s, %s, %s, %s, NULL) RETURNING id",
            (name, email, contact, auth_token, unique_volunteer_code),
        )
        v_id = cursor.fetchone()[0]
    else:
        cursor.execute(
            "INSERT INTO volunteers (name, email, contact, auth_token, volunteer_code, profile_pic) VALUES (?, ?, ?, ?, ?, NULL)",
            (name, email, contact, auth_token, unique_volunteer_code),
        )
        v_id = cursor.lastrowid

    qr_payload = {
        "system": "KABS_SECURE_AUTH",
        "id": v_id,
        "name": name,
        "token": auth_token,
        "code": unique_volunteer_code,
    }

    qr_filename = f"volunteer_{v_id}.png"
    qr_path = os.path.join(QR_FOLDER, qr_filename)
    img = qrcode.make(json.dumps(qr_payload))
    img.save(qr_path)

    cursor.execute(
        "UPDATE volunteers SET qr_code = %s WHERE id = %s"
        if DATABASE_URL
        else "UPDATE volunteers SET qr_code = ? WHERE id = ?",
        (qr_filename, v_id),
    )
    conn.commit()
    cursor.close()
    conn.close()

    session["user"] = {
        "id": v_id,
        "name": name,
        "email": email,
        "contact": contact,
        "volunteer_code": unique_volunteer_code,
        "qr_code": qr_filename,
        "profile_pic": None,
    }
    flash(f"✅ Registration complete! Welcome, {name}.", "success")
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Naka-log out ka na.", "success")
    return redirect(url_for("index"))


@app.route("/delete-log/<int:log_id>", methods=["POST"])
def delete_log(log_id):
    if not session.get("user"):
        flash(
            "Kailangan munang mag-login para makapagbura ng record.", "danger"
        )
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT time_out FROM attendance WHERE id = %s"
        if DATABASE_URL
        else "SELECT time_out FROM attendance WHERE id = ?",
        (log_id,),
    )
    target = cursor.fetchone()

    if not target:
        flash("Hindi natagpuan ang attendance record.", "danger")
    elif target[0] is None:
        flash(
            "❌ Bawal burahin ang attendance record habang naka-Clocked In pa! Mag-Time Out muna.",
            "warning",
        )
    else:
        cursor.execute(
            "DELETE FROM attendance WHERE id = %s"
            if DATABASE_URL
            else "DELETE FROM attendance WHERE id = ?",
            (log_id,),
        )
        conn.commit()
        flash("🗑️ Matagumpay na nabura ang attendance log!", "success")

    cursor.close()
    conn.close()
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
