import os
import uuid
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, flash
from dotenv import load_dotenv
from supabase import create_client, Client
from werkzeug.security import generate_password_hash, check_password_hash
import random

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")

# Supabase setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# --- Auth Routes ---

@app.route("/", methods=["GET"])
def index():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        password = request.form.get("password")

        existing = supabase.table("users").select("id").eq("email", email).execute()
        if existing.data:
            flash("Email already registered.", "error")
            return redirect(url_for("signup"))

        hashed_pw = generate_password_hash(password)
        script_id = str(uuid.uuid4())

        result = supabase.table("users").insert({
            "name": name,
            "email": email,
            "password": hashed_pw,
            "script_id": script_id
        }).execute()

        user = result.data[0]
        session["user_id"] = user["id"]
        session["name"] = user["name"]
        session["script_id"] = user["script_id"]

        flash("Account created successfully!", "success")
        return redirect(url_for("dashboard"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        result = supabase.table("users").select("*").eq("email", email).execute()

        if not result.data:
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        user = result.data[0]

        if not check_password_hash(user["password"], password):
            flash("Invalid email or password.", "error")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        session["name"] = user["name"]
        session["script_id"] = user["script_id"]

        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("login"))


# --- Dashboard ---
@app.route("/dashboard")
def dashboard():
    if not session.get("user_id"):
        return redirect(url_for("login"))

    script_id = session.get("script_id")
    
    # Fetch ALL events
    result = supabase.table("events") \
        .select("*") \
        .eq("script_id", script_id) \
        .order("created_at", desc=True) \
        .execute()

    raw_events = result.data
    
    # 1. Prepare Data for Chart (Counting occurrences of each unique step)
    steps_count = {}
    for row in raw_events:
        step = row["step"]
        # Grouping by the unique step name from Supabase
        steps_count[step] = steps_count.get(step, 0) + 1

    # Convert these to lists so Chart.js can read them as labels and values
    chart_labels = list(steps_count.keys())
    chart_values = list(steps_count.values())

    # 2. Fix the Sessions Table (Grouping by unique Session ID)
    user_sessions_dict = {}
    for row in reversed(raw_events): # Process oldest to newest so the 'latest' state wins
        sid = row["session_id"]
        user_sessions_dict[sid] = {
            "session_id": sid,
            "email": row.get("email"),
            "last_step": row["step"],
            "created_at": row["created_at"]
        }

    user_sessions = list(user_sessions_dict.values())
    user_sessions.reverse() # Show newest sessions at the top

    return render_template("dashboard.html",
                           script_id=script_id,
                           chart_labels=chart_labels,
                           chart_values=chart_values,
                           user_sessions=user_sessions)


# --- Track Endpoint ---

@app.route("/track", methods=["POST", "OPTIONS"])
def track():
    # Handle CORS Pre-flight
    if request.method == "OPTIONS":
        response = jsonify({"ok": True})
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        return response

    data = request.get_json()
    print("🔥 TRACK HIT:", data)

    # Extract all fields including the new 'email' field
    script_id = data.get("script_id")
    step = data.get("step")
    url = data.get("url", "")
    session_id = data.get("session_id", "")
    email = data.get("email") # Get email from the script

    # Basic validation
    if not script_id or not step:
        print("❌ Missing required fields: script_id or step")
        return jsonify({"error": "Missing required fields"}), 400

    try:
        # Build the payload
        payload = {
            "script_id": script_id,
            "step": step,
            "url": url,
            "session_id": session_id
        }

        # Only add email to payload if it's actually provided
        if email:
            payload["email"] = email
            print(f"📧 Captured Identity: {email}")

        # Insert into Supabase
        result = supabase.table("events").insert(payload).execute()
        print("✅ Inserted into Supabase:", result.data)

    except Exception as e:
        print("❌ Supabase insertion error:", e)
        return jsonify({"error": str(e)}), 500

    # Success Response with CORS
    response = jsonify({"ok": True})
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response

@app.route('/nudge/<session_id>')
def generate_nudge(session_id):
    # 1. Fetch the last event for this session to see where they stopped
    result = supabase.table("events").select("*").eq("session_id", session_id).order("created_at", desc=True).limit(1).execute()
    
    if not result.data:
        return jsonify({"error": "No data found"}), 404
        
    event = result.data[0]
    step = event.get('step', 'Home')

    # 2. Heuristic message generation
    messages = {
        "signup": "It looks like you started signing up but didn't finish! Need a hand?",
        "pricing": "Still checking out our plans? We'd love to help you find the right fit.",
        "checkout": "You're almost there! Your cart is waiting for you. Complete your order now."
    }
    
    selected_msg = messages.get(step, "We noticed you left—is there anything we can do to help you finish your journey?")

    return jsonify({
        "message": selected_msg,
        "session": session_id,
        "last_step": step
    })


# --- Debug Route ---

@app.route("/debug/events")
def debug_events():
    script_id = session.get("script_id")
    result = supabase.table("events").select("*").eq("script_id", script_id).execute()
    return jsonify(result.data)


@app.route('/flowtrace.js')
def flowtrace_js():
    script_id = request.args.get('id')
    host = request.host_url.rstrip('/')
    
    js_code = f"""
    (function() {{
        const scriptId = "{script_id}";
        const host = "{host}";
        
        let sessionId = localStorage.getItem('ft_session_id');
        if (!sessionId) {{
            sessionId = 'sess_' + Math.random().toString(36).substr(2, 9);
            localStorage.setItem('ft_session_id', sessionId);
        }}

        // PERSISTENCE: Get saved email if we already found it before
        let savedEmail = localStorage.getItem('ft_user_email') || null;

        window.FlowTrace = {{
            track: function(step, email = null) {{
                // If a new email is provided, save it locally
                if (email) {{
                    savedEmail = email;
                    localStorage.setItem('ft_user_email', email);
                }}

                fetch(host + "/track", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    mode: "cors",
                    body: JSON.stringify({{
                        script_id: scriptId,
                        step: step,
                        email: savedEmail, // Always send the saved email if it exists
                        url: window.location.href,
                        session_id: sessionId
                    }})
                }}).catch(err => console.error("FlowTrace Error:", err));
            }}
        }};

        // 1. Auto-track Page Load (will include email if already known)
        window.FlowTrace.track(window.location.pathname);

        // 2. Identity Listener
        document.addEventListener('blur', function(e) {{
            if (e.target.type === 'email' && e.target.value.includes('@')) {{
                console.log("📧 Identity captured: " + e.target.value);
                // This call will update 'savedEmail' and push it to DB
                window.FlowTrace.track("identity_captured", e.target.value);
            }}
        }}, true);

    }})();
    """
    return Response(js_code, mimetype='application/javascript')

if __name__ == "__main__":
    app.run()