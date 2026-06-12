"""GraphService — Microsoft Graph API client for email enrichment.

Follows the lilAmy service pattern (QObject + signals, threading.Thread).
Pure API — no UI dependency.  The UI layer (GraphSignInDialog) is a thin
consumer that connects to these signals.

Architecture::

    AMail fetches emails (COM) → displays immediately
                              → GraphService.enrich_async() (background)
                                   → device-code OAuth if needed
                                   → batched Graph API queries
                                   → enrichment_complete signal
                                   → Others toggle updates

Usage (detached from any UI)::

    svc = GraphService(client_id="...", data_root="/path/to/LILAMY_DATA_DIR")
    svc.auth_complete.connect(on_auth_done)
    svc.enrichment_complete.connect(on_enrichment_done)
    svc.authenticate()           # starts device-code flow in background thread
    svc.enrich_async(emails)     # enriches list of email dicts with
                                 #   inferenceClassification → is_focused bool

Token cache: ``data_root/.lilamy_graph_token.json``
"""

import json
import os
import threading
import time
from pathlib import Path

import requests
from PyQt6.QtCore import QObject, pyqtSignal


# ── Constants ──────────────────────────────────────────────────────────

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTH_BASE = "https://login.microsoftonline.com"
SCOPES = ["Mail.ReadWrite", "offline_access"]
TOKEN_CACHE_FILENAME = ".lilamy_graph_token.json"


