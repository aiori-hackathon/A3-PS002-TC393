"""Local synthetic demo of purpose-based consent and data-access enforcement."""

from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    url_for,
)


APP_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = APP_DIR / "instance"
DATABASE = INSTANCE_DIR / "consent.sqlite3"
SUBJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ALLOWED_ACTIONS = {"GRANTED", "REFUSED", "WITHDRAWN"}

PURPOSE_SEEDS = [
    {
        "purpose_id": "project_updates",
        "name": "Project updates",
        "description": "Send occasional updates about this project and its features.",
        "data_items": ["synthetic update channel", "synthetic update frequency"],
        "allowed_fields": ["channel", "frequency"],
        "demo_data": {"channel": "demo inbox", "frequency": "monthly"},
    },
    {
        "purpose_id": "service_analytics",
        "name": "Service improvement analytics",
        "description": "Use activity information to understand and improve the service.",
        "data_items": ["synthetic activity count", "synthetic feature usage summary"],
        "allowed_fields": ["activity_count", "feature_usage_summary"],
        "demo_data": {
            "activity_count": 12,
            "feature_usage_summary": "consent demo page",
        },
    },
]

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)


@app.before_request
def ensure_database_ready():
    """Initialize or migrate the active database once per configured path."""
    active_path = str(Path(DATABASE).resolve())
    if app.config.get("INITIALIZED_DATABASE_PATH") != active_path:
        initialize_database()
        app.config["INITIALIZED_DATABASE_PATH"] = active_path


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_notice_text(name, description, data_items):
    """Create the clear, itemized notice used by this synthetic prototype."""
    items = "; ".join(data_items)
    return (
        f"Purpose: {name}.\n"
        f"Data used: {items}.\n"
        f"How it is used: {description}\n"
        "This is a synthetic demonstration and uses no real personal data. "
        "You may refuse or withdraw this purpose separately at any time."
    )


