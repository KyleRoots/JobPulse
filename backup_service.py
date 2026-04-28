"""
Database Backup Service — automated pg_dump → gzip → OneDrive upload.

Provides nightly backups of the production PostgreSQL database to OneDrive,
with configurable retention, admin-triggered on-demand runs, and email alerts
on failure.

Upload strategy:
  - Files ≤ 4 MB  → simple PUT  /me/drive/root:/path:/content
  - Files > 4 MB  → resumable upload session with 5 MB chunks
"""

import gzip
import logging
import os
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
ONEDRIVE_BACKUP_FOLDER = "/ScoutGenius/Backups"
UPLOAD_CHUNK_SIZE = 5 * 1024 * 1024
SIMPLE_UPLOAD_LIMIT = 4 * 1024 * 1024
DEFAULT_RETENTION_DAYS = 14


class BackupService:

    def __init__(self, app=None):
        self.app = app

    def _get_onedrive_token(self) -> str:
        hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
        repl_identity = os.environ.get("REPL_IDENTITY")
        web_repl_renewal = os.environ.get("WEB_REPL_RENEWAL")

        if repl_identity:
            x_replit_token = f"repl {repl_identity}"
        elif web_repl_renewal:
            x_replit_token = f"depl {web_repl_renewal}"
        else:
            raise ConnectionError("Backup: No Replit identity token available")

        if not hostname:
            raise ConnectionError("Backup: REPLIT_CONNECTORS_HOSTNAME not set")

        resp = requests.get(
            f"https://{hostname}/api/v2/connection",
            params={"include_secrets": "true", "connector_names": "onedrive"},
            headers={
                "Accept": "application/json",
                "X-Replit-Token": x_replit_token,
            },
            timeout=10,
        )
        resp.raise_for_status()

        data = resp.json()
        connection = data.get("items", [None])[0] if data.get("items") else None
        if not connection:
            raise ConnectionError(
                "Backup: No OneDrive connection found. "
                "Please connect OneDrive in Replit settings."
            )

        settings = connection.get("settings", {})
        token = (
            settings.get("access_token")
            or settings.get("oauth", {}).get("credentials", {}).get("access_token")
        )
        if not token:
            raise ConnectionError(
                "Backup: No OneDrive access token. Please reconnect OneDrive."
            )
        return token

    def _graph_put(self, endpoint: str, data: bytes,
                   content_type: str = "application/octet-stream") -> dict:
        token = self._get_onedrive_token()
        url = f"{GRAPH_BASE_URL}{endpoint}"
        resp = requests.put(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": content_type,
            },
            data=data,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()

    def _graph_get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        token = self._get_onedrive_token()
        url = f"{GRAPH_BASE_URL}{endpoint}"
        resp = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _graph_delete(self, endpoint: str) -> None:
        token = self._get_onedrive_token()
        url = f"{GRAPH_BASE_URL}{endpoint}"
        resp = requests.delete(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()

    def _get_database_url(self) -> str:
        url = os.environ.get("DATABASE_URL")
        if not url or url.startswith("sqlite"):
            raise RuntimeError(
                "Backup requires a PostgreSQL DATABASE_URL. "
                "Cannot back up a SQLite database."
            )
        return url

    def _run_pg_dump(self, database_url: str, output_path: str) -> None:
        parsed = urlparse(database_url)
        env = os.environ.copy()
        env["PGPASSWORD"] = parsed.password or ""

        host = parsed.hostname or "localhost"
        port = str(parsed.port or 5432)
        dbname = parsed.path.lstrip("/")
        username = parsed.username or "postgres"

        ssl_params = {}
        if parsed.query:
            for param in parsed.query.split("&"):
                if "=" in param:
                    k, v = param.split("=", 1)
                    ssl_params[k] = v

        cmd = [
            "pg_dump",
            "-h", host,
            "-p", port,
            "-U", username,
            "-d", dbname,
            "--no-owner",
            "--no-acl",
            "-F", "plain",
        ]

        if ssl_params.get("sslmode") in ("require", "verify-ca", "verify-full"):
            env["PGSSLMODE"] = ssl_params["sslmode"]

        logger.info(f"Running pg_dump on {host}:{port}/{dbname} ...")
        with open(output_path, "wb") as f_out:
            proc = subprocess.Popen(
                cmd,
                env=env,
                stdout=f_out,
                stderr=subprocess.PIPE,
            )
            _, stderr_bytes = proc.communicate(timeout=600)

        if proc.returncode != 0:
            stderr = stderr_bytes.decode("utf-8", errors="replace")[:2000]
            raise RuntimeError(f"pg_dump failed (exit {proc.returncode}): {stderr}")

        dump_size = os.path.getsize(output_path)
        logger.info(
            f"pg_dump completed: {dump_size:,} bytes "
            f"({dump_size / 1024 / 1024:.1f} MB)"
        )

    def _compress_file(self, input_path: str, output_path: str) -> int:
        with open(input_path, "rb") as f_in:
            with gzip.open(output_path, "wb", compresslevel=6) as f_out:
                while True:
                    chunk = f_in.read(1024 * 1024)
                    if not chunk:
                        break
                    f_out.write(chunk)
        size = os.path.getsize(output_path)
        logger.info(f"Compressed to {size:,} bytes ({size / 1024 / 1024:.1f} MB)")
        return size

    def _upload_simple(self, file_path: str, onedrive_path: str) -> dict:
        with open(file_path, "rb") as f:
            data = f.read()
        endpoint = f"/me/drive/root:{onedrive_path}:/content"
        return self._graph_put(endpoint, data)

    def _upload_large(self, file_path: str, onedrive_path: str) -> dict:
        token = self._get_onedrive_token()
        file_size = os.path.getsize(file_path)

        create_url = (
            f"{GRAPH_BASE_URL}/me/drive/root:{onedrive_path}:/createUploadSession"
        )
        session_resp = requests.post(
            create_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "item": {
                    "@microsoft.graph.conflictBehavior": "replace",
                    "name": os.path.basename(onedrive_path),
                }
            },
            timeout=30,
        )
        session_resp.raise_for_status()
        upload_url = session_resp.json()["uploadUrl"]

        with open(file_path, "rb") as f:
            offset = 0
            last_resp = None
            while offset < file_size:
                chunk = f.read(UPLOAD_CHUNK_SIZE)
                chunk_end = offset + len(chunk) - 1
                headers = {
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{chunk_end}/{file_size}",
                }
                resp = requests.put(
                    upload_url,
                    headers=headers,
                    data=chunk,
                    timeout=120,
                )
                resp.raise_for_status()
                last_resp = resp
                offset += len(chunk)
                logger.info(
                    f"  Uploaded {offset:,}/{file_size:,} bytes "
                    f"({offset * 100 / file_size:.0f}%)"
                )

        return last_resp.json() if last_resp else {}

    def _upload_to_onedrive(
        self, file_path: str, file_name: str
    ) -> Tuple[str, str]:
        onedrive_path = f"{ONEDRIVE_BACKUP_FOLDER}/{file_name}"
        file_size = os.path.getsize(file_path)

        if file_size <= SIMPLE_UPLOAD_LIMIT:
            result = self._upload_simple(file_path, onedrive_path)
        else:
            result = self._upload_large(file_path, onedrive_path)

        item_id = result.get("id", "")
        web_url = result.get("webUrl", "")
        logger.info(f"Uploaded to OneDrive: {onedrive_path} (id={item_id})")
        return item_id, web_url

    def _cleanup_old_backups(self, retention_days: int) -> int:
        try:
            endpoint = f"/me/drive/root:{ONEDRIVE_BACKUP_FOLDER}:/children"
            result = self._graph_get(endpoint, params={"$orderby": "createdDateTime"})
            items = result.get("value", [])

            if not items:
                return 0

            cutoff = datetime.utcnow() - timedelta(days=retention_days)
            deleted = 0
            for item in items:
                name = item.get("name", "")
                if not name.endswith(".sql.gz"):
                    continue
                created_str = item.get("createdDateTime", "")
                if not created_str:
                    continue
                try:
                    created = datetime.fromisoformat(
                        created_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except (ValueError, TypeError):
                    continue

                if created < cutoff:
                    item_id = item["id"]
                    self._graph_delete(f"/me/drive/items/{item_id}")
                    deleted += 1
                    logger.info(
                        f"Deleted old backup: {name} "
                        f"(created {created.strftime('%Y-%m-%d')})"
                    )

            if deleted:
                logger.info(
                    f"Retention cleanup: removed {deleted} backup(s) "
                    f"older than {retention_days} days"
                )
            return deleted
        except Exception as e:
            logger.warning(f"Retention cleanup failed (non-fatal): {e}")
            return 0

    def _send_failure_alert(self, error_message: str) -> None:
        try:
            from extensions import db
            from models import VettingConfig
            admin_setting = VettingConfig.query.filter_by(
                setting_key="admin_notification_email"
            ).first()
            if not admin_setting or not admin_setting.setting_value:
                logger.warning(
                    "No admin_notification_email configured — "
                    "cannot send backup failure alert"
                )
                return

            admin_email = admin_setting.setting_value
            from email_service import EmailService
            from models import EmailDeliveryLog
            svc = EmailService(db=db, EmailDeliveryLog=EmailDeliveryLog)

            truncated = error_message[:1500]
            subject = "⚠️ Scout Genius — Database Backup Failed"
            body = f"""
            <div style="font-family: -apple-system, sans-serif; max-width: 600px;">
                <h2 style="color: #ef4444;">⚠️ Database Backup Failed</h2>
                <p>The scheduled database backup failed at
                   <strong>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</strong>.</p>
                <div style="background: #1a1a2e; color: #e2e8f0; padding: 16px;
                            border-radius: 8px; font-family: monospace; font-size: 13px;
                            white-space: pre-wrap; margin: 16px 0;">
{truncated}
                </div>
                <p>Please investigate and, if needed, trigger a manual backup from the
                   <a href="https://app.scoutgenius.ai/admin/backups">Backup Dashboard</a>.</p>
            </div>
            """

            svc.send_email(
                to_email=admin_email,
                subject=subject,
                html_content=body,
                notification_type="backup_failure",
            )
            logger.info(f"Backup failure alert sent to {admin_email}")
        except Exception as e:
            logger.error(f"Could not send backup failure alert: {e}")

    def _get_retention_days(self) -> int:
        try:
            from models import VettingConfig
            setting = VettingConfig.query.filter_by(
                setting_key="backup_retention_days"
            ).first()
            if setting and setting.setting_value:
                return int(setting.setting_value)
        except Exception:
            pass
        return DEFAULT_RETENTION_DAYS

    def run_backup(self, triggered_by: str = "scheduler",
                   existing_log_id: int = None) -> dict:
        from extensions import db
        from models import BackupLog

        if existing_log_id:
            log = db.session.get(BackupLog, existing_log_id)
            if not log:
                raise RuntimeError(f"BackupLog id={existing_log_id} not found")
        else:
            log = BackupLog(
                status="running",
                triggered_by=triggered_by,
                started_at=datetime.utcnow(),
            )
            db.session.add(log)
            db.session.commit()

        start_time = time.time()

        try:
            database_url = self._get_database_url()

            timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H%M-UTC")
            file_name = f"scoutgenius_{timestamp}.sql.gz"

            with tempfile.TemporaryDirectory() as tmpdir:
                sql_path = os.path.join(tmpdir, "dump.sql")
                gz_path = os.path.join(tmpdir, file_name)

                self._run_pg_dump(database_url, sql_path)
                file_size = self._compress_file(sql_path, gz_path)

                item_id, web_url = self._upload_to_onedrive(gz_path, file_name)

            duration = time.time() - start_time

            log.status = "success"
            log.file_name = file_name
            log.file_size_bytes = file_size
            log.onedrive_item_id = item_id
            log.onedrive_web_url = web_url
            log.duration_seconds = round(duration, 1)
            log.completed_at = datetime.utcnow()
            db.session.commit()

            retention = self._get_retention_days()
            self._cleanup_old_backups(retention)

            logger.info(
                f"✅ Backup complete: {file_name} "
                f"({file_size / 1024 / 1024:.1f} MB, {duration:.0f}s)"
            )
            return {
                "status": "success",
                "file_name": file_name,
                "file_size_bytes": file_size,
                "duration_seconds": round(duration, 1),
            }

        except Exception as e:
            duration = time.time() - start_time
            error_msg = str(e)[:2000]
            logger.error(f"❌ Backup failed after {duration:.0f}s: {error_msg}")

            log.status = "failed"
            log.error_message = error_msg
            log.duration_seconds = round(duration, 1)
            log.completed_at = datetime.utcnow()
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()

            self._send_failure_alert(error_msg)

            return {
                "status": "failed",
                "error": error_msg,
                "duration_seconds": round(duration, 1),
            }
