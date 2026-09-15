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
    render_template_string,
    request,
    session,
    url_for,
)
import qrcode

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "kabs_attendance_secret_key")

# Kapag may persistent disk sa Render (/var/data), doon ise-save para hindi mabura
DATA_DIR = "/var/data" if os.path.exists("/var/data") else "."
DB_PATH = os.path.join(DATA_DIR, "kabs.db")

QR_FOLDER = os.path.join("static", "qrcodes")
os.makedirs(QR_FOLDER, exist_ok=True)


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
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
            time_in TEXT,
            time_out TEXT,
            FOREIGN KEY (volunteer_id) REFERENCES volunteers (id)
        )
    """
    )

    cursor.execute("PRAGMA table_info(volunteers)")
    columns = [col[1] for col in cursor.fetchall()]
    if "volunteer_code" not in columns:
        cursor.execute("ALTER TABLE volunteers ADD COLUMN volunteer_code TEXT")

    cursor.execute("SELECT id FROM volunteers WHERE volunteer_code IS NULL")
    missing_codes = cursor.fetchall()
    for row in missing_codes:
        new_code = f"KABS-{secrets.token_hex(2).upper()}"
        cursor.execute("UPDATE volunteers SET volunteer_code = ? WHERE id = ?", (new_code, row[0]))

    conn.commit()
    conn.close()


init_db()


MAIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>KABS Attendance Portal</title>
    <script src="https://unpkg.com/html5-qrcode"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background-color: #f1f5f9; color: #1e293b; padding: 15px; }
        
        .header {
            background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
            color: white;
            padding: 14px 20px;
            border-radius: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.1);
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 10px;
        }
        .brand { display: flex; align-items: center; gap: 12px; }
        .brand-logo { width: 48px; height: 48px; border-radius: 8px; object-fit: cover; background: #ffffff; padding: 2px; }
        .brand h1 { font-size: 1.15rem; line-height: 1.2; font-weight: 700; margin: 0; }
        .sub-title { font-size: 0.75rem; color: #94a3b8; }
        .nav-btn { background: rgba(255, 255, 255, 0.1); color: #f8fafc; padding: 8px 14px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; font-weight: 600; }
        .nav-btn:hover { background: rgba(255, 255, 255, 0.25); }
        .user-greeting { margin-right: 10px; font-size: 0.85rem; color: #cbd5e1; }
        
        .grid-layout { display: flex; flex-direction: column; gap: 20px; }
        @media (min-width: 850px) {
            .grid-layout { display: grid; grid-template-columns: 1fr 1fr; align-items: start; }
            body { max-width: 1200px; margin: 0 auto; padding: 25px; }
        }

        .card { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); border: 1px solid #e2e8f0; }
        .card h2 { font-size: 1.15rem; margin-bottom: 15px; color: #0f172a; }

        label { display: block; font-size: 0.85rem; font-weight: 600; margin-bottom: 6px; color: #475569; }
        input[type=text], input[type=email], input[type=tel] { width: 100%; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 0.95rem; margin-bottom: 15px; outline: none; }
        input:focus { border-color: #2563eb; }

        button, .btn { display: inline-block; width: 100%; text-align: center; background-color: #2563eb; color: white; padding: 12px; border: none; border-radius: 8px; font-size: 0.95rem; font-weight: 600; cursor: pointer; text-decoration: none; }
        button:hover, .btn:hover { background-color: #1d4ed8; }

        .alert { padding: 12px; border-radius: 8px; margin-bottom: 15px; font-size: 0.9rem; font-weight: 500; }
        .success { background: #dcfce7; border: 1px solid #86efac; color: #166534; }
        .danger { background: #fee2e2; border: 1px solid #fca5a5; color: #991b1b; }
        .warning { background: #fef3c7; border: 1px solid #fcd34d; color: #92400e; }

        #reader { width: 100% !important; border-radius: 8px; overflow: hidden; border: none !important; }
        #reader video { border-radius: 8px; }

        .table-responsive { width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; margin-top: 10px; }
        table { width: 100%; border-collapse: collapse; min-width: 480px; font-size: 0.85rem; }
        th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
        th { background: #f8fafc; font-weight: 600; color: #64748b; }
        
        .tag-in { color: #16a34a; font-weight: 600; }
        .tag-out { color: #dc2626; font-weight: 600; }
        .tag-pending { color: #d97706; font-style: italic; }

        .pass-code-pill {
            display: inline-block; background: #e2e8f0; color: #0f172a; padding: 5px 12px; border-radius: 20px; font-size: 1rem; font-weight: 700; letter-spacing: 1px; margin-top: 8px; border: 1px dashed #64748b;
        }
    </style>
</head>
<body>

    <div class="header">
        <div class="brand">
            <img src="/static/images/logo.jpg" alt="KABS Logo" class="brand-logo" onerror="this.style.display='none'">
            <div>
                <h1>KABATAAN PARA SA AKSYON, BAYANIHAN, AT SERBISYO - KABS</h1>
                <span class="sub-title">Volunteer Attendance System</span>
            </div>
        </div>
        <div>
            {% if user %}
                <span class="user-greeting">Welcome, <b>{{ user.name }}</b></span>
                <a href="/logout" class="nav-btn">Logout</a>
            {% else %}
                <a href="#login-box" class="nav-btn">Login</a>
            {% endif %}
        </div>
    </div>

    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        {% for category, message in messages %}
          <div class="alert {{ category }}">{{ message | safe }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    <div class="grid-layout">
        <div class="card">
            <h2>📷 Attendance Scanner</h2>
            <p style="font-size: 0.85rem; color: #64748b; margin-bottom: 15px;">Scan QR badge directly via camera (TIME IN / OUT)</p>
            <div id="reader"></div>
            <div id="scan-status" style="margin-top: 12px; font-size: 0.9rem; font-weight: 600;"></div>

            <hr style="margin: 20px 0; border: none; border-top: 1px solid #e2e8f0;">
            <form action="/scan-manual" method="POST">
                <label>Manual Input / Fallback</label>
                <input type="text" name="qr_payload" placeholder="Type Unique Volunteer Code (e.g. KABS-4F2A)" style="text-transform: uppercase;" required>
                <button type="submit">Submit Attendance</button>
            </form>
        </div>

        <div class="card">
            {% if user %}
                <div style="text-align: center;">
                    <h2>Verified Volunteer Pass</h2>
                    <p style="font-size: 1.1rem; font-weight: bold; margin-bottom: 4px;">{{ user.name }}</p>
                    <p style="color: #16a34a; font-weight: 600; font-size: 0.85rem; margin-bottom: 12px;">AUTHENTICATED PASS</p>
                    
                    <img src="/static/qrcodes/{{ user.qr_code }}" style="max-width: 180px; width: 100%; border: 2px solid #0f172a; border-radius: 8px;"><br>
                    
                    <div style="margin-bottom: 15px;">
                        <span style="font-size: 0.75rem; color: #64748b; display: block; margin-top: 8px;">Fallback Manual Code:</span>
                        <span class="pass-code-pill">{{ user.volunteer_code }}</span>
                    </div>

                    <a href="/static/qrcodes/{{ user.qr_code }}" download class="btn">Save / Download QR</a>
                </div>
            {% else %}
                <div id="login-box">
                    <h2>Volunteer Login</h2>
                    <p style="font-size: 0.8rem; color: #64748b; margin-bottom: 12px;">Registered na? Mag-login gamit ang iyong Gmail at Contact Number.</p>
                    <form action="/login" method="POST">
                        <label>Email Address (@gmail.com only)</label>
                        <input type="email" name="email" required placeholder="juandelacruz@gmail.com">
                        <label>Contact Number (11 digits)</label>
                        <input type="tel" name="contact" maxlength="11" minlength="11" pattern="[0-9]{11}" required placeholder="09xxxxxxxxx">
                        <button type="submit">Access Profile & QR</button>
                    </form>
                </div>

                <hr style="margin: 20px 0; border: none; border-top: 1px solid #e2e8f0;">

                <div>
                    <h2>Register New Volunteer</h2>
                    <p style="font-size: 0.8rem; color: #64748b; margin-bottom: 12px;">Para lamang sa mga bago at wala pang account.</p>
                    <form action="/register" method="POST">
                        <label>Full Name (Max 50 chars)</label>
                        <input type="text" name="name" maxlength="50" required placeholder="Juan Dela Cruz">
                        <label>Email Address (@gmail.com only)</label>
                        <input type="email" name="email" required placeholder="juandelacruz@gmail.com">
                        <label>Contact Number (11 digits)</label>
                        <input type="tel" name="contact" maxlength="11" minlength="11" pattern="[0-9]{11}" required placeholder="09xxxxxxxxx">
                        <button type="submit" style="background-color: #0f172a;">Register & Generate Pass</button>
                    </form>
                </div>
            {% endif %}
        </div>
    </div>

    <div class="card" style="margin-top: 20px;">
        <h2>All-Time Attendance Records</h2>
        <div class="table-responsive">
            <table>
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Time In</th>
                        <th>Time Out</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {% for log in logs %}
                    <tr>
                        <td><b>{{ log[0] }}</b></td>
                        <td class="tag-in">{{ log[1] }}</td>
                        <td class="{{ 'tag-out' if log[2] else 'tag-pending' }}">{{ log[2] if log[2] else 'Clocked In' }}</td>
                        <td><span style="color:#16a34a; font-weight:600;">Verified</span></td>
                    </tr>
                    {% else %}
                    <tr><td colspan="4" style="text-align:center; color:#94a3b8;">No records logged yet.</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <script>
        function onScanSuccess(decodedText) {
            html5QrcodeScanner.clear();
            document.getElementById('scan-status').innerHTML = "⏳ Processing record...";
            
            fetch('/scan-api', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ qr_payload: decodedText })
            })
            .then(res => res.json())
            .then(data => {
                alert(data.message);
                location.reload();
            })
            .catch(() => {
                alert("Scan submission error!");
                location.reload();
            });
        }

        let html5QrcodeScanner = new Html5QrcodeScanner("reader", { 
            fps: 10, 
            qrbox: { width: 220, height: 220 },
            aspectRatio: 1.0
        });
        html5QrcodeScanner.render(onScanSuccess);
    </script>
</body>
</html>
"""


