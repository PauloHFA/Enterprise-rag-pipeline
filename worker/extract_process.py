"""Extraction subprocess: CPU/address-space limits and parent-enforced timeout."""

import base64
import json
import os
import subprocess
import sys
from dataclasses import asdict

from .extraction import ExtractedDocument, TextSection


def extract_isolated(content, mime, filename, **options):
    request = {
        "content": base64.b64encode(content).decode("ascii"),
        "mime": mime,
        "filename": filename,
        "options": options,
    }
    result = subprocess.run(
        [sys.executable, "-m", "worker.extract_process"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=int(os.getenv("EXTRACTION_TIMEOUT_SECONDS", "120")),
        check=False,
    )
    if result.returncode:
        raise ValueError("Extraction subprocess rejected or failed")
    payload = json.loads(result.stdout)
    return ExtractedDocument(
        payload["title"],
        [TextSection(**value) for value in payload["sections"]],
        payload["metadata"],
    )


def main():
    if os.name == "posix":
        import resource

        cpu = int(os.getenv("EXTRACTION_CPU_SECONDS", "90"))
        memory = int(os.getenv("EXTRACTION_MEMORY_MB", "1024")) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from .extraction import extract_document

    request = json.load(sys.stdin)
    document = extract_document(
        base64.b64decode(request["content"]),
        request["mime"],
        request["filename"],
        **request["options"],
    )
    if sum(len(section.text) for section in document.sections) > int(
        os.getenv("MAX_EXTRACTED_CHARS", "2000000")
    ):
        raise ValueError("Extracted text exceeds limit")
    json.dump(asdict(document), sys.stdout)


if __name__ == "__main__":
    main()
