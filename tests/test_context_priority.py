"""The application gate should favor direct student-vehicle research."""

from models import Paper
from ranking import application_context_signal, rank_papers


def test_direct_baja_context_outranks_general_offroad_transfer():
    direct = Paper("", "LoRa telemetry for Baja SAE", sources=["openalex"])
    transfer = Paper("", "LoRa telemetry for an off-road vehicle", sources=["openalex"])
    assert application_context_signal(direct) > application_context_signal(transfer)
    ranked = rank_papers([transfer, direct], ["LoRa telemetry"], technical_focus="LoRa")
    assert ranked[0].title == direct.title


def test_bare_baja_vehicle_title_is_context_not_spanish_adjective():
    paper = Paper("", "Sistema de telemetria para veículos de competição Baja baseado em LoRa")
    assert application_context_signal(paper) == 0.95