class GraphService(QObject):
    """Device-code OAuth + batched Graph API email enrichment.

    Signals:
        auth_pending(device_code, verification_url, message)
            Emitted when the user needs to sign in (device-code flow started).
        auth_complete()
            Emitted when authentication succeeds (tokens cached).
        auth_failed(error_message)
            Emitted when authentication fails.
        enrichment_progress(current, total)
            Emitted during batch enrichment to update progress UI.
        enrichment_complete(results: dict[str, bool])
            Emitted when enrichment finishes.  Maps ``internetMessageId``
            → ``is_focused`` (True=focused, False=other).
        enrichment_error(message)
            Emitted when enrichment encounters an unrecoverable error.
    """

    auth_pending = pyqtSignal(str, str, str)     # device_code, verification_url, message
    auth_complete = pyqtSignal()
    auth_failed = pyqtSignal(str)
    enrichment_progress = pyqtSignal(int, int)    # current, total
    enrichment_complete = pyqtSignal(dict)         # {internetMessageId: is_focused}
    enrichment_error = pyqtSignal(str)

    def __init__(
        self,
        client_id: str,
        data_root: str | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._client_id = client_id
        self._data_root = Path(data_root or os.environ.get("LILAMY_DATA_DIR", "."))
        self._token_cache_path = self._data_root / TOKEN_CACHE_FILENAME
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._lock = threading.Lock()
        self._auth_thread: threading.Thread | None = None
        self._enrich_thread: threading.Thread | None = None
        self._cancelled = False

        # Try to load cached tokens on init
        self._load_tokens()

    # ── Public API ─────────────────────────────────────────────────

    @property
    def is_authenticated(self) -> bool:
        return self._access_token is not None

    def authenticate(self) -> None:
        """Start device-code OAuth flow in a background thread.

        On success, emits ``auth_complete`` and caches tokens to disk.
        On failure, emits ``auth_failed``.
        While waiting for user to sign in, emits ``auth_pending``."""
        if self._auth_thread and self._auth_thread.is_alive():
            return  # already authenticating
        self._cancelled = False
        self._auth_thread = threading.Thread(target=self._auth_flow, daemon=True)
        self._auth_thread.start()

    def cancel_auth(self) -> None:
        """Cancel an in-progress authentication flow."""
        self._cancelled = True

    def enrich_async(self, emails: list[dict]) -> None:
        """Enrich a list of email dicts with ``is_focused`` boolean.

        Each dict must have an ``internet_message_id`` key.  The enrichment
        runs in a background thread and emits ``enrichment_complete`` when
        done, mapping each ``internetMessageId`` to its classification.

        If not authenticated, emits ``auth_pending`` and then proceeds with
        the auth flow before enriching."""
        if self._enrich_thread and self._enrich_thread.is_alive():
            return  # already enriching
        self._cancelled = False
        self._enrich_thread = threading.Thread(
            target=self._enrich_flow, args=(emails,), daemon=True
        )
        self._enrich_thread.start()

    def cancel_enrich(self) -> None:
        """Cancel in-progress enrichment."""
        self._cancelled = True

    def clear_auth(self) -> None:
        """Delete cached tokens (force re-auth on next use)."""
        self._access_token = None
        self._refresh_token = None
        try:
            self._token_cache_path.unlink(missing_ok=True)
        except Exception:
            pass

    # ── Auth flow (background thread) ──────────────────────────────

    def _auth_flow(self) -> None:
        """Device-code OAuth flow — runs in background thread."""
        try:
            # Step 1: request device code
            r = requests.post(
                f"{AUTH_BASE}/common/oauth2/v2.0/devicecode",
                data={
                    "client_id": self._client_id,
                    "scope": " ".join(SCOPES),
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_uri = data["verification_uri"]
            expires_in = int(data.get("expires_in", 900))
            interval = int(data.get("interval", 5))

            self.auth_pending.emit(
                user_code,
                verification_uri,
                data.get("message", f"Go to {verification_uri} and enter code {user_code}"),
            )

            # Step 2: poll for token
            deadline = time.time() + expires_in
            while time.time() < deadline and not self._cancelled:
                time.sleep(interval)
                try:
                    r = requests.post(
                        f"{AUTH_BASE}/common/oauth2/v2.0/token",
                        data={
                            "client_id": self._client_id,
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                            "device_code": device_code,
                        },
                        timeout=30,
                    )
                    resp = r.json()
                    if "access_token" in resp:
                        with self._lock:
                            self._access_token = resp["access_token"]
                            self._refresh_token = resp.get("refresh_token")
                        self._save_tokens()
                        self.auth_complete.emit()
                        return
                    elif resp.get("error") == "authorization_pending":
                        continue  # user hasn't signed in yet
                    elif resp.get("error") == "slow_down":
                        interval += 5  # server asked us to slow down
                        continue
                    else:
                        self.auth_failed.emit(resp.get("error_description", str(resp)))
                        return
                except requests.RequestException:
                    continue  # transient network error, keep polling

            if self._cancelled:
                self.auth_failed.emit("Authentication cancelled.")
            else:
                self.auth_failed.emit("Authentication timed out — code expired.")

        except Exception as e:
            self.auth_failed.emit(str(e))

    # ── Enrich flow (background thread) ────────────────────────────

    def _enrich_flow(self, emails: list[dict]) -> None:
        """Query Graph API for inferenceClassification of each email."""
        try:
            # Ensure authenticated
            if not self._access_token:
                self._ensure_token()
            if not self._access_token:
                self.enrichment_error.emit("Not authenticated — cannot enrich.")
                return

            # Filter emails that have an internetMessageId
            id_map: dict[str, str] = {}  # internetMessageId → entry_id
            for e in emails:
                mid = e.get("internet_message_id", "").strip()
                eid = e.get("entry_id", "")
                if mid:
                    id_map[mid] = eid

            if not id_map:
                self.enrichment_complete.emit({})
                return

            total = len(id_map)
            results: dict[str, bool] = {}
            message_ids = list(id_map.keys())

            # Batch query: 20 messages per batch request
            BATCH_SIZE = 20
            for batch_start in range(0, total, BATCH_SIZE):
                if self._cancelled:
                    break
                batch = message_ids[batch_start : batch_start + BATCH_SIZE]
                classification = self._batch_classify(batch)
                for mid, is_focused in classification.items():
                    entry_id = id_map.get(mid, mid)
                    results[entry_id] = is_focused
                progress = min(batch_start + BATCH_SIZE, total)
                self.enrichment_progress.emit(progress, total)

            self.enrichment_complete.emit(results)

        except Exception as e:
            self.enrichment_error.emit(str(e))

    def _batch_classify(self, message_ids: list[str]) -> dict[str, bool]:
        """Query Graph API for inferenceClassification of a batch of messages.

        Uses individual ``$filter`` queries with ``internetMessageId`` since
        Microsoft Graph does not support batch ``$search`` for this property.
        Falls back with progressive back-off on throttling."""
        results: dict[str, bool] = {}
        headers = self._auth_headers()

        for mid in message_ids:
            if self._cancelled:
                break
            try:
                # Encode the message ID for URL (it contains < > characters)
                encoded_id = requests.utils.quote(mid, safe="")
                url = (
                    f"{GRAPH_BASE}/me/messages?$filter=internetMessageId eq '{mid}'"
                    f"&$select=internetMessageId,inferenceClassification&$top=1"
                )
                r = requests.get(url, headers=headers, timeout=20)

                if r.status_code == 429:
                    # Throttled — wait and retry
                    retry_after = int(r.headers.get("Retry-After", 5))
                    time.sleep(retry_after)
                    r = requests.get(url, headers=headers, timeout=20)

                if r.status_code == 401:
                    # Token expired — refresh and retry
                    self._refresh_access_token()
                    headers = self._auth_headers()
                    r = requests.get(url, headers=headers, timeout=20)

                if r.status_code == 200:
                    data = r.json()
                    messages = data.get("value", [])
                    if messages:
                        ic = messages[0].get("inferenceClassification", "")
                        results[mid] = (ic == "focused")
                    else:
                        # Message not found in Graph — default to focused
                        results[mid] = True
                else:
                    results[mid] = True  # default: focused

            except requests.RequestException:
                results[mid] = True  # default on network error

        return results

    # ── Token management ───────────────────────────────────────────

    def _ensure_token(self) -> None:
        """Ensure we have a valid access token, refreshing if needed."""
        if self._access_token:
            return
        if self._refresh_token:
            self._refresh_access_token()
        # If still no token, caller should trigger auth flow

    def _refresh_access_token(self) -> bool:
        """Try to get a new access token using the refresh token."""
        if not self._refresh_token:
            return False
        try:
            r = requests.post(
                f"{AUTH_BASE}/common/oauth2/v2.0/token",
                data={
                    "client_id": self._client_id,
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "scope": " ".join(SCOPES),
                },
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                with self._lock:
                    self._access_token = data["access_token"]
                    if "refresh_token" in data:
                        self._refresh_token = data["refresh_token"]
                self._save_tokens()
                return True
        except Exception:
            pass
        return False

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _load_tokens(self) -> None:
        """Load cached tokens from disk."""
        try:
            if self._token_cache_path.exists():
                data = json.loads(self._token_cache_path.read_text())
                self._access_token = data.get("access_token")
                self._refresh_token = data.get("refresh_token")
        except Exception:
            pass

    def _save_tokens(self) -> None:
        """Persist tokens to disk (one JSON file in the data root)."""
        try:
            self._token_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._token_cache_path.write_text(json.dumps({
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
            }))
        except Exception:
            pass