def open_database():
    """Open SQLite and enable foreign-key checks for this connection."""
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def ensure_column(connection, table, column, declaration):
    existing = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def append_audit_event(
    connection,
    *,
    event_type,
    subject_id=None,
    purpose_id=None,
    action=None,
    decision=None,
    occurred_at=None,
    notice_version=None,
    notice_hash=None,
    request_fields=None,
    details=None,
    source_consent_event_id=None,
):
    cursor = connection.execute(
        """
        INSERT INTO audit_events (
            event_type, subject_id, purpose_id, action, decision, occurred_at,
            notice_version, notice_hash, request_fields, details,
            source_consent_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_type,
            subject_id,
            purpose_id,
            action,
            decision,
            occurred_at or utc_now(),
            notice_version,
            notice_hash,
            json_text(request_fields or []),
            json_text(details or {}),
            source_consent_event_id,
        ),
    )
    return cursor.lastrowid


def initialize_database():
    """Create the schema and migrate the original demo database in place."""
    connection = open_database()
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS purposes (
                purpose_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                notice_version TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS consent_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                subject_id TEXT NOT NULL,
                purpose_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK (
                    action IN ('GRANTED', 'REFUSED', 'WITHDRAWN')
                ),
                occurred_at TEXT NOT NULL,
                notice_version TEXT NOT NULL,
                actor_type TEXT NOT NULL DEFAULT 'demo_subject',
                FOREIGN KEY (purpose_id) REFERENCES purposes(purpose_id)
            );

            CREATE TABLE IF NOT EXISTS notice_versions (
                notice_id INTEGER PRIMARY KEY AUTOINCREMENT,
                purpose_id TEXT NOT NULL,
                notice_version TEXT NOT NULL,
                notice_text TEXT NOT NULL,
                notice_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (purpose_id, notice_version)
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                subject_id TEXT,
                purpose_id TEXT,
                action TEXT,
                decision TEXT,
                occurred_at TEXT NOT NULL,
                notice_version TEXT,
                notice_hash TEXT,
                request_fields TEXT NOT NULL DEFAULT '[]',
                details TEXT NOT NULL DEFAULT '{}',
                source_consent_event_id INTEGER UNIQUE
            );

            CREATE INDEX IF NOT EXISTS idx_consent_subject_purpose_event
                ON consent_events(subject_id, purpose_id, event_id DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_subject_event
                ON audit_events(subject_id, audit_id DESC);
            """
        )

        # Additive migration keeps the events already recorded by the first demo.
        ensure_column(connection, "purposes", "data_items", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(connection, "purposes", "allowed_fields", "TEXT NOT NULL DEFAULT '[]'")
        ensure_column(connection, "purposes", "demo_data", "TEXT NOT NULL DEFAULT '{}'" )
        ensure_column(connection, "purposes", "notice_text", "TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, "purposes", "notice_hash", "TEXT NOT NULL DEFAULT ''")
        ensure_column(connection, "consent_events", "notice_hash", "TEXT NOT NULL DEFAULT ''")

        for seed in PURPOSE_SEEDS:
            row = connection.execute(
                "SELECT * FROM purposes WHERE purpose_id = ?",
                (seed["purpose_id"],),
            ).fetchone()
            if row is None:
                version = "1"
                notice = make_notice_text(
                    seed["name"], seed["description"], seed["data_items"]
                )
                connection.execute(
                    """
                    INSERT INTO purposes (
                        purpose_id, name, description, notice_version, active,
                        data_items, allowed_fields, demo_data, notice_text, notice_hash
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        seed["purpose_id"],
                        seed["name"],
                        seed["description"],
                        version,
                        json_text(seed["data_items"]),
                        json_text(seed["allowed_fields"]),
                        json_text(seed["demo_data"]),
                        notice,
                        sha256_text(notice),
                    ),
                )
            else:
                version = row["notice_version"] or "1"
                notice = row["notice_text"] or make_notice_text(
                    row["name"], row["description"], seed["data_items"]
                )
                connection.execute(
                    """
                    UPDATE purposes
                    SET data_items = ?, allowed_fields = ?, demo_data = ?,
                        notice_text = ?, notice_hash = ?
                    WHERE purpose_id = ?
                    """,
                    (
                        json_text(seed["data_items"]),
                        json_text(seed["allowed_fields"]),
                        json_text(seed["demo_data"]),
                        notice,
                        sha256_text(notice),
                        seed["purpose_id"],
                    ),
                )

            current = connection.execute(
                "SELECT * FROM purposes WHERE purpose_id = ?",
                (seed["purpose_id"],),
            ).fetchone()
            connection.execute(
                """
                INSERT OR IGNORE INTO notice_versions
                    (purpose_id, notice_version, notice_text, notice_hash, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    current["purpose_id"],
                    current["notice_version"],
                    current["notice_text"],
                    current["notice_hash"],
                    utc_now(),
                ),
            )

        # Backfill the original consent history into the unified audit export.
        old_events = connection.execute(
            """
            SELECT event_id, subject_id, purpose_id, action, occurred_at,
                   notice_version, notice_hash
            FROM consent_events ORDER BY event_id
            """
        ).fetchall()
        for event in old_events:
            notice_hash = event["notice_hash"]
            if not notice_hash:
                # The earlier prototype stored only a version label, not the
                # exact notice text. Do not claim that such a grant matches the
                # expanded notice now on file; require fresh consent instead.
                notice_hash = sha256_text(
                    f"legacy:{event['purpose_id']}:{event['notice_version']}"
                )
                connection.execute(
                    "UPDATE consent_events SET notice_hash = ? WHERE event_id = ?",
                    (notice_hash, event["event_id"]),
                )

            already_migrated = connection.execute(
                "SELECT 1 FROM audit_events WHERE source_consent_event_id = ?",
                (event["event_id"],),
            ).fetchone()
            if already_migrated is None:
                append_audit_event(
                    connection,
                    event_type="CONSENT_CHANGE",
                    subject_id=event["subject_id"],
                    purpose_id=event["purpose_id"],
                    action=event["action"],
                    decision="ALLOW" if event["action"] == "GRANTED" else "DENY",
                    occurred_at=event["occurred_at"],
                    notice_version=event["notice_version"],
                    notice_hash=notice_hash,
                    details={"migrated_from": "consent_events"},
                    source_consent_event_id=event["event_id"],
                )
        connection.commit()
    finally:
        connection.close()


def valid_subject_id(subject_id):
    """Accept a short synthetic ID, not a name, email, or real identifier."""
    return bool(SUBJECT_ID_PATTERN.fullmatch(subject_id or ""))


def latest_event(connection, subject_id, purpose_id):
    return connection.execute(
        """
        SELECT event_id, action, occurred_at, notice_version, notice_hash
        FROM consent_events
        WHERE subject_id = ? AND purpose_id = ?
        ORDER BY event_id DESC
        LIMIT 1
        """,
        (subject_id, purpose_id),
    ).fetchone()


def status_for(connection, subject_id, purpose):
    """Derive current consent; missing, stale, or non-grant states deny access."""
    event = latest_event(connection, subject_id, purpose["purpose_id"])
    current_version = purpose["notice_version"]
    current_hash = purpose["notice_hash"]
    if event is None:
        return {
            "status": "NOT_RECORDED",
            "decision": "DENY",
            "effective_at": None,
            "event_id": None,
            "consent_notice_version": None,
            "consent_notice_hash": None,
            "current_notice_version": current_version,
            "current_notice_hash": current_hash,
        }

    status = event["action"]
    decision = "ALLOW" if status == "GRANTED" else "DENY"
    if status == "GRANTED" and (
        event["notice_version"] != current_version
        or event["notice_hash"] != current_hash
    ):
        status = "RECONSENT_REQUIRED"
        decision = "DENY"

    return {
        "status": status,
        "decision": decision,
        "effective_at": event["occurred_at"],
        "event_id": event["event_id"],
        "consent_notice_version": event["notice_version"],
        "consent_notice_hash": event["notice_hash"],
        "current_notice_version": current_version,
        "current_notice_hash": current_hash,
    }


def record_consent_event(subject_id, purpose, action):
    """Append consent and matching audit events in a single transaction."""
    occurred_at = utc_now()
    connection = open_database()
    try:
        cursor = connection.execute(
            """
            INSERT INTO consent_events (
                subject_id, purpose_id, action, occurred_at,
                notice_version, actor_type, notice_hash
            ) VALUES (?, ?, ?, ?, ?, 'demo_subject', ?)
            """,
            (
                subject_id,
                purpose["purpose_id"],
                action,
                occurred_at,
                purpose["notice_version"],
                purpose["notice_hash"],
            ),
        )
        event_id = cursor.lastrowid
        append_audit_event(
            connection,
            event_type="CONSENT_CHANGE",
            subject_id=subject_id,
            purpose_id=purpose["purpose_id"],
            action=action,
            decision="ALLOW" if action == "GRANTED" else "DENY",
            occurred_at=occurred_at,
            notice_version=purpose["notice_version"],
            notice_hash=purpose["notice_hash"],
            details={"actor_type": "demo_subject"},
            source_consent_event_id=event_id,
        )
        connection.commit()
        return event_id
    finally:
        connection.close()


def audit_data_request(
    connection,
    *,
    subject_id,
    purpose_id,
    decision,
    action,
    fields=None,
    purpose=None,
    details=None,
):
    return append_audit_event(
        connection,
        event_type="DATA_REQUEST",
        subject_id=subject_id,
        purpose_id=purpose_id,
        action=action,
        decision=decision,
        notice_version=purpose["notice_version"] if purpose else None,
        notice_hash=purpose["notice_hash"] if purpose else None,
        request_fields=fields or [],
        details=details or {},
    )


@app.get("/")
def home():
    subject_id = request.args.get("subject_id", "demo-001").strip()
    if not valid_subject_id(subject_id):
        subject_id = "demo-001"

    connection = open_database()
    try:
        purposes = connection.execute(
            "SELECT * FROM purposes WHERE active = 1 ORDER BY name"
        ).fetchall()
        purpose_cards = []
        for purpose_row in purposes:
            card = dict(purpose_row)
            card["data_items"] = json.loads(card["data_items"])
            card["allowed_fields"] = json.loads(card["allowed_fields"])
            card["current"] = status_for(connection, subject_id, purpose_row)
            purpose_cards.append(card)

        history = connection.execute(
            """
            SELECT a.audit_id, a.event_type, a.action, a.decision, a.occurred_at,
                   a.notice_version, a.notice_hash, a.request_fields, a.details,
                   p.name AS purpose_name
            FROM audit_events AS a
            LEFT JOIN purposes AS p ON p.purpose_id = a.purpose_id
            WHERE a.subject_id = ?
            ORDER BY a.audit_id DESC
            LIMIT 25
            """,
            (subject_id,),
        ).fetchall()
    finally:
        connection.close()

    return render_template(
        "index.html",
        subject_id=subject_id,
        purposes=purpose_cards,
        history=history,
    )


@app.post("/consent")
def save_consent():
    subject_id = request.form.get("subject_id", "").strip()
    purpose_id = request.form.get("purpose_id", "").strip()
    action = request.form.get("action", "").strip().upper()

    if not valid_subject_id(subject_id):
        flash("Use a synthetic ID with letters, numbers, hyphens, or underscores.", "error")
        return redirect(url_for("home"))
    if action not in ALLOWED_ACTIONS:
        abort(400, "Unknown consent action.")

    connection = open_database()
    try:
        purpose = connection.execute(
            "SELECT * FROM purposes WHERE purpose_id = ? AND active = 1",
            (purpose_id,),
        ).fetchone()
        if purpose is None:
            abort(404, "Purpose not found.")

        if action == "WITHDRAWN":
            current_event = latest_event(connection, subject_id, purpose_id)
            if current_event is None or current_event["action"] != "GRANTED":
                flash("Withdrawal is available after a grant for this purpose.", "error")
                return redirect(url_for("home", subject_id=subject_id))
    finally:
        connection.close()

    record_consent_event(subject_id, purpose, action)
    flash(f"Recorded {action.lower()} for this purpose.", "success")
    return redirect(url_for("home", subject_id=subject_id))


@app.post("/api/v1/data/share")
def mock_data_share():
    """Mock downstream API: return synthetic data only with current consent."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"decision": "DENY", "error": "A JSON request body is required."}), 400

    subject_id = str(payload.get("subject_id", "")).strip()
    purpose_id = str(payload.get("purpose_id", "")).strip()
    fields = payload.get("fields")
    if not valid_subject_id(subject_id) or not purpose_id:
        return jsonify(
            {"decision": "DENY", "error": "Provide a valid subject_id and purpose_id."}
        ), 400

    connection = open_database()
    try:
        purpose = connection.execute(
            "SELECT * FROM purposes WHERE purpose_id = ? AND active = 1",
            (purpose_id,),
        ).fetchone()
        if purpose is None:
            audit_id = audit_data_request(
                connection,
                subject_id=subject_id,
                purpose_id=purpose_id,
                decision="DENY",
                action="REJECTED",
                fields=fields if isinstance(fields, list) else [],
                details={"reason": "UNKNOWN_PURPOSE"},
            )
            connection.commit()
            return jsonify(
                {"decision": "DENY", "error": "Unknown purpose_id.", "audit_id": audit_id}
            ), 404

        if not isinstance(fields, list) or not fields or any(
            not isinstance(field, str) or not field for field in fields
        ):
            audit_id = audit_data_request(
                connection,
                subject_id=subject_id,
                purpose_id=purpose_id,
                decision="DENY",
                action="REJECTED",
                purpose=purpose,
                details={"reason": "INVALID_FIELDS"},
            )
            connection.commit()
            return jsonify(
                {
                    "decision": "DENY",
                    "error": "fields must be a non-empty list of field names.",
                    "audit_id": audit_id,
                }
            ), 400

        current = status_for(connection, subject_id, purpose)
        if current["decision"] != "ALLOW":
            audit_id = audit_data_request(
                connection,
                subject_id=subject_id,
                purpose_id=purpose_id,
                decision="DENY",
                action="REJECTED",
                fields=fields,
                purpose=purpose,
                details={"reason": "NO_CURRENT_GRANT", "consent_status": current["status"]},
            )
            connection.commit()
            return jsonify(
                {
                    "decision": "DENY",
                    "status": current["status"],
                    "purpose_id": purpose_id,
                    "audit_id": audit_id,
                    "message": "No current grant covers this purpose and notice version.",
                }
            ), 403

        allowed_fields = json.loads(purpose["allowed_fields"])
        if len(set(fields)) != len(fields) or any(
            field not in allowed_fields for field in fields
        ):
            audit_id = audit_data_request(
                connection,
                subject_id=subject_id,
                purpose_id=purpose_id,
                decision="DENY",
                action="REJECTED",
                fields=fields,
                purpose=purpose,
                details={"reason": "FIELD_OUTSIDE_PURPOSE_SCOPE"},
            )
            connection.commit()
            return jsonify(
                {
                    "decision": "DENY",
                    "status": "FIELD_OUTSIDE_PURPOSE_SCOPE",
                    "purpose_id": purpose_id,
                    "audit_id": audit_id,
                    "message": "The request includes fields not allowed for this purpose.",
                }
            ), 403

        demo_data = json.loads(purpose["demo_data"])
        returned_data = {field: demo_data[field] for field in fields}
        audit_id = audit_data_request(
            connection,
            subject_id=subject_id,
            purpose_id=purpose_id,
            decision="ALLOW",
            action="SHARED",
            fields=fields,
            purpose=purpose,
            details={"data_source": "synthetic_demo_data"},
        )
        connection.commit()
        return jsonify(
            {
                "decision": "ALLOW",
                "status": "GRANTED",
                "subject_id": subject_id,
                "purpose_id": purpose_id,
                "notice_version": purpose["notice_version"],
                "notice_hash": purpose["notice_hash"],
                "data": returned_data,
                "audit_id": audit_id,
            }
        )
    finally:
        connection.close()


@app.get("/api/v1/consent/status")
def consent_status():
    """Return the current status for one synthetic subject-purpose pair."""
    subject_id = request.args.get("subject_id", "").strip()
    purpose_id = request.args.get("purpose_id", "").strip()
    if not valid_subject_id(subject_id) or not purpose_id:
        return jsonify({"error": "Provide a valid subject_id and purpose_id."}), 400

    connection = open_database()
    try:
        purpose = connection.execute(
            "SELECT * FROM purposes WHERE purpose_id = ? AND active = 1",
            (purpose_id,),
        ).fetchone()
        if purpose is None:
            return jsonify({"error": "Unknown purpose_id."}), 404
        result = status_for(connection, subject_id, purpose)
    finally:
        connection.close()

    return jsonify({"subject_id": subject_id, "purpose_id": purpose_id, **result})


@app.post("/api/v1/demo/notices/<purpose_id>/change")
def change_demo_notice(purpose_id):
    """Create a new local demo notice version to exercise re-consent behavior."""
    payload = request.get_json(silent=True) or {}
    subject_id = str(payload.get("subject_id", "")).strip()
    if subject_id and not valid_subject_id(subject_id):
        return jsonify({"error": "Use a synthetic subject ID."}), 400

    connection = open_database()
    try:
        purpose = connection.execute(
            "SELECT * FROM purposes WHERE purpose_id = ? AND active = 1",
            (purpose_id,),
        ).fetchone()
        if purpose is None:
            return jsonify({"error": "Unknown purpose_id."}), 404

        try:
            next_version = str(int(purpose["notice_version"]) + 1)
        except (TypeError, ValueError):
            next_version = f"{purpose['notice_version']}-next"
        new_text = (
            purpose["notice_text"].rstrip()
            + "\n\nNotice update: review this changed notice and make a fresh choice "
            "before this purpose can be used again."
        )
        new_hash = sha256_text(new_text)
        changed_at = utc_now()
        connection.execute(
            """
            UPDATE purposes
            SET notice_version = ?, notice_text = ?, notice_hash = ?
            WHERE purpose_id = ?
            """,
            (next_version, new_text, new_hash, purpose_id),
        )
        connection.execute(
            """
            INSERT INTO notice_versions
                (purpose_id, notice_version, notice_text, notice_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (purpose_id, next_version, new_text, new_hash, changed_at),
        )
        audit_id = append_audit_event(
            connection,
            event_type="NOTICE_CHANGE",
            subject_id=subject_id or None,
            purpose_id=purpose_id,
            action="UPDATED",
            decision="INFO",
            occurred_at=changed_at,
            notice_version=next_version,
            notice_hash=new_hash,
            details={"previous_version": purpose["notice_version"]},
        )
        connection.commit()
    finally:
        connection.close()

    return jsonify(
        {
            "purpose_id": purpose_id,
            "notice_version": next_version,
            "notice_hash": new_hash,
            "decision": "INFO",
            "audit_id": audit_id,
        }
    )


@app.get("/api/v1/audit/export")
def audit_export():
    """Download the synthetic audit trail as CSV or JSON."""
    export_format = request.args.get("format", "csv").lower()
    if export_format not in {"csv", "json"}:
        return jsonify({"error": "format must be csv or json."}), 400

    connection = open_database()
    try:
        rows = connection.execute(
            """
            SELECT audit_id, event_type, subject_id, purpose_id, action, decision,
                   occurred_at, notice_version, notice_hash, request_fields, details
            FROM audit_events ORDER BY audit_id
            """
        ).fetchall()
        events = [dict(row) for row in rows]
    finally:
        connection.close()

    if export_format == "json":
        response = Response(json.dumps(events, indent=2), mimetype="application/json")
        response.headers["Content-Disposition"] = "attachment; filename=consent-audit.json"
        return response

    output = io.StringIO(newline="")
    fieldnames = [
        "audit_id",
        "event_type",
        "subject_id",
        "purpose_id",
        "action",
        "decision",
        "occurred_at",
        "notice_version",
        "notice_hash",
        "request_fields",
        "details",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(events)
    response = Response(output.getvalue(), content_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = "attachment; filename=consent-audit.csv"
    return response


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # Local-only demo server. Do not expose this prototype to the public internet.
    initialize_database()
    app.config["INITIALIZED_DATABASE_PATH"] = str(Path(DATABASE).resolve())
    app.run(host="127.0.0.1", port=5000, debug=False)
