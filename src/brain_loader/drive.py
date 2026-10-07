import os
from pathlib import Path

from .core import save_json

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
EXPORTS = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
}
EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".html",
    ".htm",
    ".md",
    ".txt",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}
FIELDS = "id,name,mimeType,modifiedTime,version,md5Checksum,webViewLink,shortcutDetails,capabilities"


def authenticate():
    from google_auth_oauthlib.flow import InstalledAppFlow

    client = os.getenv("GOOGLE_OAUTH_CLIENT_FILE", ".secrets/google-client.json")
    token = Path(os.getenv("GOOGLE_TOKEN_FILE", ".secrets/google-token.json"))
    credentials = InstalledAppFlow.from_client_secrets_file(client, SCOPES).run_local_server(port=0)
    import json

    save_json(token, json.loads(credentials.to_json()))


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google.oauth2.service_account import Credentials as ServiceCredentials

    account = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if account:
        return ServiceCredentials.from_service_account_file(account, scopes=SCOPES)
    token = Path(os.getenv("GOOGLE_TOKEN_FILE", ".secrets/google-token.json"))
    if not token.exists():
        raise ValueError("Set GOOGLE_APPLICATION_CREDENTIALS or run brain-loader auth")
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        import json

        save_json(token, json.loads(creds.to_json()))
    if not creds.valid:
        raise ValueError("Google credentials expired; run brain-loader auth")
    return creds


class Drive:
    def __init__(self, service=None):
        if service is None:
            from googleapiclient.discovery import build

            service = build("drive", "v3", credentials=credentials(), cache_discovery=False)
        self.service = service

    def inventory(self, root):
        root_meta = (
            self.service.files()
            .get(fileId=root, fields=FIELDS, supportsAllDrives=True)
            .execute(num_retries=5)
        )
        if root_meta["mimeType"] != FOLDER:
            raise ValueError("DRIVE_FOLDER_ID must identify a folder")
        queue = [(root, root_meta["name"])]
        seen_folders, seen_files, results = set(), set(), []
        while queue:
            folder, parent = queue.pop()
            if folder in seen_folders:
                continue
            seen_folders.add(folder)
            page = None
            while True:
                response = (
                    self.service.files()
                    .list(
                        q=f"'{folder}' in parents and trashed = false",
                        pageSize=1000,
                        pageToken=page,
                        fields=f"nextPageToken,incompleteSearch,files({FIELDS})",
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute(num_retries=5)
                )
                if response.get("incompleteSearch"):
                    raise RuntimeError(
                        "Drive returned an incomplete inventory; refusing to treat it as complete"
                    )
                for original in response.get("files", []):
                    item = dict(original)
                    path = parent + "/" + item["name"]
                    if item["mimeType"] == SHORTCUT:
                        try:
                            item = (
                                self.service.files()
                                .get(
                                    fileId=item["shortcutDetails"]["targetId"],
                                    fields=FIELDS,
                                    supportsAllDrives=True,
                                )
                                .execute(num_retries=5)
                            )
                        except Exception as error:
                            original.update(path=path, inventory_error=type(error).__name__)
                            results.append(original)
                            continue
                    if item["mimeType"] == FOLDER:
                        queue.append((item["id"], path))
                    elif item["id"] not in seen_files:
                        seen_files.add(item["id"])
                        item["path"] = path
                        results.append(item)
                page = response.get("nextPageToken")
                if not page:
                    break
        return sorted(results, key=lambda item: item["path"])

    def download(self, item, directory):
        from googleapiclient.http import MediaIoBaseDownload

        mime = item["mimeType"]
        if not item.get("capabilities", {}).get("canDownload", True):
            raise PermissionError("Drive disallows download for this file")
        if mime in EXPORTS:
            export_mime, extension = EXPORTS[mime]
            request = self.service.files().export_media(fileId=item["id"], mimeType=export_mime)
        else:
            extension = Path(item["name"]).suffix.lower()
            if extension not in EXTENSIONS:
                raise ValueError(f"Unsupported file type: {mime} ({extension})")
            request = self.service.files().get_media(fileId=item["id"], supportsAllDrives=True)
        path = directory / ("source" + extension)
        if path.exists():
            return path
        directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(extension + ".part")
        with temporary.open("wb") as stream:
            loader = MediaIoBaseDownload(stream, request)
            done = False
            while not done:
                _, done = loader.next_chunk(num_retries=5)
        # Do not publish a download if the source changed during transfer.
        latest = (
            self.service.files()
            .get(fileId=item["id"], fields="version,modifiedTime", supportsAllDrives=True)
            .execute(num_retries=5)
        )
        for key in ("version", "modifiedTime"):
            if latest.get(key) != item.get(key):
                raise RuntimeError("Drive file changed during download; rerun to load its new revision")
        temporary.replace(path)
        return path
