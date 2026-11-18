import pytest

from speedtest import (
    AttemptResult,
    aggregate_statistics,
    bytes_to_mb,
    calculate_speed_mbps,
)


def test_bytes_to_mb() -> None:
    assert bytes_to_mb(2_500_000) == 2.5
    assert bytes_to_mb(0) == 0.0


def test_calculate_speed_mbps() -> None:
    assert calculate_speed_mbps(2_500_000, 2.0) == 10.0


def test_calculate_speed_mbps_with_zero_time() -> None:
    assert calculate_speed_mbps(1_000_000, 0.0) == 0.0


def test_aggregate_statistics_with_empty_results() -> None:
    assert aggregate_statistics([]) is None


def test_aggregate_statistics_with_empty_responses() -> None:
    statistics = aggregate_statistics([AttemptResult(elapsed=0.5, downloaded_bytes=0)])

    assert statistics is not None
    assert statistics.successful_attempts == 1
    assert statistics.total_bytes == 0
    assert statistics.total_mb == 0.0
    assert statistics.speed_mbps == 0.0


def test_aggregate_statistics() -> None:
    statistics = aggregate_statistics(
        [
            AttemptResult(elapsed=0.5, downloaded_bytes=1_000_000),
            AttemptResult(elapsed=1.5, downloaded_bytes=3_000_000),
        ]
    )

    assert statistics is not None
    assert statistics.successful_attempts == 2
    assert statistics.average_time == 1.0
    assert statistics.total_bytes == 4_000_000
    assert statistics.total_mb == 4.0
    assert statistics.speed_mbps == pytest.approx(16.0)


def test_aggregate_statistics_with_zero_total_time() -> None:
    statistics = aggregate_statistics(
        [AttemptResult(elapsed=0.0, downloaded_bytes=1_000_000)]
    )

    assert statistics is not None
    assert statistics.average_time == 0.0
    assert statistics.speed_mbps == 0.0
