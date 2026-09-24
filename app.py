import json
import os
import secrets
import sqlite3
from datetime import datetime
from flask import (
    Flask,
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
                qr_code TEXT
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
            cursor.execute("ALTER TABLE attendance ADD COLUMN agenda TEXT;")
        except Exception:
            conn.rollback()
        try:
            cursor.execute("ALTER TABLE attendance ADD COLUMN task TEXT;")
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
                qr_code TEXT
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
        cursor.execute("PRAGMA table_info(attendance)")
        cols = [c[1] for c in cursor.fetchall()]
        if "agenda" not in cols:
            cursor.execute("ALTER TABLE attendance ADD COLUMN agenda TEXT;")
        if "task" not in cols:
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
                "SELECT id, name, email, qr_code, volunteer_code FROM volunteers WHERE id = %s AND auth_token = %s"
                if DATABASE_URL
                else "SELECT id, name, email, qr_code, volunteer_code FROM volunteers WHERE id = ? AND auth_token = ?",
                (v_id, token),
            )
            user = cursor.fetchone()
    except Exception:
        pass

    if not user:
        cursor.execute(
            "SELECT id, name, email, qr_code, volunteer_code FROM volunteers WHERE UPPER(volunteer_code) = UPPER(%s)"
            if DATABASE_URL
            else "SELECT id, name, email, qr_code, volunteer_code FROM volunteers WHERE UPPER(volunteer_code) = UPPER(?)",
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
    <header class="bg-slate-900 border-b border-slate-800 sticky top-0 z-30 shadow-md">
        <div class="max-w-6xl mx-auto px-4 py-4 flex justify-between items-center">
            <div class="flex items-center space-x-3">
                <img src="/static/images/logo.jpg" alt="KABS Logo" class="w-11 h-11 rounded-lg object-cover bg-white p-0.5 border border-slate-700 shadow-sm" onerror="this.src='/static/images/logo.png';">
                <div>
                    <h1 class="font-extrabold text-white text-base sm:text-lg leading-tight">KABS ATTENDANCE PORTAL</h1>
                    <p class="text-xs text-slate-400 hidden sm:block">Kabataan para sa Aksyon, Bayanihan, at Serbisyo</p>
                </div>
            </div>
            {% if user %}
            <div class="flex items-center space-x-3">
                <span class="text-xs text-slate-300">Welcome, <b class="text-white">{{ user.name }}</b></span>
                <a href="/logout" class="bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 text-xs font-semibold px-3 py-1.5 rounded-lg transition-all">Log Out</a>
            </div>
            {% endif %}
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
                                <label class="block text-xs font-semibold text-slate-600 mb-1">Contact Number (11 digits)</label>
                                <input type="tel" name="contact" maxlength="11" minlength="11" pattern="[0-9]{11}" placeholder="09xxxxxxxxx" class="w-full text-sm py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                            </div>
                            <button type="submit" class="w-full py-2.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-sm rounded-lg shadow-sm transition-all">Access Portal & Pass</button>
                        </form>
                    </div>
                </div>

                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                    <h2 class="text-base font-bold text-slate-900 mb-1">Register New Volunteer</h2>
                    <p class="text-xs text-slate-500 mb-4">Para sa mga bago at wala pang pass.</p>
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
                            <label class="block text-xs font-semibold text-slate-600 mb-1">Contact Number (11 digits)</label>
                            <input type="tel" name="contact" maxlength="11" minlength="11" pattern="[0-9]{11}" placeholder="09xxxxxxxxx" class="w-full text-sm py-2 px-3 border border-slate-300 rounded-lg focus:ring-2 focus:ring-blue-600 outline-none">
                        </div>
                        <button type="submit" class="w-full py-2.5 bg-slate-900 hover:bg-slate-800 text-white font-semibold text-sm rounded-lg shadow-sm transition-all">Register & Generate Pass</button>
                    </form>
                </div>
            </div>
        {% else %}
            <!-- LOGGED IN VIEW -->
            <div class="grid grid-cols-1 md:grid-cols-2 gap-6 items-start">
                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
                    
                    {% if active_record %}
                        <!-- TIME OUT CARD (WALANG RE-TYPING NA KAILANGAN) -->
                        <div class="flex items-center justify-between mb-2">
                            <h2 class="text-base font-bold text-slate-900">Current Session Active</h2>
                            <span class="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-100 text-emerald-800">
                                Clocked In
                            </span>
                        </div>
                        <p class="text-xs text-slate-500 mb-4">Kasalukuyan kang naka-time in. Pindutin lamang ang button sa ibaba para mag-Time Out.</p>

                        <div class="bg-slate-50 border border-slate-200 rounded-xl p-3 mb-4 space-y-2 text-xs">
                            <div class="flex justify-between">
                                <span class="text-slate-500">Agenda:</span>
                                <span class="font-bold text-slate-800">{{ active_record[1] }}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-slate-500">Assigned Task:</span>
                                <span class="font-bold text-slate-800">{{ active_record[2] }}</span>
                            </div>
                            <div class="flex justify-between">
                                <span class="text-slate-500">Time In:</span>
                                <span class="font-bold text-emerald-700">{{ active_record[3] }}</span>
                            </div>
                        </div>

                        <form action="/log-self-attendance" method="POST">
                            <button type="submit" class="w-full py-3 bg-rose-600 hover:bg-rose-700 text-white font-bold text-sm rounded-xl shadow transition-all flex items-center justify-center gap-2">
                                <span>🔴</span> Punch Time Out
                            </button>
                        </form>

                    {% else %}
                        <!-- TIME IN CARD (NORMAL FORM) -->
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

                <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm text-center">
                    <span class="inline-block bg-blue-50 text-blue-700 text-xs font-bold px-3 py-1 rounded-full uppercase mb-2">Official Volunteer</span>
                    <h3 class="text-lg font-bold">{{ user.name }}</h3>
                    <p class="text-xs text-slate-400 mb-3">{{ user.email }}</p>
                    <img src="/static/qrcodes/{{ user.qr_code }}" class="w-40 h-40 mx-auto rounded-lg border p-1 mb-3" onerror="this.outerHTML='<div class=\\'text-xs text-slate-400 my-8\\'>QR Pass Image generated</div>'">
                    <div class="text-xs font-mono font-bold bg-slate-100 py-1.5 px-3 rounded inline-block mb-3 border border-dashed border-slate-400">{{ user.volunteer_code }}</div><br>
                    <a href="/static/qrcodes/{{ user.qr_code }}" download class="inline-block py-2 px-4 bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold rounded-lg">Download Pass</a>
                </div>
            </div>
        {% endif %}

        <div class="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
            <div class="p-4 border-b font-bold text-sm text-slate-800">Attendance Log</div>
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
                                <form action="/delete-log/{{ log[5] }}" method="POST" onsubmit="return confirm('Sigurado ka bang buburahin ang attendance record na ito?');" class="inline">
                                    <button type="submit" class="bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 text-xs font-semibold px-2.5 py-1 rounded-lg transition-all">
                                        Delete
                                    </button>
                                </form>
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
    </main>

    {% if not user %}
    <script>
        let html5QrCode = null;
        let isScannerActive = false;

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
                    isScannerActive = true;
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
                if (html5QrCode && isScannerActive) {
                    html5QrCode.stop().then(() => { isScannerActive = false; });
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
    </script>
    {% endif %}
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
        # Kunin ang active record para malaman kung naka-Time In na
        cursor.execute(
            "SELECT id, agenda, task, time_in FROM attendance WHERE volunteer_id = %s AND time_out IS NULL ORDER BY id DESC LIMIT 1"
            if DATABASE_URL
            else "SELECT id, agenda, task, time_in FROM attendance WHERE volunteer_id = ? AND time_out IS NULL ORDER BY id DESC LIMIT 1",
            (user["id"],),
        )
        active_record = cursor.fetchone()

    cursor.execute(
        """
        SELECT volunteers.name, attendance.agenda, attendance.task, attendance.time_in, attendance.time_out, attendance.id
        FROM attendance
        JOIN volunteers ON attendance.volunteer_id = volunteers.id
        ORDER BY attendance.id DESC
        LIMIT 50
    """
    )
    logs = cursor.fetchall()
    cursor.close()
    conn.close()

    if os.path.exists(os.path.join("templates", "index.html")):
        return render_template("index.html", user=user, logs=logs, active_record=active_record)

    return render_template_string(MAIN_TEMPLATE, user=user, logs=logs, active_record=active_record)


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
            "qr_code": user[3],
            "volunteer_code": user[4],
        }
        flash(f"✅ Welcome back, {user[1]}! (Logged in via QR Pass)", "success")
        return jsonify({"success": True})

    return jsonify({"success": False, "message": "❌ Invalid o hindi kinikilalang QR Code."})


@app.route("/login-code", methods=["POST"])
def login_code():
    code = request.form.get("volunteer_code", "").strip()
    user = authenticate_user_by_qr(code)

    if user:
        session["user"] = {
            "id": user[0],
            "name": user[1],
            "email": user[2],
            "qr_code": user[3],
            "volunteer_code": user[4],
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

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, email, qr_code, volunteer_code FROM volunteers WHERE email = %s AND contact = %s"
        if DATABASE_URL
        else "SELECT id, name, email, qr_code, volunteer_code FROM volunteers WHERE email = ? AND contact = ?",
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
            "qr_code": user[3],
            "volunteer_code": user[4],
        }
        flash(f"✅ Welcome back, {user[1]}!", "success")
    else:
        flash("❌ Walang profile na tumugma sa email o contact number na nilagay.", "danger")

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
        (user["id"],),
    )
    active_record = cursor.fetchone()

    # Kung may active session, Time Out agad nang hindi humihingi ng bagong input
    if active_record:
        cursor.execute(
            "UPDATE attendance SET time_out = %s WHERE id = %s"
            if DATABASE_URL
            else "UPDATE attendance SET time_out = ? WHERE id = ?",
            (now, active_record[0]),
        )
        flash(f"🔴 TIME OUT recorded for {user['name']} ({now})", "success")
    else:
        agenda = request.form.get("agenda", "").strip()
        task = request.form.get("task", "").strip()
        agenda_val = agenda if agenda else "General Assembly"
        task_val = task if task else "Volunteer Duty"

        cursor.execute(
            "INSERT INTO attendance (volunteer_id, agenda, task, time_in) VALUES (%s, %s, %s, %s)"
            if DATABASE_URL
            else "INSERT INTO attendance (volunteer_id, agenda, task, time_in) VALUES (?, ?, ?, ?)",
            (user["id"], agenda_val, task_val, now),
        )
        flash(f"🟢 TIME IN recorded for {user['name']} | Agenda: {agenda_val} ({now})", "success")

    conn.commit()
    cursor.close()
    conn.close()

    return redirect(url_for("index"))


