"""
OneDrive Integration Service — Connects to Microsoft OneDrive via Replit connector
for browsing, downloading, and syncing documents to the Knowledge Hub.

Uses Microsoft Graph REST API directly via requests library.
Token management handled by Replit's connector infrastructure.
"""

import io
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
SUPPORTED_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt'}
MAX_FILE_SIZE_MB = 25


class OneDriveService:

    def __init__(self):
        self._cached_token = None
        self._token_expires_at = 0

    def _get_access_token(self) -> str:
        now = time.time()
        if self._cached_token and self._token_expires_at > now + 60:
            return self._cached_token

        hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
        repl_identity = os.environ.get('REPL_IDENTITY')
        web_repl_renewal = os.environ.get('WEB_REPL_RENEWAL')

        if repl_identity:
            x_replit_token = f"repl {repl_identity}"
        elif web_repl_renewal:
            x_replit_token = f"depl {web_repl_renewal}"
        else:
            raise ConnectionError("OneDrive: No Replit identity token available")

        if not hostname:
            raise ConnectionError("OneDrive: REPLIT_CONNECTORS_HOSTNAME not set")

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
            raise ConnectionError("OneDrive: No connection found. Please connect OneDrive in Replit settings.")

        settings = connection.get("settings", {})
        access_token = settings.get("access_token") or settings.get("oauth", {}).get("credentials", {}).get("access_token")

        if not access_token:
            raise ConnectionError("OneDrive: No access token available. Please reconnect OneDrive.")

        expires_at = settings.get("expires_at")
        if expires_at:
            try:
                self._token_expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                self._token_expires_at = now + 3500
        else:
            self._token_expires_at = now + 3500

        self._cached_token = access_token
        return access_token

    def _graph_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        token = self._get_access_token()
        url = f"{GRAPH_BASE_URL}{endpoint}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params=params,
            timeout=30,
        )
        if resp.status_code == 401:
            self._cached_token = None
            self._token_expires_at = 0
            token = self._get_access_token()
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                params=params,
                timeout=30,
            )
        resp.raise_for_status()
        return resp.json()

    def _download_request(self, endpoint: str) -> bytes:
        token = self._get_access_token()
        url = f"{GRAPH_BASE_URL}{endpoint}"
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        if resp.status_code == 401:
            self._cached_token = None
            self._token_expires_at = 0
            token = self._get_access_token()
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=60,
            )
        resp.raise_for_status()
        return resp.content

    def is_connected(self) -> bool:
        try:
            self._get_access_token()
            return True
        except Exception:
            return False

    def get_user_info(self) -> Optional[Dict]:
        try:
            data = self._graph_request("/me")
            return {
                "name": data.get("displayName", "Unknown"),
                "email": data.get("userPrincipalName", data.get("mail", "")),
            }
        except Exception as e:
            logger.warning(f"OneDrive: Failed to get user info: {e}")
            return None

    def list_root_items(self) -> List[Dict]:
        return self.list_folder_items(None)

    def list_folder_items(self, folder_id: Optional[str] = None) -> List[Dict]:
        if folder_id:
            endpoint = f"/me/drive/items/{folder_id}/children"
        else:
            endpoint = "/me/drive/root/children"

        try:
            data = self._graph_request(endpoint, params={
                "$select": "id,name,folder,file,size,lastModifiedDateTime,parentReference",
                "$orderby": "name",
                "$top": "200",
            })
        except requests.exceptions.HTTPError as e:
            logger.error(f"OneDrive: Failed to list items: {e}")
            return []

        items = []
        for item in data.get("value", []):
            is_folder = "folder" in item
            file_info = item.get("file", {})
            ext = item.get("name", "").rsplit(".", 1)[-1].lower() if "." in item.get("name", "") else ""

            entry = {
                "id": item["id"],
                "name": item["name"],
                "is_folder": is_folder,
                "size": item.get("size", 0),
                "last_modified": item.get("lastModifiedDateTime", ""),
                "child_count": item.get("folder", {}).get("childCount", 0) if is_folder else 0,
                "mime_type": file_info.get("mimeType", ""),
                "extension": ext,
                "is_supported": ext in SUPPORTED_EXTENSIONS if not is_folder else False,
                "parent_id": item.get("parentReference", {}).get("id"),
                "path": item.get("parentReference", {}).get("path", ""),
            }
            items.append(entry)

        return items

    def get_folder_path(self, folder_id: str) -> str:
        try:
            data = self._graph_request(f"/me/drive/items/{folder_id}", params={
                "$select": "id,name,parentReference",
            })
            parent_path = data.get("parentReference", {}).get("path", "")
            name = data.get("name", "")
            if parent_path:
                clean_path = parent_path.replace("/drive/root:", "").replace("/drive/root", "")
                return f"{clean_path}/{name}" if clean_path else f"/{name}"
            return f"/{name}"
        except Exception:
            return f"/folder-{folder_id}"

    def get_file_metadata(self, file_id: str) -> Optional[Dict]:
        try:
            data = self._graph_request(f"/me/drive/items/{file_id}", params={
                "$select": "id,name,size,lastModifiedDateTime,file,eTag,parentReference",
            })
            ext = data.get("name", "").rsplit(".", 1)[-1].lower() if "." in data.get("name", "") else ""
            return {
                "id": data["id"],
                "name": data["name"],
                "size": data.get("size", 0),
                "last_modified": data.get("lastModifiedDateTime", ""),
                "etag": data.get("eTag", ""),
                "extension": ext,
                "is_supported": ext in SUPPORTED_EXTENSIONS,
            }
        except Exception as e:
            logger.error(f"OneDrive: Failed to get file metadata for {file_id}: {e}")
            return None

    def download_file(self, file_id: str) -> Optional[bytes]:
        try:
            content = self._download_request(f"/me/drive/items/{file_id}/content")
            return content
        except Exception as e:
            logger.error(f"OneDrive: Failed to download file {file_id}: {e}")
            return None

    def list_supported_files_in_folder(self, folder_id: str, recursive: bool = False) -> List[Dict]:
        items = self.list_folder_items(folder_id)
        supported = [i for i in items if not i["is_folder"] and i["is_supported"]
                     and i["size"] <= MAX_FILE_SIZE_MB * 1024 * 1024]

        if recursive:
            subfolders = [i for i in items if i["is_folder"]]
            for subfolder in subfolders:
                try:
                    sub_files = self.list_supported_files_in_folder(subfolder["id"], recursive=True)
                    supported.extend(sub_files)
                except Exception as e:
                    logger.warning(f"OneDrive: Error scanning subfolder {subfolder['name']}: {e}")

        return supported

    def sync_folder_to_knowledge(self, folder_id: str) -> Dict:
        from extensions import db
        from models import KnowledgeDocument, OneDriveSyncFolder
        from scout_support.knowledge import KnowledgeService

        sync_folder = OneDriveSyncFolder.query.filter_by(onedrive_folder_id=folder_id, sync_enabled=True).first()
        if not sync_folder:
            return {"error": "Sync folder not found or disabled", "synced": 0, "updated": 0, "removed": 0}

        ks = KnowledgeService()
        stats = {"synced": 0, "updated": 0, "removed": 0, "skipped": 0, "errors": 0}

        try:
            remote_files = self.list_supported_files_in_folder(folder_id, recursive=True)
        except Exception as e:
            logger.error(f"OneDrive sync: Failed to list files in folder {folder_id}: {e}")
            return {"error": str(e), **stats}

        remote_file_ids = set()
        for file_info in remote_files:
            remote_file_ids.add(file_info["id"])

            existing_doc = KnowledgeDocument.query.filter_by(
                onedrive_item_id=file_info["id"],
                status='active',
            ).first()

            if existing_doc:
                remote_modified = file_info.get("last_modified", "")
                if existing_doc.onedrive_etag and existing_doc.onedrive_etag == remote_modified:
                    stats["skipped"] += 1
                    continue

                try:
                    file_bytes = self.download_file(file_info["id"])
                    if not file_bytes:
                        stats["errors"] += 1
                        continue

                    raw_text = ks._extract_text(file_bytes, file_info["extension"], file_info["name"])
                    if not raw_text or len(raw_text.strip()) < 20:
                        stats["skipped"] += 1
                        continue

                    for entry in existing_doc.entries.all():
                        db.session.delete(entry)

                    existing_doc.raw_text = raw_text
                    existing_doc.onedrive_etag = remote_modified
                    existing_doc.updated_at = datetime.utcnow()

                    chunks = ks._chunk_text(raw_text)
                    ks._create_entries_with_embeddings(existing_doc, chunks)
                    db.session.commit()
                    stats["updated"] += 1
                    logger.info(f"OneDrive sync: Updated '{file_info['name']}' ({len(chunks)} chunks)")
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"OneDrive sync: Failed to update {file_info['name']}: {e}")
                    stats["errors"] += 1
            else:
                try:
                    file_bytes = self.download_file(file_info["id"])
                    if not file_bytes:
                        stats["errors"] += 1
                        continue

                    raw_text = ks._extract_text(file_bytes, file_info["extension"], file_info["name"])
                    if not raw_text or len(raw_text.strip()) < 20:
                        stats["skipped"] += 1
                        continue

                    doc = KnowledgeDocument(
                        title=file_info["name"].rsplit(".", 1)[0] if "." in file_info["name"] else file_info["name"],
                        filename=file_info["name"],
                        doc_type='onedrive_sync',
                        category='other',
                        raw_text=raw_text,
                        status='processing',
                        uploaded_by='onedrive_sync',
                        onedrive_item_id=file_info["id"],
                        onedrive_etag=file_info.get("last_modified", ""),
                        onedrive_folder_id=folder_id,
                    )
                    db.session.add(doc)
                    db.session.flush()

                    chunks = ks._chunk_text(raw_text)
                    ks._create_entries_with_embeddings(doc, chunks)

                    doc.status = 'active'
                    db.session.commit()
                    stats["synced"] += 1
                    logger.info(f"OneDrive sync: Added '{file_info['name']}' ({len(chunks)} chunks)")
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"OneDrive sync: Failed to process {file_info['name']}: {e}")
                    stats["errors"] += 1

            time.sleep(0.5)

        orphaned_docs = KnowledgeDocument.query.filter(
            KnowledgeDocument.onedrive_folder_id == folder_id,
            KnowledgeDocument.status == 'active',
            KnowledgeDocument.doc_type == 'onedrive_sync',
            ~KnowledgeDocument.onedrive_item_id.in_(remote_file_ids) if remote_file_ids else True,
        ).all()

        for doc in orphaned_docs:
            doc.status = 'deleted'
            stats["removed"] += 1
            logger.info(f"OneDrive sync: Removed orphaned doc '{doc.title}' (file no longer in OneDrive)")

        if orphaned_docs:
            db.session.commit()

        sync_folder.last_synced_at = datetime.utcnow()
        sync_folder.last_sync_files = stats["synced"] + stats["updated"]
        db.session.commit()

        logger.info(f"OneDrive sync complete for '{sync_folder.folder_name}': {stats}")
        return stats

    def sync_all_folders(self) -> Dict:
        from models import OneDriveSyncFolder

        folders = OneDriveSyncFolder.query.filter_by(sync_enabled=True).all()
        if not folders:
            return {"message": "No sync folders configured", "folders_synced": 0}

        total_stats = {"folders_synced": 0, "total_synced": 0, "total_updated": 0, "total_removed": 0, "total_errors": 0}

        for folder in folders:
            try:
                result = self.sync_folder_to_knowledge(folder.onedrive_folder_id)
                if "error" not in result:
                    total_stats["folders_synced"] += 1
                    total_stats["total_synced"] += result.get("synced", 0)
                    total_stats["total_updated"] += result.get("updated", 0)
                    total_stats["total_removed"] += result.get("removed", 0)
                else:
                    total_stats["total_errors"] += 1
                    logger.warning(f"OneDrive sync error for '{folder.folder_name}': {result['error']}")
            except Exception as e:
                total_stats["total_errors"] += 1
                logger.error(f"OneDrive sync failed for folder '{folder.folder_name}': {e}")

        logger.info(f"OneDrive sync all complete: {total_stats}")
        return total_stats