def process_qr_data(qr_data_str):
    qr_data_str = qr_data_str.strip()
    v_id = None
    name = None

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        data = json.loads(qr_data_str)
        if data.get("system") == "KABS_SECURE_AUTH":
            v_id = data.get("id")
            token = data.get("token")
            cursor.execute(
                "SELECT id, name FROM volunteers WHERE id = ? AND auth_token = ?",
                (v_id, token),
            )
            volunteer = cursor.fetchone()
            if volunteer:
                v_id, name = volunteer
    except Exception:
        pass

    if not v_id:
        cursor.execute(
            "SELECT id, name FROM volunteers WHERE UPPER(volunteer_code) = UPPER(?)",
            (qr_data_str,),
        )
        volunteer = cursor.fetchone()
        if volunteer:
            v_id, name = volunteer

    if not v_id:
        conn.close()
        return False, f"❌ Invalid code: '{qr_data_str}' not recognized."

    now = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")

    cursor.execute(
        "SELECT id FROM attendance WHERE volunteer_id = ? AND time_out IS NULL ORDER BY id DESC LIMIT 1",
        (v_id,),
    )
    active_record = cursor.fetchone()

    if active_record:
        cursor.execute(
            "UPDATE attendance SET time_out = ? WHERE id = ?",
            (now, active_record[0]),
        )
        msg = f"🔴 TIME OUT recorded for {name} ({now})"
    else:
        cursor.execute(
            "INSERT INTO attendance (volunteer_id, time_in) VALUES (?, ?)",
            (v_id, now),
        )
        msg = f"🟢 TIME IN recorded for {name} ({now})"

    conn.commit()
    conn.close()
    return True, msg


