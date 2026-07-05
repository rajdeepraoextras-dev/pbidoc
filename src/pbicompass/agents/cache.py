from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any, Optional


class LLMResponseCache:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = os.environ.get("PBICOMPASS_LLM_CACHE") or "off"
        self.db_path = db_path
        self.conn = None
        if self.db_path != "off":
            try:
                self.conn = sqlite3.connect(self.db_path)
                self._create_table()
            except Exception:
                pass

    def _create_table(self):
        if not self.conn:
            return
        with self.conn:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS llm_cache ("
                "  key TEXT PRIMARY KEY,"
                "  response TEXT,"
                "  timestamp DATETIME DEFAULT CURRENT_TIMESTAMP"
                ")"
            )

    def _get_key(self, system: str, payload: dict, schema: dict, model_id: str) -> str:
        data = {
            "system": system,
            "payload": payload,
            "schema": schema,
            "model_id": model_id
        }
        serialized = json.dumps(data, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def get(self, system: str, payload: dict, schema: dict, model_id: str) -> Optional[dict]:
        if not self.conn:
            return None
        key = self._get_key(system, payload, schema, model_id)
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT response FROM llm_cache WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
        except Exception:
            pass
        return None

    def set(self, system: str, payload: dict, schema: dict, model_id: str, response: dict) -> None:
        if not self.conn:
            return
        key = self._get_key(system, payload, schema, model_id)
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO llm_cache (key, response) VALUES (?, ?)",
                    (key, json.dumps(response))
                )
        except Exception:
            pass

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
