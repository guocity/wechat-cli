"""Durable send deduplication, including uncertain results after a server restart."""

import hashlib
import json
import logging
import sqlite3
import threading
import time

from wechat_cli.core.config import private_directory
from wechat_cli.core.errors import AppError
from wechat_cli.host import private_permissions


class Idempotency:
    def __init__(self, directory):
        private_directory(directory)
        path = directory / "requests.sqlite3"
        self.connection = sqlite3.connect(path, check_same_thread=False)
        private_permissions(path)
        self.lock = threading.Lock()
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS requests "
            "(route TEXT, key TEXT, digest TEXT, created REAL, status INTEGER, response TEXT, "
            "PRIMARY KEY(route,key))"
        )
        self.connection.commit()

    def once(self, route, key, payload, action):
        signature = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        # Serialize mutations through completion; concurrent retries see the same result.
        with self.lock:
            db = self.connection
            db.execute("DELETE FROM requests WHERE created < ?", (time.time() - 86400,))
            old = db.execute(
                "SELECT digest,status,response FROM requests WHERE route=? AND key=?", (route, key)
            ).fetchone()
            if old:
                if old[0] != signature:
                    raise AppError("idempotency_conflict", "This key was used with a different request", 409)
                if old[2] is None:
                    raise AppError(
                        "native_send_outcome_unknown",
                        "An earlier attempt was interrupted. Inspect WeChat; do not resend with another key.",
                        409,
                    )
                response = json.loads(old[2])
                if old[1] >= 400:
                    raise AppError(response["error"], response["message"], old[1])
                return response
            if db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] >= 10000:
                raise AppError("idempotency_capacity", "Request retention capacity reached", 503)
            db.execute(
                "INSERT INTO requests VALUES (?,?,?,?,NULL,NULL)", (route, key, signature, time.time())
            )
            db.commit()  # Commit intent before a native operation can enqueue anything.
            try:
                response = action()
            except AppError as error:
                db.execute(
                    "UPDATE requests SET status=?,response=? WHERE route=? AND key=?",
                    (error.status, json.dumps(error.as_dict()), route, key),
                )
                db.commit()
                raise
            except Exception:
                logging.getLogger(__name__).exception("Local operation interrupted: %s", route)
                # Leave the durable intent unresolved: retry must never send again.
                raise AppError(
                    "native_send_outcome_unknown",
                    "Operation was interrupted; inspect WeChat before retrying",
                    502,
                ) from None
            db.execute(
                "UPDATE requests SET status=200,response=? WHERE route=? AND key=?",
                (json.dumps(response), route, key),
            )
            db.commit()
            return response

    def close(self):
        with self.lock:
            self.connection.close()