@app.route("/register", methods=["POST"])
def register():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    contact = request.form.get("contact", "").strip()

    if not email.endswith("@gmail.com") or len(email) <= 10:
        flash("❌ Valid @gmail.com address lamang ang tinatanggap!", "danger")
        return redirect(url_for("index"))

    if len(name) > 50 or len(name) < 2:
        flash("❌ Ang pangalan ay dapat nasa pagitan ng 2 hanggang 50 characters.", "danger")
        return redirect(url_for("index"))

    if len(contact) != 11 or not contact.isdigit():
        flash("❌ Ang contact number ay dapat eksaktong 11 digits.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, name FROM volunteers WHERE email = %s" if DATABASE_URL else "SELECT id, name FROM volunteers WHERE email = ?",
        (email,),
    )
    existing_user = cursor.fetchone()
    if existing_user:
        cursor.close()
        conn.close()
        flash(f"⚠️ Registered ka na, {existing_user[1]}! Mag-login ka na lamang gamit ang iyong QR o Contact Number.", "warning")
        return redirect(url_for("index"))

    auth_token = secrets.token_hex(16)
    unique_volunteer_code = f"KABS-{secrets.token_hex(2).upper()}"

    if DATABASE_URL:
        cursor.execute(
            "INSERT INTO volunteers (name, email, contact, auth_token, volunteer_code) VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (name, email, contact, auth_token, unique_volunteer_code),
        )
        v_id = cursor.fetchone()[0]
    else:
        cursor.execute(
            "INSERT INTO volunteers (name, email, contact, auth_token, volunteer_code) VALUES (?, ?, ?, ?, ?)",
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
        "UPDATE volunteers SET qr_code = %s WHERE id = %s" if DATABASE_URL else "UPDATE volunteers SET qr_code = ? WHERE id = ?",
        (qr_filename, v_id),
    )
    conn.commit()
    cursor.close()
    conn.close()

    session["user"] = {
        "id": v_id,
        "name": name,
        "email": email,
        "volunteer_code": unique_volunteer_code,
        "qr_code": qr_filename,
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
        flash("Kailangan munang mag-login para makapagbura ng record.", "danger")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM attendance WHERE id = %s"
        if DATABASE_URL
        else "DELETE FROM attendance WHERE id = ?",
        (log_id,),
    )
    conn.commit()
    cursor.close()
    conn.close()

    flash("🗑️ Matagumpay na nabura ang attendance log!", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
