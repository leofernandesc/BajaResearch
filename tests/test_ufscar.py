"""Direct UFSCar records preserve official work type and Baja context."""

import httpx

from clients.http import JsonHttpClient
from clients.ufscar import UfscarClient


def test_ufscar_dspace_discovery_normalizes_tcc():
    item = {
        "id": "490ba29e-7b44-458c-8b5d-440ca648faee",
        "handle": "20.500.14289/21659",
        "name": "Sistema de telemetria para veículos de competição Baja baseado em LoRa",
        "metadata": {
            "dc.title": [{"value": "Sistema de telemetria para veículos de competição Baja baseado em LoRa"}],
            "dc.type": [{"value": "TCC"}],
            "dc.contributor.author": [{"value": "Costa, João Paulo"}],
            "dc.date.issued": [{"value": "2025-02-26"}],
            "dc.description.resumo": [{"value": "Telemetria LoRa aplicada a veículos Baja SAE."}],
            "dc.publisher": [{"value": "Universidade Federal de São Carlos"}],
        },
    }

    def handler(request):
        return httpx.Response(200, json={
            "_embedded": {"searchResult": {"_embedded": {
                "objects": [{"_embedded": {"indexableObject": item}}]
            }}}
        }, request=request)

    transport = httpx.Client(transport=httpx.MockTransport(handler))
    client = UfscarClient(http=JsonHttpClient(
        "https://repositorio.ufscar.br/server/api", "ufscar", http_client=transport,
    ))
    try:
        papers = client.search("Baja telemetria")
    finally:
        client.close()
        transport.close()
    assert len(papers) == 1
    assert papers[0].document_type == "bachelor_thesis"
    assert papers[0].year == 2025
    assert papers[0].sources == ["ufscar"]
    assert papers[0].landing_url.endswith(item["id"])
