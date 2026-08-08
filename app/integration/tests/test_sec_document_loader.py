from datetime import date
from pathlib import Path

import httpx
import pytest

from app.dilution_sec_engine import FilingDocumentReference, LoadedFilingDocument
from app.integration.sec_document_loader import SecArchiveDocumentLoader


@pytest.mark.asyncio
async def test_sec_document_loader_streams_to_limit_and_caches_by_accession(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=b"1234567890EXTRA")

    reference = FilingDocumentReference(
        cik="0001863934",
        accession_number="0001062993-26-003146",
        form="SUPPL",
        filed_at=date(2026, 6, 10),
        primary_document="primary.htm",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        loader = SecArchiveDocumentLoader(
            client=client,
            user_agent="MarketBot/0.1 operator@marketbot.test",
            cache_root=tmp_path,
            max_bytes=10,
        )
        first = await loader.load_text(reference)
        second = await loader.load_text(reference)

    assert first == LoadedFilingDocument(text="1234567890", truncated=True)
    assert second == first
    assert len(requests) == 1
    assert requests[0].url.path.endswith(
        "/1863934/000106299326003146/primary.htm"
    )