@app.route("/")
def index():
    user = session.get("user")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT volunteers.name, attendance.time_in, attendance.time_out
        FROM attendance
        JOIN volunteers ON attendance.volunteer_id = volunteers.id
        ORDER BY attendance.id DESC
    """
    )
    logs = cursor.fetchall()
    conn.close()
    return render_template_string(MAIN_TEMPLATE, user=user, logs=logs)


@app.route("/register", methods=["POST"])
def register():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    contact = request.form.get("contact", "").strip()

    if not email.endswith("@gmail.com") or len(email) <= 10:
        flash("❌ Valid @gmail.com address lamang ang tinatanggap!", "danger")
        return redirect(url_for("index"))

    if len(name) > 50 or len(name) < 2:
        flash("❌ Name must be between 2 and 50 characters long.", "danger")
        return redirect(url_for("index"))

    if len(contact) != 11 or not contact.isdigit():
        flash("❌ Contact number must be exactly 11 digits.", "danger")
        return redirect(url_for("index"))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Haharangin kapag nakarehistro na ang email
    cursor.execute("SELECT id, name FROM volunteers WHERE email = ?", (email,))
    existing_user = cursor.fetchone()
    if existing_user:
        conn.close()
        flash(f"⚠️ Registered ka na, {existing_user[1]}! Mag-login ka na lamang sa itaas gamit ang iyong Contact Number.", "warning")
        return redirect(url_for("index") + "#login-box")

    auth_token = secrets.token_hex(16)
    unique_volunteer_code = f"KABS-{secrets.token_hex(2).upper()}"

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
        "UPDATE volunteers SET qr_code = ? WHERE id = ?",
        (qr_filename, v_id),
    )
    conn.commit()
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


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()
    contact = request.form.get("contact", "").strip()

    if not email.endswith("@gmail.com"):
        flash("❌ Email must end with @gmail.com", "danger")
        return redirect(url_for("index"))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, email, qr_code, volunteer_code FROM volunteers WHERE email = ? AND contact = ?",
        (email, contact),
    )
    user = cursor.fetchone()
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


@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Naka-log out ka na.", "success")
    return redirect(url_for("index"))


@app.route("/scan-api", methods=["POST"])
def scan_api():
    payload = request.json.get("qr_payload", "")
    success, message = process_qr_data(payload)
    return jsonify({"success": success, "message": message})


@app.route("/scan-manual", methods=["POST"])
def scan_manual():
    payload = request.form.get("qr_payload", "").strip()
    success, message = process_qr_data(payload)
    flash(message, "success" if success else "danger")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80, debug=False)
