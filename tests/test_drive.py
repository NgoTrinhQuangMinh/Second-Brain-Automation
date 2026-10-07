from brain_loader.drive import FOLDER, SHORTCUT, Drive


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self, **kwargs):
        return self.value


def test_drive_pagination_nested_folders_and_shortcut_cycles():
    class Service:
        def files(self):
            return self

        def get(self, fileId, **kwargs):
            return Request({"id": "root", "mimeType": FOLDER, "name": "Root"})

        def list(self, q, pageToken, **kwargs):
            if "'child'" in q:
                return Request(
                    {
                        "files": [
                            {
                                "id": "shortcut",
                                "name": "Cycle",
                                "mimeType": SHORTCUT,
                                "shortcutDetails": {"targetId": "root"},
                            },
                            {"id": "second", "name": "Second.pdf", "mimeType": "application/pdf"},
                        ]
                    }
                )
            if pageToken:
                return Request(
                    {"files": [{"id": "first", "name": "First.pdf", "mimeType": "application/pdf"}]}
                )
            return Request(
                {"nextPageToken": "page2", "files": [{"id": "child", "name": "Nested", "mimeType": FOLDER}]}
            )

    files = Drive(Service()).inventory("root")
    assert {item["id"] for item in files} == {"first", "second"}
    assert files[1]["path"] == "Root/Nested/Second.pdf"
